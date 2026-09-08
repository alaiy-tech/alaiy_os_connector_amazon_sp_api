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
# Role-free: usable to verify a token before any SP-API role is granted,
# and the only way to learn which marketplaces a seller participates in —
# the OAuth callback carries none.
MARKETPLACE_PARTICIPATIONS_PATH = "/sellers/v1/marketplaceParticipations"

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
# `identifiers` carries the UPC/EAN/GTIN Amazon holds for the ASIN, and
# `classifications` the browse-node ancestry. Both are catalog facts rather than
# seller contributions, which is exactly why they have to come from here:
# `externally_assigned_product_identifier` in a *listing's* attributes is only
# populated for a seller who created the ASIN, so for a reseller — most sellers —
# it is simply absent, and matching a catalogue on it silently matches nothing.
CATALOG_CONTENT_INCLUDED_DATA = "summaries,attributes,images,relationships,identifiers,classifications"

# Which product identifier wins when Amazon returns several for one ASIN, most
# specific first. It usually returns both an EAN and the UPC inside it — the
# same barcode, one zero-padded to 13 digits — so a single stored value has to
# pick, and EAN is the one that survives that padding without ambiguity.
#
# Consumers matching across channels should compare the *full* list from
# `identifiers_from` rather than this pick: a Shopify barcode holding the UPC
# and an Amazon EAN of the same product differ by a leading zero and are not
# equal as strings.
PRODUCT_ID_PREFERENCE = ("EAN", "UPC", "GTIN", "ISBN")

# How deep a browse-node chain we flatten into l1/l2/l3. Amazon's tree is
# deeper than three in several categories; the schema has three columns, so a
# longer chain keeps its two topmost nodes and its leaf and drops the middle.
CATALOG_CATEGORY_LEVELS = 3

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

# --- FBA Inventory API (v1) -------------------------------------------------
# The only endpoint that knows what Amazon is holding. A Listings item's
# `fulfillmentAvailability` is the quantity the *seller* declared, which for an
# FBA SKU is nothing at all — see FULFILLMENT_CHANNEL_CODES above, where AMAZON
# is annotated "quantity managed by Amazon".
FBA_INVENTORY_SUMMARIES_PATH = "/fba/inventory/v1/summaries"

# getInventorySummaries is granular per marketplace and takes no other
# granularity in practice, though the parameter exists for future ones.
FBA_INVENTORY_GRANULARITY = "Marketplace"

# Pages are Amazon-sized (no pageSize parameter on this endpoint), so the cap is
# on page count: a safety rail against a paging bug spinning a worker, not a
# limit any real catalogue reaches.
FBA_INVENTORY_MAX_PAGES = 200

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

# --- Sales API (v1) ---------------------------------------------------------
SALES_ORDER_METRICS_PATH = "/sales/v1/orderMetrics"

# Amazon offers Hour, Day, Week, Month, Year and Total. Hour is left out because
# it caps the interval at 30 days and answers a question nobody asks a sales
# report; Year is left out because a Total over an explicit period says the same
# thing without a calendar boundary to explain.
SALES_GRANULARITIES = ("Day", "Week", "Month", "Total")

# Amazon's own ceiling for every granularity above (Hour, unused here, is 30
# days). Checked before the call so an over-wide period comes back as a sentence
# rather than as an SP-API 400.
SALES_MAX_INTERVAL_DAYS = 730

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

# --- Product Fees API (v0) --------------------------------------------------
# What Amazon will take out of a sale, *before* it happens. The Finances API
# says what it actually took, but only once the order has settled — which for a
# sale made this week is somewhere between two and four weeks away. A margin
# figure that waited for settlement would be blank on exactly the products a
# seller is currently deciding about, so both are read and every fee this app
# reports carries which of the two it came from.
FEES_ESTIMATE_BATCH_PATH = "/products/fees/v0/feesEstimate"

# Amazon's own ceiling on one batch request.
FEES_ESTIMATE_BATCH_SIZE = 20

# getMyFeesEstimates is rate-limited at 0.5 requests a second with a burst of
# 1 — the tightest limit in this app, tighter even than getOrderItems. The
# client's 429-retry alone would spend a whole sync backing off, so batches are
# paced by this rather than by the backoff.
FEES_ESTIMATE_MIN_INTERVAL = 2.0  # seconds between batch calls

