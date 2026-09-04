# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Whitelisted entry points for Desk/JS. All SP-API access is server-side.

Phase 1: connection status, connect URL, disconnect, ping, health sync/summary.
Phase 2: catalog search, product-type lookup, listing create/update/delete/sync,
        and publish — one row or a selection, creating what Amazon does not have.
        Creating the *catalog entry* for a product Amazon has never listed is its
        own verb (`create_asin`), deliberately not folded into publish.
Phase 4: Seller Central order sync into Sales Orders.
Phase 5: sales reporting over those Sales Orders, plus Amazon's own topline
        from the Sales API. Two sources answering one question on purpose —
        see `sales.py` for what makes them disagree.

The register reads (list_listings, get_listing_issues) and the two helpers over
them (get_listing_link, export_csv) call no Amazon API at all. They are gated on
the doctype they touch rather than on the manager roles, because what they read
is what the sync already wrote — same reasoning as `variation_family`.

The sales reads follow that rule rather than the manager one: they read Sales
Orders and nothing else, so `Sales Order` read permission is what the code
actually checks and what the pack declares. `get_amazon_order_metrics` is the
exception and goes the other way — it is a live Amazon call, so it carries the
manager gate that every other live call here does.
"""

import json
from urllib.parse import quote

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api import app_config as config
from alaiy_os_connector_amazon_sp_api import csv_export, links, oauth, sales
from alaiy_os_connector_amazon_sp_api.spapi import (
	health,
	listings,
	product_types,
	reconcile,
	submissions,
)
from alaiy_os_connector_amazon_sp_api.spapi import sales as spapi_sales
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	HEALTH_STATUS_UNKNOWN,
)

MANAGER_ROLES = ("System Manager", "Amazon Manager")


def _require_manager():
	if not set(frappe.get_roles()).intersection(MANAGER_ROLES):
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)


# --- connection --------------------------------------------------------------
@frappe.whitelist()
def get_connection_status(connection=None):
	"""Return the current connection status (never exposes the token)."""
	conn = connections.resolve(connection)
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
def get_connect_url(connection=None):
	"""Return the /amazon-oauth/start URL for the Connect button.

	Carries the connection, because the page it points at cannot work out which
	seller was meant: `resolve()` refuses on a bench with several, and the Desk
	form is the only party here that knows which row its operator has open.

	Resolved now rather than passed through untouched, so an id that names
	nothing fails on *this* call — where the button can report it in a dialog —
	instead of on the page the browser is about to leave for, where the only
	thing left to render is an error.
	"""
	_require_manager()
	config.assert_ready()
	name = connections.resolve_name(connection)
	# safe="" because the id is whatever was typed into `connection_id`, and a
	# "/" or "&" in it would otherwise end the query parameter early.
	return {"url": f"/amazon-oauth/start?connection={quote(name, safe='')}"}


@frappe.whitelist()
def get_consent_url(connection=None):
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
	state = oauth.issue_state(connection)  # consent_url asserts the app credentials are set
	return {
		"url": oauth.consent_url(state, connection),
		"redirect_uri": oauth.redirect_uri(),
	}


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
def disconnect(connection=None):
	"""Clear the stored refresh token and mark the connection not configured."""
	_require_manager()
	conn = connections.for_write(connection)
	conn.clear_token("Disconnected by user")
	return {"status": "not_configured"}


@frappe.whitelist()
def ping(connection=None):
	"""Verify the connection via a role-free preflight; updates last_status."""
	_require_manager()
	conn = connections.for_write(connection)
	return conn.ping()


@frappe.whitelist()
def test_connection(connection=None):
	"""OS Connector Registry test hook. Returns {success, message}.

	Called by alaiy_os_core's connector panel; must not raise.
	"""
	conn = connections.resolve(connection)
	if not conn.is_connected():
		return {"success": False, "message": "Amazon account is not connected. Use Connect to authorize."}
	result = conn.ping()
	if result.get("status") == "connected":
		return {"success": True, "message": "Connected to Amazon SP-API."}
	return {"success": False, "message": result.get("message") or "Connection check failed."}


# --- health ------------------------------------------------------------------
@frappe.whitelist()
def sync_health(marketplace=None, connection=None):
	"""On-demand account-health sync for a marketplace (defaults to primary)."""
	_require_manager()
	conn = connections.resolve(connection)
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected. Connect it first."))
	return health.run_health_sync(marketplace)


@frappe.whitelist()
def get_health_summary(marketplace=None, connection=None):
	"""Return the overall health status, metric rows, and recent feedback."""
	conn = connections.resolve(connection)
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
def preview_publish(sku, desired=None, marketplace=None):
	"""What publishing this row would do — create the offer, or push a diff.

	Read-only, and the one call that answers *which*: `exists` says whether
	Amazon lists the SKU at all, `changes` is what an update would carry, and
	`blockers` is why it cannot be published yet. `desired` defaults to the
	register row, so a screen can preview a row it has not opened.
	"""
	_require_manager()
	if isinstance(desired, str):
		desired = json.loads(desired)
	return listings.preview_publish(sku, desired=desired, marketplace=marketplace)


@frappe.whitelist(methods=["POST"])
def publish_listing(sku, desired=None, marketplace=None):
	"""Publish one register row to Amazon, creating the offer if it has none.

	Blocking — a single SKU is a handful of Amazon calls. Answers
	{action: created|updated|unchanged, changed, listing_status, issues}.
	"""
	_require_manager()
	if isinstance(desired, str):
		desired = json.loads(desired)
	return listings.publish_listing(sku, marketplace=marketplace, desired=desired)


@frappe.whitelist(methods=["POST"])
def publish_listings(skus, marketplace=None, connection=None):
	"""Publish a selection of register rows, creating the ones Amazon lacks.

	Runs in the background: each row costs several Amazon calls, so a selection
	of any size outlives a request. Every row records its own outcome in
	`last_published_at` / `last_publish_error`, which is what makes the register
	itself the report — the caller is also sent the `amazon_publish_complete`
	realtime event with the per-SKU summary.
	"""
	_require_manager()
	if isinstance(skus, str):
		skus = json.loads(skus)
	if not isinstance(skus, list | tuple):
		frappe.throw(_("Select the listings to publish."))

	# Ordered dedupe: a selection can repeat a SKU (two filters, one row), and
	# publishing it twice would submit the same change twice.
	seen = {}
	for sku in skus:
		if sku:
			seen[str(sku)] = True
	skus = list(seen)

	if not skus:
		frappe.throw(_("Select the listings to publish."))
	if len(skus) > listings.PUBLISH_MAX:
		frappe.throw(
			_("Publishing is limited to {0} listings at a time; {1} were selected.").format(
				listings.PUBLISH_MAX, len(skus)
			)
		)

	conn = connections.resolve(connection)
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))

	frappe.enqueue(
		"alaiy_os_connector_amazon_sp_api.spapi.listings.publish_listings",
		queue="long",
		# Sized to the selection rather than fixed: the default would kill a
		# large publish part-way through, and the rows it had already published
		# would be indistinguishable from the ones it never reached.
		timeout=min(3600, max(600, 15 * len(skus))),
		skus=skus,
		marketplace=marketplace,
		notify_user=frappe.session.user,
	)
	return {"queued": True, "count": len(skus)}


@frappe.whitelist(methods=["POST"])
def draft_listing(
	sku,
	asin=None,
	product_type=None,
	title=None,
	brand=None,
	description=None,
	price=None,
	quantity=None,
	condition="new_new",
	marketplace=None,
	fulfillment_channel="DEFAULT",
	product=None,
	bullet_points=None,
	keywords=None,
	images=None,
):
	"""Register a listing that is not on Amazon yet. Calls Amazon not at all.

	The row lands as `incomplete`; publishing it is a separate step, so a draft
	can be corrected first, or selected into a bulk publish with everything else.
	"""
	_require_manager()
	bullet_points = _as_list(bullet_points)
	keywords = _as_list(keywords)
	images = _as_list(images)
	return listings.draft_listing(
		sku,
		asin=asin,
		product_type=product_type,
		title=title,
		brand=brand,
		description=description,
		price=price,
		quantity=quantity,
		condition=condition,
		marketplace=marketplace,
		fulfillment_channel=fulfillment_channel,
		product=product,
		bullet_points=bullet_points,
		keywords=keywords,
		images=images,
	)


def _as_list(value):
	"""A repeatable field as a list, whether it arrived as JSON or already parsed."""
	if isinstance(value, str):
		value = json.loads(value or "[]")
	return list(value or [])


# --- creating a catalog entry (a new ASIN) -----------------------------------
# Separate from publish on purpose. Publishing an offer or an update is
# correctable; minting a public ASIN is not, so it is asked for one row at a
# time rather than reachable from a bulk selection. See spapi.listings.create_asin.
@frappe.whitelist()
def preview_asin_creation(sku, marketplace=None):
	"""What creating this product on Amazon would submit. Read-only.

	Returns {ready, blockers, warnings, attributes, required, ...}. `blockers` is
	the whole answer for a row that cannot go — each entry names what to add and
	where — and `attributes` is the payload a ready row would send.
	"""
	_require_manager()
	return listings.preview_asin_creation(sku, marketplace=marketplace)


@frappe.whitelist(methods=["POST"])
def create_asin(sku, marketplace=None):
	"""Ask Amazon to create a catalog entry for this SKU.

	Returns {sku, action, submission_id, listing_status, issues}. There is no ASIN
	in the answer: Amazon accepts the submission before it applies it, so the row
	goes to `pending` and the scheduled submission reconciler fills in the ASIN
	once the catalog entry exists.
	"""
	_require_manager()
	return listings.create_asin(sku, marketplace=marketplace)


@frappe.whitelist()
def get_pending_submissions():
	"""Rows waiting on a write Amazon accepted but has not confirmed applying."""
	_require_manager()
	return submissions.pending_submissions()


@frappe.whitelist(methods=["POST"])
def reconcile_submissions():
	"""Re-read every due pending submission now, instead of waiting for the job."""
	_require_manager()
	return submissions.reconcile_pending_submissions()


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
def list_listings(
	status=None,
	fulfillment_channel=None,
	search=None,
	page_no=1,
	page_size=None,
	marketplace=None,
):
	"""A page of the register — which SKUs are listed, and the state of each.

	Read-only and hits no Amazon API: it reads the Amazon Product Listing rows the
	sync wrote, so it is gated on read permission for the register rather than on
	the manager roles the write endpoints require, same as `variation_family`.

	This is the one read here that answers without being handed an id first, which
	makes it where a caller with only a description of a listing starts.
	"""
	frappe.has_permission("Amazon Product Listing", "read", throw=True)
	return listings.list_listings(
		status=status,
		fulfillment_channel=fulfillment_channel,
		search=search,
		page_no=page_no,
		page_size=page_size,
		marketplace=marketplace,
	)


@frappe.whitelist()
def get_listing_issues(sku=None, severity=None, limit=None):
	"""What Amazon says is wrong with a listing, or with every listing.

	Reads the `suppression_reasons` child rows the last read of each SKU wrote, so
	it is as fresh as that row's `last_synced_at` and gated like the register reads
	above. `sync_listing` is what refreshes one; nothing here calls Amazon.
	"""
	frappe.has_permission("Amazon Product Listing", "read", throw=True)
	return listings.listing_issues(sku=sku, severity=severity, limit=limit)


@frappe.whitelist()
def get_listing_link(sku=None, asin=None, marketplace=None):
	"""The buyer's product page and the Seller Central page for one listing.

	Formats URLs out of ids; makes no Amazon call. Gated on the register because a
	SKU is looked up there to find its ASIN — see links.py for why a missing piece
	comes back as a stated absence rather than a guessed URL.
	"""
	frappe.has_permission("Amazon Product Listing", "read", throw=True)
	return links.listing_link(sku=sku, asin=asin, marketplace=marketplace)


@frappe.whitelist()
def export_csv(rows_json, filename="export", columns=""):
	"""Write rows a caller already holds to a private CSV File, and return its URL.

	Gated on File create rather than on the register: the rows arrive in the
	argument, so this endpoint reads nothing, and what it needs permission for is
	the file it writes. Whatever produced the rows was gated when it did.
	"""
	frappe.has_permission("File", "create", throw=True)
	return csv_export.export_csv(rows_json, filename=filename, columns=columns)


@frappe.whitelist()
def sync_listing(sku, marketplace=None):
	"""Re-fetch one listing from Amazon and refresh its register row."""
	_require_manager()
	return listings.sync_listing(sku, marketplace=marketplace)


@frappe.whitelist()
def sync_all_listings(marketplace=None, connection=None):
	"""Pull all listings for the (primary) marketplace into the register.

	Runs in the background — a catalog can be up to 1,000 SKUs, too slow for a
	blocking request. The caller is notified via the `amazon_sync_all_complete`
	realtime event when it finishes.
	"""
	_require_manager()
	conn = connections.resolve(connection)
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
def reconcile_listings(marketplace=None, connection=None):
	"""Reconcile the full catalog from the Merchant Listings report (no 1000-SKU cap).

	Runs in the background (a full-catalog report can take a while to generate).
	The caller is notified via the `amazon_reconcile_complete` realtime event.
	"""
	_require_manager()
	conn = connections.resolve(connection)
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
def _assert_orders_configured(connection=None):
	conn = connections.resolve(connection)
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
def get_orders_sync_status(connection=None):
	"""Watermark + counts for the connector panel's sync-status slot.

	Also the coverage window — the first and last order date the sync has
	actually reached. That belongs here rather than on a tool of its own: it is
	the same question as "is order sync working", and without it every figure the
	sales reads return lies by omission, answering a period the sync never
	covered with a confident zero.
	"""
	conn = connections.resolve(connection)
	span = {"first_order_date": None, "last_order_date": None, "synced_orders": 0}
	if frappe.db.exists("DocType", "Sales Order"):
		span = sales.coverage()
	return {
		"configured": bool(conn.orders_customer),
		"last_sync_at": conn.last_orders_sync_at,
		"synced_orders": span["synced_orders"],
		"first_order_date": span["first_order_date"],
		"last_order_date": span["last_order_date"],
	}


# --- sales reporting (Phase 5) -----------------------------------------------
# Local reads over the Sales Orders the order sync wrote. Gated on `Sales Order`
# read rather than on the manager roles, for the same reason the register reads
# are gated on `Amazon Product Listing`: what they read is what the sync already
# wrote, and the doctype gate is the one the code can honestly declare.
@frappe.whitelist()
def get_sales_summary(
	date_from,
	date_to=None,
	granularity="day",
	fulfillment_network=None,
	marketplace=None,
):
	"""Revenue, units, orders and average order value over a period, bucketed."""
	frappe.has_permission("Sales Order", "read", throw=True)
	return sales.sales_summary(
		date_from,
		date_to=date_to,
		granularity=granularity,
		fulfillment_network=fulfillment_network,
		marketplace=marketplace,
	)


@frappe.whitelist()
def get_top_selling_products(
	date_from,
	date_to=None,
	by="revenue",
	group_by="sku",
	limit=None,
	fulfillment_network=None,
	marketplace=None,
):
	"""The best-selling SKUs or ASINs over a period, ranked."""
	frappe.has_permission("Sales Order", "read", throw=True)
	return sales.top_selling_products(
		date_from,
		date_to=date_to,
		by=by,
		group_by=group_by,
		limit=limit,
		fulfillment_network=fulfillment_network,
		marketplace=marketplace,
	)


@frappe.whitelist()
def get_product_sales(
	sku=None,
	asin=None,
	date_from=None,
	date_to=None,
	granularity="month",
	marketplace=None,
):
	"""How one SKU or ASIN sold over a period, bucketed."""
	frappe.has_permission("Sales Order", "read", throw=True)
	return sales.product_sales(
		sku=sku,
		asin=asin,
		date_from=date_from,
		date_to=date_to,
		granularity=granularity,
		marketplace=marketplace,
	)


@frappe.whitelist()
def compare_sales_periods(
	date_from,
	date_to,
	compare_to="previous_period",
	baseline_from=None,
	baseline_to=None,
	fulfillment_network=None,
	marketplace=None,
):
	"""One period's totals against another's, with the deltas already computed."""
	frappe.has_permission("Sales Order", "read", throw=True)
	return sales.compare_sales_periods(
		date_from,
		date_to,
		compare_to=compare_to,
		baseline_from=baseline_from,
		baseline_to=baseline_to,
		fulfillment_network=fulfillment_network,
		marketplace=marketplace,
	)


@frappe.whitelist()
def list_amazon_orders(
	date_from,
	date_to=None,
	status=None,
	fulfillment_network=None,
	sku=None,
	marketplace=None,
	page_no=1,
	page_size=None,
):
	"""A page of the Amazon orders behind the sales figures."""
	frappe.has_permission("Sales Order", "read", throw=True)
	return sales.list_amazon_orders(
		date_from,
		date_to=date_to,
		status=status,
		fulfillment_network=fulfillment_network,
		sku=sku,
		marketplace=marketplace,
		page_no=page_no,
		page_size=page_size,
	)


@frappe.whitelist()
def get_amazon_order_metrics(
	date_from,
	date_to=None,
	granularity="day",
	fulfillment_network=None,
	asin=None,
	sku=None,
	marketplace=None,
):
	"""Amazon's own sales figures for this account, live from the Sales API.

	The manager gate, not the `Sales Order` one: this reads nothing local and
	spends a live Amazon call, which is the line every other live endpoint here
	is drawn on. It needs the Selling Partner Insights role on the SP-API app —
	a 403 comes back saying so.
	"""
	_require_manager()
	return spapi_sales.order_metrics(
		date_from,
		date_to=date_to,
		granularity=granularity,
		fulfillment_network=fulfillment_network,
		asin=asin,
		sku=sku,
		marketplace=marketplace,
	)
