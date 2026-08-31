# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Sales API v1: Amazon's own answer to "what did this account sell".

One endpoint, `getOrderMetrics`, and it is the only figure in this app that
Amazon computed rather than we did. `sales.py` at the app root aggregates the
Sales Orders the sync wrote, which is flexible — by SKU, by ASIN, by channel —
and is only ever as complete as the sync and as correct as what the sync maps.
This is the number to check that against.

They will not agree, and the difference is worth reporting rather than
reconciling away:

- The sync maps neither shipping nor Amazon's fees (see `amazon_order_total`'s
  own description in install.py), so local figures are gross merchandise value.
- Amazon buckets by *its* day boundary in the marketplace's time zone. We bucket
  on `Sales Order.transaction_date`, which is the purchase date converted to the
  site's time zone. A sale near midnight falls in different days.
- A cancellation moves Amazon's historical figures; a cancelled Sales Order is
  excluded from ours from the moment the sync sees it, which is later.

## The interval, which is where this goes wrong

`interval` is two ISO-8601 instants joined by `--`, each carrying a UTC offset,
and `granularityTimeZone` must be a zone whose offset matches. `_interval` builds
both from the site's own time zone, so they agree by construction rather than by
a configured string someone has to keep in step.

The end instant is **midnight at the start of the day after `date_to`**, because
the interval is half-open: passing `date_to` itself as the end would silently
drop that whole day, which is the last one anybody asked about.

Building the instants through `ZoneInfo` rather than a fixed offset is what makes
a period spanning a daylight-saving change come out right — the start and end
instants get their own offsets, which is exactly what the format allows for.

## The role

`getOrderMetrics` needs the **Selling Partner Insights** role on the SP-API
application — the same one the performance reports need, which is why
`describe_forbidden` already names it. A 403 here is a role gap, not a bad
token, and comes back saying so.

Rate limit is 0.5 requests a second, burst 15; the client's own backoff covers
it and nothing here retries separately.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import cint, flt, get_system_timezone, getdate, nowdate

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	SALES_GRANULARITIES,
	SALES_MAX_INTERVAL_DAYS,
	SALES_ORDER_METRICS_PATH,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

# Amazon's own vocabulary for the parameter, and the same values the order sync
# stamps on Sales Order.amazon_fulfillment_channel.
FULFILLMENT_NETWORKS = ("AFN", "MFN")


def _interval(date_from, date_to, tz):
	"""'2026-08-01T00:00:00+05:30--2026-09-01T00:00:00+05:30' for a date range.

	Half-open: the end is midnight *after* `date_to`, so `date_to` is included
	whole. See the module docstring on why the offsets are built per instant.
	"""
	zone = ZoneInfo(tz)
	start = datetime.combine(getdate(date_from), time.min, tzinfo=zone)
	end = datetime.combine(getdate(date_to) + timedelta(days=1), time.min, tzinfo=zone)
	return f"{start.isoformat()}--{end.isoformat()}"


def _money(node):
	"""A Money object as (amount, currencyCode), tolerating an absent one.

	Amazon omits the whole object for an interval with no sales rather than
	sending a zero, so this has to answer for a missing node without inventing a
	currency for it.
	"""
	node = node or {}
	return flt(node.get("amount")), (node.get("currencyCode") or None)


