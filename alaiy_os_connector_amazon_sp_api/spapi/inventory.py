# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""What Amazon is holding, which no other endpoint in this app can tell you.

## Why this module has to exist

The obvious place to read FBA stock is the quantity already on every listing —
`fulfillmentAvailability`, which `spapi.listings` fetches for free alongside
status and price. It is the wrong number, and quietly so. That field is the
quantity **the seller declared**, and this app's own vocabulary says as much:
`constants.FULFILLMENT_CHANNEL_CODES` annotates AMAZON as "quantity managed by
Amazon". A seller running FBA declares nothing, because Amazon is counting for
them — so the field is absent or frozen at whatever it held when the SKU last
shipped merchant-fulfilled.

Read as stock, that reports **zero for a product with a pallet in a fulfilment
centre**, and it does it without erroring. Anything computing days of cover on
top would put the seller's best-stocked SKU at the top of the reorder list.

The real figure is in the FBA Inventory API, and it is not one number.

## Five quantities, and why the difference matters

Amazon returns a breakdown per SKU, and collapsing it is the mistake this
module is shaped to avoid:

  * **fulfillable** — in a fulfilment centre, sellable today. The only one that
    is stock in the sense a reorder decision means.
  * **inbound** (working + shipped + receiving) — bought, not yet sellable.
    Real, and worth showing, but it is *incoming*: counting it as cover is how
    a seller stocks out while their dashboard says nine days.
  * **reserved** — allocated to orders already placed. Physically present,
    already spoken for.
  * **unfulfillable** — damaged, expired, or otherwise unsellable. Present in
    `totalQuantity` and never sellable again.
  * **researching** — Amazon has lost track of it and is looking.

`totalQuantity` sums all of those, which is why this module keeps it as a
reported figure and never as the answer to "how much do I have". A caller that
wants one number wants `fulfillable_qty`.

## Scope

Fetching is separate from storing on purpose. `fetch_summaries` is a pure read
that any consumer can use with its own tenancy model — which is what the
self-serve app needs, since it keys everything on its own workspace and cannot
share a site-global register. `sync_inventory` is the connector's own
persistence for benches that want the rows in the desk.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi import times
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	FBA_INVENTORY_GRANULARITY,
	FBA_INVENTORY_MAX_PAGES,
	FBA_INVENTORY_SUMMARIES_PATH,
)

DOCTYPE = "Amazon FBA Inventory"


def _int(value):
	"""A quantity, or 0. Amazon omits a breakdown key rather than sending zero."""
	return cint(value or 0)


def summary_from(entry: dict) -> dict:
	"""One `inventorySummaries` entry, flattened.

	The nested totals (`reservedQuantity.totalReservedQuantity` and friends)
	each also carry a breakdown — which fulfilment centre, which kind of damage
	— that nothing upstream asks for yet. Only the totals are kept; the raw
	entry rides along under `raw` for a caller that needs more.
	"""
	details = entry.get("inventoryDetails") or {}
	reserved = details.get("reservedQuantity") or {}
	researching = details.get("researchingQuantity") or {}
	unfulfillable = details.get("unfulfillableQuantity") or {}

	inbound_working = _int(details.get("inboundWorkingQuantity"))
	inbound_shipped = _int(details.get("inboundShippedQuantity"))
	inbound_receiving = _int(details.get("inboundReceivingQuantity"))

	return {
		"seller_sku": entry.get("sellerSku"),
		"asin": entry.get("asin"),
		"fnsku": entry.get("fnSku"),
		"condition": entry.get("condition"),
		"product_name": entry.get("productName"),
		# The one that means "sellable today".
		"fulfillable_qty": _int(details.get("fulfillableQuantity")),
		"inbound_working_qty": inbound_working,
		"inbound_shipped_qty": inbound_shipped,
		"inbound_receiving_qty": inbound_receiving,
		"inbound_qty": inbound_working + inbound_shipped + inbound_receiving,
		"reserved_qty": _int(reserved.get("totalReservedQuantity")),
		"researching_qty": _int(researching.get("totalResearchingQuantity")),
		"unfulfillable_qty": _int(unfulfillable.get("totalUnfulfillableQuantity")),
		# Amazon's own sum across every bucket above. Reported, never treated
		# as stock — see the module docstring.
		"total_qty": _int(entry.get("totalQuantity")),
		"last_updated_at": times.from_amazon_iso(entry.get("lastUpdatedTime")),
		"raw": entry,
	}


def _page(payload: dict) -> tuple[list, str | None]:
	"""(summaries, nextToken) out of one response.

	Both live in places that have moved between gateway versions: the summaries
	under `payload` and the token under a sibling `pagination`, but unwrapped
	responses do exist. Checking both costs two dict lookups and saves a sync
	that silently returns one page.
	"""
	body = payload.get("payload") or payload
	summaries = body.get("inventorySummaries") or []
	pagination = payload.get("pagination") or body.get("pagination") or {}
	return summaries, pagination.get("nextToken")


