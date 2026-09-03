# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Full-catalog listing reconciliation via the Merchant Listings report.

searchListingsItems (see listings.sync_all_listings) caps at ~1000 SKUs per
marketplace, so it cannot reconcile large catalogs. The
GET_MERCHANT_LISTINGS_ALL_DATA report returns *every* SKU as a TSV with no such
cap; we use it to reconcile offer/status fields (status, price, quantity, asin,
fulfillment channel).

Content and variation parentage are *also* this module's job, and that is not
obvious. They come from the Catalog Items API, which the report cannot supply —
and the Listings-API sync that does fetch them (listings.sync_all_listings) stops
at its ~1000-SKU cap. So for any catalog bigger than that, this is the only path
that ever fills title, description, bullets, keywords, images and parentage. It
used to fill none of them, which left every row past the cap showing its SKU
instead of a title and an empty variation family.

Enrichment happens once per row, not once per run: `catalog_synced_at` marks a
row as done, so a steady-state reconcile makes no catalog calls at all.
RECONCILE_CATALOG_BUDGET bounds the first run over a large catalog, and what it
defers is reported and picked up next run rather than silently dropped.

The report's own `item-name`, `item-description` and `image-url` remain a
fallback, filling only the gaps the catalog left. Rows currently in `pending`
state (a just-pushed change Amazon has not confirmed yet) are skipped so
reconciliation never fights an in-flight update.
"""

import csv
import io

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi import catalog, reports
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	RECONCILE_CATALOG_BUDGET,
	REPORT_MERCHANT_LISTINGS_ALL,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import (
	_apply_content,
	_apply_variation,
	_marketplace,
)

# The report's `status` column -> our listing_status. The report does not expose
# suppression (no issues), so non-active rows land as inactive.
_REPORT_STATUS = {"active": "active", "inactive": "inactive"}
_COMMIT_EVERY = 200  # persist periodically so a long run makes forward progress


def reconcile_all_listings(marketplace=None, notify_user=None, connection=None):
	"""Reconcile every listing for a marketplace from the Merchant Listings report.

	Intended to run as a background/scheduled job. Publishes an
	`amazon_reconcile_complete` realtime event to `notify_user` when done.
	"""
	conn = connections.resolve(connection)
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))
	mp = _marketplace(marketplace)
	client = SpApiClient(conn)

	try:
		text = reports.fetch_report(
			REPORT_MERCHANT_LISTINGS_ALL, [mp.marketplace_id], client=client, context="reconcile"
		)
		summary = _apply_report(mp, text, client=client)
		summary.update({"success": True, "marketplace": mp.name})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Amazon listing reconciliation failed", message=frappe.get_traceback())
		summary = {"success": False, "marketplace": mp.name, "error": str(e)}

	if notify_user:
		frappe.publish_realtime("amazon_reconcile_complete", summary, user=notify_user)
	return summary


def _needs_enrichment(mp, rows):
	"""The report rows whose SKU has never had catalog content applied.

	Asked as one query rather than per row: a reconcile can walk tens of thousands
	of rows, and this decides whether each one costs an API call.
	"""
	done = set(
		frappe.get_all(
			"Amazon Product Listing",
			filters={"marketplace": mp.name, "catalog_synced_at": ["is", "set"]},
			pluck="name",
		)
	)
	return [r for r in rows if r.get("asin1") and r["seller-sku"] not in done]


def _enrich_from_catalog(mp, rows, client):
	"""{asin: content} for the rows that still need it, within the run's budget.

	Returns the map plus counts, because "we did not enrich these" has to end up
	in the run summary. A deferred row is normal (budget) and a missing one is not
	(Amazon returned nothing for the ASIN, or the batch errored inside
	catalog.fetch_content) — conflating them would hide a real failure behind a
	number that looks like throttling.
	"""
	stats = {"enriched": 0, "enrich_deferred": 0, "enrich_missing": 0}
	if not client:
		return {}, stats

	pending = _needs_enrichment(mp, rows)
	budgeted = pending[:RECONCILE_CATALOG_BUDGET]
	stats["enrich_deferred"] = len(pending) - len(budgeted)
	if not budgeted:
		return {}, stats

	asins = list(dict.fromkeys(r["asin1"] for r in budgeted))
	content = catalog.fetch_content(asins, mp, client=client)
	stats["enriched"] = len(content)
	stats["enrich_missing"] = len(asins) - len(content)
	return content, stats


def _apply_report(mp, text, client=None):
	"""Parse the report TSV and upsert each SKU's offer/status fields.

	Content and parentage ride along for rows that have never had them — see the
	module docstring for why that belongs here rather than only in the
	Listings-API sync.
	"""
	rows = [r for r in _parse_rows(text) if r.get("seller-sku")]
	content_by_asin, stats = _enrich_from_catalog(mp, rows, client)

	seen = created = updated = skipped_pending = 0
	by_status = {}

	for row in rows:
		sku = row["seller-sku"]
		seen += 1
		catalog_content = content_by_asin.get(row.get("asin1")) if row.get("asin1") else None

		status = _REPORT_STATUS.get((row.get("status") or "").strip().lower(), "inactive")
		# Offer/status fields — reconciled on every row (Amazon is the source of truth).
		fields = {
			"listing_status": status,
			"asin": row.get("asin1") or None,
			"price": flt(row.get("price")) if row.get("price") else None,
			"quantity": cint(row.get("quantity")) if row.get("quantity") not in (None, "") else None,
			"fulfillment_channel": _fulfillment(row.get("fulfillment-channel")),
		}

		if frappe.db.exists("Amazon Product Listing", sku):
			doc = frappe.get_doc("Amazon Product Listing", sku)
			# Don't overwrite an in-flight push that Amazon hasn't confirmed yet.
			if doc.listing_status == "pending":
				skipped_pending += 1
				continue
			_assign(doc, fields, is_new=False)
			_apply_catalog(doc, mp, catalog_content)
			_seed_content(doc, row)
			doc.last_synced_at = now_datetime()
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			doc = frappe.get_doc(
				{"doctype": "Amazon Product Listing", "sku": sku, "marketplace": mp.name, "currency": mp.currency}
			)
			_assign(doc, fields, is_new=True)
			_apply_catalog(doc, mp, catalog_content)
			_seed_content(doc, row)
			doc.last_synced_at = now_datetime()
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			created += 1

		by_status[status] = by_status.get(status, 0) + 1
		if seen % _COMMIT_EVERY == 0:
			frappe.db.commit()

	frappe.db.commit()
	if stats["enrich_deferred"]:
		# A capped run must say so: "reconciled 40,000" reads as full coverage
		# otherwise, and the missing titles would look like a bug rather than a
		# queue.
		frappe.logger("amazon_seller").info(
			f"Reconcile enriched {stats['enriched']} SKUs from the catalog; "
			f"{stats['enrich_deferred']} deferred to the next run "
			f"(budget {RECONCILE_CATALOG_BUDGET})."
		)
	return {
		"seen": seen,
		"created": created,
		"updated": updated,
		"skipped_pending": skipped_pending,
		"by_status": by_status,
		**stats,
	}


def _apply_catalog(doc, mp, catalog_content):
	"""Apply catalog content + parentage, reusing the Listings-sync precedence.

	Passed an empty Listings payload on purpose: there is none here, so
	_apply_content falls straight through to the catalog values and _apply_variation
	sees a definitive answer. Called *before* _seed_content so the catalog wins and
	the report's thinner columns only fill what it left — that ordering is what
	stops a variation parent keeping the SKU-ish string the report calls its
	`item-name`.

	`catalog_synced_at` is stamped only when an answer actually arrived, so a row
	Amazon returned nothing for is retried on the next run instead of being marked
	done.
	"""
	if catalog_content is None:
		return
	_apply_content(doc, mp, {}, {}, catalog_content=catalog_content)
	_apply_variation(doc, catalog_content)
	doc.catalog_synced_at = now_datetime()


def _seed_content(doc, row):
	"""Fill title / description / main image from the report, gaps only.

	Fill-only in both directions: it seeds a newly discovered SKU, and it repairs
	a row that reached us through a path with no content (an offer-only listing,
	or a variation parent whose Listings summary carries no itemName — those are
	the rows that end up displaying their SKU because `title` is NULL and
	title_field falls back to `name`). It never overwrites content that is already
	there, which is what keeps operator edits and richer spapi.catalog content
	safe from a thinner report column.
	"""
	# doc.get(), not doc.title: on the insert path the field was never assigned,
	# and BaseDocument has no __getattr__ — a bare attribute read raises.
	if not doc.get("title") and row.get("item-name"):
		doc.title = row["item-name"]
	if not doc.get("description") and row.get("item-description"):
		doc.description = row["item-description"]
	if not doc.get("images") and row.get("image-url"):
		doc.set("images", [{"image_url": row["image-url"], "is_main": 1}])


def _assign(doc, fields, *, is_new):
	"""Set offer/status fields, leaving unknowns (None) untouched on existing rows."""
	for key, value in fields.items():
		# On an existing row, a missing column shouldn't blank an existing value;
		# on a new row we set whatever we have.
		if value is None and not is_new:
			continue
		doc.set(key, value)


def _fulfillment(raw):
	"""Report gives DEFAULT (MFN) or AMAZON_<region> (FBA)."""
	return "AMAZON" if (raw or "").strip().upper().startswith("AMAZON") else "DEFAULT"


def _parse_rows(text):
	"""Tab-separated report with a header row -> list of {column: value} dicts.

	QUOTE_NONE is not optional here. Amazon's report TSVs are not quoted, but
	`item-name` and `item-description` are seller free text — and when one of them
	*begins* with a double quote (a quoted phrase, an inch measurement) csv's
	default quotechar reads it as an opening quote and consumes tabs and newlines
	until it finds a closing one. An unbalanced quote therefore eats the rest of
	the row and however many rows follow, which surfaces as scrambled titles and
	statuses, or as SKUs that silently never reconcile at all.
	"""
	if not text:
		return []
	reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
	rows = []
	for raw in reader:
		rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items() if k})
	return rows
