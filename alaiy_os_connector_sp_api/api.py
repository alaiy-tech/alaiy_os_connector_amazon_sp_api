# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Whitelisted entry points for Desk/JS. All SP-API access is server-side.

Phase 1 surface: connection status, connect URL, disconnect, ping, health sync
and the health summary. Listing CRUD (Phase 2) and reconcile (Phase 3) will be
added here.
"""

import frappe
from frappe import _

from alaiy_os_connector_sp_api import config, oauth
from alaiy_os_connector_sp_api.spapi import health
from alaiy_os_connector_sp_api.spapi.constants import (
	HEALTH_STATUS_UNKNOWN,
)

MANAGER_ROLES = ("System Manager", "Amazon Manager")


def _require_manager():
	if not set(frappe.get_roles()).intersection(MANAGER_ROLES):
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)


# --- connection --------------------------------------------------------------
@frappe.whitelist()
def get_connection_status():
	"""Return the current connection status (never exposes the token)."""
	conn = frappe.get_cached_doc("Amazon Connection")
	return {
		"status": conn.last_status or "not_configured",
		"message": conn.last_status_message,
		"connected": conn.is_connected(),
		"selling_partner_id": conn.selling_partner_id,
		"region": conn.region,
		"app_status": conn.app_status,
		"connected_at": conn.connected_at,
		"primary_marketplace": conn.primary_marketplace,
	}


@frappe.whitelist()
def get_config_status():
	"""Report which site_config credential keys are set (no secret values)."""
	_require_manager()
	return config.get_config_status()


@frappe.whitelist()
def get_connect_url():
	"""Return the /amazon-oauth/start URL for the Connect button."""
	_require_manager()
	config.assert_ready()
	return {"url": "/amazon-oauth/start"}


@frappe.whitelist()
def disconnect():
	"""Clear the stored refresh token and mark the connection not configured."""
	_require_manager()
	conn = frappe.get_doc("Amazon Connection")
	conn.clear_token("Disconnected by user")
	return {"status": "not_configured"}


@frappe.whitelist()
def ping():
	"""Verify the connection via a role-free preflight; updates last_status."""
	_require_manager()
	conn = frappe.get_doc("Amazon Connection")
	return conn.ping()


@frappe.whitelist()
def test_connection():
	"""OS Connector Registry test hook. Returns {success, message}.

	Called by alaiy_os_core's connector panel; must not raise.
	"""
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		return {"success": False, "message": "Amazon account is not connected. Use Connect to authorize."}
	result = conn.ping()
	if result.get("status") == "connected":
		return {"success": True, "message": "Connected to Amazon SP-API."}
	return {"success": False, "message": result.get("message") or "Connection check failed."}


# --- health ------------------------------------------------------------------
@frappe.whitelist()
def sync_health(marketplace=None):
	"""On-demand account-health sync for a marketplace (defaults to primary)."""
	_require_manager()
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected. Connect it first."))
	return health.run_health_sync(marketplace)


@frappe.whitelist()
def get_health_summary(marketplace=None):
	"""Return the overall health status, metric rows, and recent feedback."""
	conn = frappe.get_cached_doc("Amazon Connection")
	marketplace = marketplace or conn.primary_marketplace

	filters = {}
	if marketplace:
		filters["marketplace"] = marketplace

	metrics = frappe.get_all(
		"Account Health Metric",
		filters=filters,
		fields=[
			"metric_key",
			"metric_label",
			"metric_value",
			"metric_target",
			"higher_is_better",
			"section",
			"health_status",
			"finances_guarantees",
			"finances_chargebacks",
			"synced_at",
			"marketplace",
		],
		order_by="section asc, metric_label asc",
	)

	overall = health.rollup_status([m["health_status"] for m in metrics]) if metrics else HEALTH_STATUS_UNKNOWN
	synced_at = max((m["synced_at"] for m in metrics if m["synced_at"]), default=None)

	feedback = frappe.get_all(
		"Seller Feedback",
		fields=["order_id", "rating", "comment", "feedback_date"],
		order_by="feedback_date desc",
		limit=50,
	)

	return {
		"overall_status": overall,
		"marketplace": marketplace,
		"metrics": metrics,
		"feedback": feedback,
		"synced_at": synced_at,
	}
