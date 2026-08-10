# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Product type for a product title, from the Product Type Definitions API (2020-09-01).

Why this is not `listings.search_catalog`: catalog search answers "which
existing ASIN is this?", and the product types it reports are simply whatever
was attached to the ASINs that matched. A product Amazon has never listed
matches no ASIN, so catalog search returns nothing for it — and that is exactly
the case where the product type is most needed, because every write through the
Listings API has to declare one.

searchDefinitionsProductTypes answers the other question: given a title, which
of Amazon's product types does this product belong to? It classifies against the
product-type registry rather than the catalog, so a title alone is enough.

Read-only, and it writes nothing to the register — picking one of the
suggestions and storing it on Amazon Product Listing.product_type is the
caller's decision, not this module's.
"""

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	PRODUCT_TYPE_DEFINITIONS_PATH,
	PRODUCT_TYPE_SUGGESTION_LIMIT,
)


def suggestions_from_response(resp, marketplace_id):
	"""Normalise a ProductTypeList payload into [{product_type, display_name}].

	Amazon returns the list best-match-first with no score attached, so order is
	the only signal of confidence there is and must be preserved.

	Entries are marketplace-scoped even though the request named one marketplace:
	the parameter is `marketplaceIds`, plural, and an entry lists every
	marketplace its definition covers. Anything that does not cover ours is
	dropped rather than shown — a product type without a definition here cannot
	be used to publish here.
	"""
	out = []
	for entry in (resp or {}).get("productTypes") or []:
		name = entry.get("name")
		if not name:
			continue
		covered = entry.get("marketplaceIds")
		if covered and marketplace_id not in covered:
			continue
		out.append({"product_type": name, "display_name": entry.get("displayName") or name})
	return out


def suggest_product_types(title, marketplace=None, client=None, limit=None):
	"""Amazon's product types for a product title, best match first.

	Returns [{product_type, display_name}] — a list, not a single answer, because
	a title is genuinely ambiguous ("Apple case" is a phone accessory or fruit
	storage) and only the operator can settle it. An empty list means Amazon
	recognised nothing, which is a real answer and not an error.
	"""
	# Local import: listings imports nothing from here, but keeping the
	# marketplace resolver in one place is worth the deferred import.
	from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

	title = (title or "").strip()
	if not title:
		frappe.throw(_("Enter a product title to look up its product type."))

	mp = _marketplace(marketplace)
	client = client or SpApiClient()

	# `itemName` and `keywords` are mutually exclusive, and itemName is the one
	# meant for a whole title. `locale`/`searchLocale` are deliberately omitted:
	# both default to the marketplace's primary locale, which is what we would
	# send anyway, and sending a locale Amazon does not support for a marketplace
	# is a 400 we have nothing to gain from.
	params = {"marketplaceIds": mp.marketplace_id, "itemName": title}

	try:
		resp = client.get(PRODUCT_TYPE_DEFINITIONS_PATH, params=params, context="listing")
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		raise

	limit = limit or PRODUCT_TYPE_SUGGESTION_LIMIT
	return suggestions_from_response(resp, mp.marketplace_id)[:limit]
