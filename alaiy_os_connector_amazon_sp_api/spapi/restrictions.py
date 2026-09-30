# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Whether a seller may list against an existing ASIN — the Listings Restrictions API.

Nothing else in this connector asks Amazon this question: `search_catalog` finds
an ASIN, `create_listing` publishes an offer against one, but neither says
whether *this* seller is approved to sell it first. A gated ASIN accepted by
`create_listing` fails at Amazon's own submission step instead, which is a
worse place to find out — the caller has already committed to the SKU.

One restriction check per ASIN; Amazon does not batch this endpoint the way
catalog search does.
"""

import frappe

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError
from alaiy_os_connector_amazon_sp_api.spapi.constants import LISTINGS_RESTRICTIONS_PATH


def check_eligibility(asin, marketplace_id, seller_id, client=None, condition_type="new_new"):
	"""{asin, eligible, reasons} for one ASIN.

	Amazon answers with an empty `restrictions` array when the seller may list
	freely, and a populated one — each entry carrying its own `reasons` — when
	they may not. `eligible` is None (not True) when the call itself fails, so a
	transient SP-API error never reads as "you can sell this."
	"""
	client = client or SpApiClient()
	params = {
		"asin": asin,
		"sellerId": seller_id,
		"marketplaceIds": marketplace_id,
		"conditionType": condition_type,
	}
	try:
		resp = client.get(LISTINGS_RESTRICTIONS_PATH, params=params, context="listing")
	except SpApiError as e:
		frappe.log_error(title="Amazon listing-restrictions check failed", message=f"{asin}: {e}")
		return {"asin": asin, "eligible": None, "reasons": [f"Could not check eligibility: {e.message}"]}

	restrictions = resp.get("restrictions") or []
	reasons = [
		reason.get("message")
		for r in restrictions
		for reason in (r.get("reasons") or [])
		if reason.get("message")
	]
	return {"asin": asin, "eligible": not restrictions, "reasons": reasons}
