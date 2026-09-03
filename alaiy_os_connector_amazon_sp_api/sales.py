# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Sales reporting over the Sales Orders the Amazon order sync wrote.

`spapi/orders.py` materialises every Amazon order as a Sales Order and stamps
`amazon_order_id`, `amazon_order_status`, `amazon_fulfillment_channel` and
`amazon_marketplace` on it, with `amazon_seller_sku` and `amazon_asin` on every
line. Nothing read any of it back out until this module. There is no Amazon call
anywhere here: this is the local half of the sales answer, and `spapi/sales.py`
is the live half.

## Which orders count

Not `docstatus = 1`. That filter is wrong twice over, and both ways book revenue
that does not exist:

- A **draft** Sales Order is an Amazon `Pending` order, and Amazon withholds
  buyer-visible pricing while an order is Pending (see `ORDER_STATUS_DRAFT` and
  the note above it in constants). Its totals are absent or zero, so counting
  drafts does not add a small error, it adds orders worth nothing.
- An order Amazon **cancelled after we shipped or invoiced it** is deliberately
  left submitted by `orders._update_existing`, because cancelling a Sales Order
  with a Delivery Note against it would corrupt the downstream documents. It
  carries `amazon_order_status` in `ORDER_STATUS_CANCEL` and is the only signal
  that it is dead.

So the filter is docstatus 1 *and* a status outside `ORDER_STATUS_CANCEL`. The
status test is written `IFNULL(..., '') NOT IN` rather than a bare `NOT IN`
because SQL's `NULL NOT IN (...)` is NULL, not true — a submitted order whose
status was never stamped would silently vanish from every total.

## Which currency

`orders._order_currency` takes the currency from Amazon's own
`OrderTotal.CurrencyCode`, per order, so `grand_total` on a two-marketplace
account is pounds in some rows and euros in others and summing it is meaningless.
Every figure here sums a `base_*` column — company currency, via each order's
`conversion_rate` — and every result names the currency it is in.

That is right rather than perfect: `orders._exchange_rate` falls back to `1.0`
and logs when it cannot resolve a rate, so a base total can be quietly wrong for
those orders. `_totals` counts them into `orders_at_fallback_rate` so an answer
can say so instead of the caller never learning.

The company is the one the sync books to, and reads are scoped to it — mixing
companies would mix default currencies again, one level up.

## These are not Amazon's revenue figures

Shipping and fees are not mapped by the order sync at all; `amazon_order_total`
exists on the Sales Order specifically to make that gap visible. So:

- `product_sales` sums `base_net_total` — items only, no tax. It is the figure
  that lines up with Amazon's "ordered product sales", and it is the headline.
- `order_total` sums `base_grand_total` — items plus the tax rows
  `orders._append_tax` wrote. Still no shipping, and still nothing net of
  Amazon's fees.

Neither is a payout. Every tool description says so, `prompts/pack.md` says so,
and `spapi/sales.py` exists so there is an Amazon-side number to check them
against.

## Envelope width

`csv_export._rows_from` reads a dict as a wrapper around its rows only while it
carries at most `_ENVELOPE_METADATA_KEYS` (5) non-list values; past that the
export writes the summary line instead of the series it summarises. Every result
here therefore keeps its scalars nested — `period`, `filters`, `totals` — which
holds each envelope to four non-list keys with one to spare. Adding a top-level
scalar to any of these is what would break the CSV, silently.

## MariaDB

The bucket expressions use `WEEKDAY` and `DAYOFMONTH`. The app is already
MariaDB-only through `orders._acquire_order_lock`'s `GET_LOCK`, so this adds no
constraint that was not there; it is worth knowing it is here.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_last_day, getdate, nowdate

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi.constants import ORDER_STATUS_CANCEL

# Bucket start per granularity, as a MariaDB expression over `so.transaction_date`.
# `total` groups on a constant expression so one code path serves every
# granularity: it yields a single group whose start is the period's own start.
BUCKET_SQL = {
	"day": "so.transaction_date",
	"week": "DATE_SUB(so.transaction_date, INTERVAL WEEKDAY(so.transaction_date) DAY)",
	"month": "DATE_SUB(so.transaction_date, INTERVAL DAYOFMONTH(so.transaction_date) - 1 DAY)",
	"total": "DATE(%(date_from)s)",
}
GRANULARITIES = tuple(BUCKET_SQL)

