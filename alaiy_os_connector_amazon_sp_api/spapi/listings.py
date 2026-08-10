# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Catalog search + listing lifecycle against the Listings Items API (2021-08-01).

Create publishes an offer against an existing catalog ASIN
(requirements=LISTING_OFFER_ONLY). Update prefers a JSON-Patch PATCH and falls
back to a full PUT when an attribute can't be patched. Delete ends the listing.
After every write we re-fetch the item and upsert the Amazon Product Listing row
so the register reflects Amazon's actual state.

Attribute shapes (purchasable_offer, fulfillment_availability, ...) follow the
common product-type schema; they may need per-marketplace/product-type tuning
against a real account.
"""

import json
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from alaiy_os_connector_amazon_sp_api.spapi import catalog
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	CATALOG_ITEMS_PATH,
	DEFAULT_ISSUE_LOCALE,
	FULFILLMENT_CHANNEL_CODES,
	LISTINGS_ITEMS_BASE,
)


# --- shared helpers ----------------------------------------------------------
def _connection():
	conn = frappe.get_cached_doc("Amazon Connection")
	if not conn.is_connected():
		frappe.throw(_("Amazon account is not connected."))
	if not conn.selling_partner_id:
		frappe.throw(_("No selling partner id on the connection. Reconnect the Amazon account."))
	return conn


def _marketplace(marketplace=None):
	"""Resolve to an Amazon Marketplace doc (defaults to the primary)."""
	conn = frappe.get_cached_doc("Amazon Connection")
	marketplace = marketplace or conn.primary_marketplace
	if not marketplace:
		frappe.throw(_("No marketplace given and no primary marketplace set on the connection."))
	return frappe.get_cached_doc("Amazon Marketplace", marketplace)


def _seller_path(seller_id, sku):
	return f"{LISTINGS_ITEMS_BASE}/{seller_id}/{quote(str(sku), safe='')}"


def _handle_forbidden(err):
	"""Turn a Listings 403 into the role-oriented actionable message."""
	if err.is_forbidden():
		frappe.throw(describe_forbidden(err, role_free=False))
	raise err


def _issues_from(response):
	"""Normalise the Listings response `issues` array."""
	out = []
	for issue in (response or {}).get("issues", []) or []:
		out.append(
			{
				"code": issue.get("code"),
				"message": issue.get("message"),
				"severity": (issue.get("severity") or "").upper(),
				"attribute_names": ", ".join(issue.get("attributeNames", []) or []),
			}
		)
	return out


def _raise_on_error_issues(issues, action):
	errors = [i for i in issues if i["severity"] == "ERROR"]
	if errors:
		joined = "; ".join(f"[{i['code']}] {i['message']}" for i in errors)
		frappe.throw(_("Amazon rejected the {0}: {1}").format(action, joined))


# --- catalog search ----------------------------------------------------------
def search_catalog(query, marketplace=None, page_size=10):
	"""Search the Amazon catalog for an ASIN + product type before listing.

	Returns [{asin, title, brand, image_url, product_type}].
	"""
	if not query:
		frappe.throw(_("Enter a search term (keywords or an ASIN)."))
	mp = _marketplace(marketplace)
	client = SpApiClient()

	params = {
		"marketplaceIds": mp.marketplace_id,
		"includedData": "summaries,images,productTypes",
		"pageSize": cint(page_size) or 10,
	}
	# A bare ASIN (10 chars, alphanumeric) is looked up directly; else keywords.
	q = query.strip()
	if len(q) == 10 and q.isalnum() and not q.isdigit():
		params["identifiers"] = q
		params["identifiersType"] = "ASIN"
	else:
		params["keywords"] = q

	try:
		resp = client.get(CATALOG_ITEMS_PATH, params=params, context="listing")
	except SpApiError as e:
		_handle_forbidden(e)

	results = []
	for item in resp.get("items", []) or []:
		summaries = item.get("summaries") or []
		summary = summaries[0] if summaries else {}
		images = item.get("images") or []
		image_url = None
		if images and images[0].get("images"):
			image_url = images[0]["images"][0].get("link")
		product_types = item.get("productTypes") or []
		product_type = product_types[0].get("productType") if product_types else None
		results.append(
			{
				"asin": item.get("asin"),
				"title": summary.get("itemName"),
				"brand": summary.get("brand"),
				"image_url": image_url,
				"product_type": product_type,
			}
		)
	return results


# --- attribute builders ------------------------------------------------------
def _offer_attributes(
	mp,
	*,
	condition,
	asin,
	price,
	quantity,
	fulfillment_channel,
	title=None,
	description=None,
	bullets=None,
	keywords=None,
	images=None,
):
	"""Attributes for an offer-only listing against an existing ASIN."""
	mp_id = mp.marketplace_id
	attrs = {
		"condition_type": [{"marketplace_id": mp_id, "value": condition}],
		"merchant_suggested_asin": [{"marketplace_id": mp_id, "value": asin}],
	}
	if title:
		attrs["item_name"] = _title_attribute(mp, title)
	if description:
		attrs["product_description"] = _description_attribute(mp, description)
	if bullets:
		attrs["bullet_point"] = _bullet_attribute(mp, bullets)
	if keywords:
		attrs["generic_keyword"] = _keyword_attribute(mp, keywords)
	if images:
		attrs.update(_image_attributes(mp, images))
	if price is not None:
		attrs["purchasable_offer"] = _price_attribute(mp, price)
	if quantity is not None:
		attrs["fulfillment_availability"] = _availability_attribute(quantity, fulfillment_channel)
	return attrs


def _localized(mp, value):
	"""A marketplace-scoped text value, tagged with the marketplace language when set."""
	entry = {"marketplace_id": mp.marketplace_id, "value": value}
	language = mp.get("language")
	if language:
		entry["language_tag"] = language
	return entry


def _title_attribute(mp, title):
	return [_localized(mp, title)]


def _description_attribute(mp, description):
	return [_localized(mp, description)]


def _bullet_attribute(mp, bullets):
	return [_localized(mp, b) for b in (bullets or []) if b]


def _keyword_attribute(mp, keywords):
	return [_localized(mp, k) for k in (keywords or []) if k]


def _normalize_images(images):
	"""Coerce mixed image input into an ordered [{url, is_main}] list.

	Accepts plain URL strings or {url|image_url, is_main} dicts. The main image is
	the first row flagged is_main, else the first row.
	"""
	norm = []
	for img in images or []:
		if isinstance(img, str):
			url, is_main = img, False
		else:
			url = img.get("url") or img.get("image_url")
			is_main = bool(img.get("is_main"))
		if url:
			norm.append({"url": url, "is_main": is_main})
	if not norm:
		return []
	main_idx = next((i for i, im in enumerate(norm) if im["is_main"]), 0)
	# Reorder so the main image is first and marked; the rest follow in order.
	main = {"url": norm[main_idx]["url"], "is_main": True}
	others = [{"url": im["url"], "is_main": False} for i, im in enumerate(norm) if i != main_idx]
	return [main, *others]


def _image_attributes(mp, images):
	"""{attr_name: [locator]} for the main image + up to 8 additional images."""
	norm = _normalize_images(images)
	if not norm:
		return {}
	out = {
		"main_product_image_locator": [
			{"marketplace_id": mp.marketplace_id, "media_location": norm[0]["url"]}
		]
	}
	for i, im in enumerate(norm[1:9], start=1):
		out[f"other_product_image_locator_{i}"] = [
			{"marketplace_id": mp.marketplace_id, "media_location": im["url"]}
		]
	return out


def _price_attribute(mp, price):
	return [
		{
			"marketplace_id": mp.marketplace_id,
			"currency": mp.currency,
			"our_price": [{"schedule": [{"value_with_tax": flt(price)}]}],
		}
	]


def _availability_attribute(quantity, fulfillment_channel):
	code = FULFILLMENT_CHANNEL_CODES.get(fulfillment_channel or "DEFAULT", "DEFAULT")
	return [{"fulfillment_channel_code": code, "quantity": cint(quantity)}]


# --- create ------------------------------------------------------------------
def create_listing(
	sku,
	*,
	asin,
	product_type,
	price=None,
	quantity=None,
	condition="new_new",
	marketplace=None,
	fulfillment_channel="DEFAULT",
	product=None,
):
	"""Publish an offer for an existing ASIN via PUT (LISTING_OFFER_ONLY)."""
	conn = _connection()
	mp = _marketplace(marketplace)
	if not asin:
		frappe.throw(_("An ASIN is required to create an offer."))
	if not product_type:
		frappe.throw(_("A product type is required (from catalog search)."))

	body = {
		"productType": product_type,
		"requirements": "LISTING_OFFER_ONLY",
		"attributes": _offer_attributes(
			mp,
			condition=condition,
			asin=asin,
			price=price,
			quantity=quantity,
			fulfillment_channel=fulfillment_channel,
		),
	}
	params = {"marketplaceIds": mp.marketplace_id, "issueLocale": DEFAULT_ISSUE_LOCALE}

	client = SpApiClient(conn)
	try:
		resp = client.put(
			_seller_path(conn.selling_partner_id, sku), params=params, body=body, context="listing"
		)
	except SpApiError as e:
		_handle_forbidden(e)

	issues = _issues_from(resp)
	_raise_on_error_issues(issues, _("listing"))
	# Re-fetch to capture Amazon's derived state, then upsert the register row.
	return sync_listing(
		sku,
		marketplace=mp.name,
		product=product,
		fulfillment_channel=fulfillment_channel,
		product_type=product_type,
	)


# --- update ------------------------------------------------------------------
# Amazon Product Listing fieldnames we allow through to the Listings Items attributes.
_PATCHABLE = {
	"title",
	"price",
	"quantity",
	"condition",
	"description",
	"bullet_points",
	"keywords",
	"images",
}
# Product-content attributes (as opposed to offer attributes); their presence
# means a PUT fallback must not use requirements=LISTING_OFFER_ONLY (which strips them).
_CONTENT_FIELDS = {"title", "description", "bullet_points", "keywords", "images"}


def update_listing(sku, changes, marketplace=None):
	"""Update offer + content attributes. Tries PATCH, falls back to PUT."""
	conn = _connection()
	mp = _marketplace(marketplace)
	changes = {k: v for k, v in (changes or {}).items() if k in _PATCHABLE}
	if not changes:
		frappe.throw(
			_(
				"Nothing to update. Provide title, price, quantity, condition, "
				"description, bullet points, keywords, and/or images."
			)
		)

	patches = _build_patches(mp, changes)
	params = {"marketplaceIds": mp.marketplace_id, "issueLocale": DEFAULT_ISSUE_LOCALE}
	body = {"productType": _stored_product_type(sku), "patches": patches}

	client = SpApiClient(conn)
	path = _seller_path(conn.selling_partner_id, sku)
	try:
		resp = client.patch(path, params=params, body=body, context="listing")
	except SpApiError as e:
		if e.is_forbidden():
			_handle_forbidden(e)
		# PATCH unsupported for this attribute/product-type -> full PUT rebuild.
		resp = _put_fallback(client, conn, mp, sku, changes)

	issues = _issues_from(resp)
	_raise_on_error_issues(issues, _("update"))
	# Amazon's Listings API is asynchronous: the submission above is only
	# *accepted*, not yet applied, so an immediate re-fetch would return the
	# pre-update state and clobber the operator's edits. Persist the submitted
	# values locally and mark the row pending; the real state is reconciled by
	# "Sync from Amazon" or the scheduled job once Amazon has processed it.
	return _apply_submitted_changes(sku, mp, changes, issues)


def _apply_submitted_changes(sku, mp, changes, issues):
	"""Write the operator's accepted changes to the register row (status pending)."""
	if not frappe.db.exists("Amazon Product Listing", sku):
		# No local row yet (e.g. updating a SKU synced elsewhere) — fall back to a
		# fetch so the register has something to show.
		return sync_listing(sku, marketplace=mp.name)
	row = frappe.get_doc("Amazon Product Listing", sku)
	for field in ("title", "price", "quantity", "condition", "description"):
		if field in changes and changes[field] is not None:
			row.set(field, changes[field])
	if "bullet_points" in changes:
		row.set("bullet_points", [{"bullet": b} for b in (changes["bullet_points"] or []) if b])
	if "keywords" in changes:
		row.set("keywords", [{"keyword": k} for k in (changes["keywords"] or []) if k])
	if "images" in changes:
		row.set(
			"images",
			[
				{"image_url": im["url"], "is_main": 1 if im["is_main"] else 0}
				for im in _normalize_images(changes["images"])
			],
		)
	row.listing_status = "pending"
	row.last_synced_at = now_datetime()
	row.set("suppression_reasons", [])
	for issue in issues:
		row.append("suppression_reasons", issue)
	row.flags.ignore_permissions = True
	row.save(ignore_permissions=True)
	return {"sku": sku, "listing_status": row.listing_status, "issues": issues}


