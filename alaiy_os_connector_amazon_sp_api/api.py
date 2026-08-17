# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Whitelisted entry points for Desk/JS. All SP-API access is server-side.

Phase 1: connection status, connect URL, disconnect, ping, health sync/summary.
Phase 2: catalog search, product-type lookup, listing create/update/delete/sync.
Phase 4: Seller Central order sync into Sales Orders.
"""

import json

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api import app_config as config
from alaiy_os_connector_amazon_sp_api import oauth
from alaiy_os_connector_amazon_sp_api.spapi import health, listings, product_types, reconcile
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
	"""Report which site_config credential keys are set (no secret values).

	Carries `redirect_uri` too — the URL Amazon must have registered for this
	deployment. It is derived from `app_url`, so it is also the thing that decides
	whether the consent redirect lands on the OS's own callback screen or on
	Frappe's www page, and a screen showing it can say which.
	"""
	_require_manager()
	return {**config.get_config_status(), "redirect_uri": oauth.redirect_uri()}


@frappe.whitelist()
def get_connect_url():
	"""Return the /amazon-oauth/start URL for the Connect button."""
	_require_manager()
	config.assert_ready()
	return {"url": "/amazon-oauth/start"}


@frappe.whitelist()
def get_consent_url():
	"""Issue the OAuth state and return Amazon's consent URL to send the browser to.

	What `/amazon-oauth/start` does, as data instead of a redirect — for the OS
	frontend, which cannot reach that www page: it is served from the site's
	hostname, which in that arrangement belongs to the frontend, and the frontend
	only proxies `/api` through to Frappe.

	The URL carries no secret. The app id in it is public (it is on the consent
	screen), and `state` is single-use, session-bound and worthless to anyone who
	is not already this session.
	"""
	_require_manager()
	state = oauth.issue_state()  # consent_url asserts the app credentials are set
	return {"url": oauth.consent_url(state), "redirect_uri": oauth.redirect_uri()}


@frappe.whitelist(methods=["POST"])
def complete_oauth(
	spapi_oauth_code=None, state=None, selling_partner_id=None, error=None, error_description=None
):
	"""Finish the consent round trip on behalf of the OS's callback screen.

	Same flow, same outcomes as the www callback page — see
	`oauth.complete_authorization`, which both go through. The arguments are
	Amazon's own query parameters, forwarded verbatim by whichever of the two
	Amazon redirected to.
	"""
	_require_manager()
	return oauth.complete_authorization(
		spapi_oauth_code,
		state,
		selling_partner_id=selling_partner_id,
		error=error,
		error_description=error_description,
	)


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
def suggest_product_type(title, marketplace=None):
	"""Ask Amazon which product type a product title belongs to.

	Returns [{product_type, display_name}], best match first — a list, because a
	title is often ambiguous and the caller is the one who can choose. Unlike
	`search_catalog` this needs no ASIN, so it answers for products that are not
	in Amazon's catalog yet, which is when a product type is hardest to come by
	and most needed (every Listings write must declare one).

	Other apps calling this in-process should import
	`spapi.product_types.suggest_product_types` instead; this wrapper exists for
	RPC callers and carries the manager-role gate that goes with them.
	"""
	_require_manager()
	return product_types.suggest_product_types(title, marketplace=marketplace)


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
	"""Publish an offer for an existing ASIN and upsert the Amazon Product Listing row."""
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
def compare_listing(sku, desired, marketplace=None):
	"""What a push would change: Amazon's live state vs the values on the form.

	Read-only. `desired` is the operator's intended state, so unsaved edits count
	— what makes a field a change is Amazon not having it, not the row being
	dirty. Feed the returned `changes` straight to update_listing.
	"""
	_require_manager()
	if isinstance(desired, str):
		desired = json.loads(desired)
	return listings.compare_listing(sku, desired, marketplace=marketplace)


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
def variation_family(parent_asin, marketplace=None):
	"""Every SKU this seller lists under one parent ASIN.

	Read-only and hits no Amazon API — it reads the parentage the sync recorded —
	so it is gated on read permission for the register rather than on the manager
	roles the write endpoints require.
	"""
	frappe.has_permission("Amazon Product Listing", "read", throw=True)
	return listings.variation_family(parent_asin, marketplace=marketplace)


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


# --- orders (Phase 4) --------------------------------------------------------
def _assert_orders_configured():
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))
	if not conn.orders_customer:
		frappe.throw(
			_("Set a Default Customer under Orders on the Amazon Connection before syncing orders.")
		)
	return conn


@frappe.whitelist()
def sync_orders(marketplace=None):
	"""Pull orders updated since the watermark into Sales Orders.

	Runs in the background: a busy window is hundreds of orders, each needing
	its own getOrderItems call at 0.5 rps. The caller is notified via the
	`amazon_orders_sync_complete` realtime event.
	"""
	_require_manager()
	_assert_orders_configured()

	frappe.enqueue(
		"alaiy_os_connector_amazon_sp_api.spapi.orders.sync_orders",
		queue="long",
		timeout=3600,
		marketplace=marketplace,
		notify_user=frappe.session.user,
	)
	return {"queued": True}


@frappe.whitelist()
def backfill_orders(date_from, date_to=None, marketplace=None):
	"""Re-read an explicit date range without disturbing the scheduled watermark.

	Chunked internally, and idempotent on the Amazon order id — re-running an
	overlapping range creates nothing new.
	"""
	_require_manager()
	_assert_orders_configured()

	frappe.enqueue(
		"alaiy_os_connector_amazon_sp_api.spapi.orders.backfill_orders",
		queue="long",
		timeout=7200,
		marketplace=marketplace,
		date_from=date_from,
		date_to=date_to,
		notify_user=frappe.session.user,
	)
	return {"queued": True}


@frappe.whitelist()
def get_orders_sync_status():
	"""Watermark + counts for the connector panel's sync-status slot."""
	conn = frappe.get_cached_doc("Amazon Connection")
	synced = 0
	if frappe.db.exists("DocType", "Sales Order"):
		synced = frappe.db.count("Sales Order", {"amazon_order_id": ["is", "set"]})
	return {
		"configured": bool(conn.orders_customer),
		"last_sync_at": conn.last_orders_sync_at,
		"synced_orders": synced,
	}