# Amazon returns one FeeDetail per charge with no grouping, and the set differs
# per marketplace and per fulfilment channel. These are the buckets this app
# reports, because they are the three a seller reasons about: what Amazon takes
# for the sale, what it takes for the shipping, and everything else.
#
# ReferralFee is the commission. VariableClosingFee and PerItemFee are separate
# charges Amazon levies on media and on individual-plan sellers respectively;
# they are commission in every sense that matters to a margin, so they land in
# the same bucket rather than in "other" where nobody would look for them.
FEE_TYPES_REFERRAL = ("ReferralFee", "VariableClosingFee", "PerItemFee")

# The FBA family. `FBAFees` is the roll-up Amazon sends for most marketplaces,
# with the pick-pack and weight-handling components nested inside it under
# IncludedFeeDetailList; the per-unit and per-order names are what older
# marketplaces send instead. Summing the roll-up *and* its own components would
# double-count, which is why `fees.py` never descends into IncludedFeeDetailList.
FEE_TYPES_FBA = (
	"FBAFees",
	"FBAFulfillmentFee",
	"FBAPerUnitFulfillmentFee",
	"FBAPerOrderFulfillmentFee",
	"FBAWeightBasedFee",
	"FBATransportationFee",
)

# Amazon needs telling which fee schedule to quote. A SKU fulfilled by Amazon is
# charged the FBA schedule and a merchant-fulfilled one is not, and asking for
# the wrong one does not error — it returns a confidently wrong number.
FEES_FBA_PROGRAM = "FBA_CORE"

# --- Product Pricing API (2022-05-01) ---------------------------------------
# The competitive read. v0's getItemOffers still works, but 2022-05-01 is where
# Amazon's development went and it answers in one batch what v0 answers per
# ASIN — so a page of listings costs one call rather than twenty.
COMPETITIVE_SUMMARY_PATH = "/batches/products/pricing/2022-05-01/items/competitiveSummary"

# Amazon's ceiling on one competitiveSummary batch.
COMPETITIVE_SUMMARY_BATCH_SIZE = 20

# What to ask for per ASIN. `featuredBuyingOptions` carries the Buy Box winner's
# price, which is the only way to learn what a seller is being beaten by;
# `referencePrices` carries Amazon's own competitive and list prices.
COMPETITIVE_SUMMARY_INCLUDED_DATA = ("featuredBuyingOptions", "referencePrices")

# The buying option Amazon calls the Buy Box. It sends others (used, subscribe
# and save) in the same array, and treating the first entry as the Buy Box price
# would compare a new-condition listing against a used offer.
FEATURED_OFFER_BUYING_OPTION = "New"

# How stale a competitive price is allowed to get before it is worth nothing.
# The Buy Box changes within hours, so a price snapshot from yesterday is
# history rather than a decision input — see the cadence note in
# `alaiy_os_self_serve_apis.selfserve.profitability`.
COMPETITIVE_PRICE_MAX_AGE_HOURS = 6

# --- Buy Box win rate ------------------------------------------------------
# Not a pricing endpoint at all, and this is the single most-missed fact about
# Buy Box data on SP-API: `getCompetitiveSummary` says who holds the Buy Box
# *right now*, and nothing anywhere says what share of the day a seller held it.
# That figure — the one a seller means by "Buy Box win rate" — exists only in the
# Sales & Traffic business report, per ASIN, as `buyBoxPercentage`.
#
# So the win rate is a report and the competitor price is an API call, on
# different cadences, and this app never derives one from the other.
REPORT_SALES_AND_TRAFFIC = "GET_SALES_AND_TRAFFIC_REPORT"

# The report's own granularity vocabulary. DAY is the finest it offers per ASIN.
SALES_AND_TRAFFIC_GRANULARITY = "DAY"

# --- Finances API (2024-06-19) ----------------------------------------------
# The settled truth. `listTransactions` replaced the twenty-odd event-type
# arrays of `/finances/v0/financialEvents` with one shape, which is why this app
# reads fee actuals here and keeps the v0 call only for the two counters
# `spapi.health` needs from it.
TRANSACTIONS_PATH = "/finances/2024-06-19/transactions"

# Page cap. A month of transactions for a busy seller runs to thousands of rows
# and the caller wants a fee total, not the ledger — this is the rail that keeps
# a paging bug from spinning a worker.
TRANSACTIONS_MAX_PAGES = 50

# The transaction types that carry a sale's fees. Amazon files a refund's fee
# reversal under `Refund` with positive amounts, so a fee total that swept up
# every type would net a refunded referral fee against a charged one and report
# less commission than was paid.
TRANSACTION_TYPES_SALE = ("Shipment", "Order")

