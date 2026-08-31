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

/**
 * Amazon's condition codes, in the order `Amazon Product Listing.condition`
 * lists them. Here rather than in a screen because two of them offer the choice
 * — the detail form and the new-listing form — and a select that disagreed with
 * the DocType's options would save a value Frappe then refuses.
 */
export const LISTING_CONDITIONS = [
  "new_new",
  "new_open_box",
  "new_oem",
  "used_like_new",
  "used_very_good",
  "used_good",
  "used_acceptable",
  "collectible_like_new",
  "collectible_very_good",
  "collectible_good",
  "collectible_acceptable",
  "refurbished_refurbished",
] as const;

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
  /** When a publish last submitted this row. Null on a row never published. */
  last_published_at?: string | null;
  /** Why the last publish failed, cleared by the next one that succeeds. */
  last_publish_error?: string | null;
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

// ── publishing ───────────────────────────────────────────────────────────────

/**
 * A publish preview, which is `compare_listing` plus the answer to the question
 * that decides everything else: does Amazon list this SKU at all?
 *
 * `exists: false` means publishing *creates* the offer, so `changes` is empty —
 * a create sends the offer wholesale rather than a diff — and the fields worth
 * reading instead are `blockers` (why it cannot be created yet) and
 * `content_pending` (content a create cannot carry; see `AmazonPublishResult`).
 */
export interface AmazonPublishPreview {
  sku: string;
  marketplace: string;
  /** Amazon has a listing for this SKU. False means a create. */
  exists: boolean;
  action: "create" | "update" | "unchanged";
  /** Null when Amazon has no listing to compare against. */
  remote: AmazonRemoteSnapshot | null;
  listing_status?: string | null;
  /** The identifiers the write declares — off the row on a create, off Amazon otherwise. */
  asin?: string | null;
  product_type?: string | null;
  changes: Partial<Record<AmazonPushField, unknown>>;
  changed: AmazonPushField[];
  content_changed: AmazonPushField[];
  /** Content this row holds that a newly created offer cannot carry. */
  content_pending: AmazonPushField[];
  /** Amazon requirements the row does not meet. Non-empty means it cannot go. */
  blockers: string[];
  /** Not blockers — an offer Amazon takes, but that nobody can buy. */
  warnings: string[];
}

export type AmazonPublishAction = "created" | "updated" | "unchanged" | "failed";

export interface AmazonPublishResult {
  sku: string;
  action: AmazonPublishAction;
  marketplace?: string;
  listing_status?: string | null;
  changed?: AmazonPushField[];
  /** Present on a create: content that reaches Amazon on the next publish. */
  content_pending?: AmazonPushField[];
  issues?: AmazonListingIssue[];
  /** Present only on `failed`. */
  error?: string;
}

/** What the background bulk publish reports, per run and per SKU. */
export interface AmazonPublishSummary {
  success: boolean;
  marketplace?: string | null;
  total: number;
  created: number;
  updated: number;
  unchanged: number;
  failed: number;
  results: AmazonPublishResult[];
}

// ── catalog search + drafting ────────────────────────────────────────────────

/** One `search_catalog` hit: the ASIN an offer would attach to, and its type. */
export interface AmazonCatalogMatch {
  asin: string;
  title?: string | null;
  brand?: string | null;
  image_url?: string | null;
  /** Every Listings write must declare one, so a match without it is unusable. */
  product_type?: string | null;
}

/** `suggest_product_type` answers a list because a title is genuinely ambiguous. */
export interface AmazonProductTypeSuggestion {
  product_type: string;
  display_name: string;
}

/** What `draft_listing` takes: a register row for a listing not yet on Amazon. */
export interface AmazonDraftListing {
  sku: string;
  asin?: string;
  product_type?: string;
  title?: string;
  brand?: string;
  description?: string;
  price?: number;
  quantity?: number;
  condition?: string;
  marketplace?: string;
  fulfillment_channel?: string;
  product?: string;
  bullet_points?: string[];
  keywords?: string[];
  images?: AmazonPushImage[];
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

/** A queued job over a known set of rows — the count is what the toast says. */
export interface AmazonQueuedCount extends AmazonQueued {
  count: number;
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

/**
 * What creating a *catalog entry* would submit — a different thing from
 * publishing an offer, and the preview says so by what it carries.
 *
 * There is no `changes` and no `remote`: nothing exists on Amazon to diff
 * against, which is the whole reason this path exists. `blockers` is therefore
 * the substance of the answer — each entry names a requirement of the product
 * type and what to do about it — and `attributes` is the payload a ready row
 * would send, so the operator agrees to a submission rather than to a button.
 */
export interface AmazonAsinCreatePreview {
  sku: string;
  marketplace: string;
  product_type?: string | null;
  listing_status?: AmazonListingStatus | null;
  /** The Amazon attribute payload, keyed by attribute name. */
  attributes: Record<string, unknown>;
  /** Attribute names this product type requires, from Amazon's own schema. */
  required: string[];
  blockers: string[];
  warnings: string[];
  ready: boolean;
}

/**
 * The answer to a creation. No ASIN in it — Amazon accepts the submission before
 * it mints one, so `submission_id` is what identifies the wait and the row sits
 * at `pending` until the submission reconciler settles it.
 */
export interface AmazonAsinCreateResult {
  sku: string;
  action: "submitted";
  submission_id?: string | null;
  listing_status?: AmazonListingStatus | null;
  issues?: AmazonListingIssue[];
}
