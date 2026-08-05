# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Rename the `Amazon Listing` DocType to `Amazon Product Listing`.

This runs in **pre_model_sync**, and it has to. By the time post_model_sync runs,
migrate has already synced amazon_product_listing.json and created a second,
empty DocType — leaving `tabAmazon Listing` behind with every row still in it and
nothing pointing at it. Renaming first means the existing table becomes the new
DocType, and the sync that follows is an update rather than a create.

frappe.rename_doc does the rest of the work for a DocType rename: it renames the
table, rewrites Link/Table `options` in any DocType that referenced this one, and
updates `parenttype` in the child tables (Amazon Listing Image / Bullet / Keyword
/ Issue) so their rows stay attached — see frappe.model.rename_doc's
update_parenttype_values.

The four child DocTypes keep their existing names on purpose: renaming them would
multiply the migration risk for a cosmetic gain, and nothing outside this app
refers to them.
"""

import frappe

OLD = "Amazon Listing"
NEW = "Amazon Product Listing"


def execute():
	if not frappe.db.exists("DocType", OLD):
		return  # fresh install, or this patch already ran

	if frappe.db.exists("DocType", NEW):
		# Both names exist, so a migrate synced the new JSON without this patch
		# having renamed first. Which table holds the real rows is a judgement
		# call about live data, so stop rather than guess.
		frappe.log_error(
			title="Amazon listing rename needs manual attention",
			message=(
				f"Both '{OLD}' and '{NEW}' exist. The rename patch did not run before the new "
				f"DocType was created, so listing rows may still be in `tab{OLD}` while the desk "
				f"reads `tab{NEW}`. Move the rows and drop the empty table by hand, then this "
				f"patch will no-op."
			),
		)
		return

	frappe.rename_doc("DocType", OLD, NEW, force=True)
	frappe.reload_doctype(NEW)
