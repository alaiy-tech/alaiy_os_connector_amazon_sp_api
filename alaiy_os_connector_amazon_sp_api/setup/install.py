# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""AlaiyOS integration: register the connector and surface it in the sidebar.

Two integration points with alaiy_os_core:
  1. OS Connector Registry  — a row (from connector_meta) that lets the core
     render the connector in the OS Settings 'Connectors' panel.
  2. The main "OS" Workspace Sidebar — we inject an 'Amazon' section with links
     to the app's DocTypes. The core rebuilds that sidebar on every migrate
     (wiping our items), so we re-inject via the Workspace Sidebar on_update
     doc-event as well.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import install
from alaiy_os_connector_amazon_sp_api.connector_meta import connector_meta

# --- main "OS" sidebar injection ---------------------------------------------
_SIDEBAR_NAME = "OS"
_SECTION_LABEL = "Amazon"

# Section + child links appended to the main OS sidebar. Item schema mirrors
# alaiy_os_core.constants.workspace.WORKSPACE_SIDEBAR_ITEMS.
_SIDEBAR_ITEMS = [
	{"type": "Section Break", "label": _SECTION_LABEL, "icon": "shopping-cart", "child": 0, "indent": 1},
	{"type": "Link", "link_type": "DocType", "link_to": "Amazon Connection",
	 "label": "Connection", "child": 1, "icon": "plug"},
	{"type": "Link", "link_type": "DocType", "link_to": "Amazon Listing",
	 "label": "Listings", "child": 1, "icon": "list"},
	{"type": "Link", "link_type": "DocType", "link_to": "Account Health Metric",
	 "label": "Account Health", "child": 1, "icon": "heart-pulse"},
	{"type": "Link", "link_type": "DocType", "link_to": "Seller Feedback",
	 "label": "Seller Feedback", "child": 1, "icon": "star"},
	{"type": "Link", "link_type": "DocType", "link_to": "Amazon Marketplace",
	 "label": "Marketplaces", "child": 1, "icon": "globe"},
	{"type": "Link", "link_type": "DocType", "link_to": "SP-API Log",
	 "label": "SP-API Logs", "child": 1, "icon": "activity"},
]
_INJECTED_LABELS = {i["label"] for i in _SIDEBAR_ITEMS}


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
	"""Ask the core to rebuild its sidebars, then inject our section.

	Used on standalone install-app (when the core's own after_migrate won't run).
	"""
	try:
		from alaiy_os_core.setup.install import (
			create_or_update_os_settings_workspace,
			create_or_update_os_settings_workspace_sidebar,
			create_or_update_workspace_sidebar,
		)

		create_or_update_workspace_sidebar()
		create_or_update_os_settings_workspace()
		create_or_update_os_settings_workspace_sidebar()
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="Amazon connector: core sidebar rebuild failed",
			message=frappe.get_traceback(),
		)
	_inject_sidebar()


def _on_sidebar_update(doc, method):
	"""Re-inject after the core rebuilds/wipes the OS sidebar on migrate."""
	if doc.name != _SIDEBAR_NAME or frappe.flags.get("in_amazon_sidebar_inject"):
		return
	labels = {r.label for r in doc.items}
	if _SECTION_LABEL not in labels:
		_inject_sidebar()


def _inject_sidebar():
	"""Append the Amazon section to the OS sidebar (idempotent)."""
	if not frappe.db.exists("Workspace Sidebar", _SIDEBAR_NAME):
		return
	frappe.flags.in_amazon_sidebar_inject = True
	try:
		doc = frappe.get_doc("Workspace Sidebar", _SIDEBAR_NAME)
		doc.items = [r for r in doc.items if r.label not in _INJECTED_LABELS]
		for item in _SIDEBAR_ITEMS:
			doc.append("items", item)
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="Amazon connector: sidebar injection failed",
			message=frappe.get_traceback(),
		)
	finally:
		frappe.flags.in_amazon_sidebar_inject = False
