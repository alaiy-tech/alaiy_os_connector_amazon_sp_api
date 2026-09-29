# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""What Amazon will take out of a sale, quoted before the sale happens.

## Why an estimate is the primary number and not the fallback

There are two ways to learn an Amazon fee and they answer different questions.
The Finances API says what Amazon *actually took*, which is the truth and is
only available once the order has settled — two to four weeks after the sale
for most sellers. This module asks Amazon what it *would* take, per SKU, right
now.

A margin table built only on settled fees is therefore blank on precisely the
products a seller is deciding about this morning: a SKU launched three weeks ago
has no settled history, and the one whose price changed on Monday has settled
history describing the old price. So both are read, and every fee figure this
app reports carries a `basis` saying which it came from. Presenting an estimate
as an actual is the one failure mode here that a seller cannot detect for
themselves — see `FeeBasis` in the frontend's `lib/profitability/types.ts`,
which exists for the same reason.

## The three buckets, and the double-count Amazon invites

`getMyFeesEstimates` returns a flat `FeeDetailList` with no grouping, and the
set of names differs by marketplace and by fulfilment channel. This module
folds them into referral / FBA / other — see `FEE_TYPES_REFERRAL` and
`FEE_TYPES_FBA` in constants for which name lands where and why commission-like
charges are not filed under "other".

The trap is `IncludedFeeDetailList`. Amazon sends `FBAFees` as a roll-up with
its pick-pack and weight-handling components nested inside it, and a summing
walk that recursed would count the fulfilment fee twice — plausibly, quietly,
and only on the marketplaces that send the roll-up form. `_fold` is deliberately
one level deep for that reason and nothing here recurses.

## Fulfilment channel is an input, not something Amazon works out

A SKU fulfilled by Amazon is charged the FBA schedule; a merchant-fulfilled one
is not. Amazon quotes whichever schedule it is asked for and asking for the
wrong one does not error — it returns a confidently wrong number. So `fba` is
required per item rather than defaulted, and the caller reads it off the
listing's own fulfilment channel.

## Scope

A pure read, like `spapi.inventory.fetch_summaries` and for the same reason:
the self-serve app keys every row it stores on its own workspace and cannot
share a site-global register, so fetching lives here and storing lives there.

## Role

