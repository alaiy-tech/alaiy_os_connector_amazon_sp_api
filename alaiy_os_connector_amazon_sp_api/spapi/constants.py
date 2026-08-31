# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Static SP-API metadata: endpoints, report types, and health-metric definitions."""

# --- LWA (Login With Amazon) ------------------------------------------------
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Refresh the cached access token this many seconds before it actually expires,
# so an in-flight request never races the expiry.
ACCESS_TOKEN_EXPIRY_BUFFER = 60

# --- Regional SP-API endpoints ----------------------------------------------
# region -> API base host. Drives Amazon Connection.endpoint.
REGION_ENDPOINTS = {
	"NA": "https://sellingpartnerapi-na.amazon.com",
	"EU": "https://sellingpartnerapi-eu.amazon.com",
	"FE": "https://sellingpartnerapi-fe.amazon.com",
}

# region -> SP-API sandbox host (used when amazon_use_sandbox is set).
SANDBOX_ENDPOINTS = {
	"NA": "https://sandbox.sellingpartnerapi-na.amazon.com",
	"EU": "https://sandbox.sellingpartnerapi-eu.amazon.com",
	"FE": "https://sandbox.sellingpartnerapi-fe.amazon.com",
}

# region -> default Seller Central consent host. Used to build the OAuth
# consent URL when `amazon_consent_base_url` is not set in site_config.
REGION_CONSENT_HOSTS = {
	"NA": "https://sellercentral.amazon.com",
	"EU": "https://sellercentral-europe.amazon.com",
	"FE": "https://sellercentral.amazon.co.jp",
}

# --- Catalog & Listings Items APIs ------------------------------------------
CATALOG_ITEMS_PATH = "/catalog/2022-04-01/items"
LISTINGS_ITEMS_BASE = "/listings/2021-08-01/items"

# searchDefinitionsProductTypes (Product Type Definitions API 2020-09-01). A
# different API from the two above: it searches Amazon's registry of product
# types rather than the catalog of products, which is why it can answer for a
# product Amazon has never seen.
PRODUCT_TYPE_DEFINITIONS_PATH = "/definitions/2020-09-01/productTypes"

# How many suggestions the title look-up hands back. Amazon returns its list
# best-match-first and unranked beyond that; past a handful the tail is noise
# an operator won't read.
PRODUCT_TYPE_SUGGESTION_LIMIT = 10

# getDefinitionsProductType returns the JSON Schema a product type's attributes
# must satisfy. `LISTING` is the full set a *create* has to meet;
# LISTING_OFFER_ONLY is the reduced set an offer against someone else's ASIN
# meets, which is why publishing an offer never needed this call.
PRODUCT_TYPE_DEFINITION_REQUIREMENTS = "LISTING"

# The definition response carries a *link* to the schema, not the schema — a
# presigned URL on Amazon's own storage, fetched without SP-API credentials.
PRODUCT_TYPE_SCHEMA_TIMEOUT = 60

# Schemas are large (hundreds of KB) and change on Amazon's release cadence, not
# ours, so they are cached per product type + marketplace. A day is short enough
# that a schema change reaches us the next morning and long enough that a bulk
# creation across one product type pays for the fetch once.
PRODUCT_TYPE_SCHEMA_CACHE_TTL = 24 * 60 * 60


# --- accepted-but-not-yet-applied submissions --------------------------------
# How long a submission is left alone before the reconciler first re-reads it.
# Amazon has plausibly not processed it yet inside this window, so an earlier
# read spends rate limit to be told 404.
SUBMISSION_GRACE_MINUTES = 15

# When an accepted submission stops being late and starts being lost. Amazon
# gives no deadline; a creation that has not produced a readable listing in this
# long has, in practice, been rejected somewhere the API does not report.
SUBMISSION_MAX_AGE_HOURS = 24

# Rows re-read per scheduled run. Each costs a Listings GET plus a catalog
# look-up, and the job runs every 15 minutes, so this is a rate-limit budget
# rather than a correctness limit — the backlog drains across runs.
SUBMISSION_RECONCILE_BATCH = 100

# searchCatalogItems accepts at most 20 values in `identifiers`, which is what
# lets spapi.catalog fetch content for a whole page of listings in one call.
CATALOG_MAX_IDENTIFIERS = 20

# What spapi.catalog needs to reconstruct a listing's content: attributes carry
# product_description / bullet_point / generic_keyword, images carry the variant
# set, summaries carry itemName as a fallback title, relationships carry the
# variation family (parent/child ASINs + theme).
CATALOG_CONTENT_INCLUDED_DATA = "summaries,attributes,images,relationships"

# The relationship type that describes a variation family. The same array also
# carries PACKAGE_HIERARCHY, which is a different thing entirely.
CATALOG_VARIATION_RELATIONSHIP = "VARIATION"

