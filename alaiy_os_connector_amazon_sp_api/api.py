# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Whitelisted entry points for Desk/JS. All SP-API access is server-side.

Phase 1: connection status, connect URL, disconnect, ping, health sync/summary.
Phase 2: catalog search + listing create/update/delete/sync.
"""

import json

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api import app_config as config
from alaiy_os_connector_amazon_sp_api.spapi import health, listings, reconcile
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
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
	marketplace_id = None
	if conn.primary_marketplace:
		marketplace_id = frappe.db.get_value(
			"Amazon Marketplace", conn.primary_marketplace, "marketplace_id"
		)
	return {
		"status": conn.last_status or "not_configured",
		"message": conn.last_status_message,
		"connected": conn.is_connected(),
		"selling_partner_id": conn.selling_partner_id,
		"region": config.resolve_region(conn.region),
		"endpoint": config.resolve_endpoint(conn.region),
		"consent_base_url": config.consent_base_url(conn.region),
		"use_sandbox": config.use_sandbox(),
		"app_status": conn.app_status,
		"connected_at": conn.connected_at,
		"primary_marketplace": conn.primary_marketplace,
		"primary_marketplace_id": marketplace_id,
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


# --- listings (Phase 2) ------------------------------------------------------
@frappe.whitelist()
def search_catalog(query, marketplace=None, page_size=10):
	"""Search the Amazon catalog for an ASIN + product type."""
	_require_manager()
	return listings.search_catalog(query, marketplace=marketplace, page_size=page_size)


@frappe.whitelist()
def create_listing(
	sku,
	asin,
	product_type,
	price=None,
	quantity=None,
	condition="new_new",
	marketplace=None,
	fulfillment_channel="DEFAULT",
	product=None,
):
	"""Publish an offer for an existing ASIN and upsert the Amazon Listing row."""
	_require_manager()
	return listings.create_listing(
		sku,
		asin=asin,
		product_type=product_type,
		price=price,
		quantity=quantity,
		condition=condition,
		marketplace=marketplace,
		fulfillment_channel=fulfillment_channel,
		product=product,
	)


@frappe.whitelist()
def update_listing(sku, changes, marketplace=None):
	"""Update price/quantity/condition on an existing listing (PATCH, PUT fallback)."""
	_require_manager()
	if isinstance(changes, str):
		changes = json.loads(changes)
	return listings.update_listing(sku, changes, marketplace=marketplace)


@frappe.whitelist()
def delete_listing(sku, marketplace=None):
	"""End a listing on Amazon; keeps the row as inactive."""
	_require_manager()
	return listings.delete_listing(sku, marketplace=marketplace)


@frappe.whitelist()
def sync_listing(sku, marketplace=None):
	"""Re-fetch one listing from Amazon and refresh its register row."""
	_require_manager()
	return listings.sync_listing(sku, marketplace=marketplace)


@frappe.whitelist()
def sync_all_listings(marketplace=None):
	"""Pull all listings for the (primary) marketplace into the register.

	Runs in the background — a catalog can be up to 1,000 SKUs, too slow for a
	blocking request. The caller is notified via the `amazon_sync_all_complete`
	realtime event when it finishes.
	"""
	_require_manager()
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))

	frappe.enqueue(
		"alaiy_os_connector_amazon_sp_api.spapi.listings.sync_all_listings",
		queue="long",
		timeout=1500,
		marketplace=marketplace,
		notify_user=frappe.session.user,
	)
	return {"queued": True}


@frappe.whitelist()
def reconcile_listings(marketplace=None):
	"""Reconcile the full catalog from the Merchant Listings report (no 1000-SKU cap).

	Runs in the background (a full-catalog report can take a while to generate).
	The caller is notified via the `amazon_reconcile_complete` realtime event.
	"""
	_require_manager()
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))

	frappe.enqueue(
		"alaiy_os_connector_amazon_sp_api.spapi.reconcile.reconcile_all_listings",
		queue="long",
		timeout=1500,
		marketplace=marketplace,
		notify_user=frappe.session.user,
	)
	return {"queued": True}
