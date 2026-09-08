# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Let existing listings pick up the barcode and category the catalog now returns.

Unlike `backfill_listing_product_type`, this cannot fill anything itself: the
values were never fetched. `CATALOG_CONTENT_INCLUDED_DATA` did not ask Amazon
for `identifiers` or `classifications` until now, so no stored payload contains
them and there is nothing local to copy across.

What it does instead is clear `catalog_synced_at`, which is the flag
`spapi.reconcile._needs_enrichment` reads to decide a row is done. Enrichment is
deliberately once-per-row — that is what keeps a steady-state reconcile from
spending a single API call — and the consequence is that a newly added catalog
field reaches no already-enriched row, ever, without something like this.

Scoped to rows that have no `amazon_browse_node_id`, so it is idempotent in the
way that matters: it targets exactly the rows predating the change, once. A row
whose ASIN genuinely has no classification will be re-read, re-stamped, and left
alone thereafter, because the patch does not run again. The weekly
`tasks.refresh_catalog_facts` is what keeps them current from here on.

Nothing is fetched during migration. The six-hourly reconcile does the work on
its next run, within RECONCILE_CATALOG_BUDGET, draining across runs on a large
catalogue.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Amazon Product Listing"):
		return

	names = frappe.get_all(
		"Amazon Product Listing",
		filters={
			"catalog_synced_at": ["is", "set"],
			"amazon_browse_node_id": ["is", "not set"],
		},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(
			"Amazon Product Listing", name, "catalog_synced_at", None, update_modified=False
		)

	if names:
		frappe.db.commit()
	print(
		f"Amazon Product Listing: {len(names)} row(s) marked for catalog re-read "
		"(barcode + category). The next reconcile fetches them."
	)