def fetch_summaries(
	marketplace_id: str,
	*,
	client=None,
	connection=None,
	seller_skus: list[str] | None = None,
	context: str = "inventory",
) -> list[dict]:
	"""Every FBA SKU Amazon holds in one marketplace, flattened by `summary_from`.

	`seller_skus` narrows the read to specific SKUs. Amazon caps that list at 50
	per call, and this does not batch beyond it: the whole-marketplace read is
	one paged call and is cheaper than slicing, so a caller wanting more than 50
	SKUs should ask for all of them and filter.

	Raises on failure rather than swallowing. A stock figure that is quietly
	absent is indistinguishable from genuinely having none, and the caller is
	the only one that knows whether it would rather show nothing or stop.
	"""
	client = client or SpApiClient(connection)

	params = {
		"details": "true",
		"granularityType": FBA_INVENTORY_GRANULARITY,
		"granularityId": marketplace_id,
		"marketplaceIds": marketplace_id,
	}
	if seller_skus:
		params["sellerSkus"] = ",".join(seller_skus[:50])

	out = []
	next_token = None
	pages = 0

	while pages < FBA_INVENTORY_MAX_PAGES:
		if next_token:
			# With a token Amazon wants the token and the granularity, nothing
			# else — a repeated `sellerSkus` on a continuation is rejected.
			params = {
				"granularityType": FBA_INVENTORY_GRANULARITY,
				"granularityId": marketplace_id,
				"marketplaceIds": marketplace_id,
				"nextToken": next_token,
			}

		try:
			payload = client.get(FBA_INVENTORY_SUMMARIES_PATH, params=params, context=context)
		except SpApiError as e:
			if e.is_forbidden():
				# The FBA Inventory role is separate from the listings one, so a
				# seller can be fully connected and still 403 here. Say which.
				raise SpApiError(
					describe_forbidden(e)
					+ " This call needs the 'Amazon Fulfilment' role for FBA inventory.",
					status_code=e.status_code,
					error_code=e.error_code,
					path=e.path,
					body=e.body,
				)
			raise

		summaries, next_token = _page(payload or {})
		out.extend(summary_from(entry) for entry in summaries if entry.get("sellerSku"))
		pages += 1
		if not next_token:
			break

	return out


# --- connector-side persistence ----------------------------------------------
def _row_key(connection: str, marketplace_id: str, seller_sku: str) -> str:
	"""What makes a stock row unique.

	All three parts, and the connection first. A key of SKU alone is the bug
	this app already carries elsewhere: on a bench with two sellers, both of
	whom have a SKU called TOTE-001, the second sync overwrites the first and a
	read afterwards returns the other seller's stock.
	"""
	return f"{connection}::{marketplace_id}::{seller_sku}"


def _upsert(connection: str, marketplace_id: str, summary: dict, synced_at) -> None:
	key = _row_key(connection, marketplace_id, summary["seller_sku"])
	name = frappe.db.get_value(DOCTYPE, {"row_key": key}, "name")

	values = {
		"connection": connection,
		"marketplace": marketplace_id,
		"row_key": key,
		"seller_sku": summary["seller_sku"],
		"asin": summary.get("asin"),
		"fnsku": summary.get("fnsku"),
		"condition": summary.get("condition"),
		"product_name": summary.get("product_name"),
		"fulfillable_qty": summary["fulfillable_qty"],
		"inbound_working_qty": summary["inbound_working_qty"],
		"inbound_shipped_qty": summary["inbound_shipped_qty"],
		"inbound_receiving_qty": summary["inbound_receiving_qty"],
		"inbound_qty": summary["inbound_qty"],
		"reserved_qty": summary["reserved_qty"],
		"researching_qty": summary["researching_qty"],
		"unfulfillable_qty": summary["unfulfillable_qty"],
		"total_qty": summary["total_qty"],
		"amazon_updated_at": summary.get("last_updated_at"),
		"synced_at": synced_at,
	}

	if name:
		doc = frappe.get_doc(DOCTYPE, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return

	doc = frappe.new_doc(DOCTYPE)
	doc.update(values)
	doc.insert(ignore_permissions=True)


def sync_inventory(connection=None, marketplace=None) -> dict:
	"""Refresh this seller's FBA stock into `Amazon FBA Inventory`.

	Rows are updated in place rather than appended: this is current stock, and
	the history that would make a snapshot table worth keeping is a different
	feature with a different retention question. A SKU that has left FBA stops
	being returned and keeps its last known row — deleting it would erase the
	only record that Amazon ever held any, and the `synced_at` stamp already
	says the figure is stale.
	"""
	conn = connections.resolve(connection)
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))

	from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

	mp = _marketplace(marketplace, connection=conn)
	client = SpApiClient(conn)
	synced_at = now_datetime()

	summaries = fetch_summaries(mp.marketplace_id, client=client, context="inventory")
	for summary in summaries:
		_upsert(conn.name, mp.marketplace_id, summary, synced_at)

	frappe.db.commit()
	return {
		"connection": conn.name,
		"marketplace": mp.marketplace_id,
		"skus": len(summaries),
		"fulfillable_units": sum(s["fulfillable_qty"] for s in summaries),
		"inbound_units": sum(s["inbound_qty"] for s in summaries),
		"synced_at": synced_at,
	}
