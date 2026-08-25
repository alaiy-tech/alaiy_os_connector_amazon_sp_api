/**
 * The payload shapes this connector's whitelisted methods and DocTypes return.
 *
 * `alaiy_os_connector_amazon_sp_api/api.py` and the DocType JSON are the
 * contract, not this file. Two different sources are represented here and they
 * behave differently:
 *
 *   * **Register rows** (`Amazon Product Listing` and friends) come from Frappe's
 *     REST API, so their fields exist but are routinely null — a listing synced
 *     from the Merchant Listings report has no parentage, an offer-only listing
 *     has no product type.
 *   * **Live reads** (`remote_snapshot`, health, catalog search) come from Amazon
 *     through SP-API and can omit anything at all.
 *
 * Which is why almost everything below is optional. A field that is *never*
 * absent is left required, so that the few load-bearing ones stand out.
 */

// ── connection ───────────────────────────────────────────────────────────────

/** `Amazon Connection.last_status`. "not_configured" also covers "never tried". */
export type AmazonConnectionState = "not_configured" | "connected" | "error";

export interface AmazonConnectionStatus {
  status: AmazonConnectionState | string;
  message?: string | null;
  /** A refresh token is stored. Not the same as "the last check passed". */
  connected: boolean;
  selling_partner_id?: string | null;
  /** Resolved, not raw: site_config's `amazon_region` overrides the DocType's. */
  region?: string | null;
  endpoint?: string | null;
  consent_base_url?: string | null;
  use_sandbox: boolean;
  app_status?: "Draft" | "Published" | string | null;
  connected_at?: string | null;
  /** The `Amazon Marketplace` docname, which *is* the marketplace id. */
  primary_marketplace?: string | null;
  primary_marketplace_id?: string | null;
}

/** One `site_config.json` key the app's identity depends on. */
export interface AmazonConfigKey {
  key: string;
  label: string;
  required: boolean;
  secret: boolean;
  is_set: boolean;
  description: string;
  /** Present for non-secret keys only — a secret reports `is_set` and nothing else. */
  value?: string | null;
}

export interface AmazonConfigStatus {
  /** Every *required* key is set, so the OAuth flow can start. */
  ready: boolean;
  keys: AmazonConfigKey[];
  /** What Seller Central must have registered. Derived from `app_url`. */
  redirect_uri: string;
}

export interface AmazonConsentUrl {
  url: string;
  redirect_uri: string;
}

export interface AmazonOauthResult {
  success: boolean;
  message: string;
  /** The connection's status after the attempt; null if nothing was stored. */
  status: AmazonConnectionState | string | null;
  selling_partner_id?: string | null;
}

export interface AmazonTestResult {
  success: boolean;
  message: string;
}

// ── marketplaces ─────────────────────────────────────────────────────────────

/**
 * `Amazon Marketplace` is autonamed `field:marketplace_id`, so `name` is the
 * opaque id ("A21TJRUUN4KGV") and `country` is the part anyone recognises. Every
 * picker in these screens shows the country and keys on the name.
 */
export interface AmazonMarketplace {
  name: string;
  marketplace_id: string;
  country?: string | null;
  country_code?: string | null;
  region?: string | null;
  currency?: string | null;
  domain?: string | null;
}

// ── listings ─────────────────────────────────────────────────────────────────

export type AmazonListingStatus = "active" | "inactive" | "suppressed" | "incomplete" | "pending";

export type AmazonFulfillmentChannel = "DEFAULT" | "AMAZON";

/** The columns the register table reads. `name` is the SKU (autonamed `field:sku`). */
export interface AmazonListingRow {
  name: string;
  sku: string;
  title?: string | null;
  asin?: string | null;
  product_type?: string | null;
  /** Read from Amazon and never pushed back — the ASIN's owner sets it. */
  brand?: string | null;
  /** The catalog `Item` this SKU books against. Empty is the thing to fix. */
  product?: string | null;
  marketplace?: string | null;
  listing_status?: AmazonListingStatus | string | null;
  fulfillment_channel?: AmazonFulfillmentChannel | string | null;
  is_variation_parent?: 0 | 1;
  parent_asin?: string | null;
  variation_theme?: string | null;
  price?: number | null;
  currency?: string | null;
  quantity?: number | null;
  last_synced_at?: string | null;
}

export interface AmazonListingIssue {
  code?: string | null;
  severity?: "ERROR" | "WARNING" | "INFO" | string | null;
  message?: string | null;
  attribute_names?: string | null;
}

/** A row of the `images` child table. Amazon's own ordering, main image first. */
export interface AmazonListingImage {
  image_url: string;
  is_main?: 0 | 1;
}

/** The whole document, child tables included — what `/api/resource/<sku>` gives. */
export interface AmazonListingDoc extends AmazonListingRow {
  condition?: string | null;
  description?: string | null;
  parent_listing?: string | null;
  catalog_synced_at?: string | null;
  images?: AmazonListingImage[];
  bullet_points?: Array<{ bullet?: string | null }>;
  keywords?: Array<{ keyword?: string | null }>;
  suppression_reasons?: AmazonListingIssue[];
}

