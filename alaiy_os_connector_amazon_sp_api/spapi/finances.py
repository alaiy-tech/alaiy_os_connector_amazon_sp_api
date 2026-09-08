# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""What Amazon actually took, once the money moved.

## The one number in the margin table nobody has to caveat

`spapi.fees` asks Amazon what it *would* charge. This module reads what it
*did*: the settled ledger, per order, per SKU. Where a settled figure exists it
wins over an estimate every time, and this is the only fee source in the app
that needs no hedging — it is not a model of Amazon's pricing, it is Amazon's
own accounting.

Its limitation is the timing, and it is severe. A sale settles two to four weeks
after it happens, so at any moment the most recent weeks of trading have no
settled fees at all. That is why the estimate exists and why both are read; see
the module docstring on `spapi.fees`.

## Why v2024-06-19 and not the v0 this app already calls

`spapi.health` calls `/finances/v0/financialEvents` today, for exactly two
counters: A-to-Z claims and chargebacks. That endpoint returns roughly twenty
parallel arrays, one per event type, each with its own shape — reading fees out
of it means handling `ShipmentEventList`, `RefundEventList`,
`ServiceFeeEventList` and the rest separately, and any type not handled is
money that silently is not counted.

`listTransactions` is Amazon's consolidation of all of them into one shape.
So this module reads the new endpoint and `spapi.health` keeps its v0 call
untouched: those two counters work, v0 has no announced deprecation, and
rewriting a working call to a working endpoint is churn. When v0 does get a
date, the counters move here and this docstring is where to start.

## Refunds are not netted, and that is deliberate

Amazon files a refund's fee reversal as its own transaction with a positive
amount. A fee total that swept up every transaction type would net a returned
order's refunded commission against a charged one, and report a lower cost of
selling than the seller actually paid — while every individual figure in it
stayed defensible. `TRANSACTION_TYPES_SALE` is the filter that keeps this to
fees charged on sales, and a caller wanting the refund side asks for it by name.

## Signs

Amazon signs money leaving the seller negative. Every figure this module returns
is a positive magnitude, because "referral fee: -₹42" reads as a credit at a
glance and every consumer of this data subtracts it. The sign convention is
stated once, here and in `_magnitude`, rather than at each of the places that
would otherwise have to guess.

## Role

