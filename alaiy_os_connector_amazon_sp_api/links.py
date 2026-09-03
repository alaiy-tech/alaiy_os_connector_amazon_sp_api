# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Amazon URLs, for a caller that has been asked for a link rather than for data.

Everything else in this app answers with ids and fields. A person who wants to
*see* a listing wants one of two pages, and they are not interchangeable:

    https://www.<domain>/dp/<asin>                     the buyer's product page
    https://<seller central>/skucentral?mSku=<sku>      the seller's own editor

The buyer page routes on the **ASIN**, so a SKU alone cannot build one — the
register row's `asin` is the bridge, and a row Amazon has never confirmed has
none. The seller page routes on the **SKU**, which every register row has by
definition (it is the row's name), so it is the link that always exists. For a
suppressed listing it is also the more useful of the two: whatever reported the
suppression cannot fix it, and this is where a person goes to.

No HTTP call and nothing read from Amazon — the ids are already on the register
row and the hosts are configuration, so a link costs a string format. This is
deliberately not built inside `spapi/`: everything there talks to Amazon, and a
reader who found a URL builder among those modules would reasonably assume this
one did too.

## Why a missing piece is an absence and not a fallback

`Amazon Marketplace.domain` is seeded for every marketplace this app knows
(constants.DEFAULT_MARKETPLACES), so a blank one means a hand-made row. The
tempting fallback — assume `amazon.com` — hands a person a page on a storefront
where the listing they own may not exist at all. Same for an ASIN the register
does not have: there is no id to guess. A broken link is worse than a stated
absence, because it looks like the answer was known.

The Seller Central host comes from `REGION_CONSENT_HOSTS` rather than from
`app_config.consent_base_url`, which layers the `amazon_consent_base_url`
site_config override on top of it. That override exists to point the *OAuth
consent flow* somewhere else — a sandbox, a proxy — and a deployment that has set
it would otherwise get UI deep links into whatever it names.

Nothing here checks that the ASIN or SKU is real beyond the register look-up a
SKU already needs. An ASIN passed straight through is trusted: verifying it would
mean a catalog round trip for a string this module can already build, and
`compare_listing` is the call for "does Amazon have this".
"""

from urllib.parse import quote, urlencode

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api import app_config as config
from alaiy_os_connector_amazon_sp_api.spapi.constants import REGION_CONSENT_HOSTS

# The buyer-facing product page. `/dp/<asin>` is the canonical short form and
# redirects to the full slug, so it needs nothing but the ASIN.
_BUYER_PATH = "dp"

# Seller Central's per-SKU page — the listing editor, keyed by the seller's own
# SKU and the marketplace it is listed in. The inventory list would need neither,
# but it also would not open the listing being asked about.
_SELLER_CENTRAL_PATH = "skucentral"


def _marketplace(marketplace=None, connection=None):
	"""The Amazon Marketplace doc to build links for, and the connection.

	Returns (None, conn) rather than throwing when no marketplace is resolvable:
	"there is no link" is an answer this module is allowed to give, and the
	caller is the one that has to say so.
	"""
	conn = connections.resolve(connection)
	marketplace = marketplace or conn.primary_marketplace
	if not marketplace or not frappe.db.exists("Amazon Marketplace", marketplace):
		return None, conn
	return frappe.get_cached_doc("Amazon Marketplace", marketplace), conn


def _domain(mp):
	"""The storefront host, without a `www.` a hand-made row may have included."""
	domain = (mp.domain or "").strip().rstrip("/") if mp else ""
	return domain.removeprefix("www.")


def product_url(asin, marketplace=None):
	"""The buyer-facing product page for one ASIN, or None if it cannot be built.

	None means either no ASIN or no storefront domain for the marketplace — both
	are "there is no link", which is what the caller has to report.
	"""
	asin = str(asin or "").strip()
	mp, _conn = _marketplace(marketplace)
	domain = _domain(mp)
	if not (asin and domain):
		return None
	return f"https://www.{domain}/{_BUYER_PATH}/{quote(asin, safe='')}"


def seller_central_url(sku, marketplace=None):
	"""Seller Central's own page for one SKU, or None if it cannot be built.

	The marketplace id goes in the query string because Seller Central resolves a
	SKU per marketplace; without it the page opens on whichever the account was
	last looking at, which for a multi-marketplace seller is the wrong listing.
	"""
	sku = str(sku or "").strip()
	mp, conn = _marketplace(marketplace)
	host = REGION_CONSENT_HOSTS.get(config.resolve_region(conn.region))
	if not (sku and host and mp and mp.marketplace_id):
		return None
	query = urlencode({"mSku": sku, "marketplaceID": mp.marketplace_id})
	return f"{host.rstrip('/')}/{_SELLER_CENTRAL_PATH}?{query}"


def listing_link(sku=None, asin=None, marketplace=None):
	"""Both links for one listing, and why either of them is missing.

	Takes a SKU, an ASIN, or both. A SKU is looked up in the register to find its
	ASIN — the buyer page needs one and the row is where it lives — so a SKU alone
	is enough for both links whenever the sync has confirmed the ASIN.

	An ASIN alone gives the buyer page only. Nothing maps an ASIN back to one of
	this seller's SKUs: several of them may list against the same ASIN, and
	picking one would link to the wrong offer.

	`found` is about the register, not about Amazon: false means this bench has no
	row for that SKU, which is also why there is no seller-central link for it.
	"""
	sku = str(sku or "").strip()
	asin = str(asin or "").strip()
	if not (sku or asin):
		frappe.throw(_("A SKU or an ASIN is required to build a link."))

	mp, _conn = _marketplace(marketplace)
	row = None
	if sku:
		filters = {"name": sku}
		if marketplace:
			filters["marketplace"] = marketplace
		row = frappe.db.get_value(
			"Amazon Product Listing",
			filters,
			["name", "title", "asin", "listing_status"],
			as_dict=True,
		)
		asin = asin or (row or {}).get("asin") or ""

	result = {
		"sku": sku or None,
		"found": bool(row) if sku else None,
		"title": (row or {}).get("title"),
		"listing_status": (row or {}).get("listing_status"),
		"asin": asin or None,
		"marketplace": mp.name if mp else None,
		"country": mp.country if mp else None,
		"product_url": product_url(asin, marketplace=marketplace) if asin else None,
		"seller_central_url": seller_central_url(sku, marketplace=marketplace) if sku else None,
	}

	# One line saying which link is absent and why, because the absence is the
	# part a caller would otherwise report as a failure.
	notes = []
	if not mp:
		notes.append("No marketplace is set on the Amazon connection, so no link can be built.")
	elif not _domain(mp):
		notes.append(
			f"Marketplace {mp.name} has no storefront domain set, so there is no "
			"product page link."
		)
	if sku and not row:
		notes.append(f"No listing row for SKU {sku} on this bench.")
	elif sku and not asin:
		notes.append(
			f"SKU {sku} has no ASIN yet, so it has no buyer-facing product page — "
			"only the Seller Central link."
		)
	if not sku:
		notes.append(
			"Without a SKU there is no Seller Central link: an ASIN does not identify "
			"one offer."
		)
	result["note"] = " ".join(notes) or None

	return result
