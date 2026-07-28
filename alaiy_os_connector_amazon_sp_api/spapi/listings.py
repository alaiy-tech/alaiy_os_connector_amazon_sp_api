# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Catalog search + listing lifecycle against the Listings Items API (2021-08-01).

Create publishes an offer against an existing catalog ASIN
(requirements=LISTING_OFFER_ONLY). Update prefers a JSON-Patch PATCH and falls
back to a full PUT when an attribute can't be patched. Delete ends the listing.
After every write we re-fetch the item and upsert the Amazon Listing row so the
register reflects Amazon's actual state.

Attribute shapes (purchasable_offer, fulfillment_availability, ...) follow the
common product-type schema; they may need per-marketplace/product-type tuning
against a real account.
"""

import json
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

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
def _offer_attributes(mp, *, condition, asin, price, quantity, fulfillment_channel, title=None):
	"""Attributes for an offer-only listing against an existing ASIN."""
	mp_id = mp.marketplace_id
	attrs = {
		"condition_type": [{"marketplace_id": mp_id, "value": condition}],
		"merchant_suggested_asin": [{"marketplace_id": mp_id, "value": asin}],
	}
	if title:
		attrs["item_name"] = _title_attribute(mp, title)
	if price is not None:
		attrs["purchasable_offer"] = _price_attribute(mp, price)
	if quantity is not None:
		attrs["fulfillment_availability"] = _availability_attribute(quantity, fulfillment_channel)
	return attrs


def _title_attribute(mp, title):
	return [{"marketplace_id": mp.marketplace_id, "value": title}]


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
# fieldname on Amazon Listing -> Listings attribute path (for PATCH)
_PATCHABLE = {"price", "quantity", "condition", "title"}


def update_listing(sku, changes, marketplace=None):
	"""Update title / price / quantity / condition. Tries PATCH, falls back to PUT."""
	conn = _connection()
	mp = _marketplace(marketplace)
	changes = {k: v for k, v in (changes or {}).items() if k in _PATCHABLE}
	if not changes:
		frappe.throw(_("Nothing to update. Provide title, price, quantity, and/or condition."))

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
	return sync_listing(sku, marketplace=mp.name)


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
	return patches


def _put_fallback(client, conn, mp, sku, changes):
	"""Rebuild a full PUT from the stored row merged with the requested changes."""
	row = frappe.get_doc("Amazon Listing", sku)
	product_type = _stored_product_type(sku)
	if not product_type:
		frappe.throw(_("Cannot fall back to PUT: no product type stored for {0}.").format(sku))
	body = {
		"productType": product_type,
		"requirements": "LISTING_OFFER_ONLY",
		"attributes": _offer_attributes(
			mp,
			condition=changes.get("condition", row.condition),
			asin=row.asin,
			price=changes.get("price", row.price),
			quantity=changes.get("quantity", row.quantity),
			fulfillment_channel=row.fulfillment_channel,
			title=changes.get("title", row.title),
		),
	}
	params = {"marketplaceIds": mp.marketplace_id, "issueLocale": DEFAULT_ISSUE_LOCALE}
	try:
		return client.put(_seller_path(conn.selling_partner_id, sku), params=params, body=body, context="listing")
	except SpApiError as e:
		_handle_forbidden(e)


def _stored_product_type(sku):
	"""Best-effort product type from the stored raw summary."""
	raw = frappe.db.get_value("Amazon Listing", sku, "raw_summary")
	if raw:
		try:
			return (json.loads(raw) or {}).get("productType")
		except (ValueError, TypeError):
			return None
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

	if frappe.db.exists("Amazon Listing", sku):
		row = frappe.get_doc("Amazon Listing", sku)
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


def sync_listing(sku, marketplace=None, product=None, fulfillment_channel=None, product_type=None):
	"""Fetch one item from Amazon and upsert the Amazon Listing register row."""
	mp = _marketplace(marketplace)
	item = get_listing_item(sku, marketplace=mp.name)
	return _upsert_from_item(
		sku, mp, item, product=product, fulfillment_channel=fulfillment_channel, product_type=product_type
	)


def _upsert_from_item(sku, mp, item, product=None, fulfillment_channel=None, product_type=None):
	"""Upsert an Amazon Listing row from a Listings-Items payload (single GET or
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

	image_url = summary.get("mainImage", {}).get("link") if summary.get("mainImage") else None

	values = {
		"doctype": "Amazon Listing",
		"sku": sku,
		"title": summary.get("itemName"),
		"asin": summary.get("asin"),
		"marketplace": mp.name,
		"currency": mp.currency,
		"price": price,
		"quantity": quantity,
		"condition": summary.get("conditionType") or "new_new",
		"image_urls": image_url,
		"fulfillment_channel": fulfillment_channel or (fa[0].get("fulfillmentChannelCode") if fa else "DEFAULT"),
		"listing_status": _derive_status(summary, issues),
		"last_synced_at": now_datetime(),
		"raw_summary": json.dumps(
			{"productType": summary.get("productType") or product_type, "summary": summary},
			default=str,
		),
	}
	if product:
		values["product"] = product

	if frappe.db.exists("Amazon Listing", sku):
		row = frappe.get_doc("Amazon Listing", sku)
		row.update({k: v for k, v in values.items() if k != "doctype"})
	else:
		row = frappe.get_doc(values)

	# Replace suppression reasons with the current issues.
	row.set("suppression_reasons", [])
	for issue in issues:
		row.append("suppression_reasons", issue)

	row.flags.ignore_permissions = True
	row.save(ignore_permissions=True)
	return {"sku": sku, "listing_status": row.listing_status, "issues": issues}


# --- bulk sync (searchListingsItems) -----------------------------------------
# searchListingsItems returns at most 1000 SKUs per marketplace; a larger
# catalog needs the Merchant Listings report path (not built yet).
SEARCH_PAGE_SIZE = 20
SEARCH_MAX_PAGES = 60  # safety cap (1000-SKU limit reached well before this)


def _search_listings_items(mp, client, page_token=None):
	params = {
		"marketplaceIds": mp.marketplace_id,
		"includedData": "summaries,issues,offers,fulfillmentAvailability",
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
			for item in resp.get("items", []) or []:
				sku = item.get("sku")
				if not sku:
					continue
				result = _upsert_from_item(sku, mp, item)
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