def _build_patches(mp, changes):
	patches = []
	if "title" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/item_name", "value": _title_attribute(mp, changes["title"])}
		)
	if "price" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/purchasable_offer", "value": _price_attribute(mp, changes["price"])}
		)
	if "quantity" in changes:
		patches.append(
			{
				"op": "replace",
				"path": "/attributes/fulfillment_availability",
				"value": _availability_attribute(changes["quantity"], changes.get("fulfillment_channel")),
			}
		)
	if "condition" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/condition_type", "value": [{"marketplace_id": mp.marketplace_id, "value": changes["condition"]}]}
		)
	if "description" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/product_description", "value": _description_attribute(mp, changes["description"])}
		)
	if "bullet_points" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/bullet_point", "value": _bullet_attribute(mp, changes["bullet_points"])}
		)
	if "keywords" in changes:
		patches.append(
			{"op": "replace", "path": "/attributes/generic_keyword", "value": _keyword_attribute(mp, changes["keywords"])}
		)
	if "images" in changes:
		# Each image locator is its own attribute, so it needs its own patch op.
		for attr, value in _image_attributes(mp, changes["images"]).items():
			patches.append({"op": "replace", "path": f"/attributes/{attr}", "value": value})
	return patches


def _put_fallback(client, conn, mp, sku, changes):
	"""Rebuild a full PUT from the stored row merged with the requested changes."""
	row = frappe.get_doc("Amazon Product Listing", sku)
	product_type = _stored_product_type(sku)
	if not product_type:
		frappe.throw(_("Cannot fall back to PUT: no product type stored for {0}.").format(sku))

	touches_content = bool(_CONTENT_FIELDS & set(changes))
	attributes = _offer_attributes(
		mp,
		condition=changes.get("condition", row.condition),
		asin=row.asin,
		price=changes.get("price", row.price),
		quantity=changes.get("quantity", row.quantity),
		fulfillment_channel=row.fulfillment_channel,
		title=changes.get("title", row.title),
		description=changes.get("description", row.description),
		bullets=changes["bullet_points"] if "bullet_points" in changes else _row_bullets(row),
		keywords=changes["keywords"] if "keywords" in changes else _row_keywords(row),
		images=changes["images"] if "images" in changes else _row_images(row),
	)
	body = {"productType": product_type, "attributes": attributes}
	# Offer-only strips product content; only assert it when nothing content-ish changed.
	if not touches_content:
		body["requirements"] = "LISTING_OFFER_ONLY"
	params = {"marketplaceIds": mp.marketplace_id, "issueLocale": DEFAULT_ISSUE_LOCALE}
	try:
		return client.put(_seller_path(conn.selling_partner_id, sku), params=params, body=body, context="listing")
	except SpApiError as e:
		_handle_forbidden(e)