# Amazon's own vocabulary, from Sales Order.amazon_fulfillment_channel.
# Deliberately NOT the `fulfillment_channel` of `list_listings`, whose values are
# DEFAULT and AMAZON: two enums under one parameter name across one pack is a trap
# for a model that has just read the other tool. Different concept, different
# parameter name.
FULFILLMENT_NETWORKS = ("AFN", "MFN")

# Two years of days is 731 buckets and about as many lines in a completion. Past
# this the request is refused naming a coarser granularity, rather than truncated:
# a series silently missing its tail is worse than one that was not produced.
MAX_BUCKETS = 400

TOP_DEFAULT_LIMIT = 10
TOP_MAX_LIMIT = 50

ORDERS_PAGE_SIZE = 20
ORDERS_MAX_PAGE_SIZE = 50

COMPARE_BASELINES = ("previous_period", "previous_year")


# --- validation --------------------------------------------------------------
def _one_of(value, allowed, label, default=None):
	"""Validate against a fixed set, naming the real values when it fails.

	The alternative — falling through to a default — answers a different question
	from the one asked without saying so.
	"""
	if value in (None, ""):
		if default is None:
			frappe.throw(_("{0} is required. One of: {1}.").format(label, ", ".join(allowed)))
		return default
	value = str(value).strip()
	if value not in allowed:
		frappe.throw(_("Unknown {0} {1}. One of: {2}.").format(label, value, ", ".join(allowed)))
	return value


def _period(date_from, date_to=None, label="date_from"):
	"""A validated, inclusive (from, to) pair of dates.

	`date_to` defaults to today rather than to `date_from`: "sales since March"
	is a whole question, "sales on the single day of March 1st" is not the one
	being asked.

	`label` names the parameter in the error, because `compare_sales_periods`
	validates two pairs and an error saying `date_from` when what was missing is
	`baseline_from` sends a caller to fix the wrong argument.
	"""
	if not date_from:
		frappe.throw(_("{0} is required (YYYY-MM-DD).").format(label))
	start = getdate(date_from)
	end = getdate(date_to) if date_to else getdate(nowdate())
	if start > end:
		frappe.throw(_("{0} {1} is after {2} {3}.").format(label, start, label.replace("from", "to"), end))
	return start, end


