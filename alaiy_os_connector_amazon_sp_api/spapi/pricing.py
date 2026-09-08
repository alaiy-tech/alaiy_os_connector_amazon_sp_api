# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""The Buy Box: who holds it, at what price, and how much of the day we held it.

## Two questions, two entirely different sources

This is the fact most often got wrong about Buy Box data on SP-API, and getting
it wrong produces a number that looks right:

  * **Who holds the Buy Box right now, and at what price** — `getCompetitiveSummary`
	(Product Pricing 2022-05-01). A live read, accurate to the second, and it
	says nothing about history.
  * **What share of the day we held it** — the *Sales & Traffic business report*,
	per ASIN, as `buyBoxPercentage`. There is no pricing endpoint that answers
	this. None.

A seller who says "my Buy Box win rate is 68%" means the second. Deriving it
from the first — sampling `getCompetitiveSummary` and counting how often we came
back as the featured offer — would produce a number that moves with this app's
polling cadence rather than with the seller's business, and there would be
nothing on the screen to reveal it. So the two live in one module and are never
computed from each other: `competitive_summary` is the price read,
`buy_box_share` is the report read, and each says which it is.

## Which offer is the Buy Box

`featuredBuyingOptions` is an array and the Buy Box is the entry whose
`buyingOptionType` is New. Amazon puts used-condition and subscribe-and-save
featured offers in the same array, so taking the first entry compares a
new-condition listing against a used offer — cheaper, not competing, and it
makes a healthy price look beaten.

## Whether *we* hold it

Every featured offer carries the `sellerId` that owns it, and the connection
carries our own `selling_partner_id`. That comparison is the whole of `is_ours`
below. Without it "buy box price" is ambiguous in the one case that matters: a
seller who *holds* the Buy Box sees their own price as the competitor's, reads a
zero gap, and concludes there is nothing to do — which happens to be true, but
for a reason the screen never told them.

## Why v2022-05-01 rather than v0

v0's `getItemOffers` still works and is not deprecated. 2022-05-01 answers for
twenty ASINs in one call where v0 answers for one, and it is where Amazon's
development has gone — building against v0 now buys a rewrite later. The
per-ASIN offer *list* v0 returns is richer, and nothing in this app needs it.

## Role

Both reads need the **Pricing** role on the SP-API application, and the report
additionally needs **Selling Partner Insights** — a different role from the one
Listings and Fees use, so a seller can be perfectly connected and 403 here.
The caller treats that as losing the Buy Box column, not as a broken
connection.
"""

import json

import frappe
from frappe.utils import add_days, flt, getdate

from alaiy_os_connector_amazon_sp_api import connections
from alaiy_os_connector_amazon_sp_api.spapi import reports
from alaiy_os_connector_amazon_sp_api.spapi.client import (
	SpApiClient,
	SpApiError,
	describe_forbidden,
)
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	COMPETITIVE_SUMMARY_BATCH_SIZE,
	COMPETITIVE_SUMMARY_INCLUDED_DATA,
	COMPETITIVE_SUMMARY_PATH,
	FEATURED_OFFER_BUYING_OPTION,
	REPORT_SALES_AND_TRAFFIC,
	SALES_AND_TRAFFIC_GRANULARITY,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

# The individual request's own uri, echoed inside each batch entry. Amazon's
# batch endpoints are a envelope around the single-item call, and this is that
# call's path.
_SUMMARY_URI = "/products/pricing/2022-05-01/items/competitiveSummary"


def _price(node) -> tuple[float | None, str | None]:
	"""A price object as (amount, currency), tolerating an absent one.

	None rather than 0.0 for a missing amount: a Buy Box with no price is
	Amazon telling us there is no featured offer, and zero is a price.
	"""
	node = node or {}
	amount = node.get("amount")
	return (flt(amount) if amount is not None else None), node.get("currencyCode")


def _shipping_total(offer: dict) -> float:
	"""What the buyer pays to have this offer shipped.

	Part of the comparison rather than a detail: an offer priced ₹40 below with
	₹80 of shipping is not the cheaper one, and a Buy Box gap computed on
	listing price alone would tell a seller to cut ₹45 they did not need to.
	"""
	total = 0.0
	for option in offer.get("shippingOptions") or []:
		amount, _currency = _price(option.get("price"))
		total += amount or 0.0
	return flt(total, 2)


def _featured_offer(body: dict) -> dict | None:
	"""The New-condition featured offer, or None when nobody holds the Buy Box.

	See the module docstring: the array carries used and subscribe-and-save
	featured offers too, and the first entry is not reliably the one being
	competed for.
	"""
	for option in body.get("featuredBuyingOptions") or []:
		if (option.get("buyingOptionType") or "").strip() != FEATURED_OFFER_BUYING_OPTION:
			continue
		offers = option.get("segmentedFeaturedOffers") or []
		if offers:
			# Amazon segments the featured offer by customer membership (Prime
			# vs. not) and can return several. They differ in *audience*, not in
			# price, so the first is the Buy Box price; a caller needing the
			# segmentation has `raw`.
			return offers[0]
	return None


def _reference_prices(body: dict) -> dict:
	"""Amazon's own reference prices, keyed by its names for them."""
	out = {}
	for reference in body.get("referencePrices") or []:
		name = (reference.get("name") or "").strip()
		amount, _currency = _price(reference.get("price"))
		if name and amount is not None:
			out[name] = amount
	return out