def _row_bullets(row):
	return [b.bullet for b in (row.get("bullet_points") or []) if b.bullet]


def _row_keywords(row):
	return [k.keyword for k in (row.get("keywords") or []) if k.keyword]


def _row_images(row):
	return [{"url": im.image_url, "is_main": im.is_main} for im in (row.get("images") or []) if im.image_url]


def _stored_product_type(sku):
	"""Best-effort product type for a SKU: the field first, raw_summary second.

	The field is the one an operator can see and correct; raw_summary is where
	this used to live, and it stays the fallback for rows last synced before the
	field existed and not yet touched by the backfill patch.
	"""
	product_type, raw = (
		frappe.db.get_value("Amazon Product Listing", sku, ["product_type", "raw_summary"]) or (None, None)
	)
	if product_type:
		return product_type
	return product_type_from_raw_summary(raw)


def product_type_from_raw_summary(raw):
	"""The productType recorded inside a stored raw_summary blob, if any."""
	if not raw:
		return None
	try:
		return (json.loads(raw) or {}).get("productType")
	except (ValueError, TypeError):
		return None


# --- delete ------------------------------------------------------------------
def delete_listing(sku, marketplace=None):
	"""End a listing. Per policy we keep the row and mark it inactive."""
	conn = _connection()
	mp = _marketplace(marketplace)
	params = {"marketplaceIds": mp.marketplace_id, "issueLocale": DEFAULT_ISSUE_LOCALE}

	client = SpApiClient(conn)
	try:
		resp = client.delete(_seller_path(conn.selling_partner_id, sku), params=params, context="listing")
	except SpApiError as e:
		_handle_forbidden(e)

	issues = _issues_from(resp)
	_raise_on_error_issues(issues, _("deletion"))

	if frappe.db.exists("Amazon Product Listing", sku):
		row = frappe.get_doc("Amazon Product Listing", sku)
		row.listing_status = "inactive"
		row.last_synced_at = now_datetime()
		row.save(ignore_permissions=True)
	return {"sku": sku, "listing_status": "inactive"}


