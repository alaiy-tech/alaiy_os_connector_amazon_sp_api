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
	SALES_CHANNEL,
	UNMAPPED_ITEM_CODE,
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
	"""SellerSKU -> Item code, via the Amazon Product Listing register.

	Never creates an Item *per SKU*: a mis-resolved SKU silently books revenue
	against the wrong product, and a stub per unknown SKU fills the catalog with
	things that look sellable but aren't managed. Unmatched SKUs go to the
	shared placeholder instead (see `_fallback_item`) so the order still lands.
	"""
	if not seller_sku:
		return None
	product = frappe.db.get_value("Amazon Product Listing", seller_sku, "product")
	if product:
		return product
	# A SKU that happens to be the item code itself is the common convention
	# on sites that list straight from their own catalog.
	if frappe.db.exists("Item", seller_sku):
		return seller_sku
	return None


def _fallback_item(conn):
	"""The placeholder Item unmatched SKUs book against.

	An explicit `orders_fallback_item` wins; otherwise one shared non-stock Item
	is created on demand. Non-stock matters — a stock placeholder would demand
	real inventory the moment anyone raised a Delivery Note against the order.

	Returns None only if creation fails, which is then the single remaining
	reason an order gets parked.
	"""
	configured = conn.orders_fallback_item
	if configured and frappe.db.exists("Item", configured):
		return configured

	if frappe.db.exists("Item", UNMAPPED_ITEM_CODE):
		return UNMAPPED_ITEM_CODE
	try:
		item = frappe.new_doc("Item")
		item.item_code = UNMAPPED_ITEM_CODE
		item.item_name = UNMAPPED_ITEM_CODE
		item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.description = (
			"Placeholder for Amazon order lines whose SellerSKU isn't linked to a catalog Item. "
			"The real SKU is on each Sales Order Item row (Amazon Seller SKU)."
		)
		item.flags.ignore_permissions = True
		item.insert()
		frappe.db.commit()
		return UNMAPPED_ITEM_CODE
	except Exception:
		frappe.log_error(
			title="Amazon orders: failed to create the unmapped-SKU placeholder Item",
			message=frappe.get_traceback(),
		)
		return None


def _line_items(order_items, warehouse, delivery_date, fallback_item=None):
	"""Amazon order items -> Sales Order Item rows, plus the SKUs that fell back.

	ItemPrice is the extended price for the whole QuantityOrdered, not a unit
	rate, and PromotionDiscount is likewise an order-item total — so the unit
	rate is (ItemPrice - PromotionDiscount) / qty.

	An unmatched SKU does not block the order: the line books against the shared
	placeholder, carrying Amazon's own title and the real SKU so nothing is lost
	and it can be re-pointed once the catalog catches up. `(None, skus)` comes
	back only when there was no placeholder to fall back to.
	"""
	rows = []
	unmapped = []
	for item in order_items:
		sku = item.get("SellerSKU")
		qty = cint(item.get("QuantityOrdered"))
		if qty <= 0:
			# Fully cancelled line: Amazon keeps the row with QuantityOrdered 0.
			continue
		gross = flt(_money(item.get("ItemPrice")))
		discount = flt(_money(item.get("PromotionDiscount")))
		asin = item.get("ASIN")
		row = {
			"qty": qty,
			"rate": (gross - discount) / qty if qty else 0,
			"warehouse": warehouse,
			"delivery_date": delivery_date,
			"amazon_order_item_id": item.get("OrderItemId"),
			"amazon_seller_sku": sku,
			# Recorded on every line, mapped or not. On an unmapped line it is
			# often the only durable handle on what was actually sold, since the
			# item_code is a shared placeholder.
			"amazon_asin": asin,
		}

		item_code = _resolve_item_code(sku)
		if item_code:
			row["item_code"] = item_code
		else:
			unmapped.append(sku or asin or "?")
			if not fallback_item:
				continue
			# Without Amazon's own title on the row, every placeholder line on
			# the order renders identically and the SKU is invisible on print.
			title = (item.get("Title") or "").strip()
			row["item_code"] = fallback_item
			row["item_name"] = (title or f"Amazon SKU {sku}")[:140]
			row["description"] = " | ".join(
				p for p in (title, f"SKU: {sku}" if sku else None, f"ASIN: {asin}" if asin else None) if p
			)
		rows.append(row)

	if not rows and unmapped:
		return None, unmapped
	return rows, unmapped


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

	Per-line identifiers need care here. Merging two lines of the *same* SKU
	keeps their shared ASIN, which is correct. But every unmapped line shares
	the placeholder item_code, so a merge can span genuinely different products
	— and carrying the first row's ASIN forward would label the merged row with
	a product it only partly represents. Identifiers that disagree are dropped
	rather than guessed, and the descriptions are concatenated so what was
	actually sold is still legible on the row.
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
		for field in ("amazon_asin", "amazon_seller_sku", "amazon_order_item_id"):
			if existing.get(field) != row.get(field):
				existing[field] = None
		for field in ("item_name", "description"):
			incoming = row.get(field)
			if incoming and incoming not in (existing.get(field) or ""):
				joined = f"{existing.get(field) or ''}; {incoming}".strip("; ")
				# item_name is a Data column; overflowing it fails the insert.
				existing[field] = joined[:140] if field == "item_name" else joined
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


