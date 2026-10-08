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

`definition_problems` checks a payload against that definition before anything
is sent: the whole of it, not only its top-level `required` list. Most of what a
product type requires is conditional — on the marketplace, on another attribute
being present — and Amazon's rejection of an incomplete payload is the slow,
public way to find that out.
"""

import json
from urllib.parse import quote

import frappe
import requests
from frappe import _
from jsonschema import Draft201909Validator, validators
from jsonschema.exceptions import ValidationError, best_match

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


def attribute_value(schema, name, value, marketplace_id, language=None):
	"""`value` as the attribute `name` takes it under this definition, or None.

	For a caller that knows WHAT to declare — a manufacturer, a warranty period —
	but not how a product type spells it. Attributes are not one shape: one requires
	`language_tag` and another refuses it, a warranty period is `{value, unit}`, an
	HSN code is `{entity, value}`. Read off the definition, the shape stays right as
	Amazon changes it.

	`value` is a plain value (wrapped as `{"value": value}`) or a dict of the item's
	own keys. `marketplace_id` and `language_tag` are added where the item has them
	and the caller did not set them. None when the product type has no such
	attribute: declaring it anyway would be refused.
	"""
	prop = ((schema or {}).get("properties") or {}).get(name)
	if not prop:
		return None
	spec = prop.get("items") if prop.get("type") == "array" else prop
	keys = (spec or {}).get("properties") or {}
	entry = dict(value) if isinstance(value, dict) else {"value": value}
	if "marketplace_id" in keys:
		entry.setdefault("marketplace_id", marketplace_id)
	if "language_tag" in keys and language:
		entry.setdefault("language_tag", language)
	return [entry] if prop.get("type") == "array" else entry


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


# --- checking a payload against the definition ---------------------------------
# Amazon's definitions are JSON Schema Draft 2019-09 plus a vocabulary of its own.
# Three of its keywords validate (the rest are labels for a UI): the ones below,
# implemented as Amazon's reference validator implements them. Ignoring them is not
# neutral — `maxUniqueItems` is what refuses several `generic_keyword` entries, and
# a check without it passes a payload Amazon then rejects.
#
# `$schema` names Amazon's meta-schema by a URI that is an identifier, not an
# address, so the Draft 2019-09 validator is used directly rather than looked up
# from it.


def _max_unique_items(validator, limit, instance, schema):
	"""At most `limit` items may share the same values for the schema's `selectors`.

	Not uniqueness, despite the name: Amazon's reference validator groups the items
	by their selector values and fails when any group is larger than the limit.
	Without selectors every item is in one group, so the limit is on the whole array.
	"""
	if not validator.is_type(instance, "array"):
		return
	selectors = schema.get("selectors") or []
	groups = {}
	for item in instance:
		values = {key: item.get(key) for key in selectors} if isinstance(item, dict) else {}
		key = json.dumps(values, sort_keys=True, default=str)
		groups[key] = groups.get(key, 0) + 1
	worst = max(groups.values(), default=0)
	if worst > limit:
		yield ValidationError(
			_("At most {0} value(s) are allowed here, but {1} are sent.").format(limit, worst),
			validator="maxUniqueItems",
		)


def _max_utf8_byte_length(validator, limit, instance, schema):
	if validator.is_type(instance, "string") and len(instance.encode("utf-8")) > limit:
		yield ValidationError(
			_("Longer than the {0} bytes allowed ({1} bytes).").format(limit, len(instance.encode("utf-8"))),
			validator="maxUtf8ByteLength",
		)


def _min_utf8_byte_length(validator, limit, instance, schema):
	if validator.is_type(instance, "string") and len(instance.encode("utf-8")) < limit:
		yield ValidationError(
			_("Shorter than the {0} bytes required.").format(limit),
			validator="minUtf8ByteLength",
		)


DefinitionValidator = validators.extend(
	Draft201909Validator,
	{
		"maxUniqueItems": _max_unique_items,
		"maxUtf8ByteLength": _max_utf8_byte_length,
		"minUtf8ByteLength": _min_utf8_byte_length,
	},
)


def definition_problems(attributes, schema):
	"""Everything in `attributes` the product type's definition refuses.

	Returns [{attribute, path, missing, message}], one per distinct problem:
	`missing` is True for an attribute that must be present and is not, the case a
	caller can turn into "fill in this field"; everything else is an attribute that
	is present but wrong, with `message` saying how. `path` locates the value inside
	the attribute (`brand[0].language_tag`), for a caller that wants to be exact.

	Empty for a payload the definition accepts, and for no definition at all — the
	absence of a definition is its own answer, and the caller already gives it.
	"""
	if not schema:
		return []
	found, seen = [], set()
	for error in DefinitionValidator(schema).iter_errors(attributes or {}):
		for problem in _problems_from(error, schema):
			key = (problem["path"], problem["message"])
			if key not in seen:
				seen.add(key)
				found.append(problem)
	return found


def _problems_from(error, schema):
	"""One validation error as the problems an operator can act on.

	A top-level `required` error is a missing attribute, named one at a time — a
	conditional rule's `then` can require several at once. A composite error
	(`anyOf`, `oneOf`) stands for whichever branch came closest, because its own
	message is a repr of the whole payload.
	"""
	path = list(error.absolute_path)
	if error.validator == "required" and not path and isinstance(error.instance, dict):
		for name in error.validator_value or []:
			if name not in error.instance:
				yield {
					"attribute": name,
					"path": name,
					"missing": True,
					"message": _("'{0}' ({1}) is required.").format(attribute_title(schema, name), name),
				}
		return
	if error.context and error.validator in ("anyOf", "oneOf"):
		yield from _problems_from(best_match(error.context), schema)
		return

	name = path[0] if path and isinstance(path[0], str) else None
	where = _path_text(path)
	yield {
		"attribute": name,
		"path": where,
		"missing": False,
		"message": _("'{0}' ({1}): {2}").format(
			attribute_title(schema, name) if name else _("Listing"), where or name or "", _error_text(error)
		),
	}


def _path_text(path):
	"""`["brand", 0, "language_tag"]` as `brand[0].language_tag`."""
	text = ""
	for part in path:
		text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
	return text


#: Long enough for an enum list or a short value, short enough that a whole
#: attribute's repr — which several of jsonschema's messages embed — does not
#: become the message.
ERROR_TEXT_LIMIT = 240


def _error_text(error):
	"""jsonschema's message, in the terms of what was wrong rather than its repr."""
	if error.validator == "required" and isinstance(error.instance, dict):
		missing = [n for n in error.validator_value or [] if n not in error.instance]
		return _("missing {0}.").format(", ".join(missing))
	if error.validator == "enum":
		allowed = [str(v) for v in error.validator_value or []]
		shown = ", ".join(allowed[:8]) + (_(" and {0} more").format(len(allowed) - 8) if len(allowed) > 8 else "")
		return _("{0} is not an accepted value. Accepted: {1}.").format(json.dumps(error.instance, default=str), shown)
	if error.validator in ("maxLength", "minLength") and isinstance(error.instance, str):
		return _("{0} characters; the limit is {1} {2}.").format(
			len(error.instance), _("at most") if error.validator == "maxLength" else _("at least"), error.validator_value
		)
	if error.validator in ("maxItems", "minItems") and isinstance(error.instance, list):
		return _("{0} value(s) sent; {1} {2} allowed.").format(
			len(error.instance), _("at most") if error.validator == "maxItems" else _("at least"), error.validator_value
		)
	if error.validator == "additionalProperties":
		return _("not an attribute this product type has. ") + error.message[:ERROR_TEXT_LIMIT]
	message = error.message
	return message if len(message) <= ERROR_TEXT_LIMIT else message[: ERROR_TEXT_LIMIT - 1] + "…"