# How many SKUs one reconcile run will enrich from the catalog. Content and
# parentage are fetched once per row (see Amazon Product Listing.catalog_synced_at)
# rather than on every run, so a steady-state reconcile spends nothing here — the
# cap exists to keep the *first* run over a large catalog inside the job timeout.
# At CATALOG_MAX_IDENTIFIERS per request this is 100 requests, and whatever it
# defers is picked up by the next run.
RECONCILE_CATALOG_BUDGET = 2000

# Locale for human-readable issue messages returned by the Listings API.
DEFAULT_ISSUE_LOCALE = "en_US"

# fulfillment_channel_code sent in the fulfillment_availability attribute.
# DEFAULT = merchant-fulfilled (MFN); AMAZON = FBA (quantity managed by Amazon).
FULFILLMENT_CHANNEL_CODES = {
	"DEFAULT": "DEFAULT",
	"AMAZON": "AMAZON_NA",
}

# --- Orders API (v0) --------------------------------------------------------
ORDERS_PATH = "/orders/v0/orders"

# Shared placeholder Item for order lines whose SellerSKU isn't linked to
# anything in the catalog. One placeholder for all of them, deliberately — a
# stub Item per unknown SKU would fill the catalog with things that look like
# real, sellable products. Non-stock, so it never demands inventory that
# doesn't exist. Overridable per site via Amazon Connection.orders_fallback_item.
UNMAPPED_ITEM_CODE = "Amazon Unmapped Item"

# Value written to Sales Order.sales_channel. The field itself belongs to
# alaiy_os, not to this app: it answers "which channel did this order come
# from" for every connector, so core defines it once and each connector writes
# its own name into it. This constant is our answer, nothing more.
SALES_CHANNEL = "Amazon"

# Orders API page size (1-100). Amazon returns fewer than this freely, so the
# loop must key off NextToken, not a short page.
ORDERS_PAGE_SIZE = 100
ORDERS_MAX_PAGES = 200  # safety cap: 20k orders in one run

# Amazon does not reliably return orders updated in the last ~2 minutes, so
# every window stops short of "now" and the watermark advances only to that
# capped end — otherwise orders landing in the blind spot are skipped forever.
ORDERS_RECENT_BLIND_SPOT = 120  # seconds

# Re-read this far behind the watermark on each run. LastUpdatedAfter is
# inclusive-ish and clock skew is real; re-reading is free because the upsert
# is idempotent on AmazonOrderId.
ORDERS_SYNC_OVERLAP = 300  # seconds

# How far back the first-ever sync reaches when `orders_sync_from` is unset.
# Kept deliberately short: the first run is the one most likely to surface a
# misconfiguration (wrong customer, unmapped SKUs), and a narrow window makes
# that cheap to inspect and undo. Reach further back with `orders_sync_from`,
# or with the manual backfill, once the first run looks right.
ORDERS_DEFAULT_LOOKBACK_DAYS = 1

# A backfill is walked in chunks: Amazon degrades badly on very wide
# LastUpdatedAfter/Before windows for high-volume sellers.
ORDERS_BACKFILL_CHUNK_DAYS = 7

# getOrderItems is rate-limited at 0.5 rps (burst 30) — far tighter than
# getOrders. One item call per order will exhaust the burst on any real
# catalog, so pace them rather than relying on 429-retry alone.
ORDER_ITEMS_MIN_INTERVAL = 2.0  # seconds between getOrderItems calls

# Amazon OrderStatus -> what the Sales Order should be.
# Pending is deliberately draft: Amazon withholds buyer-visible pricing while
# an order is Pending, so ItemPrice is routinely absent and any total we
# computed now would be wrong.
ORDER_STATUS_DRAFT = ("Pending",)
ORDER_STATUS_SUBMIT = (
	"Unshipped",
	"PartiallyShipped",
	"Shipped",
	"InvoiceUnconfirmed",
	"PendingAvailability",
)
ORDER_STATUS_CANCEL = ("Canceled", "Unfulfillable")

# --- Report types (Reports API 2021-06-30) ----------------------------------
REPORT_SELLER_PERFORMANCE = "GET_V2_SELLER_PERFORMANCE_REPORT"
REPORT_SELLER_FEEDBACK = "GET_SELLER_FEEDBACK_DATA"
REPORT_MERCHANT_LISTINGS_ALL = "GET_MERCHANT_LISTINGS_ALL_DATA"
REPORT_MERCHANT_LISTINGS_FYP = "GET_MERCHANTS_LISTINGS_FYP_REPORT"

# --- Reports polling --------------------------------------------------------
REPORT_POLL_INTERVAL = 6  # seconds between status polls
REPORT_POLL_TIMEOUT = 180  # give up after this many seconds

# --- Rate limit / retry -----------------------------------------------------
MAX_RETRIES = 4
BACKOFF_BASE = 1.0  # seconds; doubled each attempt, plus jitter