Needs the **Finance and Accounting** role on the SP-API application — a
different one again from Listings, Fees and Pricing, and one many sellers have
not granted. A 403 costs the actuals and leaves the estimates, which is a
degraded margin table rather than an empty one.
"""

import frappe
from frappe.utils import flt

from alaiy_os_connector_amazon_sp_api.spapi.client import (
	SpApiClient,
	SpApiError,
	describe_forbidden,
)
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	SETTLED_FEE_FBA_PREFIX,
	SETTLED_FEE_REFERRAL_NAMES,
	TRANSACTION_FEES_BREAKDOWN,
	TRANSACTION_PRODUCT_CONTEXT,
	TRANSACTION_TYPES_SALE,
	TRANSACTIONS_MAX_PAGES,
	TRANSACTIONS_PATH,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace


def _magnitude(node) -> float:
	"""A currencyAmount as a positive number. See the module docstring on signs."""
	node = node or {}
	return abs(flt(node.get("currencyAmount")))


def _currency(node) -> str | None:
	return (node or {}).get("currencyCode")


def _product_context(item: dict) -> dict:
	"""The SKU, ASIN and quantity a transaction item is about.

	Amazon attaches several context types to one item (product, marketplace,
	payments) and only the product one carries a SKU. An item with none cannot
	be attributed to a product at all — see `_fee_rows`, which counts those
	separately rather than dropping them, because unattributed fees are real
	money and a margin table that quietly loses them is wrong by exactly that
	amount.
	"""
	for context in item.get("contexts") or []:
		if (context.get("contextType") or "").strip() == TRANSACTION_PRODUCT_CONTEXT:
			return context
	return {}


def _bucket_for(name: str) -> str:
	"""Which of the three buckets a settled fee name belongs in.

	The FBA test is a prefix and the referral test is a list; see
	`SETTLED_FEE_FBA_PREFIX` in constants for why those differ.
	"""
	name = (name or "").strip()
	if name in SETTLED_FEE_REFERRAL_NAMES:
		return "referral_fee"
	if name.upper().startswith(SETTLED_FEE_FBA_PREFIX):
		return "fba_fee"
	return "other_fee"


def _fees_from(item: dict) -> dict:
	"""The fee breakdown of one transaction item, folded into three buckets.

	Amazon nests the individual charges one level under a `Fees` breakdown. Only
	that branch is read: `Sales`, `Promotion` and `Tax` are the same money
	described from other angles, and adding them to a fee total would charge the
	seller for their own revenue.
	"""
	out = {"referral_fee": 0.0, "fba_fee": 0.0, "other_fee": 0.0, "currency": None}

	for breakdown in item.get("breakdowns") or []:
		if (breakdown.get("breakdownType") or "").strip() != TRANSACTION_FEES_BREAKDOWN:
			continue
		for fee in breakdown.get("breakdowns") or []:
			amount = _magnitude(fee.get("breakdownAmount"))
			if not amount:
				continue
			out[_bucket_for(fee.get("breakdownType"))] += amount
			out["currency"] = out["currency"] or _currency(fee.get("breakdownAmount"))

	return out


def _order_id(transaction: dict) -> str | None:
	for identifier in transaction.get("relatedIdentifiers") or []:
		if (identifier.get("relatedIdentifierName") or "").strip() == "ORDER_ID":
			return identifier.get("relatedIdentifierValue")
	return None


def list_transactions(
	posted_after,
	posted_before=None,
	marketplace=None,
	client=None,
	connection=None,
):
	"""Every settled transaction in a window, as Amazon returns them.

	A thin page-following read that does no folding of its own — `settled_fees`
	is the aggregate most callers want, and this is here for one that needs the
	ledger. `posted_after` and `posted_before` are ISO-8601 instants.
	"""
	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)

	params = {"postedAfter": posted_after, "marketplaceId": mp.marketplace_id}
	if posted_before:
		params["postedBefore"] = posted_before

	transactions = []
	for _page in range(TRANSACTIONS_MAX_PAGES):
		try:
			response = client.get(TRANSACTIONS_PATH, params=params, context="finances")
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			raise

		payload = (response or {}).get("payload") or response or {}
		transactions.extend(payload.get("transactions") or [])

		next_token = payload.get("nextToken")
		if not next_token:
			break
		# Amazon rejects a paged call that also re-sends the filters.
		params = {"nextToken": next_token}

	return transactions


def settled_fees(
	posted_after,
	posted_before=None,
	marketplace=None,
	client=None,
	connection=None,
) -> dict:
	"""Settled fees per SKU over a window, with the orders behind each.

	Returns:

		{
		  "by_sku": {sku: {referral_fee, fba_fee, other_fee, total_fee, units,
						   orders, currency, basis: "actual"}},
		  "unattributed": {referral_fee, fba_fee, other_fee, total_fee},
		  "currency": ...,
		  "transactions": <how many sale transactions were read>,
		}

	`unattributed` is fees Amazon charged on a transaction item carrying no
	product context — order-level charges, and items whose SKU Amazon did not
	echo. It is reported rather than dropped: a per-SKU table that silently
	omits real money understates the cost of selling, and a caller can at least
	say "plus ₹x not attributable to a SKU" when it is handed the figure.
	"""
	transactions = list_transactions(
		posted_after,
		posted_before=posted_before,
		marketplace=marketplace,
		client=client,
		connection=connection,
	)

	by_sku: dict = {}
	unattributed = {"referral_fee": 0.0, "fba_fee": 0.0, "other_fee": 0.0}
	currency = None
	counted = 0

	for transaction in transactions:
		if (transaction.get("transactionType") or "").strip() not in TRANSACTION_TYPES_SALE:
			continue
		counted += 1
		order_id = _order_id(transaction)
		currency = currency or _currency(transaction.get("totalAmount"))

		for item in transaction.get("items") or []:
			fees = _fees_from(item)
			currency = currency or fees["currency"]
			context = _product_context(item)
			sku = (context.get("sku") or "").strip()

			if not sku:
				for bucket in ("referral_fee", "fba_fee", "other_fee"):
					unattributed[bucket] += fees[bucket]
				continue

			row = by_sku.setdefault(
				sku,
				{
					"sku": sku,
					"asin": context.get("asin"),
					"referral_fee": 0.0,
					"fba_fee": 0.0,
					"other_fee": 0.0,
					"units": 0,
					"orders": set(),
					"currency": fees["currency"] or currency,
					"basis": "actual",
					"fulfillment_network": context.get("fulfillmentNetwork"),
				},
			)
			for bucket in ("referral_fee", "fba_fee", "other_fee"):
				row[bucket] += fees[bucket]
			row["units"] += int(flt(context.get("quantityShipped")) or 0)
			if order_id:
				row["orders"].add(order_id)

	for row in by_sku.values():
		# A set while accumulating so an order appearing in two transactions —
		# a partial shipment, then the rest — counts once; a list on the way out
		# because a set is not JSON.
		row["orders"] = sorted(row["orders"])
		for bucket in ("referral_fee", "fba_fee", "other_fee"):
			row[bucket] = flt(row[bucket], 2)
		row["total_fee"] = flt(row["referral_fee"] + row["fba_fee"] + row["other_fee"], 2)

	unattributed = {k: flt(v, 2) for k, v in unattributed.items()}
	unattributed["total_fee"] = flt(sum(unattributed.values()), 2)

	return {
		"by_sku": by_sku,
		"unattributed": unattributed,
		"currency": currency,
		"transactions": counted,
		"window": {"posted_after": str(posted_after), "posted_before": str(posted_before or "")},
	}
