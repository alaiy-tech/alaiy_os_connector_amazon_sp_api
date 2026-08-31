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

`get_definition` answers the follow-on question, for the same never-listed
product: having settled on a product type, which attributes does Amazon require
before it will mint an ASIN under it? That is `getDefinitionsProductType` with
requirements=LISTING, and it is what separates creating a catalog entry from
publishing an offer against someone else's.
"""

from urllib.parse import quote

import frappe
import requests
from frappe import _

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	PRODUCT_TYPE_DEFINITION_REQUIREMENTS,
	PRODUCT_TYPE_DEFINITIONS_PATH,
	PRODUCT_TYPE_SCHEMA_CACHE_TTL,
	PRODUCT_TYPE_SCHEMA_TIMEOUT,
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
		if covered is not None and marketplace_id not in covered:
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
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Enter a product title to look up its product type."))

	# Local import: listings imports nothing from here, but keeping the
	# marketplace resolver in one place is worth the deferred import.
	from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace
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

	limit = PRODUCT_TYPE_SUGGESTION_LIMIT if limit is None else limit
	return suggestions_from_response(resp, mp.marketplace_id)[:limit]


def _definition_cache_key(product_type, marketplace_id):
	return f"amazon_sp_api:product_type_schema:{marketplace_id}:{product_type}"


def get_definition(product_type, marketplace=None, client=None, requirements=None):
	"""The JSON Schema a product type's attributes must satisfy, for a create.

	Two calls, not one. `getDefinitionsProductType` answers with metadata and a
	*link* to the schema — a presigned URL on Amazon's own storage — so the schema
	itself is a second, unauthenticated GET. Sending the SP-API access token there
	would be both useless and a credential handed to a URL we did not construct.

	`requirements=LISTING` is the point of the call: it is the full attribute set a
	product needs to exist in the catalog, as opposed to the LISTING_OFFER_ONLY set
	an offer against an existing ASIN meets. That difference is exactly why
	publishing an offer never needed this and creating an ASIN cannot do without it.

	Cached per product type + marketplace; see PRODUCT_TYPE_SCHEMA_CACHE_TTL.
	Returns the parsed schema dict, or None when Amazon has no definition for this
	product type here — a real answer (the product type does not apply to this
	marketplace), not an error.
	"""
	product_type = (product_type or "").strip()
	if not product_type:
		frappe.throw(_("A product type is required to read its attribute schema."))

	from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

	mp = _marketplace(marketplace)
	cache_key = _definition_cache_key(product_type, mp.marketplace_id)
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached or None

	client = client or SpApiClient()
	params = {
		"marketplaceIds": mp.marketplace_id,
		"requirements": requirements or PRODUCT_TYPE_DEFINITION_REQUIREMENTS,
	}
	try:
		definition = client.get(
			f"{PRODUCT_TYPE_DEFINITIONS_PATH}/{quote(product_type, safe='')}",
			params=params,
			context="listing",
		)
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		if e.status_code == 404:
			# Not an error: this product type has no definition in this
			# marketplace, so nothing can be created under it here.
			frappe.cache().set_value(cache_key, {}, expires_in_sec=PRODUCT_TYPE_SCHEMA_CACHE_TTL)
			return None
		raise

	schema = _fetch_schema(definition)
	if schema is None:
		return None
	frappe.cache().set_value(cache_key, schema, expires_in_sec=PRODUCT_TYPE_SCHEMA_CACHE_TTL)
	return schema


def _fetch_schema(definition):
	"""Follow the definition's `schema.link.resource` and parse what it returns."""
	link = ((definition or {}).get("schema") or {}).get("link") or {}
	resource = link.get("resource")
	if not resource:
		return None
	try:
		resp = requests.get(resource, timeout=PRODUCT_TYPE_SCHEMA_TIMEOUT)
		resp.raise_for_status()
		return resp.json()
	except (requests.RequestException, ValueError) as e:
		frappe.throw(
			_("Could not read the product type schema Amazon linked to: {0}").format(e),
			title=_("Product type schema unavailable"),
		)


def required_attributes(schema):
	"""The attribute names a create must supply, from a product type schema.

	Only the top level. Amazon's schemas nest `required` deep inside each
	attribute's own object shape, but those inner ones describe how an attribute
	we *are* sending must be formed — a shape the attribute builders already
	produce — whereas the top-level list is the question being asked here: which
	attributes have to be present at all.
	"""
	return [name for name in (schema or {}).get("required") or [] if isinstance(name, str)]


def attribute_title(schema, name):
	"""Amazon's human label for an attribute, for a message an operator reads.

	Falls back to the raw attribute name: a blocker that says `supplier_declared_dg_hz_regulation`
	is still more use than one that says an attribute is missing without saying which.
	"""
	prop = ((schema or {}).get("properties") or {}).get(name) or {}
	return prop.get("title") or name


def attribute_options(schema, name, limit=8):
	"""The values a required attribute accepts, when it is an enumerated one.

	Returns (shown, total). Amazon puts the enum on the `value` property inside
	the attribute's item shape, and for attributes like country_of_origin it runs
	to hundreds — hence the cap and the total, so a caller can say "and N more"
	rather than either truncating silently or pasting a wall of country codes.
	"""
	prop = ((schema or {}).get("properties") or {}).get(name) or {}
	spec = prop.get("items") if prop.get("type") == "array" else prop
	value = ((spec or {}).get("properties") or {}).get("value") or {}
	enum = [v for v in (value.get("enum") or []) if isinstance(v, str)]
	return enum[:limit], len(enum)


def attribute_example(schema, name, marketplace_id):
	"""A paste-ready value for one attribute, shaped the way its schema says.

	The point is to remove a guess that has no business being one. Amazon's
	attribute values are nested, marketplace-scoped and inconsistent between
	attributes — `[{"marketplace_id": ..., "value": ...}]` for most, something
	else for the rest — and an operator told only that an attribute is missing has
	to go and read a JSON Schema to find out what to type. Generating the shape
	from that schema is strictly better than describing it in a docstring nobody
	reads at the moment they need it.

	The values in it are placeholders: the first enum member where there is one, a
	blank otherwise. It is a form to fill in, not an answer.
	"""
	prop = ((schema or {}).get("properties") or {}).get(name) or {}
	if prop.get("type") == "array":
		return [_example_object(prop.get("items") or {}, marketplace_id)]
	return _example_object(prop, marketplace_id)


def _example_object(spec, marketplace_id):
	"""One object in an attribute's value, filled with placeholders."""
	properties = (spec or {}).get("properties") or {}
	# Required keys only where the schema says which; otherwise every key, because
	# an example missing the one key that mattered is worse than a longer one.
	keys = [k for k in ((spec or {}).get("required") or list(properties)) if k in properties]
	out = {}
	for key in keys:
		sub = properties.get(key) or {}
		if key == "marketplace_id":
			out[key] = marketplace_id
			continue
		enum = [v for v in (sub.get("enum") or []) if isinstance(v, str)]
		if enum:
			out[key] = enum[0]
		elif sub.get("type") in ("number", "integer"):
			out[key] = 0
		elif sub.get("type") == "boolean":
			out[key] = True
		elif sub.get("type") == "array":
			out[key] = [_example_object(sub.get("items") or {}, marketplace_id)]
		elif sub.get("type") == "object":
			out[key] = _example_object(sub, marketplace_id)
		else:
			out[key] = ""
	return out
