# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""AlaiyOS integration: register the connector so the core can surface it.

Two integration points with the core (`alaiy_os`), both idempotent upserts on the
same schedule. The OS Connector Registry row (from connector_meta) is what makes the
connector visible: off that row the core builds everything else — the Connectors
panel in OS Settings, and this connector's own top-level section in the main
"OS" Workspace Sidebar, which it always places before the trailing Settings
item. The section's child links come from the app's own
`alaiy_os_sidebar_connector_items` hook (see hooks.py), so nothing here
touches the Workspace Sidebar doc directly.

The OS Agent Registry row (from pack_meta) is what makes it *askable*: one pack whose
tool rows name this app's whitelisted reads as dotted-path handlers, which the core's
agent engine hydrates and runs. Registering it is the same shape as registering the
connector, so both live here.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import install, pack_meta
from alaiy_os_connector_amazon_sp_api.connector_meta import connector_meta


# --- entry points ------------------------------------------------------------
def after_install():
	install.ensure_base_data()
	sync_connector_registry()
	sync_agent_registry()


def after_migrate():
	install.ensure_base_data()
	sync_connector_registry()
	sync_agent_registry()


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


# --- agent pack --------------------------------------------------------------
# Fields written from the manifest on every reconcile — except these. `is_enabled`
# is admin-controlled: it is toggled in the Desk form (or through
# alaiy_os.api.agent_settings) and has to survive a migrate, so it is only ever set
# by the DocType's own default on the first insert. Re-asserting the manifest here
# would switch a pack an operator turned off back on, silently, on the next migrate.
_AGENT_RUNTIME_FIELDS = {"is_enabled"}

# Manifest keys that are not plain fields on the row.
_AGENT_NON_REGISTRY_FIELDS = {"agent_id", "tools"}


def sync_agent_registry():
	"""Upsert this connector's OS Agent Registry pack. Safe to call repeatedly.

	The manifest is pack_meta.py; this only writes it, so editing a tool description
	or the prompt and running `bench migrate` is the whole reconcile loop.
	"""
	if not frappe.db.exists("DocType", "OS Agent Registry"):
		# Core not installed yet, or predates the agent engine.
		return

	meta = pack_meta.build_pack_meta()
	agent_id = meta["agent_id"]

	if frappe.db.exists("OS Agent Registry", agent_id):
		doc = frappe.get_doc("OS Agent Registry", agent_id)
	else:
		doc = frappe.new_doc("OS Agent Registry")
		doc.agent_id = agent_id

	for key, value in meta.items():
		if key in _AGENT_NON_REGISTRY_FIELDS or key in _AGENT_RUNTIME_FIELDS:
			continue
		doc.set(key, value)

	doc.set("tools", [pack_meta.as_registry_tool(tool) for tool in meta["tools"]])

	# save() inserts when new. The OS Agent Tool child controller validates every
	# handler dotted path and every parameters_schema here, so a typo in the
	# manifest fails at migrate with the tool named, rather than mid-run.
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def unregister_agent():
	"""Drop the pack's registry row on uninstall, keeping its run history.

	Without this the row outlives the app: its handlers stop importing, so alaiy_os's
	own migrate check (check_dotted_path_handlers) marks every tool broken and the
	pack sits in the Desk advertising an app that is gone.

	force=True because every past OS Agent Run links to the agent, so the default
	link check refuses the delete and the uninstall dies on LinkExistsError the moment
	the pack has been run once. alaiy_os lists OS Agent Run in
	`ignore_links_on_delete` for exactly that reason; the runs keep their agent id as
	recorded history and simply stop pointing at a live row.
	"""
	if not frappe.db.exists("DocType", "OS Agent Registry"):
		return

	if frappe.db.exists("OS Agent Registry", pack_meta.PACK_ID):
		frappe.delete_doc(
			"OS Agent Registry", pack_meta.PACK_ID, force=True, ignore_permissions=True
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
