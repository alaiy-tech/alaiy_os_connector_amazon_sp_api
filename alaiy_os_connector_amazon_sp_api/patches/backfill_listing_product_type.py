# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Fill `product_type` on listing rows synced before the field existed.

The value was never missing, only unreachable: every sync since the register was
introduced has written `{"productType": ..., "summary": ...}` into `raw_summary`,
and that JSON blob is what `_stored_product_type` used to read. This copies it
onto the real field so the desk can show and filter on it.

Doing it here rather than leaving it to the next sync matters for the rows that
need it most. A variation parent, and any offer-only SKU, gets no `productType`
back in its summary at all, so its only copy is the one already stored — a sync
would leave the field empty forever, and with it every write to Amazon for that
SKU (which must declare a productType) would keep failing.

Idempotent: only writes where `product_type` is currently empty.
"""

import frappe

from alaiy_os_connector_amazon_sp_api.spapi.listings import product_type_from_raw_summary


def execute():
	if not frappe.db.exists("DocType", "Amazon Product Listing"):
		return

	rows = frappe.get_all(
		"Amazon Product Listing",
		filters={"product_type": ["is", "not set"], "raw_summary": ["is", "set"]},
		fields=["name", "raw_summary"],
	)
	filled = 0
	for row in rows:
		product_type = product_type_from_raw_summary(row.raw_summary)
		if not product_type:
			continue
		# db.set_value, not a save: this is a value the row already carried in
		# another column, so it is not an edit worth a new document version.
		frappe.db.set_value(
			"Amazon Product Listing", row.name, "product_type", product_type, update_modified=False
		)
		filled += 1

	if filled:
		frappe.db.commit()
	print(f"Amazon Product Listing: filled product_type on {filled} of {len(rows)} row(s).")
