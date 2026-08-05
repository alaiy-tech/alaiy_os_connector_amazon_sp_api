# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Fill Amazon provenance fields on Sales Orders that were synced before those
fields existed.

Re-running the order sync does NOT do this. A submitted Sales Order's items are
immutable in ERPNext, so `_update_existing` only refreshes four header status
fields and never touches the lines — meaning `amazon_asin` (added later) stays
empty on every order imported before it, no matter how many times you backfill.

Nothing here calls Amazon. Both sources are already on the site:

  1. `Amazon Product Listing` is named by SKU and carries the ASIN, so any line whose
     SellerSKU is in the register can be filled directly.
  2. Placeholder lines for unmapped SKUs aren't in that register, but the
     connector wrote "... | ASIN: B0XXXXXXXX" into their description itself, so
     the value can be read back out of the text it put there.

Idempotent: only ever writes where the target field is currently empty.
"""

import re

import frappe

from alaiy_os_connector_amazon_sp_api.spapi.constants import SALES_CHANNEL

# Matches the "ASIN: B0XXXXXXXX" fragment written by spapi.orders._line_items.
_ASIN_IN_DESCRIPTION = re.compile(r"ASIN:\s*([A-Z0-9]{10})\b")


def execute():
	if not frappe.db.exists("DocType", "Sales Order"):
		return

	# The custom fields are normally created by the after_migrate hook — which
	# runs AFTER post_model_sync patches. Without this, `amazon_asin` wouldn't
	# exist yet on the migrate that first runs this patch, the backfill would
	# quietly do nothing, and patches never run twice. Creating them here is
	# idempotent (create_custom_fields(update=True)) and makes the ordering
	# irrelevant.
	from alaiy_os_connector_amazon_sp_api.install import _ensure_order_custom_fields

	_ensure_order_custom_fields()
	frappe.db.commit()
	frappe.clear_cache(doctype="Sales Order")
	frappe.clear_cache(doctype="Sales Order Item")

	if not frappe.get_meta("Sales Order Item").get_field("amazon_asin"):
		return

	filled_from_register = _fill_asin_from_listing_register()
	filled_from_text = _fill_asin_from_description()
	channels = _fill_sales_channel()

	if filled_from_register or filled_from_text or channels:
		frappe.db.commit()

	remaining = frappe.db.count(
		"Sales Order Item", {"amazon_seller_sku": ["is", "set"], "amazon_asin": ["is", "not set"]}
	)
	print(
		f"Amazon order provenance: {filled_from_register} ASIN(s) from Amazon Product Listing, "
		f"{filled_from_text} from line descriptions, {channels} sales channel(s). "
		f"{remaining} line(s) still without an ASIN."
	)


def _fill_asin_from_listing_register():
	"""Authoritative source: the SKU's own Amazon Product Listing row."""
	rows = frappe.db.sql(
		"""
		SELECT soi.name, al.asin
		FROM `tabSales Order Item` soi
		JOIN `tabAmazon Product Listing` al ON al.name = soi.amazon_seller_sku
		WHERE COALESCE(soi.amazon_asin, '') = ''
		  AND COALESCE(al.asin, '') != ''
		""",
		as_dict=True,
	)
	for row in rows:
		# db.set_value on the child row: these are read-only provenance fields
		# with no accounting effect, and the parent is typically submitted, so a
		# document-level save is neither possible nor appropriate.
		frappe.db.set_value("Sales Order Item", row.name, "amazon_asin", row.asin, update_modified=False)
	return len(rows)


def _fill_asin_from_description():
	"""Placeholder lines: read back the ASIN the connector wrote into the text."""
	rows = frappe.db.get_all(
		"Sales Order Item",
		filters={
			"amazon_seller_sku": ["is", "set"],
			"amazon_asin": ["is", "not set"],
			"description": ["like", "%ASIN:%"],
		},
		fields=["name", "description"],
	)
	filled = 0
	for row in rows:
		match = _ASIN_IN_DESCRIPTION.search(row.description or "")
		if not match:
			continue
		frappe.db.set_value(
			"Sales Order Item", row.name, "amazon_asin", match.group(1), update_modified=False
		)
		filled += 1
	return filled


def _fill_sales_channel():
	"""Orders imported before `sales_channel` existed carry a blank channel.

	Unlike the `amazon_*` fields above, this one is created by alaiy_os, whose
	fixture sync and after_migrate hook both run *after* post_model_sync
	patches — so on a site where the field doesn't exist yet this legitimately
	skips and reports 0. Only relevant for a site that has never had the field,
	which by definition has no orders that predate it.
	"""
	if not frappe.get_meta("Sales Order").get_field("sales_channel"):
		return 0
	names = frappe.db.get_all(
		"Sales Order",
		filters={"amazon_order_id": ["is", "set"], "sales_channel": ["is", "not set"]},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("Sales Order", name, "sales_channel", SALES_CHANNEL, update_modified=False)
	return len(names)
