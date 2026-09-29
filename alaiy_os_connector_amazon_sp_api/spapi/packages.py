# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Who carried the parcel, when it left, and whether it arrived.

## The gap this closes, and why it was a gap

`spapi/orders.py` has said since it was written that shipment tracking is
"deliberately not touched". That was not a scoping decision so much as a fact
about the API: Orders v0 returns an order and its items and says nothing about
the shipment that carried them. There was no tracking number to read.

Orders API **2026-01-01** adds `packages` to the order, available through
`includedData=PACKAGES`. That changes this from "wait for Amazon" to "adopt the
new version", and this module is that adoption.

## Two fulfilment channels, two entirely separate sources

This is the shape of the whole module and getting it wrong produces a dashboard
that says a seller ships nothing late:

  * **Merchant-fulfilled (MFN)** — the seller picked the carrier and bought the
    label, so the packages are on the *order*, read live through Orders
    2026-01-01.
  * **Amazon-fulfilled (AFN/FBA)** — Amazon picked, packed and shipped, so the
    order carries no packages at all. Asking the Orders API for them returns an
    empty array rather than an error. The data is in the fulfilled-shipments
    report instead.

So `order_packages` is the MFN read, `fba_shipments` is the AFN read, and
`FULFILLMENT_CHANNEL_*` is how a caller decides which to ask. A sync that ran
only the first would report an FBA seller as having no shipments; one that ran
only the second would lose every seller-controlled handling time, which is the
only fulfilment number a seller can actually act on.

## Reading the response tolerantly, on purpose

The package fields are read through `_first`, checking several plausible
spellings for each. That is the same tolerance `spapi.health.parse_performance`
applies to the performance report and for the same reason: this is the newest
API surface anything in this app calls, and the exact casing of a nested field
is the kind of thing that shifts between a preview and general availability.
A carrier name that arrives under a key this module does not know should cost
that one field, not the shipment.

**What is not made tolerant is the absence of the endpoint itself.** If Amazon
refuses the version, `order_packages` raises. An empty package list and a
refused API call mean completely different things — "this order has not shipped"
versus "we cannot see shipments at all" — and a dashboard that conflated them
would show a seller a clean fulfilment record they had not earned.

## What Amazon does not provide at any version

There is no carrier-performance endpoint. On-time percentage, average transit
days and exception classification per carrier are all aggregation over the raw
package rows this module returns, and that aggregation belongs to whoever owns
the seller's own tenancy — see `selfserve/shipping.py` in the self-serve app.
This module returns facts about parcels and computes no rates.

## Role