# --- Account-health metric definitions --------------------------------------
# One row per tracked metric. `target` is the Amazon policy limit;
# `higher_is_better` flips the ok/warn/critical comparison. `section` groups
# the metric on the dashboard.
HEALTH_METRICS = [
	{
		"metric_key": "orderDefectRate",
		"metric_label": "Order Defect Rate (ODR)",
		"metric_target": 1.0,
		"higher_is_better": 0,
		"section": "customer_service",
	},
	{
		"metric_key": "invoiceDefectRate",
		"metric_label": "Invoice Defect Rate",
		"metric_target": 1.0,
		"higher_is_better": 0,
		"section": "customer_service",
	},
	{
		"metric_key": "lateShipmentRate",
		"metric_label": "Late Shipment Rate",
		"metric_target": 4.0,
		"higher_is_better": 0,
		"section": "shipping",
	},
	{
		"metric_key": "preFulfillmentCancellationRate",
		"metric_label": "Pre-Fulfillment Cancel Rate",
		"metric_target": 2.5,
		"higher_is_better": 0,
		"section": "shipping",
	},
	{
		"metric_key": "validTrackingRate",
		"metric_label": "Valid Tracking Rate",
		"metric_target": 95.0,
		"higher_is_better": 1,
		"section": "shipping",
	},
	{
		"metric_key": "onTimeDeliveryRate",
		"metric_label": "On-Time Delivery Rate",
		"metric_target": 97.0,
		"higher_is_better": 1,
		"section": "shipping",
	},
	{
		"metric_key": "unitOnTimeDeliveryRate",
		"metric_label": "Unit On-Time Delivery Rate",
		"metric_target": 97.0,
		"higher_is_better": 1,
		"section": "shipping",
	},
]

# metric_key -> definition, for quick lookup during parsing.
HEALTH_METRICS_BY_KEY = {m["metric_key"]: m for m in HEALTH_METRICS}

# Overall account-health rollup states (worst-metric wins).
HEALTH_STATUS_NORMAL = "NORMAL"
HEALTH_STATUS_AT_RISK = "AT_RISK"
HEALTH_STATUS_DEACTIVATED = "DEACTIVATED"
HEALTH_STATUS_UNKNOWN = "UNKNOWN"

# Default marketplaces seeded on install: (marketplace_id, country, code, region, currency, domain)
# (marketplace_id, country, country_code, region, currency, domain, language_tag)
# language_tag is Amazon's default listing language for that marketplace; it is
# sent as `language_tag` on text attributes (title/description/bullets/keywords).
DEFAULT_MARKETPLACES = [
	("ATVPDKIKX0DER", "United States", "US", "NA", "USD", "amazon.com", "en_US"),
	("A2EUQ1WTGCTBG2", "Canada", "CA", "NA", "CAD", "amazon.ca", "en_CA"),
	("A1AM78C64UM0Y8", "Mexico", "MX", "NA", "MXN", "amazon.com.mx", "es_MX"),
	("A1F83G8C2ARO7P", "United Kingdom", "UK", "EU", "GBP", "amazon.co.uk", "en_GB"),
	("A1PA6795UKMFR9", "Germany", "DE", "EU", "EUR", "amazon.de", "de_DE"),
	("A13V1IB3VIYZZH", "France", "FR", "EU", "EUR", "amazon.fr", "fr_FR"),
	("APJ6JRA9NG5V4", "Italy", "IT", "EU", "EUR", "amazon.it", "it_IT"),
	("A1RKKUPIHCS9HS", "Spain", "ES", "EU", "EUR", "amazon.es", "es_ES"),
	("A1805IZSGTT6HS", "Netherlands", "NL", "EU", "EUR", "amazon.nl", "nl_NL"),
	("A1C3SOZRARQ6R3", "Poland", "PL", "EU", "PLN", "amazon.pl", "pl_PL"),
	("A2NODRKZP88ZB9", "Sweden", "SE", "EU", "SEK", "amazon.se", "sv_SE"),
	("AMEN7PMS3EDWL", "Belgium", "BE", "EU", "EUR", "amazon.com.be", "fr_BE"),
	("A28R8C7NBKEWEA", "Ireland", "IE", "EU", "EUR", "amazon.ie", "en_IE"),
	("A21TJRUUN4KGV", "India", "IN", "EU", "INR", "amazon.in", "en_IN"),
	("A2VIGQ35RCS4UG", "United Arab Emirates", "AE", "EU", "AED", "amazon.ae", "en_AE"),
	("A17E79C6D8DWNP", "Saudi Arabia", "SA", "EU", "SAR", "amazon.sa", "ar_SA"),
	("ARBP9OOSHTCHU", "Egypt", "EG", "EU", "EGP", "amazon.eg", "ar_EG"),
	("A33AVAJ2PDY3EV", "Turkey", "TR", "EU", "TRY", "amazon.com.tr", "tr_TR"),
	("A2Q3Y263D00KWC", "Brazil", "BR", "NA", "BRL", "amazon.com.br", "pt_BR"),
	("A19VAU5U5O7RUS", "Singapore", "SG", "FE", "SGD", "amazon.sg", "en_SG"),
	("A1VC38T7YXB528", "Japan", "JP", "FE", "JPY", "amazon.co.jp", "ja_JP"),
	("A39IBJ37TRP1C6", "Australia", "AU", "FE", "AUD", "amazon.com.au", "en_AU"),
]