# --- get + upsert ------------------------------------------------------------
def get_listing_item(sku, marketplace=None):
	"""GET the full Listings item with summaries/attributes/issues/offers."""
	conn = _connection()
	mp = _marketplace(marketplace)
	params = {
		"marketplaceIds": mp.marketplace_id,
		"issueLocale": DEFAULT_ISSUE_LOCALE,
		"includedData": "summaries,attributes,issues,offers,fulfillmentAvailability",
	}
	client = SpApiClient(conn)
	try:
		return client.get(_seller_path(conn.selling_partner_id, sku), params=params, context="listing")
	except SpApiError as e:
		if e.status_code == 404:
			frappe.throw(
				_(
					"SKU '{0}' is not a listing on this Amazon account (marketplace {1}). "
					"Sync only works for SKUs you already have in Seller Central — this must be a "
					"seller SKU, not a UPC/EAN or ASIN. To create a new offer, use Search Catalog "
					"then Publish Offer."
				).format(sku, mp.country or mp.marketplace_id),
				title=_("Listing not found"),
			)
		_handle_forbidden(e)


def _derive_status(summary, issues):
	statuses = summary.get("status") or []  # e.g. ["BUYABLE", "DISCOVERABLE"]
	if any(i["severity"] == "ERROR" for i in issues):
		return "suppressed"
	if "BUYABLE" in statuses:
		return "active"
	return "inactive"


