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

# region -> default Seller Central consent host. Used to build the OAuth
# consent URL when `amazon_consent_base_url` is not set in site_config.
REGION_CONSENT_HOSTS = {
	"NA": "https://sellercentral.amazon.com",
	"EU": "https://sellercentral-europe.amazon.com",
	"FE": "https://sellercentral.amazon.co.jp",
}

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
DEFAULT_MARKETPLACES = [
	("ATVPDKIKX0DER", "United States", "US", "NA", "USD", "amazon.com"),
	("A2EUQ1WTGCTBG2", "Canada", "CA", "NA", "CAD", "amazon.ca"),
	("A1AM78C64UM0Y8", "Mexico", "MX", "NA", "MXN", "amazon.com.mx"),
	("A1F83G8C2ARO7P", "United Kingdom", "UK", "EU", "GBP", "amazon.co.uk"),
	("A1PA6795UKMFR9", "Germany", "DE", "EU", "EUR", "amazon.de"),
	("A13V1IB3VIYZZH", "France", "FR", "EU", "EUR", "amazon.fr"),
	("APJ6JRA9NG5V4", "Italy", "IT", "EU", "EUR", "amazon.it"),
	("A1RKKUPIHCS9HS", "Spain", "ES", "EU", "EUR", "amazon.es"),
	("A1805IZSGTT6HS", "Netherlands", "NL", "EU", "EUR", "amazon.nl"),
	("A1VC38T7YXB528", "Japan", "JP", "FE", "JPY", "amazon.co.jp"),
	("A39IBJ37TRP1C6", "Australia", "AU", "FE", "AUD", "amazon.com.au"),
]