def _summary_row(body: dict, seller_id: str | None) -> dict:
	"""One competitiveSummary body, flattened to what a pricing decision needs."""
	offer = _featured_offer(body) or {}
	amount, currency = _price(offer.get("listingPrice"))
	offer_seller = (offer.get("sellerId") or "").strip()

	return {
		"asin": body.get("asin"),
		"buy_box_price": amount,
		"buy_box_shipping": _shipping_total(offer) if offer else None,
		"currency": currency,
		"buy_box_seller_id": offer_seller or None,
		# See the module docstring. None when nobody holds the Buy Box, which is
		# a different fact from "someone else holds it".
		"is_ours": (offer_seller == (seller_id or "") if offer_seller and seller_id else None),
		"fulfillment_type": offer.get("fulfillmentType"),
		"reference_prices": _reference_prices(body),
		"raw": body,
	}


def _batches(values: list) -> list:
	return [
		values[i : i + COMPETITIVE_SUMMARY_BATCH_SIZE]
		for i in range(0, len(values), COMPETITIVE_SUMMARY_BATCH_SIZE)
	]


def competitive_summary(asins, marketplace=None, client=None, connection=None) -> dict:
	"""Live Buy Box price and holder per ASIN, keyed by ASIN.

	A pure read. Returns `{asin: {buy_box_price, buy_box_shipping, currency,
	buy_box_seller_id, is_ours, fulfillment_type, reference_prices, raw}}`,
	omitting an ASIN Amazon answered with a non-200 — a batch is twenty
	independent questions and one refusal is not an answer for the other
	nineteen.
	"""
	asins = [a for a in dict.fromkeys(a.strip() for a in (asins or []) if a and a.strip())]
	if not asins:
		return {}

	conn = connections.resolve(connection)
	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(conn)
	seller_id = (conn.selling_partner_id or "").strip() or None
	out: dict = {}

	for batch in _batches(asins):
		body = {
			"requests": [
				{
					"uri": _SUMMARY_URI,
					"method": "GET",
					"asin": asin,
					"marketplaceId": mp.marketplace_id,
					"includedData": list(COMPETITIVE_SUMMARY_INCLUDED_DATA),
				}
				for asin in batch
			]
		}

		try:
			response = client.post(COMPETITIVE_SUMMARY_PATH, body=body, context="pricing")
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			raise

		for entry in (response or {}).get("responses") or []:
			status = (entry.get("status") or {}).get("statusCode")
			entry_body = entry.get("body") or {}
			asin = entry_body.get("asin")
			if not asin or status != 200:
				continue
			out[asin] = _summary_row(entry_body, seller_id)

	return out


# ---------------------------------------------------------------------------
# Buy Box share — the report, not the API. See the module docstring.
# ---------------------------------------------------------------------------


def _traffic_rows(text: str) -> list:
	"""The per-ASIN section of a Sales & Traffic report.

	The report carries two independent sections — one by date, one by ASIN — and
	only the second has `buyBoxPercentage`. The by-date section carries a
	`buyBoxPercentage` too, but it is the account's, across every product, which
	is not the number a per-SKU margin row is asking for.
	"""
	if not text:
		return []
	try:
		parsed = json.loads(text)
	except ValueError:
		# A report Amazon delivered as something other than JSON is a format
		# change, not a reason to fail the sync that asked for it — the caller
		# loses the Buy Box column and keeps its fees.
		frappe.log_error(title="Amazon Sales & Traffic report was not JSON")
		return []
	return parsed.get("salesAndTrafficByAsin") or []


def buy_box_share(
	date_from,
	date_to=None,
	marketplace=None,
	client=None,
	connection=None,
) -> dict:
	"""Buy Box percentage per ASIN over a window, keyed by ASIN.

	**The window is the grain.** Amazon aggregates the per-ASIN section over the
	whole reporting period rather than by day, whatever `dateGranularity` says —
	that parameter shapes the *by-date* section, which has no per-ASIN detail. So
	this returns one figure per ASIN for the period asked about, and a caller
	wanting "this week vs. last week" asks twice with two windows rather than
	slicing one answer.

	Returns `{asin: {buy_box_pct, units, sessions, page_views, date_from,
	date_to}}`. Empty when the report came back cancelled, which Amazon uses for
	"no data in this window" — a new seller's first week, not a failure.
	"""
	date_from = getdate(date_from)
	date_to = getdate(date_to) if date_to else getdate(add_days(date_from, 6))

	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)

	try:
		text = reports.fetch_report(
			REPORT_SALES_AND_TRAFFIC,
			[mp.marketplace_id],
			data_start=f"{date_from}T00:00:00Z",
			data_end=f"{date_to}T23:59:59Z",
			report_options={
				# CHILD is the SKU-level grain. PARENT would roll every size and
				# colour of a product into one Buy Box figure, which cannot be
				# attributed back to the SKU whose price a seller would change.
				"asinGranularity": "CHILD",
				"dateGranularity": SALES_AND_TRAFFIC_GRANULARITY,
			},
			client=client,
			context="pricing",
		)
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		raise

	out = {}
	for row in _traffic_rows(text):
		asin = row.get("childAsin") or row.get("parentAsin")
		if not asin:
			continue
		traffic = row.get("trafficByAsin") or {}
		sales = row.get("salesByAsin") or {}
		buy_box = traffic.get("buyBoxPercentage")
		out[asin] = {
			# Absent is unknown, not zero: a product with no sessions in the
			# window has no Buy Box percentage, and 0% reads as "never won".
			"buy_box_pct": flt(buy_box) if buy_box is not None else None,
			"units": sales.get("unitsOrdered"),
			"sessions": traffic.get("sessions"),
			"page_views": traffic.get("pageViews"),
			"date_from": str(date_from),
			"date_to": str(date_to),
		}
	return out