def _assert_bucket_count(date_from, date_to, granularity):
	if granularity == "total":
		return
	days = (date_to - date_from).days + 1
	buckets = {"day": days, "week": days // 7 + 1, "month": days // 28 + 1}[granularity]
	if buckets > MAX_BUCKETS:
		frappe.throw(
			_(
				"{0} days at {1} granularity is about {2} buckets, over the limit of {3}. "
				"Ask for a coarser granularity (week, month or total) or a shorter period."
			).format(days, granularity, buckets, MAX_BUCKETS)
		)


# --- company & currency ------------------------------------------------------
def _company(connection=None):
	"""The company Amazon orders book to.

	`orders._company` resolves the same two sources and throws when neither is
	set, which is right for a sync that is about to write. A read can do better:
	if the connection's company was cleared after orders were already synced, the
	orders themselves still name the company they were booked to, and answering
	from them beats refusing.
	"""
	conn = connections.resolve(connection)
	company = conn.orders_company or frappe.defaults.get_global_default("company")
	if not company:
		company = frappe.db.get_value(
			"Sales Order",
			{"amazon_order_id": ["is", "set"]},
			"company",
			order_by="modified desc",
		)
	if not company:
		frappe.throw(
			_(
				"No company is set for Amazon orders and none has ever been booked, so there "
				"is no currency to report these figures in. Set 'Company' under Orders on the "
				"Amazon Connection."
			)
		)
	return company


def _currency(company):
	return frappe.get_cached_value("Company", company, "default_currency")


# --- coverage ----------------------------------------------------------------
def coverage(company=None):
	"""The date span of what the order sync has actually fetched.

	Deliberately unfiltered by docstatus: a cancelled or still-Pending order is
	data the sync reached, and this answers how far it reached, not what sold.

	Without this every total below lies by omission. A bench whose sync has only
	ever run back to March answers "sales in January" with a confident zero, and
	nothing in the number says which of "you sold nothing" and "we have no data"
	it means.
	"""
	where = ["so.amazon_order_id IS NOT NULL", "so.amazon_order_id != ''"]
	params = {}
	if company:
		where.append("so.company = %(company)s")
		params["company"] = company

	# Raw SQL like the rest of this module, rather than `frappe.get_all` with
	# aggregate fields: from v16 the query builder rejects a SQL function written
	# as a string in `fields` and wants `{"MIN": "transaction_date"}` instead, and
	# a module that already speaks SQL does not need a second dialect to say MIN.
	row = frappe.db.sql(
		f"""
		SELECT MIN(so.transaction_date) AS first_order_date,
		       MAX(so.transaction_date) AS last_order_date,
		       COUNT(so.name) AS synced_orders
		FROM `tabSales Order` so
		WHERE {" AND ".join(where)}
		""",
		params,
		as_dict=True,
	)[0]
	return {
		"first_order_date": str(row.first_order_date) if row.first_order_date else None,
		"last_order_date": str(row.last_order_date) if row.last_order_date else None,
		"synced_orders": cint(row.synced_orders),
	}


def _coverage_note(cov, date_from, date_to):
	"""Why a zero might not mean zero, in words the answer can pass on."""
	first = cov["first_order_date"]
	last = cov["last_order_date"]
	if not first:
		return (
			"No Amazon orders have ever synced to this site, so every figure below is zero "
			"because there is no data — not because nothing sold."
		)
	first, last = getdate(first), getdate(last)
	if date_to < first or date_from > last:
		return (
			f"This period lies entirely outside the synced order data, which runs "
			f"{first} to {last}. The figures below are zero because there is no data for "
			f"this period, not because nothing sold."
		)
	if date_from < first:
		return (
			f"Synced order data starts {first}, after this period does. Everything before "
			f"{first} is missing rather than empty."
		)
	return None


# --- the shared WHERE --------------------------------------------------------
def _sold_where(company, date_from, date_to, fulfillment_network=None, marketplace=None):
	"""The conditions every figure in this module is built on, and its params.

	See the module docstring for why the status test is here and why it is written
	with IFNULL.
	"""
	where = [
		"so.docstatus = 1",
		"so.amazon_order_id IS NOT NULL",
		"so.amazon_order_id != ''",
		"IFNULL(so.amazon_order_status, '') NOT IN %(cancelled)s",
		"so.company = %(company)s",
		"so.transaction_date BETWEEN %(date_from)s AND %(date_to)s",
	]
	params = {
		"cancelled": tuple(ORDER_STATUS_CANCEL),
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
	}
	if fulfillment_network:
		where.append("so.amazon_fulfillment_channel = %(fulfillment_network)s")
		params["fulfillment_network"] = fulfillment_network
	if marketplace:
		where.append("so.amazon_marketplace = %(marketplace)s")
		params["marketplace"] = marketplace
	return where, params


# --- bucket shaping ----------------------------------------------------------
def _bucket_bounds(start, granularity, date_from, date_to):
	"""One bucket's real coverage, clamped to the period it was asked for.

	A month bucket for a period starting mid-month covers half a month, and
	saying "2026-08-15 to 2026-08-31" is what makes that visible. Reporting the
	calendar month would invite a comparison against a whole one.
	"""
	if granularity == "total":
		return date_from, date_to
	start = getdate(start)
	if granularity == "day":
		end = start
	elif granularity == "week":
		end = add_days(start, 6)
	else:
		end = get_last_day(start)
	return max(start, date_from), min(getdate(end), date_to)


def _series(rows, granularity, date_from, date_to):
	"""Grouped SQL rows -> the bucket list, ordered, with real dates on each.

	Buckets with no qualifying orders are absent rather than zero-filled. Filling
	them would be the friendlier default for a chart and the wrong one here: a
	year of days is 365 rows of mostly nothing in a completion, and the tool
	descriptions say plainly that a missing bucket is an empty one.
	"""
	out = []
	for row in rows:
		start, end = _bucket_bounds(row["bucket"], granularity, date_from, date_to)
		out.append(
			{
				"period_start": str(start),
				"period_end": str(end),
				"product_sales": flt(row.get("product_sales"), 2),
				"order_total": flt(row.get("order_total"), 2),
				"units": cint(row.get("units")),
				"order_count": cint(row.get("order_count")),
			}
		)
	return out


def _totals_from(buckets):
	"""Totals as the sum of the buckets, never as a separate query.

	Two queries would let a rounding or filter difference put a total beside a
	series that does not add up to it, which reads as a bug in the data rather
	than in the tool. `avg_order_value` is computed from the summed totals, not
	averaged across buckets, because an average of averages is not one.
	"""
	product_sales = flt(sum(b["product_sales"] for b in buckets), 2)
	order_count = sum(b["order_count"] for b in buckets)
	return {
		"product_sales": product_sales,
		"order_total": flt(sum(b["order_total"] for b in buckets), 2),
		"units": sum(b["units"] for b in buckets),
		"order_count": order_count,
		"avg_order_value": flt(product_sales / order_count, 2) if order_count else 0.0,
	}


def _marketplaces(rows):
	"""The distinct marketplaces behind a set of grouped rows, sorted.

	GROUP_CONCAT gives one comma-joined string per bucket; this flattens them.
	A bucket with none is a bucket whose orders predate the field being stamped,
	which is absence rather than a marketplace called "".
	"""
	found = set()
	for row in rows:
		for name in (row.get("marketplaces") or "").split(","):
			if name.strip():
				found.add(name.strip())
	return sorted(found)


def _merge_units(money_rows, unit_rows):
	"""Fold the item-level unit counts into the order-level money rows.

	Two queries because one cannot answer both: `SUM(so.base_net_total)` over a
	join to the item table multiplies every order's total by its line count. So
	money and counts come from the order table, units come from the item table,
	and they meet here on the bucket key.

	A unit bucket with no money bucket cannot normally happen — same WHERE, same
	grouping — but if it did, dropping it would hide units that were sold. It is
	carried through with zero money instead.
	"""
	units = {str(row["bucket"]): cint(row["units"]) for row in unit_rows}
	merged = []
	for row in money_rows:
		row = dict(row)
		row["units"] = units.pop(str(row["bucket"]), 0)
		merged.append(row)
	for bucket, count in units.items():
		merged.append({"bucket": bucket, "units": count})
	merged.sort(key=lambda row: str(row["bucket"]))
	return merged


# --- the reads ---------------------------------------------------------------
def sales_summary(date_from, date_to=None, granularity="day", fulfillment_network=None, marketplace=None):
	"""Revenue, units, orders and average order value over a period, bucketed."""
	date_from, date_to = _period(date_from, date_to)
	granularity = _one_of(granularity, GRANULARITIES, "granularity", default="day")
	if fulfillment_network:
		fulfillment_network = _one_of(fulfillment_network, FULFILLMENT_NETWORKS, "fulfillment network")
	_assert_bucket_count(date_from, date_to, granularity)

	company = _company()
	where, params = _sold_where(company, date_from, date_to, fulfillment_network, marketplace)
	bucket = BUCKET_SQL[granularity]
	conditions = " AND ".join(where)

	money_rows = frappe.db.sql(
		f"""
		SELECT {bucket} AS bucket,
		       COUNT(DISTINCT so.name) AS order_count,
		       SUM(so.base_net_total) AS product_sales,
		       SUM(so.base_grand_total) AS order_total,
		       SUM(CASE WHEN so.currency != %(company_currency)s AND so.conversion_rate = 1
		                THEN 1 ELSE 0 END) AS fallback_rate_orders,
		       GROUP_CONCAT(DISTINCT so.amazon_marketplace) AS marketplaces
		FROM `tabSales Order` so
		WHERE {conditions}
		GROUP BY bucket
		ORDER BY bucket
		""",
		{**params, "company_currency": _currency(company)},
		as_dict=True,
	)
	unit_rows = frappe.db.sql(
		f"""
		SELECT {bucket} AS bucket, SUM(soi.qty) AS units
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE {conditions}
		GROUP BY bucket
		ORDER BY bucket
		""",
		params,
		as_dict=True,
	)

	buckets = _series(_merge_units(money_rows, unit_rows), granularity, date_from, date_to)
	totals = _totals_from(buckets)
	# A conversion that fell back to 1.0 is logged by the sync and invisible here
	# otherwise; surfacing the count lets an answer caveat itself rather than
	# quoting a base total that silently is not one.
	totals["orders_at_fallback_rate"] = sum(cint(r.get("fallback_rate_orders")) for r in money_rows)

	cov = coverage(company)
	return {
		"period": {
			"date_from": str(date_from),
			"date_to": str(date_to),
			"granularity": granularity,
			"fulfillment_network": fulfillment_network,
			# Every marketplace that contributed, because unlike the live read
			# these figures are not scoped to the primary one. On a single-
			# marketplace account it is a list of one and costs nothing; on a
			# multi-marketplace account it is the reason the two sources differ.
			"marketplaces": _marketplaces(money_rows),
		},
		"currency": _currency(company),
		"totals": totals,
		"coverage": {**cov, "note": _coverage_note(cov, date_from, date_to)},
		"buckets": buckets,
	}


def top_selling_products(
	date_from,
	date_to=None,
	by="revenue",
	group_by="sku",
	limit=None,
	fulfillment_network=None,
	marketplace=None,
):
	"""The best-selling SKUs or ASINs over a period, ranked."""
	date_from, date_to = _period(date_from, date_to)
	by = _one_of(by, ("revenue", "units"), "ranking", default="revenue")
	group_by = _one_of(group_by, ("sku", "asin"), "grouping", default="sku")
	if fulfillment_network:
		fulfillment_network = _one_of(fulfillment_network, FULFILLMENT_NETWORKS, "fulfillment network")
	limit = min(cint(limit) or TOP_DEFAULT_LIMIT, TOP_MAX_LIMIT)

	company = _company()
	where, params = _sold_where(company, date_from, date_to, fulfillment_network, marketplace)
	conditions = " AND ".join(where)

	key = "soi.amazon_seller_sku" if group_by == "sku" else "soi.amazon_asin"
	order = "product_sales DESC" if by == "revenue" else "units DESC"

	# Lines whose grouping key was never stamped are excluded from the ranking and
	# counted separately below: a row keyed on the empty string would rank as a
	# product, and it is several unrelated ones.
	rows = frappe.db.sql(
		f"""
		SELECT {key} AS group_key,
		       MAX(soi.item_code) AS item_code,
		       MAX(soi.item_name) AS item_name,
		       MAX(soi.amazon_asin) AS amazon_asin,
		       COUNT(DISTINCT soi.amazon_seller_sku) AS sku_count,
		       COUNT(DISTINCT so.name) AS order_count,
		       SUM(soi.qty) AS units,
		       SUM(soi.base_net_amount) AS product_sales
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE {conditions} AND IFNULL({key}, '') != ''
		GROUP BY group_key
		ORDER BY {order}
		LIMIT %(limit)s
		""",
		{**params, "limit": limit},
		as_dict=True,
	)
	overall = frappe.db.sql(
		f"""
		SELECT SUM(soi.qty) AS units,
		       SUM(soi.base_net_amount) AS product_sales,
		       COUNT(DISTINCT CASE WHEN IFNULL({key}, '') != '' THEN {key} END) AS products,
		       SUM(CASE WHEN IFNULL({key}, '') = '' THEN soi.base_net_amount ELSE 0 END)
		           AS unattributed_product_sales
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE {conditions}
		""",
		params,
		as_dict=True,
	)[0]

	period_sales = flt(overall.product_sales, 2)
	ranked = []
	for row in rows:
		sales = flt(row.product_sales, 2)
		ranked.append(
			{
				("amazon_seller_sku" if group_by == "sku" else "amazon_asin"): row.group_key,
				**({"amazon_asin": row.amazon_asin} if group_by == "sku" else {"sku_count": cint(row.sku_count)}),
				"item_code": row.item_code,
				"item_name": row.item_name,
				"units": cint(row.units),
				"order_count": cint(row.order_count),
				"product_sales": sales,
				"share_of_product_sales": flt(sales / period_sales, 4) if period_sales else None,
			}
		)

	cov = coverage(company)
	return {
		"period": {
			"date_from": str(date_from),
			"date_to": str(date_to),
			"fulfillment_network": fulfillment_network,
		},
		"ranking": {"by": by, "group_by": group_by, "limit": limit},
		"currency": _currency(company),
		"totals": {
			"product_sales": period_sales,
			"units": cint(overall.units),
			"products": cint(overall.products),
			"ranked_product_sales": flt(sum(r["product_sales"] for r in ranked), 2),
			"unattributed_product_sales": flt(overall.unattributed_product_sales, 2),
			"coverage_note": _coverage_note(cov, date_from, date_to),
		},
		"rows": ranked,
	}


def product_sales(sku=None, asin=None, date_from=None, date_to=None, granularity="month", marketplace=None):
	"""How one SKU or ASIN sold over a period, bucketed."""
	sku = (sku or "").strip()
	asin = (asin or "").strip()
	if bool(sku) == bool(asin):
		frappe.throw(_("Pass exactly one of sku or asin."))
	date_from, date_to = _period(date_from, date_to)
	granularity = _one_of(granularity, GRANULARITIES, "granularity", default="month")
	_assert_bucket_count(date_from, date_to, granularity)

	company = _company()
	where, params = _sold_where(company, date_from, date_to, marketplace=marketplace)
	if sku:
		where.append("soi.amazon_seller_sku = %(sku)s")
		params["sku"] = sku
	else:
		where.append("soi.amazon_asin = %(asin)s")
		params["asin"] = asin
	bucket = BUCKET_SQL[granularity]

	# One query, unlike sales_summary: money is per line here (base_net_amount),
	# so the join does not multiply anything and there is nothing to merge.
	rows = frappe.db.sql(
		f"""
		SELECT {bucket} AS bucket,
		       COUNT(DISTINCT so.name) AS order_count,
		       SUM(soi.qty) AS units,
		       SUM(soi.base_net_amount) AS product_sales,
		       SUM(soi.base_net_amount) AS order_total
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE {" AND ".join(where)}
		GROUP BY bucket
		ORDER BY bucket
		""",
		params,
		as_dict=True,
	)

	buckets = _series(rows, granularity, date_from, date_to)
	totals = _totals_from(buckets)
	# `order_total` on a per-line read would just repeat product_sales — the tax
	# rows it would add are per order, not per line — so it is dropped rather than
	# reported as a second figure that is the same number. Dropped after the
	# totals are summed, because `_totals_from` reads the key it removes.
	for entry in buckets:
		entry.pop("order_total", None)
	totals.pop("order_total", None)

	cov = coverage(company)
	return {
		"product": {"sku": sku or None, "asin": asin or None},
		"period": {"date_from": str(date_from), "date_to": str(date_to), "granularity": granularity},
		"currency": _currency(company),
		"totals": {**totals, "coverage_note": _coverage_note(cov, date_from, date_to)},
		"buckets": buckets,
	}


def _baseline_period(date_from, date_to, compare_to):
	"""The period to measure against.

	`previous_period` is the same number of days ending the day before this one
	starts, so a 30-day window is compared with 30 days and not with a calendar
	month of a different length. `previous_year` shifts the same dates back a
	year, which is the right comparison for anything seasonal and the wrong one
	for a window that crosses a leap day — 365 days back, stated as dates, is
	what it is.
	"""
	if compare_to == "previous_year":
		return add_days(date_from, -365), add_days(date_to, -365)
	span = (date_to - date_from).days
	end = add_days(date_from, -1)
	return add_days(end, -span), end


def _change(current, baseline):
	"""Absolute and percentage movement, with no percentage invented from zero.

	Growth from nothing has no percentage — 0 to 500 is not "infinite" or "100%"
	— so `percent` is null there and the absolute figure carries the answer. A
	model handed a number would quote it.
	"""
	out = {}
	for key in ("product_sales", "order_total", "units", "order_count", "avg_order_value"):
		now, before = flt(current.get(key)), flt(baseline.get(key))
		out[key] = {
			"current": now,
			"baseline": before,
			"absolute": flt(now - before, 2),
			"percent": flt((now - before) / abs(before) * 100, 2) if before else None,
		}
	return out


def compare_sales_periods(
	date_from,
	date_to,
	compare_to="previous_period",
	baseline_from=None,
	baseline_to=None,
	fulfillment_network=None,
	marketplace=None,
):
	"""One period's totals against another's, with the deltas already computed.

	Its own tool rather than two summary calls, because the arithmetic is the
	part that goes wrong: a percentage change worked out inside a completion
	comes out plausible, unlabelled and occasionally wrong, and this is the
	question people ask most.
	"""
	date_from, date_to = _period(date_from, date_to)
	if baseline_from or baseline_to:
		base_from, base_to = _period(baseline_from, baseline_to or baseline_from, label="baseline_from")
		compare_to = "explicit"
	else:
		compare_to = _one_of(compare_to, COMPARE_BASELINES, "baseline", default="previous_period")
		base_from, base_to = _baseline_period(date_from, date_to, compare_to)

	current = sales_summary(
		date_from, date_to, "total", fulfillment_network=fulfillment_network, marketplace=marketplace
	)
	baseline = sales_summary(
		base_from, base_to, "total", fulfillment_network=fulfillment_network, marketplace=marketplace
	)

	return {
		"current": {**current["period"], **current["totals"]},
		"baseline": {**baseline["period"], **baseline["totals"], "basis": compare_to},
		"currency": current["currency"],
		"change": _change(current["totals"], baseline["totals"]),
		"coverage": current["coverage"],
	}


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
	"""A page of the Amazon orders behind the figures above.

	Paged and shaped like `listings.list_listings` on purpose: it is the same
	move — a total, a page, and `has_more` — and the pack already teaches a model
	to read that shape and quote the denominator.

	Unlike every other read here this one does NOT apply the sold filter. It
	takes `status`, so refusing to show a cancelled order would make that
	parameter a lie; each row carries `counts_as_sold` instead, so a caller can
	see which rows the totals were built from and which were excluded.

	Two currencies land on every row, which is the price of showing an order
	rather than a total. `product_sales` and `order_total` are company currency,
	as everywhere else in this module; `amazon_order_total` is Amazon's own
	figure in the order's own currency, which `order_currency` names. On a
	single-marketplace account they are the same currency and the distinction
	costs nothing; on a two-marketplace one it is the difference between a column
	that can be summed and one that cannot.
	"""
	date_from, date_to = _period(date_from, date_to)
	if fulfillment_network:
		fulfillment_network = _one_of(fulfillment_network, FULFILLMENT_NETWORKS, "fulfillment network")
	page_no = max(cint(page_no), 1)
	page_size = min(cint(page_size) or ORDERS_PAGE_SIZE, ORDERS_MAX_PAGE_SIZE)

	company = _company()
	where = [
		"so.amazon_order_id IS NOT NULL",
		"so.amazon_order_id != ''",
		"so.company = %(company)s",
		"so.transaction_date BETWEEN %(date_from)s AND %(date_to)s",
	]
	params = {"company": company, "date_from": date_from, "date_to": date_to}
	if status:
		where.append("so.amazon_order_status = %(status)s")
		params["status"] = status
	if fulfillment_network:
		where.append("so.amazon_fulfillment_channel = %(fulfillment_network)s")
		params["fulfillment_network"] = fulfillment_network
	if marketplace:
		where.append("so.amazon_marketplace = %(marketplace)s")
		params["marketplace"] = marketplace
	if sku:
		where.append(
			"EXISTS (SELECT 1 FROM `tabSales Order Item` soi "
			"WHERE soi.parent = so.name AND soi.amazon_seller_sku = %(sku)s)"
		)
		params["sku"] = sku
	conditions = " AND ".join(where)

	total = frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabSales Order` so WHERE {conditions}", params
	)[0][0]
	rows = frappe.db.sql(
		f"""
		SELECT so.name AS sales_order,
		       so.amazon_order_id,
		       so.transaction_date,
		       so.amazon_order_status,
		       so.amazon_fulfillment_channel AS fulfillment_network,
		       so.amazon_marketplace,
		       so.docstatus,
		       so.currency AS order_currency,
		       so.base_net_total AS product_sales,
		       so.base_grand_total AS order_total,
		       so.amazon_order_total,
		       (SELECT SUM(soi.qty) FROM `tabSales Order Item` soi WHERE soi.parent = so.name) AS units
		FROM `tabSales Order` so
		WHERE {conditions}
		ORDER BY so.transaction_date DESC, so.name DESC
		LIMIT %(page_size)s OFFSET %(offset)s
		""",
		{**params, "page_size": page_size, "offset": (page_no - 1) * page_size},
		as_dict=True,
	)

	cancelled = set(ORDER_STATUS_CANCEL)
	orders = []
	for row in rows:
		orders.append(
			{
				"amazon_order_id": row.amazon_order_id,
				"sales_order": row.sales_order,
				"transaction_date": str(row.transaction_date),
				"amazon_order_status": row.amazon_order_status,
				"fulfillment_network": row.fulfillment_network,
				"amazon_marketplace": row.amazon_marketplace,
				"units": cint(row.units),
				"order_currency": row.order_currency,
				"amazon_order_total": flt(row.amazon_order_total, 2),
				"product_sales": flt(row.product_sales, 2),
				"order_total": flt(row.order_total, 2),
				# Amazon withholds pricing on a Pending order, so a draft's totals
				# are absent rather than small — the flag is what stops a caller
				# adding them up.
				"counts_as_sold": cint(row.docstatus) == 1
				and (row.amazon_order_status or "") not in cancelled,
			}
		)

	return {
		"total": cint(total),
		"page_no": page_no,
		"page_size": page_size,
		"has_more": page_no * page_size < cint(total),
		"orders": orders,
	}
