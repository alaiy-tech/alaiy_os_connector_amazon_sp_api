# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Product content for an ASIN, from the Catalog Items API (2022-04-01).

Why this module exists: the Listings Items API only ever returns the attributes
*this seller* contributes. A listing published against an existing catalog ASIN
— which is what `listings.create_listing` does (requirements=LISTING_OFFER_ONLY)
and what any reseller has — contributes offer attributes only: condition_type,
merchant_suggested_asin, purchasable_offer, fulfillment_availability. The
content attributes (item_name, product_description, bullet_point,
generic_keyword, main/other_product_image_locator_*) belong to whoever owns the
ASIN's detail page and are simply absent from the seller's payload. Reading
content from Listings therefore yields nothing for those SKUs.

The content lives in the catalog, and searchCatalogItems returns it regardless
of who contributed it. Look-ups are batched — `identifiers` takes up to 20 ASINs
per call — so reconciling a whole catalog costs one extra call per page rather
than one per SKU.

The seller's own attributes still win where they exist (see
listings._apply_content); this is the fallback that makes offer-only listings
show their title, brand, description, bullets, keywords and images at all.
"""

import frappe
from frappe.utils import cint

from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	CATALOG_CATEGORY_LEVELS,
	CATALOG_CONTENT_INCLUDED_DATA,
	CATALOG_ITEMS_PATH,
	CATALOG_MAX_IDENTIFIERS,
	CATALOG_VARIATION_RELATIONSHIP,
	PRODUCT_ID_PREFERENCE,
)

# Amazon's image variants, in the order our schema wants them: MAIN becomes the
# row flagged is_main, PT01..PT08 become the eight additional images. Everything
# else (SWCH colour swatches, and unknown future variants) is skipped — a swatch
# is not a product shot and would push a real image out of the eight slots.
_IMAGE_VARIANTS = ["MAIN"] + [f"PT{i:02d}" for i in range(1, 9)]


def _pick_for_marketplace(blocks, marketplace_id):
	"""The block for this marketplace out of a per-marketplace array.

	summaries / images / relationships are arrays keyed by `marketplaceId`
	(camelCase — unlike the `marketplace_id` used inside `attributes`, which is
	Amazon's inconsistency, not ours). Falls back to the first block: a
	single-marketplace request often returns one unkeyed-in-practice entry.
	"""
	blocks = blocks or []
	for block in blocks:
		if block.get("marketplaceId") == marketplace_id:
			return block
	return blocks[0] if blocks else None


def _attr_values(attributes, name, marketplace_id, language=None):
	"""Text values of one catalog attribute, scoped to marketplace and language.

	Entries without a marketplace_id are kept: some product types return values
	unscoped, and dropping them would lose the only content available. Non-string
	values are skipped — a handful of attributes nest objects, and those are not
	something we can put in a text field.
	"""
	entries = attributes.get(name) or []
	scoped = [e for e in entries if e.get("marketplace_id") in (None, "", marketplace_id)]
	if language:
		preferred = [e for e in scoped if e.get("language_tag") == language]
		if preferred:
			scoped = preferred
	return [e["value"] for e in scoped if isinstance(e.get("value"), str) and e["value"].strip()]


def _images_from(item, marketplace_id):
	"""[{url, is_main}] for an ASIN, main first, capped at MAIN + 8 others."""
	block = _pick_for_marketplace(item.get("images"), marketplace_id) or {}
	# Amazon lists several resolutions per variant; keep the largest of each.
	best = {}
	for img in block.get("images") or []:
		variant = (img.get("variant") or "").upper()
		link = img.get("link")
		if not link or variant not in _IMAGE_VARIANTS:
			continue
		area = cint(img.get("height")) * cint(img.get("width"))
		if variant not in best or area > best[variant][0]:
			best[variant] = (area, link)

	out = [
		{"url": best[variant][1], "is_main": variant == "MAIN"}
		for variant in _IMAGE_VARIANTS
		if variant in best
	]
	# An ASIN with product shots but no MAIN variant still needs one main row —
	# both our child table and Amazon's schema assume exactly one.
	if out and not out[0]["is_main"]:
		out[0]["is_main"] = True
	return out


def _variation_from(item, marketplace_id):
	"""The ASIN's place in its variation family.

	An ASIN is a child (it has parentAsins), a parent (it has childAsins), or
	neither — never both. Amazon states this per marketplace, and the same
	`relationships` array also carries PACKAGE_HIERARCHY entries, which describe
	case-packs rather than variations and must not be read as parentage.

	`child_asin_count` counts the family as *Amazon* sees it, which is usually
	larger than the number of SKUs this seller lists against it.
	"""
	empty = {
		"parent_asin": None,
		"is_variation_parent": False,
		"child_asin_count": 0,
		"variation_theme": None,
	}
	block = _pick_for_marketplace(item.get("relationships"), marketplace_id) or {}
	for rel in block.get("relationships") or []:
		if (rel.get("type") or "").upper() != CATALOG_VARIATION_RELATIONSHIP:
			continue
		parents = [a for a in (rel.get("parentAsins") or []) if a]
		children = [a for a in (rel.get("childAsins") or []) if a]
		return {
			"parent_asin": parents[0] if parents else None,
			"is_variation_parent": bool(children),
			"child_asin_count": len(children),
			"variation_theme": (rel.get("variationTheme") or {}).get("theme") or None,
		}
	return empty


def identifiers_from(item, marketplace_id):
	"""Every product identifier Amazon holds for this ASIN, most specific first.

	This is the barcode, and it is the reason `identifiers` was added to
	CATALOG_CONTENT_INCLUDED_DATA. The seller's own
	`externally_assigned_product_identifier` attribute answers the same question
	and is populated only for a seller who *created* the ASIN — so for a reseller
	it is absent, and a catalogue matched on it matches nothing at all while
	looking like it ran.

	The full list is returned rather than one value because Amazon commonly holds
	both an EAN and the UPC inside it: `0819752013274` and `819752013274` are the
	same barcode and are not equal as strings. A caller matching against another
	channel should compare every value here, not just the pick.
	"""
	block = _pick_for_marketplace(item.get("identifiers"), marketplace_id) or {}
	out = []
	seen = set()
	for entry in block.get("identifiers") or []:
		kind = (entry.get("identifierType") or "").upper().strip()
		value = (entry.get("identifier") or "").strip()
		if not kind or not value or (kind, value) in seen:
			continue
		seen.add((kind, value))
		out.append({"type": kind, "value": value})

	order = {kind: i for i, kind in enumerate(PRODUCT_ID_PREFERENCE)}
	out.sort(key=lambda e: order.get(e["type"], len(order)))
	return out


def _category_chain(node):
	"""One classification flattened root-first.

	Amazon nests the ancestry the other way up — the leaf carries a `parent`,
	which carries its own — so the walk collects leaf-first and reverses. The
	guard on `seen` is not paranoia about Amazon: a self-referential parent would
	otherwise spin here forever, and this runs inside a sync.
	"""
	chain = []
	seen = set()
	while isinstance(node, dict):
		node_id = node.get("classificationId")
		name = (node.get("displayName") or "").strip()
		if name:
			chain.append({"id": node_id, "name": name})
		if node_id in seen:
			break
		seen.add(node_id)
		node = node.get("parent")
	chain.reverse()
	return chain


def classifications_from(item, marketplace_id):
	"""The ASIN's browse-node ancestry, flattened to l1/l2/l3 + the leaf node id.

	An ASIN can sit in several browse nodes; the first is taken, which is the one
	Amazon returns as primary.

	The schema has three levels and Amazon's tree is deeper than three in several
	categories. A longer chain keeps its two topmost nodes and its leaf, and drops
	what is between — the levels stay comparable across products, which is what a
	roll-up needs, at the cost of detail no column exists for. `browse_node_id` is
	always the leaf, so nothing has to be reconstructed from the names.
	"""
	empty = {"category_l1": None, "category_l2": None, "category_l3": None, "browse_node_id": None}
	block = _pick_for_marketplace(item.get("classifications"), marketplace_id) or {}
	nodes = block.get("classifications") or []
	if not nodes:
		return empty

	chain = _category_chain(nodes[0])
	if not chain:
		return empty

	if len(chain) > CATALOG_CATEGORY_LEVELS:
		chain = [*chain[: CATALOG_CATEGORY_LEVELS - 1], chain[-1]]

	names = [n["name"] for n in chain]
	names += [None] * (CATALOG_CATEGORY_LEVELS - len(names))
	return {
		"category_l1": names[0],
		"category_l2": names[1],
		"category_l3": names[2],
		"browse_node_id": chain[-1]["id"],
	}


def content_from_item(item, mp):
	"""Normalise one catalog item into the fields of an Amazon Product Listing.

	Content keys (title/brand/description/bullets/keywords/images) may each be
	None/empty — an ASIN can legitimately have no keywords — and the caller must
	not write those gaps over values it already has.

	The variation keys are different: when `relationships` was requested, Amazon
	answers definitively, so "no parent" means standalone rather than unknown.
	listings._apply_variation relies on that distinction.
	"""
	marketplace_id = mp.marketplace_id
	language = mp.get("language")
	attributes = item.get("attributes") or {}
	summary = _pick_for_marketplace(item.get("summaries"), marketplace_id) or {}

	titles = _attr_values(attributes, "item_name", marketplace_id, language)
	descriptions = _attr_values(attributes, "product_description", marketplace_id, language)
	# Brand is one of the few things the summary states outright, so it is
	# readable even when includedData=attributes comes back thin — which is the
	# usual case for an ASIN this seller does not own.
	brands = _attr_values(attributes, "brand", marketplace_id, language)

	images = _images_from(item, marketplace_id)
	if not images:
		# includedData=images omitted, or an ASIN with only a summary thumbnail.
		main_image = summary.get("mainImage") or {}
		if main_image.get("link"):
			images = [{"url": main_image["link"], "is_main": True}]

	product_ids = identifiers_from(item, marketplace_id)

	return {
		"title": (titles[0] if titles else None) or summary.get("itemName") or None,
		"brand": (brands[0] if brands else None) or summary.get("brand") or None,
		"description": descriptions[0] if descriptions else None,
		"bullets": _attr_values(attributes, "bullet_point", marketplace_id, language),
		"keywords": _attr_values(attributes, "generic_keyword", marketplace_id, language),
		"images": images,
		# The whole list, for a caller matching barcodes across channels, and the
		# single pick the register row has one field for.
		"product_ids": product_ids,
		"product_id": product_ids[0]["value"] if product_ids else None,
		"product_id_type": product_ids[0]["type"] if product_ids else None,
		**classifications_from(item, marketplace_id),
		**_variation_from(item, marketplace_id),
	}


def fetch_content(asins, mp, client=None):
	"""{asin: content} for any number of ASINs, batched CATALOG_MAX_IDENTIFIERS per call.

	A failed batch is logged and skipped rather than raised: content is
	supplementary, and a catalog hiccup must not fail an otherwise good
	offer/status sync for every SKU behind it.
	"""
	wanted = list(dict.fromkeys(a for a in (asins or []) if a))
	if not wanted:
		return {}

	client = client or SpApiClient()
	out = {}
	for start in range(0, len(wanted), CATALOG_MAX_IDENTIFIERS):
		batch = wanted[start : start + CATALOG_MAX_IDENTIFIERS]
		params = {
			"marketplaceIds": mp.marketplace_id,
			"identifiers": ",".join(batch),
			"identifiersType": "ASIN",
			"includedData": CATALOG_CONTENT_INCLUDED_DATA,
		}
		try:
			resp = client.get(CATALOG_ITEMS_PATH, params=params, context="listing")
		except SpApiError as e:
			frappe.log_error(
				title="Amazon catalog content fetch failed",
				message=f"ASINs {', '.join(batch)} on {mp.name}: {e}",
			)
			continue
		for item in resp.get("items", []) or []:
			asin = item.get("asin")
			if asin:
				out[asin] = content_from_item(item, mp)
	return out