# --- unmapped-SKU reporting --------------------------------------------------
def _note_unmapped(config, unmapped):
	"""Record SKUs that fell back to the placeholder.

	Accumulate only — no per-order Error Log. The order *imported*; an unlinked
	SKU is a catalog gap to tidy up, not an error, and a backfill over hundreds
	of such orders would otherwise bury the Error Log in rows about successful
	imports. `_report_unmapped` writes one aggregated entry per run instead.
	"""
	if not unmapped:
		return
	config["unmapped_skus"].update(unmapped)


def _report_unmapped(unmapped_skus):
	"""One Error Log entry per run naming every SKU that needs linking."""
	if not unmapped_skus:
		return
	frappe.log_error(
		title=f"Amazon orders: {len(unmapped_skus)} SKU(s) imported against the placeholder Item",
		message=(
			"These orders imported successfully — this is a catalog gap, not a failure.\n\n"
			"No Item is linked to:\n"
			+ "\n".join(f"  {sku}" for sku in sorted(unmapped_skus))
			+ "\n\nSet the Product field on each matching Amazon Product Listing so future orders "
			"map correctly. Orders already imported are not re-pointed automatically."
		),
	)


def _park_unmapped(amazon_order_id, unmapped):
	"""Only reachable when even the placeholder Item couldn't be resolved."""
	frappe.log_error(
		title=f"Amazon order {amazon_order_id}: not imported, no placeholder Item",
		message=(
			f"No Item is linked to: {', '.join(sorted(set(unmapped)))}, and the fallback "
			"placeholder could not be created. Set 'Unmapped SKU Item' under Orders on the "
			"Amazon Connection to an existing Item, then re-run the sync."
		),
	)
	return "skipped_unresolved"


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

	rows, unmapped = _line_items(
		order_items,
		config["warehouse"],
		config["delivery_date_for"](order),
		fallback_item=config["fallback_item"],
	)
	if rows is None:
		return _park_unmapped(amazon_order_id, unmapped)
	_note_unmapped(config, unmapped)
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
	so.sales_channel = SALES_CHANNEL
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

	rows, unmapped = _line_items(
		order_items,
		config["warehouse"],
		config["delivery_date_for"](order),
		fallback_item=config["fallback_item"],
	)
	if rows is None:
		return _park_unmapped(amazon_order_id, unmapped)
	_note_unmapped(config, unmapped)
	if not rows:
		return "unchanged"

	so = frappe.get_doc("Sales Order", so_name)
	so.set("items", [])
	for row in _merge_duplicate_rows(rows):
		so.append("items", row)
	so.set("taxes", [])
	_append_tax(so, order_items, config["company"])
	so.sales_channel = SALES_CHANNEL
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
		"fallback_item": _fallback_item(conn),
		"delivery_date_for": delivery_date_for,
		# Accumulated across the run so the summary can name every SKU that
		# needs linking, in one place.
		"unmapped_skus": set(),
	}