/** An image as the push endpoints take it — flat, and main-first after normalising. */
export interface AmazonPushImage {
  url: string;
  is_main: boolean;
}

/**
 * The operator's intended state for a SKU, as `compare_listing` takes it.
 *
 * Every field is optional and a blank one means *no opinion*, never "clear this
 * on Amazon" — see `diff_from_remote`. Clearing content is not something these
 * screens can express, deliberately: it would let a half-rendered form wipe a
 * live listing.
 */
export interface AmazonDesiredListing {
  title?: string;
  price?: number;
  quantity?: number;
  condition?: string;
  description?: string;
  bullet_points?: string[];
  keywords?: string[];
  images?: AmazonPushImage[];
}

/** What Amazon holds right now, keyed like a register row. The push baseline. */
export interface AmazonRemoteSnapshot extends AmazonDesiredListing {
  sku?: string;
  asin?: string | null;
  product_type?: string | null;
  brand?: string | null;
  listing_status?: string | null;
  currency?: string | null;
  fulfillment_channel?: string | null;
  issues?: AmazonListingIssue[];
}

/** The fields a push can carry, in the order `compare_listing` reports them. */
export type AmazonPushField =
  | "title"
  | "price"
  | "quantity"
  | "condition"
  | "description"
  | "bullet_points"
  | "keywords"
  | "images";

export interface AmazonCompareResult {
  sku: string;
  marketplace: string;
  listing_status?: string | null;
  remote: AmazonRemoteSnapshot;
  /** Feed this straight to `updateListing` — it is already the diff. */
  changes: Partial<Record<AmazonPushField, unknown>>;
  changed: AmazonPushField[];
  /** The subset of `changed` that is product content rather than offer data. */
  content_changed: AmazonPushField[];
}

export interface AmazonUpdateResult {
  sku: string;
  listing_status?: string | null;
  issues?: AmazonListingIssue[];
}

export interface AmazonVariationChild {
  sku: string;
  title?: string | null;
  asin?: string | null;
  listing_status?: string | null;
  price?: number | null;
  quantity?: number | null;
  variation_theme?: string | null;
}

export interface AmazonVariationFamily {
  parent_asin: string;
  /** Null whenever the seller has no SKU of their own for the parent — usual. */
  parent_sku?: string | null;
  parent_title?: string | null;
  variation_theme?: string | null;
  children: AmazonVariationChild[];
  child_count: number;
}

/** What a background job answers: it was queued, not that it finished. */
export interface AmazonQueued {
  queued: boolean;
}

// ── account health ───────────────────────────────────────────────────────────

export type AmazonHealthStatus = "ok" | "warn" | "critical" | "unknown";

export interface AmazonHealthMetric {
  metric_key: string;
  metric_label?: string | null;
  metric_value?: number | null;
  metric_target?: number | null;
  /** Whether clearing the target means being above it or below it. */
  higher_is_better?: 0 | 1;
  section?: "customer_service" | "shipping" | string | null;
  health_status?: AmazonHealthStatus | string | null;
  finances_guarantees?: number | null;
  finances_chargebacks?: number | null;
  synced_at?: string | null;
  marketplace?: string | null;
}

export interface AmazonSellerFeedback {
  order_id?: string | null;
  rating?: number | null;
  comment?: string | null;
  feedback_date?: string | null;
}

export interface AmazonHealthSummary {
  overall_status: AmazonHealthStatus | string;
  marketplace?: string | null;
  metrics: AmazonHealthMetric[];
  feedback: AmazonSellerFeedback[];
  /** The newest `synced_at` across the metrics; null before the first sync. */
  synced_at?: string | null;
}

// ── orders ───────────────────────────────────────────────────────────────────

export interface AmazonOrdersSyncStatus {
  /** A Default Customer is set. The scheduled job stays dormant until it is. */
  configured: boolean;
  last_sync_at?: string | null;
  synced_orders: number;
}

// ── helpers ──────────────────────────────────────────────────────────────────

/** Child tables come back as rows; the editors and the push want plain strings. */
export function bulletTexts(doc: AmazonListingDoc | null): string[] {
  return (doc?.bullet_points ?? []).map((row) => row.bullet ?? "").filter((text) => text.trim() !== "");
}

export function keywordTexts(doc: AmazonListingDoc | null): string[] {
  return (doc?.keywords ?? []).map((row) => row.keyword ?? "").filter((text) => text.trim() !== "");
}

export function pushImages(doc: AmazonListingDoc | null): AmazonPushImage[] {
  return (doc?.images ?? [])
    .filter((row) => (row.image_url ?? "").trim() !== "")
    .map((row) => ({ url: row.image_url, is_main: row.is_main === 1 }));
}