def _item_asin(sku, item):
	"""The ASIN for a listing payload, falling back to the one we already stored.

	An offer-only summary can omit the ASIN, but we recorded it when the offer was
	published — and without an ASIN there is no catalog content to look up.
	"""
	summaries = item.get("summaries") or []
	asin = (summaries[0] if summaries else {}).get("asin")
	if asin:
		return asin
	return frappe.db.get_value("Amazon Product Listing", sku, "asin")


def sync_listing(sku, marketplace=None, product=None, fulfillment_channel=None, product_type=None):
	"""Fetch one item from Amazon and upsert the Amazon Product Listing register row."""
	mp = _marketplace(marketplace)
	item = get_listing_item(sku, marketplace=mp.name)
	# Content comes from the ASIN's catalog entry, not from our own listing
	# attributes — see spapi.catalog for why an offer-only listing has none.
	asin = _item_asin(sku, item)
	catalog_content = catalog.fetch_content([asin], mp).get(asin) if asin else None
	return _upsert_from_item(
		sku,
		mp,
		item,
		product=product,
		fulfillment_channel=fulfillment_channel,
		product_type=product_type,
		catalog_content=catalog_content,
	)


def _upsert_from_item(
	sku, mp, item, product=None, fulfillment_channel=None, product_type=None, catalog_content=None
):
	"""Upsert an Amazon Product Listing row from a Listings-Items payload (single GET or
	a searchListingsItems entry — both share the summaries/issues/offers shape)."""
	summaries = item.get("summaries") or []
	summary = summaries[0] if summaries else {}
	issues = _issues_from(item)

	price = None
	offers = item.get("offers") or []
	if offers:
		price_obj = offers[0].get("price") or {}
		price = flt(price_obj.get("amount")) if price_obj.get("amount") is not None else None

	quantity = None
	fa = item.get("fulfillmentAvailability") or []
	if fa:
		quantity = cint(fa[0].get("quantity"))

	values = {
		"doctype": "Amazon Product Listing",
		"sku": sku,
		"marketplace": mp.name,
		"currency": mp.currency,
		"price": price,
		"quantity": quantity,
		"condition": summary.get("conditionType") or "new_new",
		"fulfillment_channel": fulfillment_channel or (fa[0].get("fulfillmentChannelCode") if fa else "DEFAULT"),
		"listing_status": _derive_status(summary, issues),
		"last_synced_at": now_datetime(),
		"raw_summary": json.dumps(
			{"productType": summary.get("productType") or product_type, "summary": summary},
			default=str,
		),
	}
	# Title, ASIN and product type come from the summary, which an offer-only or
	# variation-parent SKU can omit entirely. Assigning them unconditionally is
	# how a sync used to blank a perfectly good title (and with it the row's
	# display name, since title_field falls back to `name` == the SKU), so only
	# set what Amazon actually gave us. _apply_content may still supply a title
	# from the catalog below. For the product type that also protects the value
	# Search Catalog put on the row before the offer was ever published: without
	# it, every write to Amazon fails for want of a productType.
	for field, value in (
		("title", summary.get("itemName")),
		("asin", summary.get("asin")),
		("product_type", summary.get("productType") or product_type),
	):
		if value:
			values[field] = value
	if product:
		values["product"] = product

	if frappe.db.exists("Amazon Product Listing", sku):
		row = frappe.get_doc("Amazon Product Listing", sku)
		row.update({k: v for k, v in values.items() if k != "doctype"})
	else:
		row = frappe.get_doc(values)

	# Replace suppression reasons with the current issues.
	row.set("suppression_reasons", [])
	for issue in issues:
		row.append("suppression_reasons", issue)

	_apply_content(row, mp, item, summary, catalog_content=catalog_content)
	_apply_variation(row, catalog_content)

	row.flags.ignore_permissions = True
	row.save(ignore_permissions=True)
	return {"sku": sku, "listing_status": row.listing_status, "issues": issues}