# Outcomes that mean "Amazon gave us this order and no Sales Order exists for
# it". The watermark must never advance past one of these — that is precisely
# how an order gets seen once and then skipped forever.
NON_PERSISTING_OUTCOMES = ("skipped_unresolved", "conflict")


def _earlier(current, candidate):
	"""Running minimum that tolerates either side being None."""
	if candidate is None:
		return current
	if current is None:
		return candidate
	return min(current, candidate)


def _capped_watermark(end, retry_from):
	"""Where the watermark may advance to, given orders that didn't land.

	Held one second before the oldest un-imported order so the next run's
	LastUpdatedAfter definitely re-includes it — the API filter is inclusive at
	second granularity, and landing exactly on the boundary is not worth
	gambling an order on.

	This can pin the watermark indefinitely if an order fails permanently. That
	is deliberate: a stuck watermark is visible in the summary and on the
	connection, whereas silently stepping over the order is not. The run
	reports `watermark_held_at` and the offending ids so it can be resolved.
	"""
	if retry_from is None:
		return end
	return min(end, retry_from - timedelta(seconds=1))


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

	Intended to run as a background/scheduled job.

	The watermark never moves past an order that didn't land. A whole-run
	failure re-reads the window next time, and — just as importantly — a single
	order that was *seen and refused* holds the watermark at its own
	LastUpdateDate, so the next run picks it up again instead of stepping over
	it forever. Orders ahead of that point are still not re-read.
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
	config = None
	# Oldest LastUpdateDate among orders that were seen but did NOT persist.
	# The watermark is not allowed past this, or those orders are lost.
	retry_from = None
	failed_ids = []

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
					outcome = upsert_order(order, mp, client, config)
					counts[outcome] += 1
				except Exception:
					outcome = "conflict"
					counts["conflict"] += 1
					frappe.db.rollback()
					frappe.log_error(
						title=f"Amazon order {order.get('AmazonOrderId')} failed to import",
						message=frappe.get_traceback(),
					)
				if outcome in NON_PERSISTING_OUTCOMES:
					retry_from = _earlier(retry_from, _from_amazon_iso(order.get("LastUpdateDate")))
					failed_ids.append(order.get("AmazonOrderId"))
			frappe.db.commit()  # persist each page as we go
			if not next_token:
				break
			if page == ORDERS_MAX_PAGES - 1:
				truncated = True

		if not truncated and not updated_after:
			# Only a complete, unbounded-by-cap walk earns a watermark advance —
			# and only up to the oldest order that didn't land.
			watermark = _capped_watermark(end, retry_from)
			conn.db_set("last_orders_sync_at", watermark, update_modified=False)
			frappe.db.commit()
			if watermark != end:
				summary["watermark_held_at"] = str(watermark)
				summary["retry_orders"] = [i for i in failed_ids if i][:20]

		summary.update({"success": True, "seen": seen, "truncated": truncated, **counts})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Amazon order sync failed", message=frappe.get_traceback())
		summary.update({"success": False, "seen": seen, "error": str(e), **counts})

	# Named in the summary so the operator sees what still needs linking without
	# digging through the Error Log, and logged once per run rather than once
	# per order.
	summary["unmapped_skus"] = sorted(config["unmapped_skus"]) if config else []
	_report_unmapped(summary["unmapped_skus"])
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
	unmapped = set()

	cursor = start
	while cursor < end:
		chunk_end = min(cursor + timedelta(days=ORDERS_BACKFILL_CHUNK_DAYS), end)
		result = sync_orders(marketplace=marketplace, updated_after=cursor, updated_before=chunk_end)
		chunks += 1
		seen += cint(result.get("seen"))
		for key in totals:
			totals[key] += cint(result.get(key))
		unmapped.update(result.get("unmapped_skus") or [])
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
		"unmapped_skus": sorted(unmapped),
		**totals,
	}
	if failures:
		summary["error"] = "; ".join(failures[:5])
	return _notify(summary, notify_user)


def _notify(summary, notify_user):
	if notify_user:
		frappe.publish_realtime("amazon_orders_sync_complete", summary, user=notify_user)
	return summary
