# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Order ingestion from Seller Central via the Orders API (v0).

Orders are materialised **directly as Sales Orders** — there is no staging
DocType. The Amazon order id lives on `Sales Order.amazon_order_id` and is the
idempotency key: every poll re-reads a window that overlaps the previous one,
so the same order is seen many times and must update rather than duplicate.

Scope here is the order header plus its line items. Buyer identity and
addresses (restricted endpoints), shipment tracking, and the Finances API are
deliberately not touched — see the module docstring notes on `_customer`.

The state machine is the fiddly part. Amazon's OrderStatus drives the Sales
Order's docstatus:

    Pending                     -> draft   (Amazon withholds pricing here)
    Unshipped/Shipped/...       -> submitted
    Canceled/Unfulfillable      -> cancelled

A submitted Sales Order's items can never be edited, so once an order leaves
draft we only maintain the Amazon status fields on it. If Amazon cancels an
order we have already delivered or invoiced, we refuse to touch it and surface
the conflict instead of silently corrupting the downstream documents.
"""

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, get_system_timezone, now_datetime

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	ORDER_ITEMS_MIN_INTERVAL,
	ORDER_STATUS_CANCEL,
	ORDER_STATUS_SUBMIT,
	ORDERS_BACKFILL_CHUNK_DAYS,
	ORDERS_DEFAULT_LOOKBACK_DAYS,
	ORDERS_MAX_PAGES,
	ORDERS_PAGE_SIZE,
	ORDERS_PATH,
	ORDERS_RECENT_BLIND_SPOT,
	ORDERS_SYNC_OVERLAP,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace


# --- time helpers ------------------------------------------------------------
# Frappe stores naive datetimes in system time; Amazon speaks ISO-8601 UTC.
# Every crossing of that boundary goes through these two, so a timezone bug can
# only ever live in one place — and on a site whose timezone isn't UTC, getting
# this wrong silently shifts the whole sync window by hours.
def _to_amazon_iso(dt):
	"""System-time naive datetime -> '2026-08-03T09:15:00Z'."""
	dt = get_datetime(dt)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_amazon_iso(value):
	"""Amazon ISO-8601 UTC string -> naive system-time datetime (or None)."""
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=UTC)
	return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _window_end():
	"""Latest instant worth asking Amazon about (see ORDERS_RECENT_BLIND_SPOT)."""
	return now_datetime() - timedelta(seconds=ORDERS_RECENT_BLIND_SPOT)


# --- connection / configuration ----------------------------------------------
def _connection():
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))
	return conn


def _company(conn):
	company = conn.orders_company or frappe.defaults.get_global_default("company")
	if not company:
		frappe.throw(_("No company set. Set 'Company' under Orders on the Amazon Connection."))
	return company


def _customer(conn):
	"""The Customer every Amazon order books against.

	Buyer info is a restricted SP-API endpoint (PII-approved roles + RDT), so
	with it out of scope there is no real buyer identity to create a Customer
	from. Everything lands on one configured marketplace customer instead; the
	buyer is identifiable from `amazon_order_id` when it's actually needed.
	"""
	if not conn.orders_customer:
		frappe.throw(_("No customer set. Set 'Default Customer' under Orders on the Amazon Connection."))
	return conn.orders_customer


def _warehouse(conn, company):
	"""A real (non-Group) warehouse; a Group one kills every stock document."""
	configured = conn.orders_warehouse
	if configured and not frappe.db.get_value("Warehouse", configured, "is_group"):
		return configured
	if configured:
		frappe.log_error(
			title="Amazon orders: Default Warehouse is a Group Warehouse, falling back",
			message=f"Configured: {configured}. Set a leaf warehouse on the Amazon Connection.",
		)
	fallback = frappe.db.get_value("Warehouse", {"is_group": 0, "company": company}, "name")
	if not fallback:
		frappe.throw(
			_(
				"No usable (non-Group) Warehouse exists for {0}. Create one, then set it under Orders on the Amazon Connection."
			).format(company)
		)
	return fallback


def _price_list(conn):
	return conn.orders_selling_price_list or "Standard Selling"


# --- SP-API calls ------------------------------------------------------------
def _handle_forbidden(err):
	if err.is_forbidden():
		frappe.throw(describe_forbidden(err, role_free=False))
	raise err


def _payload(response):
	"""Orders v0 wraps everything in `payload`; tolerate its absence."""
	return (response or {}).get("payload") or {}


def fetch_orders(mp, client, *, updated_after=None, updated_before=None, next_token=None):
	"""One page of getOrders. Returns (orders, next_token).

	When paginating, Amazon wants NextToken and the marketplace only — resending
	the date filters alongside it is rejected.
	"""
	if next_token:
		params = {"MarketplaceIds": mp.marketplace_id, "NextToken": next_token}
	else:
		params = {
			"MarketplaceIds": mp.marketplace_id,
			"MaxResultsPerPage": ORDERS_PAGE_SIZE,
			"LastUpdatedAfter": _to_amazon_iso(updated_after),
		}
		if updated_before:
			params["LastUpdatedBefore"] = _to_amazon_iso(updated_before)
	try:
		payload = _payload(client.get(ORDERS_PATH, params=params, context="orders"))
	except SpApiError as e:
		_handle_forbidden(e)
	return payload.get("Orders") or [], payload.get("NextToken")


def fetch_order_items(client, amazon_order_id):
	"""Every line item for one order, following NextToken.

	Paced to ORDER_ITEMS_MIN_INTERVAL: this endpoint allows 0.5 rps and a
	burst of 30, so a run of any size will hit the limit within the first
	minute if we just fire and rely on 429-retry.
	"""
	items = []
	next_token = None
	path = f"{ORDERS_PATH}/{amazon_order_id}/orderItems"
	for _page in range(ORDERS_MAX_PAGES):
		params = {"NextToken": next_token} if next_token else {}
		try:
			payload = _payload(client.get(path, params=params, context="orders"))
		except SpApiError as e:
			_handle_forbidden(e)
		items.extend(payload.get("OrderItems") or [])
		next_token = payload.get("NextToken")
		if not next_token:
			break
		time.sleep(ORDER_ITEMS_MIN_INTERVAL)
	return items


# --- item resolution ---------------------------------------------------------
def _resolve_item_code(seller_sku):
	"""SellerSKU -> Item code, via the Amazon Listing register.

	Never creates an Item: a mis-resolved SKU silently books revenue against
	the wrong product, and an auto-created one pollutes the catalog with
	unmanaged stubs. Unresolvable SKUs park the whole order instead.
	"""
	if not seller_sku:
		return None
	product = frappe.db.get_value("Amazon Listing", seller_sku, "product")
	if product:
		return product
	# A SKU that happens to be the item code itself is the common convention
	# on sites that list straight from their own catalog.
	if frappe.db.exists("Item", seller_sku):
		return seller_sku
	return None


def _line_items(order_items, warehouse, delivery_date):
	"""Amazon order items -> Sales Order Item rows, or (None, unresolved_skus).

	ItemPrice is the extended price for the whole QuantityOrdered, not a unit
	rate, and PromotionDiscount is likewise an order-item total — so the unit
	rate is (ItemPrice - PromotionDiscount) / qty.
	"""
	rows = []
	unresolved = []
	for item in order_items:
		sku = item.get("SellerSKU")
		qty = cint(item.get("QuantityOrdered"))
		if qty <= 0:
			# Fully cancelled line: Amazon keeps the row with QuantityOrdered 0.
			continue
		item_code = _resolve_item_code(sku)
		if not item_code:
			unresolved.append(sku or item.get("ASIN") or "?")
			continue
		gross = flt(_money(item.get("ItemPrice")))
		discount = flt(_money(item.get("PromotionDiscount")))
		rows.append(
			{
				"item_code": item_code,
				"qty": qty,
				"rate": (gross - discount) / qty if qty else 0,
				"warehouse": warehouse,
				"delivery_date": delivery_date,
				"amazon_order_item_id": item.get("OrderItemId"),
				"amazon_seller_sku": sku,
			}
		)
	if unresolved:
		return None, unresolved
	return rows, []


def _money(node):
	"""{'CurrencyCode': 'USD', 'Amount': '12.34'} -> 12.34"""
	if not node:
		return 0
	return flt((node or {}).get("Amount") or 0)


def _merge_duplicate_rows(rows):
	"""ERPNext rejects two rows with the same item_code on one Sales Order.

	Amazon splits a single SKU across multiple order items routinely (partial
	fulfilment, split shipments), so this is the normal case, not an edge one.
	Qty is summed and the rate recomputed from the combined amount so the order
	total is unchanged.
	"""
	merged = {}
	order = []
	for row in rows:
		key = row["item_code"]
		if key not in merged:
			merged[key] = dict(row)
			order.append(key)
			continue
		existing = merged[key]
		total = flt(existing["qty"]) * flt(existing["rate"]) + flt(row["qty"]) * flt(row["rate"])
		existing["qty"] = flt(existing["qty"]) + flt(row["qty"])
		existing["rate"] = (total / existing["qty"]) if existing["qty"] else 0
		# Only the first row's Amazon ids survive the merge; the full set is
		# always recoverable from Amazon via amazon_order_id.
	return [merged[key] for key in order]


# --- tax ---------------------------------------------------------------------
def _resolve_tax_account(company):
	"""A usable Tax-type leaf account, self-healing rather than requiring config."""
	existing = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Tax", "is_group": 0, "disabled": 0},
		"name",
	)
	if existing:
		return existing

	abbr = frappe.db.get_value("Company", company, "abbr")
	parent = None
	if abbr and frappe.db.exists("Account", f"Duties and Taxes - {abbr}"):
		parent = f"Duties and Taxes - {abbr}"
	if not parent:
		parent = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "root_type": "Liability"}, "name"
		)
	if not parent:
		return None
	try:
		acc = frappe.new_doc("Account")
		acc.account_name = "Amazon Tax"
		acc.parent_account = parent
		acc.company = company
		acc.account_type = "Tax"
		acc.is_group = 0
		acc.insert(ignore_permissions=True)
		return acc.name
	except Exception:
		frappe.log_error(
			title=f"Amazon orders: failed to auto-create Tax account for {company}",
			message=frappe.get_traceback(),
		)
		return None


def _append_tax(so, order_items, company):
	"""Book Amazon's own computed ItemTax as a single Actual charge.

	ItemPrice is tax-exclusive on Amazon, so tax is added on top. Amazon's
	figure is used verbatim rather than recomputed from a rate, to avoid
	rounding drift between the two systems.
	"""
	tax = sum(_money(item.get("ItemTax")) for item in order_items)
	if flt(tax) <= 0:
		return
	account = _resolve_tax_account(company)
	if not account:
		frappe.log_error(
			title=f"Amazon order {so.amazon_order_id}: tax skipped, no tax account",
			message="Could not resolve or create a Tax account — order imported tax-free.",
		)
		return
	so.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": account,
			"description": "Amazon Item Tax",
			"tax_amount": flt(tax),
		},
	)


# --- currency ----------------------------------------------------------------
def _order_currency(order, mp, company):
	currency = ((order.get("OrderTotal") or {}).get("CurrencyCode") or "").strip().upper()
	if currency:
		return currency
	if mp.currency:
		return mp.currency
	return frappe.get_cached_value("Company", company, "default_currency")


def _exchange_rate(from_currency, to_currency, transaction_date):
	if from_currency == to_currency:
		return 1.0
	try:
		from erpnext.setup.utils import get_exchange_rate

		return get_exchange_rate(from_currency, to_currency, transaction_date) or 1.0
	except Exception:
		frappe.log_error(
			title=f"Amazon orders: could not resolve exchange rate {from_currency}->{to_currency}",
			message=frappe.get_traceback(),
		)
		return 1.0


# --- locking -----------------------------------------------------------------
def _acquire_order_lock(amazon_order_id, timeout=30):
	"""MySQL advisory lock, held across worker processes.

	The scheduled poll and a manual backfill can cover the same window at the
	same time; without this both see "no Sales Order yet" and each insert one.
	"""
	return bool(frappe.db.sql("SELECT GET_LOCK(%s, %s)", (f"amazon_order_{amazon_order_id}", timeout))[0][0])


def _release_order_lock(amazon_order_id):
	frappe.db.sql("SELECT RELEASE_LOCK(%s)", (f"amazon_order_{amazon_order_id}",))


# --- Sales Order lookup / state ----------------------------------------------
def get_sales_order(amazon_order_id):
	"""The live Sales Order for an Amazon order id, ignoring cancelled ones."""
	return frappe.db.get_value(
		"Sales Order",
		{"amazon_order_id": amazon_order_id, "docstatus": ["!=", 2]},
		"name",
		order_by="creation desc",
	)


def _has_downstream_documents(so_name):
	"""True if a Delivery Note or Sales Invoice already draws on this order.

	The two child tables name the link differently — Delivery Note Item has
	`against_sales_order`, Sales Invoice Item has `sales_order` — so this can't
	be a single loop over one fieldname.
	"""
	links = (("Delivery Note Item", "against_sales_order"), ("Sales Invoice Item", "sales_order"))
	for doctype, fieldname in links:
		if frappe.db.exists(doctype, {fieldname: so_name, "docstatus": ["<", 2]}):
			return True
	return False


# --- upsert ------------------------------------------------------------------
def upsert_order(order, mp, client, config):
	"""Create or update the Sales Order for one Amazon order.

	Returns one of: created, updated, unchanged, skipped_unresolved, conflict.
	"""
	amazon_order_id = order.get("AmazonOrderId")
	if not amazon_order_id:
		return "unchanged"
	if not _acquire_order_lock(amazon_order_id):
		frappe.log_error(
			title=f"Amazon order {amazon_order_id}: lock timed out",
			message="Another process held this order's lock for 30s+ — skipped to avoid a duplicate.",
		)
		return "conflict"
	try:
		return _upsert_order_unlocked(order, mp, client, config, amazon_order_id)
	finally:
		_release_order_lock(amazon_order_id)


def _upsert_order_unlocked(order, mp, client, config, amazon_order_id):
	status = order.get("OrderStatus") or ""
	existing = get_sales_order(amazon_order_id)

	if existing:
		return _update_existing(existing, order, status, amazon_order_id, client, config)

	if status in ORDER_STATUS_CANCEL:
		# Cancelled before we ever saw it — nothing worth creating.
		return "unchanged"

	order_items = fetch_order_items(client, amazon_order_id)
	time.sleep(ORDER_ITEMS_MIN_INTERVAL)
	if not order_items:
		return "unchanged"

	rows, unresolved = _line_items(order_items, config["warehouse"], config["delivery_date_for"](order))
	if unresolved:
		frappe.log_error(
			title=f"Amazon order {amazon_order_id}: unmapped SKUs, not imported",
			message=(
				f"No Item is linked to: {', '.join(sorted(set(unresolved)))}.\n"
				"Link the SKU on its Amazon Listing (Product field), then re-run the sync."
			),
		)
		return "skipped_unresolved"
	if not rows:
		return "unchanged"

	so = _build_sales_order(order, mp, config, amazon_order_id, _merge_duplicate_rows(rows))
	_append_tax(so, order_items, config["company"])
	so.flags.ignore_permissions = True
	so.insert()
	if status in ORDER_STATUS_SUBMIT:
		so.submit()
	return "created"


def _build_sales_order(order, mp, config, amazon_order_id, rows):
	purchase_date = _from_amazon_iso(order.get("PurchaseDate")) or now_datetime()
	currency = _order_currency(order, mp, config["company"])
	transaction_date = purchase_date.date()

	so = frappe.new_doc("Sales Order")
	so.customer = config["customer"]
	so.company = config["company"]
	so.currency = currency
	so.conversion_rate = _exchange_rate(currency, config["company_currency"], transaction_date)
	so.transaction_date = transaction_date
	so.delivery_date = config["delivery_date_for"](order)
	so.selling_price_list = config["price_list"]
	so.set_warehouse = config["warehouse"]
	so.po_no = amazon_order_id
	so.amazon_order_id = amazon_order_id
	so.amazon_marketplace = mp.name
	so.amazon_order_status = order.get("OrderStatus")
	so.amazon_fulfillment_channel = order.get("FulfillmentChannel")
	so.amazon_order_total = _money(order.get("OrderTotal"))
	so.amazon_last_updated_at = _from_amazon_iso(order.get("LastUpdateDate"))
	for row in rows:
		so.append("items", row)
	return so


def _update_existing(so_name, order, status, amazon_order_id, client, config):
	"""Maintain an already-imported order.

	A submitted Sales Order's items are immutable, so past the draft stage this
	only tracks Amazon's status — the one exception being a cancellation, which
	has to propagate or the two systems disagree about whether the order exists.
	"""
	docstatus = cint(frappe.db.get_value("Sales Order", so_name, "docstatus"))

	if status in ORDER_STATUS_CANCEL:
		if docstatus == 1 and _has_downstream_documents(so_name):
			frappe.log_error(
				title=f"Amazon order {amazon_order_id}: cancelled on Amazon but already fulfilled here",
				message=(
					f"Sales Order {so_name} has a Delivery Note or Sales Invoice against it, so it "
					"was left alone. Resolve the return/credit note manually."
				),
			)
			return "conflict"
		so = frappe.get_doc("Sales Order", so_name)
		_stamp_status(so, order)
		if docstatus == 1:
			so.flags.ignore_permissions = True
			so.cancel()
		# A draft can't be cancelled in ERPNext, and deleting it would throw away
		# an operator's edits without asking. Stamping the status is enough —
		# the Amazon Order Status column makes it obvious the draft is dead.
		return "updated"

	if docstatus == 0:
		return _refresh_draft(so_name, order, status, amazon_order_id, client, config)

	so = frappe.get_doc("Sales Order", so_name)
	return "updated" if _stamp_status(so, order) else "unchanged"


def _refresh_draft(so_name, order, status, amazon_order_id, client, config):
	"""Re-price a draft from Amazon, then submit it if the order has gone live.

	This is the whole reason Pending lands as a draft rather than a submitted
	document: Amazon withholds ItemPrice while an order is Pending, so the rates
	captured at creation are routinely zero. Submitting that draft as-is would
	book the order at the wrong value permanently — a submitted Sales Order's
	items can't be corrected afterwards. So the lines are rebuilt from a fresh
	getOrderItems every time we see the draft.
	"""
	order_items = fetch_order_items(client, amazon_order_id)
	time.sleep(ORDER_ITEMS_MIN_INTERVAL)
	if not order_items:
		return "unchanged"

	rows, unresolved = _line_items(order_items, config["warehouse"], config["delivery_date_for"](order))
	if unresolved:
		frappe.log_error(
			title=f"Amazon order {amazon_order_id}: unmapped SKUs, draft left as-is",
			message=(
				f"No Item is linked to: {', '.join(sorted(set(unresolved)))}.\n"
				"Link the SKU on its Amazon Listing (Product field), then re-run the sync."
			),
		)
		return "skipped_unresolved"
	if not rows:
		return "unchanged"

	so = frappe.get_doc("Sales Order", so_name)
	so.set("items", [])
	for row in _merge_duplicate_rows(rows):
		so.append("items", row)
	so.set("taxes", [])
	_append_tax(so, order_items, config["company"])
	so.amazon_order_status = order.get("OrderStatus")
	so.amazon_fulfillment_channel = order.get("FulfillmentChannel")
	so.amazon_order_total = _money(order.get("OrderTotal"))
	so.amazon_last_updated_at = _from_amazon_iso(order.get("LastUpdateDate"))
	so.flags.ignore_permissions = True
	so.save()

	if status in ORDER_STATUS_SUBMIT:
		so.submit()
	return "updated"


def _stamp_status(so, order):
	"""Write the Amazon status fields via db_set (works on submitted docs)."""
	values = {
		"amazon_order_status": order.get("OrderStatus"),
		"amazon_fulfillment_channel": order.get("FulfillmentChannel"),
		"amazon_order_total": _money(order.get("OrderTotal")),
		"amazon_last_updated_at": _from_amazon_iso(order.get("LastUpdateDate")),
	}
	changed = False
	for field, value in values.items():
		if so.get(field) != value:
			so.db_set(field, value, update_modified=False)
			changed = True
	return changed


# --- sync drivers ------------------------------------------------------------
def _build_config(conn, mp):
	company = _company(conn)
	warehouse = _warehouse(conn, company)

	def delivery_date_for(order):
		# LatestShipDate is Amazon's ship-by commitment, the closest thing to a
		# promised date it gives us. Pending orders often carry neither date.
		ship_by = _from_amazon_iso(order.get("LatestShipDate")) or _from_amazon_iso(
			order.get("EarliestShipDate")
		)
		purchase = _from_amazon_iso(order.get("PurchaseDate")) or now_datetime()
		return (ship_by or purchase).date()

	return {
		"company": company,
		"company_currency": frappe.get_cached_value("Company", company, "default_currency"),
		"customer": _customer(conn),
		"warehouse": warehouse,
		"price_list": _price_list(conn),
		"delivery_date_for": delivery_date_for,
	}


def _resolve_window(conn, updated_after, updated_before):
	"""Work out the LastUpdatedAfter/Before window for a scheduled run."""
	end = get_datetime(updated_before) if updated_before else _window_end()
	if updated_after:
		return get_datetime(updated_after), end

	watermark = conn.last_orders_sync_at or conn.orders_sync_from
	if watermark:
		start = get_datetime(watermark) - timedelta(seconds=ORDERS_SYNC_OVERLAP)
	else:
		start = add_to_date(now_datetime(), days=-ORDERS_DEFAULT_LOOKBACK_DAYS)
	return start, end


def sync_orders(marketplace=None, updated_after=None, updated_before=None, notify_user=None):
	"""Pull every order updated in the window and upsert it as a Sales Order.

	Intended to run as a background/scheduled job. The watermark advances only
	on a clean full walk — a mid-run failure re-reads the window next time
	rather than skipping the orders it never got to.
	"""
	conn = _connection()
	mp = _marketplace(marketplace)
	client = SpApiClient(conn)
	start, end = _resolve_window(conn, updated_after, updated_before)

	summary = {"marketplace": mp.name, "window_from": str(start), "window_to": str(end)}
	if start >= end:
		# Runs more often than the blind spot is wide; nothing new can exist.
		summary.update({"success": True, "seen": 0, "skipped_window": True})
		return _notify(summary, notify_user)

	counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped_unresolved": 0, "conflict": 0}
	seen = 0
	truncated = False

	try:
		config = _build_config(conn, mp)
		next_token = None
		for page in range(ORDERS_MAX_PAGES):
			orders, next_token = fetch_orders(
				mp,
				client,
				updated_after=start,
				updated_before=end,
				next_token=next_token,
			)
			for order in orders:
				seen += 1
				try:
					counts[upsert_order(order, mp, client, config)] += 1
				except Exception:
					counts["conflict"] += 1
					frappe.db.rollback()
					frappe.log_error(
						title=f"Amazon order {order.get('AmazonOrderId')} failed to import",
						message=frappe.get_traceback(),
					)
			frappe.db.commit()  # persist each page as we go
			if not next_token:
				break
			if page == ORDERS_MAX_PAGES - 1:
				truncated = True

		if not truncated and not updated_after:
			# Only a complete, unbounded-by-cap walk earns a watermark advance.
			conn.db_set("last_orders_sync_at", end, update_modified=False)
			frappe.db.commit()

		summary.update({"success": True, "seen": seen, "truncated": truncated, **counts})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Amazon order sync failed", message=frappe.get_traceback())
		summary.update({"success": False, "seen": seen, "error": str(e), **counts})

	return _notify(summary, notify_user)


def backfill_orders(marketplace=None, date_from=None, date_to=None, notify_user=None):
	"""Re-read an explicit date range, in chunks, without touching the watermark.

	Amazon degrades badly on very wide LastUpdated windows for high-volume
	sellers, so the range is walked ORDERS_BACKFILL_CHUNK_DAYS at a time. The
	upsert is idempotent, so a backfill that overlaps already-synced orders is
	safe and simply reports them as unchanged.
	"""
	if not date_from:
		frappe.throw(_("A start date is required for a backfill."))
	start = get_datetime(date_from)
	end = get_datetime(date_to) if date_to else _window_end()
	if start >= end:
		frappe.throw(_("The backfill start must be before its end."))

	totals = {"created": 0, "updated": 0, "unchanged": 0, "skipped_unresolved": 0, "conflict": 0}
	seen = 0
	chunks = 0
	failures = []

	cursor = start
	while cursor < end:
		chunk_end = min(cursor + timedelta(days=ORDERS_BACKFILL_CHUNK_DAYS), end)
		result = sync_orders(marketplace=marketplace, updated_after=cursor, updated_before=chunk_end)
		chunks += 1
		seen += cint(result.get("seen"))
		for key in totals:
			totals[key] += cint(result.get(key))
		if not result.get("success"):
			failures.append(f"{cursor} .. {chunk_end}: {result.get('error')}")
		cursor = chunk_end

	summary = {
		"success": not failures,
		"backfill": True,
		"window_from": str(start),
		"window_to": str(end),
		"chunks": chunks,
		"seen": seen,
		**totals,
	}
	if failures:
		summary["error"] = "; ".join(failures[:5])
	return _notify(summary, notify_user)


def _notify(summary, notify_user):
	if notify_user:
		frappe.publish_realtime("amazon_orders_sync_complete", summary, user=notify_user)
	return summary