def _bucket(entry):
	"""One OrderMetricsInterval as a row, with its interval split back into dates.

	The interval comes back in Amazon's half-open form, so the end instant is the
	day after the last day it covers. It is turned back into an inclusive date
	here for the same reason it was built half-open there: the dates a person
	asked about are the dates the answer should carry.
	"""
	interval = entry.get("interval") or ""
	start_text, _sep, end_text = interval.partition("--")
	total_sales, currency = _money(entry.get("totalSales"))
	avg_price, _avg_currency = _money(entry.get("averageUnitPrice"))

	period_start = period_end = None
	try:
		period_start = str(datetime.fromisoformat(start_text).date())
		period_end = str(datetime.fromisoformat(end_text).date() - timedelta(days=1))
	except ValueError:
		# An unparseable interval is Amazon changing the format, not a reason to
		# throw away the figures in it. The raw string goes back untouched.
		pass

	return {
		"period_start": period_start,
		"period_end": period_end,
		"interval": interval,
		"total_sales": total_sales,
		"units": cint(entry.get("unitCount")),
		"order_items": cint(entry.get("orderItemCount")),
		"order_count": cint(entry.get("orderCount")),
		"avg_unit_price": avg_price,
		"currency": currency,
	}


def order_metrics(
	date_from,
	date_to=None,
	granularity="day",
	fulfillment_network=None,
	asin=None,
	sku=None,
	marketplace=None,
	client=None,
):
	"""Amazon's ordered product sales, units and order counts over a period.

	One live SP-API call. Returns `{period, currency, totals, buckets}` — the same
	envelope shape as the local reads, so an answer can put the two side by side
	without restating either.
	"""
	date_from = getdate(date_from)
	date_to = getdate(date_to) if date_to else getdate(nowdate())
	if date_from > date_to:
		frappe.throw(_("date_from {0} is after date_to {1}.").format(date_from, date_to))

	granularity = (granularity or "day").strip().capitalize()
	if granularity not in SALES_GRANULARITIES:
		frappe.throw(
			_("Unknown granularity {0}. One of: {1}.").format(
				granularity, ", ".join(SALES_GRANULARITIES)
			)
		)

	# Amazon's own ceiling for every granularity this exposes. Checked here so it
	# comes back as a sentence naming the limit rather than as an SP-API 400.
	span = (date_to - date_from).days + 1
	if span > SALES_MAX_INTERVAL_DAYS:
		frappe.throw(
			_("Amazon accepts at most {0} days in one call; this period is {1}.").format(
				SALES_MAX_INTERVAL_DAYS, span
			)
		)

	asin = (asin or "").strip()
	sku = (sku or "").strip()
	if asin and sku:
		frappe.throw(_("Amazon accepts asin or sku, not both."))

	if fulfillment_network:
		fulfillment_network = str(fulfillment_network).strip().upper()
		if fulfillment_network not in FULFILLMENT_NETWORKS:
			frappe.throw(
				_("Unknown fulfillment network {0}. One of: {1}.").format(
					fulfillment_network, ", ".join(FULFILLMENT_NETWORKS)
				)
			)

	mp = _marketplace(marketplace)
	tz = get_system_timezone()
	params = {
		"marketplaceIds": mp.marketplace_id,
		"interval": _interval(date_from, date_to, tz),
		"granularity": granularity,
		"granularityTimeZone": tz,
	}
	if fulfillment_network:
		params["fulfillmentNetwork"] = fulfillment_network
	if asin:
		params["asin"] = asin
	if sku:
		params["sku"] = sku

	client = client or SpApiClient()
	try:
		response = client.get(SALES_ORDER_METRICS_PATH, params=params, context="sales")
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		raise

	buckets = [_bucket(entry) for entry in (response or {}).get("payload") or []]
	currency = next((b["currency"] for b in buckets if b["currency"]), mp.currency)

	return {
		"period": {
			"date_from": str(date_from),
			"date_to": str(date_to),
			"granularity": granularity,
			"time_zone": tz,
			"fulfillment_network": fulfillment_network,
			"asin": asin or None,
			"sku": sku or None,
			"marketplace": mp.marketplace_id,
		},
		"currency": currency,
		"totals": {
			"total_sales": flt(sum(b["total_sales"] for b in buckets), 2),
			"units": sum(b["units"] for b in buckets),
			"order_items": sum(b["order_items"] for b in buckets),
			"order_count": sum(b["order_count"] for b in buckets),
		},
		"buckets": buckets,
	}