Orders, same as the existing order sync — a seller already syncing orders can
read their packages. The fulfilled-shipments report needs the **Amazon
Fulfilment** role, which is a different grant; a 403 there costs the FBA half
and leaves the MFN half working.
"""

import csv
import io

import frappe
from frappe.utils import add_days, cint, getdate

from alaiy_os_connector_amazon_sp_api.spapi import reports, times
from alaiy_os_connector_amazon_sp_api.spapi.client import (
	SpApiClient,
	SpApiError,
	describe_forbidden,
)
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	FBA_SHIPMENTS_MAX_WINDOW_DAYS,
	ORDERS_INCLUDED_DATA_PACKAGES,
	ORDERS_PACKAGES_BASE,
	PACKAGE_STATUS_MAP,
	REPORT_FBA_SHIPMENTS,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace


def _first(node: dict, *names):
	"""The first of several plausible key spellings that carries a value.

	See the module docstring on tolerance. Nested paths are given dotted:
	`_first(pkg, "carrier.name", "carrierName")`.
	"""
	for name in names:
		value = node
		for part in name.split("."):
			if not isinstance(value, dict):
				value = None
				break
			value = value.get(part)
		if value not in (None, ""):
			return value
	return None


def normalise_status(raw) -> str:
	"""Amazon's package status as this app's own vocabulary.

	An unmapped status becomes `unknown` rather than the nearest friendly guess
	— see PACKAGE_STATUS_MAP. A new Amazon status quietly reading as "in
	transit" is a parcel that stopped moving and a dashboard that says it is on
	its way.
	"""
	if not raw:
		return "unknown"
	key = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
	return PACKAGE_STATUS_MAP.get(key, "unknown")


def _package_row(order_id: str, package: dict) -> dict:
	"""One `packages[]` entry, flattened."""
	raw_status = _first(package, "packageStatus", "status", "shipmentStatus")

	return {
		"order_id": order_id,
		"package_id": _first(package, "packageId", "packageNumber", "shipmentId", "id"),
		"carrier_code": _first(package, "carrierCode", "carrier.code", "carrierId"),
		"carrier_name": _first(package, "carrierName", "carrier.name", "carrier"),
		"tracking_number": _first(package, "trackingNumber", "tracking.number", "trackingId"),
		"package_status": normalise_status(raw_status),
		"package_status_raw": raw_status,
		"ship_date": times.from_amazon_iso(_first(package, "shipDate", "shipmentDate", "shippedDate")),
		"estimated_delivery_date": times.from_amazon_iso(
			_first(package, "estimatedDeliveryDate", "estimatedArrivalDate", "promisedDeliveryDate")
		),
		"delivered_date": times.from_amazon_iso(
			_first(package, "deliveryDate", "deliveredDate", "actualDeliveryDate")
		),
		# Which lines travelled in this parcel, when Amazon says. A split
		# shipment carries a subset, and per-SKU carrier attribution needs it.
		"skus": [
			sku
			for sku in (
				_first(item, "sellerSku", "sellerSKU", "sku")
				for item in (package.get("packageItems") or package.get("items") or [])
				if isinstance(item, dict)
			)
			if sku
		],
		"source": "orders_api",
		"raw": package,
	}


def _packages_of(payload) -> tuple[str | None, list]:
	"""(order id, packages) out of a getOrder response, either envelope."""
	payload = payload or {}
	order = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
	order = order or {}
	order_id = _first(order, "amazonOrderId", "AmazonOrderId", "orderId")
	packages = order.get("packages") or order.get("Packages") or []
	return order_id, [p for p in packages if isinstance(p, dict)]


def order_packages(order_ids, client=None, connection=None) -> dict:
	"""Packages per merchant-fulfilled order, keyed by Amazon order id.

	One `getOrder` per order — the endpoint takes a single id, and a caller with
	a day's orders pays a call each. That is why the self-serve sync only asks
	about orders it does not already have a shipped package for.

	Returns `{order_id: [package, ...]}`, with an order Amazon answered for but
	that has not shipped mapping to `[]`. **Raises** rather than returning empty
	when Amazon refuses the call: see the module docstring on why those two
	cannot be allowed to look alike.
	"""
	order_ids = [o for o in dict.fromkeys(str(o).strip() for o in (order_ids or [])) if o]
	if not order_ids:
		return {}

	client = client or SpApiClient(connection)
	out: dict = {}

	for order_id in order_ids:
		try:
			response = client.get(
				f"{ORDERS_PACKAGES_BASE}/{order_id}",
				params={"includedData": ORDERS_INCLUDED_DATA_PACKAGES},
				context="shipping",
			)
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			raise

		_returned_id, packages = _packages_of(response)
		out[order_id] = [_package_row(order_id, package) for package in packages]

	return out


# ---------------------------------------------------------------------------
# The FBA half. A report, not an API call — see the module docstring.
# ---------------------------------------------------------------------------

#: The report's column names, which are hyphenated and lower-cased. Held here
#: rather than inline so the mapping reads as one table.
_FBA_COLUMNS = {
	"order_id": ("amazon-order-id",),
	"package_id": ("shipment-id",),
	"sku": ("sku", "seller-sku"),
	"quantity": ("quantity-shipped",),
	"carrier_name": ("carrier",),
	"tracking_number": ("tracking-number",),
	"ship_date": ("shipment-date",),
	"estimated_delivery_date": ("estimated-arrival-date",),
	"purchase_date": ("purchase-date",),
	"fulfillment_center": ("fulfillment-center-id",),
}


def parse_fba_shipments(text) -> list:
	"""The fulfilled-shipments report as package rows.

	One row per shipped *item*, which is not one row per parcel: a two-line
	order shipped together appears twice with the same `shipment-id`. They are
	returned as they arrive rather than grouped, because grouping needs a
	decision — by shipment, by order, by tracking number — that depends on what
	the caller is counting, and this module does not know.

	A shipment already in the report is, by definition, shipped: the report only
	contains shipments Amazon has made. So the status is `in_transit` unless a
	delivery date says otherwise, and never `pending`.
	"""
	if not text:
		return []

	rows = []
	for raw in csv.DictReader(io.StringIO(text), delimiter="\t"):
		row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}

		def pick(field):
			for column in _FBA_COLUMNS[field]:
				if row.get(column):
					return row[column]
			return None

		order_id = pick("order_id")
		if not order_id:
			continue

		rows.append(
			{
				"order_id": order_id,
				"package_id": pick("package_id"),
				"carrier_code": None,
				"carrier_name": pick("carrier_name"),
				"tracking_number": pick("tracking_number"),
				# See the docstring: presence in this report is the shipment.
				"package_status": "in_transit",
				"package_status_raw": None,
				"ship_date": times.from_amazon_iso(pick("ship_date")),
				"estimated_delivery_date": times.from_amazon_iso(pick("estimated_delivery_date")),
				# The report carries no actual delivery date. Amazon's own
				# On-Time Delivery Rate metric is the figure for that, and it is
				# already synced — inventing a delivery from an estimate would
				# produce a second, disagreeing on-time number.
				"delivered_date": None,
				"purchase_date": times.from_amazon_iso(pick("purchase_date")),
				"fulfillment_center": pick("fulfillment_center"),
				"skus": [pick("sku")] if pick("sku") else [],
				"quantity": cint(pick("quantity")),
				"source": "fba_report",
			}
		)
	return rows


def fba_shipments(date_from, date_to=None, marketplace=None, client=None, connection=None) -> list:
	"""Amazon's own shipments for this seller over a window.

	The window is capped at `FBA_SHIPMENTS_MAX_WINDOW_DAYS` and refused rather
	than silently truncated when a caller asks for more: a backfill that quietly
	returned one month of a three-month request would leave two months looking
	like a seller with no FBA shipments.

	Returns `[]` when the report comes back cancelled, which is Amazon's way of
	saying there is no data in the window — a merchant-only seller, or a quiet
	week, not a failure.
	"""
	date_from = getdate(date_from)
	date_to = getdate(date_to) if date_to else getdate(add_days(date_from, 6))
	span = (date_to - date_from).days + 1
	if span > FBA_SHIPMENTS_MAX_WINDOW_DAYS:
		frappe.throw(
			f"Amazon degrades badly on a fulfilled-shipments window this wide; "
			f"asked for {span} days, the cap is {FBA_SHIPMENTS_MAX_WINDOW_DAYS}. "
			f"Walk a longer backfill in chunks."
		)

	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)

	try:
		text = reports.fetch_report(
			REPORT_FBA_SHIPMENTS,
			[mp.marketplace_id],
			data_start=f"{date_from}T00:00:00Z",
			data_end=f"{date_to}T23:59:59Z",
			client=client,
			context="shipping",
		)
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		raise

	return parse_fba_shipments(text)
