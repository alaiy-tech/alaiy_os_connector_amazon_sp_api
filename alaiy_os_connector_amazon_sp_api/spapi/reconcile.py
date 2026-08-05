# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Full-catalog listing reconciliation via the Merchant Listings report.

searchListingsItems (see listings.sync_all_listings) caps at ~1000 SKUs per
marketplace, so it cannot reconcile large catalogs. The
GET_MERCHANT_LISTINGS_ALL_DATA report returns *every* SKU as a TSV with no such
cap; we use it to reconcile offer/status fields (status, price, quantity, asin,
fulfillment channel).

The report carries `item-name`, `item-description` and `image-url`, so it can
seed those three catalog-wide for free — but only into rows that don't have them
yet. Once a row has content it is either Amazon's (via spapi.catalog) or the
operator's, and neither should lose to a report column. Bullet points and
keywords have no columns at all here; those come from spapi.catalog.

Rows currently in `pending` state (a just-pushed change Amazon has not confirmed
yet) are skipped so reconciliation never fights an in-flight update.
"""

import csv
import io

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from alaiy_os_connector_amazon_sp_api.spapi import reports
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient
from alaiy_os_connector_amazon_sp_api.spapi.constants import REPORT_MERCHANT_LISTINGS_ALL
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

# The report's `status` column -> our listing_status. The report does not expose
# suppression (no issues), so non-active rows land as inactive.
_REPORT_STATUS = {"active": "active", "inactive": "inactive"}
_COMMIT_EVERY = 200  # persist periodically so a long run makes forward progress


def reconcile_all_listings(marketplace=None, notify_user=None):
	"""Reconcile every listing for a marketplace from the Merchant Listings report.

	Intended to run as a background/scheduled job. Publishes an
	`amazon_reconcile_complete` realtime event to `notify_user` when done.
	"""
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))
	mp = _marketplace(marketplace)
	client = SpApiClient(conn)

	try:
		text = reports.fetch_report(
			REPORT_MERCHANT_LISTINGS_ALL, [mp.marketplace_id], client=client, context="reconcile"
		)
		summary = _apply_report(mp, text)
		summary.update({"success": True, "marketplace": mp.name})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Amazon listing reconciliation failed", message=frappe.get_traceback())
		summary = {"success": False, "marketplace": mp.name, "error": str(e)}

	if notify_user:
		frappe.publish_realtime("amazon_reconcile_complete", summary, user=notify_user)
	return summary


def _apply_report(mp, text):
	"""Parse the report TSV and upsert each SKU's offer/status fields."""
	rows = _parse_rows(text)
	seen = created = updated = skipped_pending = 0
	by_status = {}

	for row in rows:
		sku = row.get("seller-sku")
		if not sku:
			continue
		seen += 1

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
			_seed_content(doc, row)
			doc.last_synced_at = now_datetime()
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			created += 1

		by_status[status] = by_status.get(status, 0) + 1
		if seen % _COMMIT_EVERY == 0:
			frappe.db.commit()

	frappe.db.commit()
	return {
		"seen": seen,
		"created": created,
		"updated": updated,
		"skipped_pending": skipped_pending,
		"by_status": by_status,
	}


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