def _own_attr_values(attributes, name):
	"""Text values of one of *our own* Listings attributes."""
	return [
		e["value"]
		for e in (attributes.get(name) or [])
		if isinstance(e.get("value"), str) and e["value"].strip()
	]


def _own_attr(attributes, name):
	"""The first value of one of our own Listings attributes, or None."""
	values = _own_attr_values(attributes, name)
	return values[0] if values else None


def _own_images(attributes):
	"""Images from our own image-locator attributes, main first."""
	out = []
	main_loc = attributes.get("main_product_image_locator") or []
	main_url = main_loc[0].get("media_location") if main_loc else None
	if main_url:
		out.append({"url": main_url, "is_main": True})
	for i in range(1, 9):
		loc = attributes.get(f"other_product_image_locator_{i}") or []
		url = loc[0].get("media_location") if loc else None
		if url:
			out.append({"url": url, "is_main": False})
	if out and not out[0]["is_main"]:
		out[0]["is_main"] = True
	return out


def _apply_content(row, mp, item, summary, catalog_content=None):
	"""Fill title/description/bullets/keywords/images on the register row.

	Precedence: our own Listings attributes first (authoritative when this seller
	is the ASIN's content contributor), then the catalog content for the ASIN,
	then whatever the row already holds.

	That last step is the important one. An offer-only listing carries no content
	attributes at all (see spapi.catalog for why), so the previous version — which
	read only our own attributes and assigned unconditionally — wrote None over
	the description and emptied the bullet/keyword/image tables on every single
	sync. Nothing here blanks a field: a value we could not find is a value we
	leave alone, and clearing content is done by the operator, not by a sync.
	"""
	attributes = item.get("attributes") or {}
	catalog_content = catalog_content or {}

	title = _own_attr(attributes, "item_name") or catalog_content.get("title")
	if title:
		row.title = title

	description = _own_attr(attributes, "product_description") or catalog_content.get("description")
	if description:
		row.description = description

	bullets = _own_attr_values(attributes, "bullet_point") or catalog_content.get("bullets") or []
	if bullets:
		row.set("bullet_points", [{"bullet": b} for b in bullets])

	keywords = _own_attr_values(attributes, "generic_keyword") or catalog_content.get("keywords") or []
	if keywords:
		row.set("keywords", [{"keyword": k} for k in keywords])

	images = _own_images(attributes) or catalog_content.get("images") or []
	if not images and (summary.get("mainImage") or {}).get("link"):
		images = [{"url": summary["mainImage"]["link"], "is_main": True}]
	if images:
		row.set(
			"images",
			[{"image_url": im["url"], "is_main": 1 if im["is_main"] else 0} for im in images],
		)