The Product Fees API needs the **Product Listing** role on the SP-API
application — the same one Listings needs, so a seller who can publish can
price. A 403 here is a role gap and comes back saying so.
"""

import time

import frappe
from frappe import _
from frappe.utils import flt

from alaiy_os_connector_amazon_sp_api.spapi import times
from alaiy_os_connector_amazon_sp_api.spapi.client import (
	SpApiClient,
	SpApiError,
	describe_forbidden,
)
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	FEE_TYPES_FBA,
	FEE_TYPES_REFERRAL,
	FEES_ESTIMATE_BATCH_PATH,
	FEES_ESTIMATE_BATCH_SIZE,
	FEES_ESTIMATE_MIN_INTERVAL,
	FEES_FBA_PROGRAM,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace


def _fee_amount(detail: dict) -> float:
	"""One FeeDetail as a number.

	`FinalFee` is what Amazon will actually charge: the fee less any promotion,
	plus tax on the fee. `FeeAmount` is the list price of the charge before
	either. Preferring FinalFee means a SKU inside a fee promotion reports the
	discount it is actually getting, and falling back keeps a marketplace that
	omits the field from reporting zero commission.
	"""
	for key in ("FinalFee", "FeeAmount"):
		node = detail.get(key)
		if isinstance(node, dict) and node.get("Amount") is not None:
			return flt(node.get("Amount"))
	return 0.0


def _fold(estimate: dict) -> dict:
	"""A FeesEstimate as {referral_fee, fba_fee, other_fee, total_fee, currency}.

	One level deep, never recursing into `IncludedFeeDetailList` — see the
	module docstring on the double-count that invites.

	`total_fee` is Amazon's own `TotalFeesEstimate` rather than the sum of the
	three buckets. They should agree; when they do not, Amazon's total is the
	one it will charge, and a bucketing this module got wrong should show up as
	buckets that do not add up rather than as a total nobody can check.
	"""
	referral = fba = other = 0.0
	currency = None

	for detail in estimate.get("FeeDetailList") or []:
		fee_type = (detail.get("FeeType") or "").strip()
		amount = _fee_amount(detail)
		currency = currency or ((detail.get("FinalFee") or detail.get("FeeAmount") or {}) or {}).get(
			"CurrencyCode"
		)

		if fee_type in FEE_TYPES_REFERRAL:
			referral += amount
		elif fee_type in FEE_TYPES_FBA:
			fba += amount
		else:
			other += amount

	total_node = estimate.get("TotalFeesEstimate") or {}

	return {
		"referral_fee": flt(referral, 2),
		"fba_fee": flt(fba, 2),
		"other_fee": flt(other, 2),
		"total_fee": flt(total_node.get("Amount") or (referral + fba + other), 2),
		"currency": total_node.get("CurrencyCode") or currency,
		"estimated_at": times.from_amazon_iso(estimate.get("TimeOfFeesEstimation")),
		"basis": "estimated",
	}


def _request(item: dict, marketplace_id: str, token: str) -> dict:
	"""One FeesEstimateByIdRequest.

	`Identifier` is the token this app matches the response back on. Amazon
	returns the estimate results in an array that is *not* guaranteed to be in
	request order and drops nothing on a per-item error, so matching by position
	would eventually file one SKU's fees against another's — the worst available
	outcome for a margin table, because every number in it stays plausible.
	"""
	price = flt(item.get("price"))
	currency = item.get("currency")
	if price <= 0:
		frappe.throw(
			_("A fee estimate needs a price to estimate against; {0} has none.").format(
				item.get("sku") or item.get("asin")
			)
		)

	estimate_request = {
		"MarketplaceId": marketplace_id,
		"IsAmazonFulfilled": bool(item.get("fba")),
		"Identifier": token,
		"PriceToEstimateFees": {
			"ListingPrice": {"Amount": price, "CurrencyCode": currency},
		},
	}
	# What the buyer pays for shipping is part of what Amazon takes commission
	# on for a merchant-fulfilled offer. Omitted rather than sent as zero when
	# the caller does not know it: a zero shipping charge is a claim, and on a
	# marketplace that commissions shipping it understates the referral fee.
	if item.get("shipping") is not None:
		estimate_request["PriceToEstimateFees"]["Shipping"] = {
			"Amount": flt(item.get("shipping")),
			"CurrencyCode": currency,
		}
	if item.get("fba"):
		estimate_request["OptionalFulfillmentProgram"] = FEES_FBA_PROGRAM

	id_value = item.get("sku") or item.get("asin")
	return {
		"FeesEstimateRequest": estimate_request,
		"IdType": "SellerSKU" if item.get("sku") else "ASIN",
		"IdValue": id_value,
	}


def _results(payload) -> list:
	"""The estimate results out of one response, whichever envelope it arrived in.

	Amazon's gateway has shipped this endpoint both wrapped in `payload` and
	unwrapped. Checking both costs two dict lookups and saves a batch silently
	reporting no fees at all.
	"""
	payload = payload or {}
	for node in (payload.get("payload"), payload):
		if isinstance(node, dict) and node.get("FeesEstimateResultList") is not None:
			return node.get("FeesEstimateResultList") or []
	return []


def _batches(items: list) -> list:
	return [items[i : i + FEES_ESTIMATE_BATCH_SIZE] for i in range(0, len(items), FEES_ESTIMATE_BATCH_SIZE)]


def estimate_fees(items, marketplace=None, client=None, connection=None) -> dict:
	"""Fee estimates for a list of SKUs, keyed by the id each was asked for by.

	`items` is a list of dicts, each carrying:

		sku or asin   which identifier to quote against
		price         the listing price fees are estimated on. Required —
					  Amazon's fees are a function of it, and there is no
					  default that is not a guess.
		currency      that price's currency
		fba           True for an Amazon-fulfilled SKU. See the module
					  docstring: this cannot be inferred and must not be
					  defaulted.
		shipping      what the buyer pays for shipping, when known

	Returns `{id_value: {referral_fee, fba_fee, other_fee, total_fee, currency,
	basis, estimated_at}}` for every item Amazon quoted, and
	`{id_value: {"error": ...}}` for one it refused. A per-item error is
	reported rather than raised: a suppressed SKU in a page of forty is Amazon
	declining to quote one product, not a reason to leave the other
	thirty-nine's margins blank.
	"""
	items = [i for i in (items or []) if i.get("sku") or i.get("asin")]
	if not items:
		return {}

	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)
	out: dict = {}

	for index, batch in enumerate(_batches(items)):
		# Paced rather than left to the client's 429-retry: at 0.5 requests a
		# second with a burst of 1, a catalogue of any size spends most of a
		# sync backing off otherwise. See FEES_ESTIMATE_MIN_INTERVAL.
		if index:
			time.sleep(FEES_ESTIMATE_MIN_INTERVAL)

		# The token is positional *within this batch* and carries the id, so a
		# response can be matched back even on a marketplace that echoes the
		# identifier and not the id value.
		tokens = {
			f"{position}-{item.get('sku') or item.get('asin')}": item for position, item in enumerate(batch)
		}
		body = {
			"FeesEstimateByIdRequestList": [
				_request(item, mp.marketplace_id, token) for token, item in tokens.items()
			]
		}

		try:
			response = client.post(FEES_ESTIMATE_BATCH_PATH, body=body, context="fees")
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			raise

		for result in _results(response):
			identifier = result.get("FeesEstimateIdentifier") or {}
			token = identifier.get("Identifier")
			item = tokens.get(token)
			# Fall back to the id value Amazon echoed when the identifier is
			# absent — one of the two always is present, and a result matched
			# by neither is dropped rather than guessed at.
			id_value = (item or {}).get("sku") or (item or {}).get("asin") or identifier.get("IdValue")
			if not id_value:
				continue

			if (result.get("Status") or "").strip() == "Success" and result.get("FeesEstimate"):
				out[id_value] = _fold(result["FeesEstimate"])
			else:
				error = result.get("Error") or {}
				out[id_value] = {
					"error": error.get("Message") or error.get("Code") or "Amazon declined to quote fees",
				}

	return out
