# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""AlaiyOS integration: register the connector so the core can surface it.

One integration point with the core (`alaiy_os`): the OS Connector Registry row (from
connector_meta). Off that row the core builds everything else — the Connectors
panel in OS Settings, and this connector's own top-level section in the main
"OS" Workspace Sidebar, which it always places before the trailing Settings
item. The section's child links come from the app's own
`alaiy_os_sidebar_connector_items` hook (see hooks.py), so nothing here
touches the Workspace Sidebar doc directly.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import install
from alaiy_os_connector_amazon_sp_api.connector_meta import connector_meta


# --- entry points ------------------------------------------------------------
def after_install():
	install.ensure_base_data()
	sync_connector_registry()


def after_migrate():
	install.ensure_base_data()
	sync_connector_registry()


def sync_connector_registry():
	"""Upsert the OS Connector Registry row, then refresh the AlaiyOS sidebar."""
	if not frappe.db.exists("DocType", "OS Connector Registry"):
		# Core not installed yet; nothing to register with.
		return

	connector_id = connector_meta["connector_id"]
	# Runtime fields are owned by the core's test flow — don't clobber them.
	runtime_fields = {"connection_status", "last_tested_at"}

	if frappe.db.exists("OS Connector Registry", connector_id):
		doc = frappe.get_doc("OS Connector Registry", connector_id)
		for key, val in connector_meta.items():
			if key not in runtime_fields:
				doc.set(key, val)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.new_doc("OS Connector Registry")
		for key, val in connector_meta.items():
			doc.set(key, val)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
	_refresh_alaiy_os_sidebar()


# --- sidebar helpers ---------------------------------------------------------
def _refresh_alaiy_os_sidebar():
	"""Ask the core to rebuild its sidebars now that our registry row exists.

	Used on standalone install-app (when the core's own after_migrate won't run).
	The rebuild is what picks up our section — and it rebuilds `items` from
	scratch, so any section a previous version of this app appended by hand is
	dropped in the same pass.
	"""
	try:
		from alaiy_os.setup import install as core_install
		from alaiy_os.setup.install import (
			create_or_update_os_settings_workspace,
			create_or_update_os_settings_workspace_sidebar,
			create_or_update_workspace,
			create_or_update_workspace_sidebar,
		)

		# The core memoises its OS Connector Registry query per process and only
		# clears it at the top of its own provisioning run. Ours runs after that
		# (same `bench migrate` process), so on the migrate that first creates
		# our row the cache would still predate it and the rebuild would omit
		# this connector.
		core_install._connector_registry_rows_cache = None

		create_or_update_workspace()
		create_or_update_workspace_sidebar()
		create_or_update_os_settings_workspace()
		create_or_update_os_settings_workspace_sidebar()
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="Amazon connector: core sidebar rebuild failed",
			message=frappe.get_traceback(),
		)
