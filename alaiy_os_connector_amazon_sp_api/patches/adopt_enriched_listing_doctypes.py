# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Adopt the enriched-listing DocTypes from the retired `alaiy_os_agent_amazon_listing`.

That app registered a second listing agent alongside Shopify's. Both were replaced
by the one channel-agnostic agent in `alaiy_os_agent_listing`, and what was
genuinely Amazon's about it — the fields, the rules, the review record — moved into
this connector, which already owns the channel.

The DocType *files* moved with it, so a fresh site builds them here and needs
nothing from this patch. What this is for is a site that already has them: their
`DocType` rows still say `module = "Alaiy Os Agent Amazon Listing"`, a module whose
app is gone. Repointing them at this app's module before the model sync is what
makes the sync an update rather than a fresh insert — and what keeps every existing
Amazon Enriched Listing row, with its bullets, keywords and images, exactly where it
is.

**pre_model_sync, and it has to be.** After the sync it would be too late: the
importer would already have decided these were new DocTypes belonging to a module
this app does not declare.

Nothing is written to the rows themselves. The tables are untouched — this only
changes which app Frappe believes owns their definitions.
"""

import frappe

OLD_MODULE = "Alaiy Os Agent Amazon Listing"
NEW_MODULE = "Alaiy Os Connector Amazon Sp Api"

MOVED = (
	"Amazon Enriched Listing",
	"Amazon Enriched Listing Bullet",
	"Amazon Enriched Listing Image",
	"Amazon Enriched Listing Keyword",
)

#: `is_enriched` was a Custom Field, because the app that wrote it did not own
#: `Amazon Product Listing`. This one does, so it is a standard field now — and a
#: standard field cannot be added while a Custom Field of the same name exists;
#: the sync refuses the duplicate. Dropping the Custom Field here lets the
#: standard one adopt the column that is already in the table, values intact.
PROMOTED = [("Amazon Product Listing", "is_enriched")]


def execute():
	if not frappe.db.table_exists("DocType"):
		return

	moved = []
	for name in MOVED:
		current = frappe.db.get_value("DocType", name, "module")
		if current is None or current == NEW_MODULE:
			# Not on this site, or already adopted — this patch is idempotent
			# because a re-run after the sync must not undo anything.
			continue
		frappe.db.set_value("DocType", name, "module", NEW_MODULE, update_modified=False)
		moved.append(f"{name} ({current} -> {NEW_MODULE})")

	for doctype, fieldname in PROMOTED:
		name = f"{doctype}-{fieldname}"
		if frappe.db.exists("Custom Field", name):
			# delete_doc, not a raw delete: the Custom Field's own on_trash is what
			# clears it from the doctype's cached meta. The column stays.
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			moved.append(f"{doctype}.{fieldname} (Custom Field -> standard field)")

	if moved:
		frappe.db.commit()
		print("adopted from the retired agent pack:")
		for line in moved:
			print(f"  {line}")