def _listing_for_asin(asin, marketplace=None):
	"""The register row for an ASIN, if this seller lists it.

	Empty is the normal answer for a variation parent: a parent is not a buyable
	offer, so most sellers have no SKU of their own for it. The link therefore
	fills in only once (and if) the parent turns up in the register — a later sync
	picks it up, which is why this is resolved on every sync rather than once.

	Scoped to the marketplace, because the same ASIN is listed on several of them
	and a family only means anything within one. Ordered, because nothing stops a
	seller having two SKUs on one ASIN: without an order the winner would vary
	between syncs and each sync would write a spurious new version of the row.
	"""
	if not asin:
		return None
	filters = {"asin": asin}
	if marketplace:
		filters["marketplace"] = marketplace
	return frappe.db.get_value("Amazon Product Listing", filters, "name", order_by="name asc")


def _apply_variation(row, catalog_content):
	"""Record where this SKU sits in its variation family.

	Applied verbatim, clears included — unlike content, parentage is something
	Amazon answers definitively whenever `relationships` is requested, so "no
	parent ASIN" means standalone, not unknown, and a listing that has left a
	family should stop claiming it.

	A missing answer is still not an answer: when the catalog look-up failed or
	the row has no ASIN to look up, catalog_content is None and nothing changes.
	"""
	if catalog_content is None:
		return

	parent_asin = catalog_content.get("parent_asin") or None
	row.parent_asin = parent_asin
	row.parent_listing = _listing_for_asin(parent_asin, marketplace=row.get("marketplace"))
	row.variation_theme = catalog_content.get("variation_theme") or None
	row.is_variation_parent = 1 if catalog_content.get("is_variation_parent") else 0