# The breakdown the settled feed files fees under, and the names inside it.
# Amazon's settled vocabulary is not its estimate vocabulary: the commission a
# fee estimate calls `ReferralFee` arrives here as `Commission`. Both spellings
# are listed rather than mapped, because a marketplace sending the estimate's
# name into the settled feed should still land in the referral bucket.
TRANSACTION_FEES_BREAKDOWN = "Fees"
SETTLED_FEE_REFERRAL_NAMES = (
	"Commission",
	"ReferralFee",
	"VariableClosingFee",
	"PerItemFee",
)

# Matched as a prefix, unlike the estimate side's exact tuple. The settled feed
# carries a long tail of FBA charge names that differ per marketplace and grows
# whenever Amazon adds a programme (`FBAPerUnitFulfillmentFee`,
# `FBAWeightBasedFee`, `FBADisposalFee`, …). An exact list would silently file
# next quarter's new fee name under "other"; every one of them is a fulfilment
# charge and belongs in the same bucket.
SETTLED_FEE_FBA_PREFIX = "FBA"

# The context Amazon attaches a product to a transaction item with. Without it a
# fee is real money against an order but cannot be attributed to a SKU, which is
# the grain the whole margin table is computed at.
TRANSACTION_PRODUCT_CONTEXT = "ProductContext"
# --- Orders API 2026-01-01: packages ----------------------------------------
# Tracking and carrier data, which `spapi.orders` has always said it
# "deliberately not touched" — because until this version there was no way to
# read it. v0 returns an order and its items and nothing about the shipment that
# carried them; the only route to a tracking number was the flat-file all-orders
# report. 2026-01-01 adds `packages` to the order itself.
#
# The version is a constant rather than inline for a specific reason: this is
# the newest surface anything here calls, and a marketplace or an app that has
# not been moved onto it answers with a 400 rather than an empty array. One
# place to change, and `packages.py` reports that refusal as itself rather than
# as "no packages".
ORDERS_API_VERSION_PACKAGES = "2026-01-01"
ORDERS_PACKAGES_BASE = f"/orders/{ORDERS_API_VERSION_PACKAGES}/orders"

# What to ask `getOrder` to include. Packages are opt-in: the call returns the
# order without them and does not hint that more was available.
ORDERS_INCLUDED_DATA_PACKAGES = "PACKAGES"

# Merchant-fulfilled only. Amazon does not put an FBA order's packages here —
# those are Amazon's own shipments and come from the fulfilled-shipments report
# below. A sync that asked this endpoint for AFN tracking would get empty
# arrays and conclude the seller ships nothing late.
FULFILLMENT_CHANNEL_MERCHANT = "MFN"
FULFILLMENT_CHANNEL_AMAZON = "AFN"

# --- FBA fulfilled shipments report -----------------------------------------
# The AFN half of the same picture: carrier, tracking number, ship date and
# estimated arrival for every shipment Amazon made on the seller's behalf.
#
# Chosen over Fulfillment Outbound's `getPackageTrackingDetails`, which answers
# for one package number at a time — a day's FBA orders would be hundreds of
# calls against a rate limit, to assemble what one report already contains.
# That endpoint is the right tool for chasing a single package and the wrong one
# for a dashboard.
REPORT_FBA_SHIPMENTS = "GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL"

# Amazon rejects a request for this report wider than about two months, and
# degrades well before that. The caller walks a longer backfill in chunks.
FBA_SHIPMENTS_MAX_WINDOW_DAYS = 30

# Amazon's package-status vocabulary, normalised. The right-hand values are this
# app's own and are what every consumer switches on, because the shape of a
# fulfilment problem is the same whoever carried it: in flight, arrived, or
# stopped somewhere it should not have.
#
# `UNKNOWN` is a real answer and not a parsing failure — a status Amazon has
# added that is not in this map must not silently become "in transit", which is
# the reassuring option and the wrong one.
PACKAGE_STATUS_MAP = {
	"PENDING": "pending",
	"LABEL_PURCHASED": "pending",
	"SHIPPED": "in_transit",
	"IN_TRANSIT": "in_transit",
	"OUT_FOR_DELIVERY": "in_transit",
	"DELIVERING": "in_transit",
	"DELIVERED": "delivered",
	"AVAILABLE_FOR_PICKUP": "delivered",
	"UNDELIVERABLE": "exception",
	"RETURNING": "returned",
	"RETURNED": "returned",
	"LOST": "lost",
	"DAMAGED": "exception",
	"REJECTED": "exception",
	"CANCELLED": "cancelled",
	"CANCELED": "cancelled",
}
