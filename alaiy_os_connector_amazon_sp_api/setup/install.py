# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""AlaiyOS integration: register the connector so the core can surface it.

One integration point with the core (`alaiy_os`), an idempotent upsert on every
install and migrate. The OS Connector Registry row (from connector_meta) is what makes the
connector visible: off that row the core builds everything else — the Connectors
panel in OS Settings, and this connector's own top-level section in the main
"OS" Workspace Sidebar, which it always places before the trailing Settings
item. The section's child links come from the app's own
`alaiy_os_sidebar_connector_items` hook (see hooks.py), so nothing here
touches the Workspace Sidebar doc directly.

What makes the connector *askable* is no longer here. `agent_export.py` declares
the tools and the Amazon facts that go with them; `alaiy_os_agents` reads that
through the `connector_agents` hook and owns the OS Agent Registry row, including
writing it on every migrate. All that is left below is the uninstall, which that
app cannot do for us — see `unregister_agent`.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import agent_export, install
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


# --- agent lifecycle ---------------------------------------------------------
# Writing the OS Agent Registry row is no longer done here. `agent_export.export()`
# declares what this connector can be asked; `alaiy_os_agents` reads it through the
# `connector_agents` hook and upserts the row on its own migrate, with the model,
# the turn budget and the prompt it decides for every connector agent on the bench.
#
# The uninstall stays, because that app cannot do it for us — see below.


def unregister_agent():
	"""Drop this connector's registry row on uninstall, keeping its run history.

	Without this the row outlives the app: its handlers stop importing, so alaiy_os's
	own migrate check (check_dotted_path_handlers) marks every tool broken and the
	agent sits in the Desk advertising an app that is gone.

	alaiy_os_agents owns the row but cannot close this case. Its `registry.unregister`
	fires when *it* is uninstalled, not when a connector is, and its `registry.sync`
	upserts what the hooks declare without pruning what they have stopped declaring —
	so uninstalling this app alone leaves the row with nothing to remove it. Hence a
	hook here, deleting by the id this app's own export asserts rather than by a
	literal.

	force=True because every past OS Agent Run links to the agent, so the default
	link check refuses the delete and the uninstall dies on LinkExistsError the moment
	the agent has been run once. alaiy_os lists OS Agent Run in
	`ignore_links_on_delete` for exactly that reason; the runs keep their agent id as
	recorded history and simply stop pointing at a live row.
	"""
	if not frappe.db.exists("DocType", "OS Agent Registry"):
		return

	if frappe.db.exists("OS Agent Registry", agent_export.AGENT_ID):
		frappe.delete_doc(
			"OS Agent Registry", agent_export.AGENT_ID, force=True, ignore_permissions=True
		)
	frappe.db.commit()


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