def variation_family(parent_asin, marketplace=None):
	"""Every SKU this seller lists under one parent ASIN.

	The parent→SKU mapping the register could not answer before. Returns the
	parent's own row when the seller happens to list it, plus the children.
	"""
	if not parent_asin:
		frappe.throw(_("A parent ASIN is required."))

	filters = {"parent_asin": parent_asin}
	if marketplace:
		filters["marketplace"] = marketplace
	children = frappe.get_all(
		"Amazon Product Listing",
		filters=filters,
		fields=[
			"name as sku",
			"title",
			"asin",
			"listing_status",
			"price",
			"quantity",
			"variation_theme",
		],
		order_by="name asc",
	)

	parent_filters = {"asin": parent_asin}
	if marketplace:
		parent_filters["marketplace"] = marketplace
	parent = frappe.db.get_value(
		"Amazon Product Listing", parent_filters, ["name", "title", "variation_theme"], as_dict=True
	)

	# The theme is a property of the family, so any row in it can supply it —
	# useful because the parent is the row most likely to be missing.
	theme = (parent or {}).get("variation_theme") or next(
		(c["variation_theme"] for c in children if c.get("variation_theme")), None
	)

	return {
		"parent_asin": parent_asin,
		"parent_sku": (parent or {}).get("name"),
		"parent_title": (parent or {}).get("title"),
		"variation_theme": theme,
		"children": children,
		"child_count": len(children),
	}


# --- bulk sync (searchListingsItems) -----------------------------------------
# searchListingsItems returns at most 1000 SKUs per marketplace but includes
# rich content (summaries/offers/issues). For catalogs beyond that cap, use
# spapi.reconcile.reconcile_all_listings, which drives the Merchant Listings
# report (every SKU, no cap) to reconcile offer/status fields.
SEARCH_PAGE_SIZE = 20
SEARCH_MAX_PAGES = 60  # safety cap (1000-SKU limit reached well before this)


def _search_listings_items(mp, client, page_token=None):
	params = {
		"marketplaceIds": mp.marketplace_id,
		# `attributes` is requested so a seller who *does* own an ASIN's content
		# gets it straight from their own listing; for offer-only SKUs it comes
		# back with offer attributes only and spapi.catalog fills the gap.
		"includedData": "summaries,attributes,issues,offers,fulfillmentAvailability",
		"pageSize": SEARCH_PAGE_SIZE,
		"issueLocale": DEFAULT_ISSUE_LOCALE,
	}
	if page_token:
		params["pageToken"] = page_token
	conn = frappe.get_cached_doc("Amazon Connection")
	path = f"{LISTINGS_ITEMS_BASE}/{conn.selling_partner_id}"
	try:
		return client.get(path, params=params, context="reconcile")
	except SpApiError as e:
		_handle_forbidden(e)


def sync_all_listings(marketplace=None, notify_user=None):
	"""Page through every listing on the primary marketplace and upsert each.

	Intended to run as a background job. Publishes an `amazon_sync_all_complete`
	realtime event to `notify_user` when done.
	"""
	conn = _connection()
	mp = _marketplace(marketplace)
	client = SpApiClient(conn)

	total = 0
	by_status = {}
	page_token = None
	truncated = False

	try:
		for page in range(SEARCH_MAX_PAGES):
			resp = _search_listings_items(mp, client, page_token=page_token)
			items = [i for i in (resp.get("items") or []) if i.get("sku")]
			# One catalog call per page, not per SKU: the page holds at most
			# SEARCH_PAGE_SIZE listings and `identifiers` takes 20 ASINs, so the
			# content look-up costs a single extra request per page.
			content_by_asin = catalog.fetch_content(
				[_item_asin(i["sku"], i) for i in items], mp, client=client
			)
			for item in items:
				sku = item["sku"]
				asin = _item_asin(sku, item)
				result = _upsert_from_item(
					sku, mp, item, catalog_content=content_by_asin.get(asin) if asin else None
				)
				total += 1
				status = result["listing_status"]
				by_status[status] = by_status.get(status, 0) + 1
			frappe.db.commit()  # persist each page as we go
			page_token = (resp.get("pagination") or {}).get("nextToken")
			if not page_token:
				break
			if page == SEARCH_MAX_PAGES - 1:
				truncated = True

		summary = {
			"success": True,
			"marketplace": mp.name,
			"synced": total,
			"by_status": by_status,
			"truncated": truncated,
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Amazon sync-all failed", message=frappe.get_traceback())
		summary = {"success": False, "marketplace": mp.name, "synced": total, "error": str(e)}

	if notify_user:
		frappe.publish_realtime("amazon_sync_all_complete", summary, user=notify_user)
	return summary
