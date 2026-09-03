# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Move the one Amazon Connection out of tabSingles and into a real row.

Amazon Connection was a Single. Every bench running this connector has its
seller's settings — and its OAuth refresh token — stored as (doctype, field,
value) rows in tabSingles, addressed by the doctype name. Flipping `issingle`
creates the table but moves nothing, so without this patch every one of those
benches comes up with an empty connection and a seller who has to reauthorize.

Two halves, and the second is the one that is easy to miss:

  * the field values, which live in tabSingles;
  * the refresh token, which does not. Password fields live in __Auth keyed by
    (doctype, name, fieldname), and for a Single the name *is* the doctype. The
    row has to be re-keyed to the new document name or the token is orphaned:
    still encrypted in the table, no longer reachable by get_password, and
    silently gone.

Idempotent. Runs post-model-sync, so the table exists by the time it does.
"""

import frappe

DOCTYPE = "Amazon Connection"
NEW_NAME = "default"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	meta = frappe.get_meta(DOCTYPE)
	if meta.issingle:
		# The DocType JSON has not been synced yet, so there is nothing to
		# migrate into. Nothing to do rather than half a migration.
		return

	if frappe.db.exists(DOCTYPE, NEW_NAME):
		return

	stored = dict(
		frappe.db.sql(
			"select field, value from `tabSingles` where doctype = %s", DOCTYPE
		)
		or []
	)
	if not stored:
		# A site that never configured Amazon. Leaving it with no connection is
		# right — `connections.resolve` says so clearly, and creating an empty
		# row would make an unconfigured site look configured.
		return

	# Only real, non-virtual fields; a Single's row set can outlive a field.
	writable = {
		df.fieldname
		for df in meta.fields
		if df.fieldname and not df.get("is_virtual") and df.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML")
	}

	doc = frappe.new_doc(DOCTYPE)
	doc.connection_id = NEW_NAME
	doc.label = stored.get("selling_partner_id") or "Amazon"
	# The only connection on the site, so it answers every call that names none.
	doc.is_default = 1
	doc.owner_app = "alaiy_os_connector_amazon_sp_api"

	for field, value in stored.items():
		if field in writable and field not in ("connection_id", "is_default", "label", "owner_app"):
			doc.set(field, value)

	# The token is re-keyed below rather than re-encrypted through the document,
	# so make sure the insert does not write a masked placeholder over it.
	doc.refresh_token = None
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)

	_move_password(doc.name)

	frappe.db.delete("Singles", {"doctype": DOCTYPE})
	frappe.db.commit()

	frappe.logger().info(
		f"Amazon connector: migrated the Single connection to {DOCTYPE} {doc.name}"
	)


def _move_password(new_name: str) -> None:
	"""
	Re-key the encrypted refresh token from the Single's name to the new row's.

	Left alone, `get_password` on the new document finds nothing and the seller
	appears disconnected while their token sits unreachable in __Auth.

	Through Frappe's own helpers rather than SQL against __Auth: they own that
	table's shape, and the round trip re-encrypts with the site key the same way
	a normal save would.
	"""
	from frappe.utils.password import (
		get_decrypted_password,
		remove_encrypted_password,
		set_encrypted_password,
	)

	token = get_decrypted_password(DOCTYPE, DOCTYPE, "refresh_token", raise_exception=False)
	if not token:
		return

	set_encrypted_password(DOCTYPE, new_name, token, "refresh_token")
	remove_encrypted_password(DOCTYPE, DOCTYPE, "refresh_token")

	# The document column carries the masked placeholder a Password field shows.
	frappe.db.set_value(DOCTYPE, new_name, "refresh_token", "*" * len(token), update_modified=False)
