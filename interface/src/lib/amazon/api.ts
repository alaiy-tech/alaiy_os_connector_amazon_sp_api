import type {
  AmazonCompareResult,
  AmazonConfigStatus,
  AmazonConnectionStatus,
  AmazonConsentUrl,
  AmazonDesiredListing,
  AmazonHealthSummary,
  AmazonListingDoc,
  AmazonListingRow,
  AmazonListingStatus,
  AmazonMarketplace,
  AmazonOauthResult,
  AmazonOrdersSyncStatus,
  AmazonPushField,
  AmazonQueued,
  AmazonTestResult,
  AmazonUpdateResult,
  AmazonVariationFamily,
} from "./types";

/**
 * The data layer over this connector's whitelisted methods and its register.
 *
 * Everything goes through the platform's own `/api/method` and `/api/resource`
 * proxies, so these run from the browser as same-origin requests: the `sid`
 * cookie stays first-party and the proxy attaches the CSRF token to mutations
 * itself. Do not add an `X-Frappe-CSRF-Token` header here — there is no token to
 * read on this origin.
 *
 * Unlike a connector whose endpoints answer `{"success": false, ...}`, most of
 * this app's `frappe.throw` instead: an unconnected account, a 403 from Amazon
 * and a rejected listing all arrive as exceptions, with the message buried in
 * `_server_messages`. `AmazonError` is that message dug back out, so a caller
 * writes one catch and gets something worth showing a user.
 *
 * The exceptions to that are the endpoints whose *answer* is a verdict —
 * `test_connection` and `complete_oauth` — which return `{success, message}` and
 * are not errors when `success` is false. They keep their own return types.
 */

const METHOD_ROOT = "alaiy_os_connector_amazon_sp_api.api";
const LISTING_DOCTYPE = "Amazon Product Listing";

export class AmazonError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "AmazonError";
    this.status = status;
  }

  /** A permission refusal — the fix is a role, not a retry. */
  get isForbidden(): boolean {
    return this.status === 403;
  }
}

/** The message to show for anything thrown out of this module. */
export function amazonErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof AmazonError && error.message) return error.message;
  return fallback;
}

type FrappeEnvelope<T> = {
  message?: T;
  _server_messages?: string;
  exception?: string;
  exc_type?: string;
};

/**
 * Frappe wraps a thrown message in `_server_messages` — a JSON string of JSON
 * strings — and puts the traceback's last line in `exception`. Prefer the former:
 * it is what `frappe.throw` was given and therefore the only part written for a
 * person to read.
 */
function serverMessage(payload: FrappeEnvelope<unknown> | null): string | null {
  if (!payload) return null;
  try {
    const messages = JSON.parse(payload._server_messages ?? "[]") as string[];
    const first = messages.map((entry) => JSON.parse(entry) as { message?: string }).find((entry) => entry.message);
    if (first?.message) return first.message.replace(/<[^>]+>/g, "").trim();
  } catch {
    // Not the shape we hoped for — fall through to the raw exception line.
  }
  return payload.exception ?? null;
}

async function unwrap<T>(res: Response, fallback: string): Promise<T> {
  const text = await res.text();
  let payload: FrappeEnvelope<T> | null = null;
  try {
    payload = JSON.parse(text) as FrappeEnvelope<T>;
  } catch {
    payload = null;
  }

  if (!res.ok) throw new AmazonError(serverMessage(payload) ?? `${fallback} (${res.status}).`, res.status);
  if (payload?.message === undefined) throw new AmazonError(serverMessage(payload) ?? fallback, res.status);
  return payload.message;
}

type QueryValue = string | number | undefined | null;

function methodUrl(method: string, params?: Record<string, QueryValue>): string {
  const url = `/api/method/${METHOD_ROOT}.${method}`;
  if (!params) return url;

  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const search = query.toString();
  return search ? `${url}?${search}` : url;
}

async function get<T>(method: string, params?: Record<string, QueryValue>, fallback = "Request failed"): Promise<T> {
  return unwrap<T>(await fetch(methodUrl(method, params), { cache: "no-store" }), fallback);
}

async function post<T>(method: string, args: Record<string, unknown> = {}, fallback = "Request failed"): Promise<T> {
  const res = await fetch(methodUrl(method), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
    cache: "no-store",
  });
  return unwrap<T>(res, fallback);
}

// ── connection ───────────────────────────────────────────────────────────────

/** Status, resolved region/endpoint and primary marketplace. Never the token. */
export function fetchConnectionStatus(): Promise<AmazonConnectionStatus> {
  return get<AmazonConnectionStatus>("get_connection_status", undefined, "Could not read the Amazon connection.");
}

/** Which `site_config` keys are set, plus the redirect URI Amazon must know. */
export function fetchConfigStatus(): Promise<AmazonConfigStatus> {
  return get<AmazonConfigStatus>("get_config_status", undefined, "Could not read the app credentials.");
}

/**
 * Amazon's consent URL, with a freshly issued single-use state.
 *
 * Fetched at click time rather than with the page: the state expires in ten
 * minutes, and a tab left open over lunch would otherwise send the operator to a
 * consent screen that can only fail on the way back.
 */
export function fetchConsentUrl(): Promise<AmazonConsentUrl> {
  return get<AmazonConsentUrl>("get_consent_url", undefined, "Could not start the Amazon authorization.");
}

/** Finish the round trip. Amazon's own query parameters, forwarded verbatim. */
export function completeOauth(params: {
  spapi_oauth_code?: string | null;
  state?: string | null;
  selling_partner_id?: string | null;
  error?: string | null;
  error_description?: string | null;
}): Promise<AmazonOauthResult> {
  return post<AmazonOauthResult>("complete_oauth", params, "Could not complete the Amazon authorization.");
}

/** Clear the stored refresh token. The register rows are left alone. */
export function disconnectAmazon(): Promise<{ status: string }> {
  return post<{ status: string }>("disconnect", {}, "Could not disconnect the Amazon account.");
}

/** Re-run the connector's own test and update the registry's status with it. */
export function testConnection(): Promise<AmazonTestResult> {
  return get<AmazonTestResult>("test_connection", undefined, "Could not test the Amazon connection.");
}

// ── marketplaces ─────────────────────────────────────────────────────────────

const MARKETPLACE_FIELDS = ["name", "marketplace_id", "country", "country_code", "region", "currency", "domain"];

/**
 * Every marketplace on the bench, for the pickers.
 *
 * Read straight off the DocType rather than through a method: it is a reference
 * table seeded at install, small enough to fetch whole, and the generic Link
 * search would only match the opaque marketplace id — not the country anyone
 * would actually type.
 */
export async function fetchMarketplaces(): Promise<AmazonMarketplace[]> {
  const query = new URLSearchParams();
  query.set("fields", JSON.stringify(MARKETPLACE_FIELDS));
  query.set("order_by", "country asc");
  query.set("limit_page_length", "0");

  const res = await fetch(`/api/resource/${encodeURIComponent("Amazon Marketplace")}?${query.toString()}`, {
    cache: "no-store",
  });
  const data = await unwrapResource<AmazonMarketplace[]>(res, "Could not load the Amazon marketplaces.");
  return data;
}

// ── the register ─────────────────────────────────────────────────────────────

const LISTING_ROW_FIELDS = [
  "name",
  "sku",
  "title",
  "asin",
  "product_type",
  "brand",
  "product",
  "marketplace",
  "listing_status",
  "fulfillment_channel",
  "is_variation_parent",
  "parent_asin",
  "variation_theme",
  "price",
  "currency",
  "quantity",
  "last_synced_at",
];

export interface ListingQuery {
  search?: string;
  status?: AmazonListingStatus | "";
  marketplace?: string;
  /** Restrict to one variation family. */
  parentAsin?: string;
  /** Rows whose `product` link is empty — the SKUs an order sync had to guess at. */
  unmappedOnly?: boolean;
  page: number;
  pageSize: number;
}

export interface ListingPage {
  rows: AmazonListingRow[];
  total: number;
}

/**
 * The REST API's `filters`/`or_filters` are ANDed and ORed respectively, and one
 * request cannot mix them freely: `or_filters` alone would drop the status and
 * marketplace constraints. So the search term is the only OR group, and it is
 * sent as `or_filters` *with* the rest as `filters` — which Frappe combines as
 * `(filters) AND (or_filters)`, the thing we want.
 */
function listingFilters(query: ListingQuery): {
  filters: Array<[string, string, unknown]>;
  orFilters: Array<[string, string, unknown]>;
} {
  const filters: Array<[string, string, unknown]> = [];
  if (query.status) filters.push(["listing_status", "=", query.status]);
  if (query.marketplace) filters.push(["marketplace", "=", query.marketplace]);
  if (query.parentAsin) filters.push(["parent_asin", "=", query.parentAsin]);
  if (query.unmappedOnly) filters.push(["product", "is", "not set"]);

  const term = (query.search ?? "").trim();
  const orFilters: Array<[string, string, unknown]> = term
    ? [
        ["sku", "like", `%${term}%`],
        ["title", "like", `%${term}%`],
        ["asin", "like", `%${term}%`],
      ]
    : [];

  return { filters, orFilters };
}

export async function fetchListings(query: ListingQuery): Promise<ListingPage> {
  const { filters, orFilters } = listingFilters(query);

  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(LISTING_ROW_FIELDS));
  if (filters.length) params.set("filters", JSON.stringify(filters));
  if (orFilters.length) params.set("or_filters", JSON.stringify(orFilters));
  params.set("order_by", "modified desc");
  params.set("limit_start", String((query.page - 1) * query.pageSize));
  params.set("limit_page_length", String(query.pageSize));

  const countParams = new URLSearchParams();
  countParams.set("doctype", LISTING_DOCTYPE);
  if (filters.length) countParams.set("filters", JSON.stringify(filters));
  if (orFilters.length) countParams.set("or_filters", JSON.stringify(orFilters));

  // Both together: a page of rows is useless for pagination without the count,
  // and the count is a separate endpoint in Frappe's REST API.
  const [rows, total] = await Promise.all([
    fetch(`/api/resource/${encodeURIComponent(LISTING_DOCTYPE)}?${params.toString()}`, { cache: "no-store" }).then(
      (res) => unwrapResource<AmazonListingRow[]>(res, "Could not load the Amazon listings."),
    ),
    fetch(`/api/method/frappe.client.get_count?${countParams.toString()}`, { cache: "no-store" })
      .then((res) => unwrap<number>(res, "Could not count the Amazon listings."))
      .catch(() => 0),
  ]);

  return { rows, total: Number(total) || 0 };
}

/** One register row in full, child tables included. */
export async function fetchListing(sku: string): Promise<AmazonListingDoc> {
  const res = await fetch(`/api/resource/${encodeURIComponent(LISTING_DOCTYPE)}/${encodeURIComponent(sku)}`, {
    cache: "no-store",
  });
  return unwrapResource<AmazonListingDoc>(res, `Could not load the listing for ${sku}.`);
}

/**
 * Link a SKU to a catalog Item, locally.
 *
 * The one field on this row that means nothing to Amazon: it is how an order
 * whose SellerSKU fell back to the placeholder item gets pointed at the real one
 * next time. Written through the REST API precisely because it is *not* a push —
 * nothing here reaches Seller Central.
 */
export async function setListingProduct(sku: string, product: string | null): Promise<AmazonListingDoc> {
  // A cleared picker sends `""`, which Frappe would store as an empty Link rather
  // than an unset one; null is what actually clears it.
  const item = (product ?? "").trim();
  const res = await fetch(`/api/resource/${encodeURIComponent(LISTING_DOCTYPE)}/${encodeURIComponent(sku)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ product: item === "" ? null : item }),
    cache: "no-store",
  });
  return unwrapResource<AmazonListingDoc>(res, "Could not link that Item.");
}

/** Frappe's REST API answers `{data: ...}`, not `{message: ...}` like a method. */
async function unwrapResource<T>(res: Response, fallback: string): Promise<T> {
  const text = await res.text();
  let payload: (FrappeEnvelope<unknown> & { data?: T }) | null = null;
  try {
    payload = JSON.parse(text) as FrappeEnvelope<unknown> & { data?: T };
  } catch {
    payload = null;
  }

  if (!res.ok) throw new AmazonError(serverMessage(payload) ?? `${fallback} (${res.status}).`, res.status);
  if (payload?.data === undefined) throw new AmazonError(serverMessage(payload) ?? fallback, res.status);
  return payload.data;
}

// ── listing operations (these do reach Amazon) ───────────────────────────────

/** Re-fetch one SKU from Amazon and refresh its row. Blocking — one API call. */
export function syncListing(sku: string, marketplace?: string): Promise<unknown> {
  return post<unknown>("sync_listing", { sku, marketplace }, `Could not sync ${sku} from Amazon.`);
}

/**
 * Pull every listing for the marketplace into the register. Backgrounded.
 *
 * Capped at Amazon's own 1,000 SKUs per marketplace — beyond that, reconcile.
 */
export function syncAllListings(marketplace?: string): Promise<AmazonQueued> {
  return post<AmazonQueued>("sync_all_listings", { marketplace }, "Could not start the listing sync.");
}

/** The Merchant Listings report instead: every SKU, no cap, slower. Backgrounded. */
export function reconcileListings(marketplace?: string): Promise<AmazonQueued> {
  return post<AmazonQueued>("reconcile_listings", { marketplace }, "Could not start the reconcile.");
}

/** End the offer on Amazon. The row survives, inactive. */
export function deleteListing(sku: string, marketplace?: string): Promise<unknown> {
  return post<unknown>("delete_listing", { sku, marketplace }, `Could not end the listing for ${sku}.`);
}

/**
 * What a push would change — Amazon's live state against the form's.
 *
 * Read-only, and the baseline is Amazon rather than the row: `update_listing`
 * writes submitted values onto the row and marks it pending, so a listing Amazon
 * rejected still reads locally as though it went through.
 */
export function compareListing(
  sku: string,
  desired: AmazonDesiredListing,
  marketplace?: string,
): Promise<AmazonCompareResult> {
  return post<AmazonCompareResult>(
    "compare_listing",
    { sku, desired, marketplace },
    "Could not read the live listing from Amazon.",
  );
}

/** Submit the diff `compareListing` produced. Amazon applies it asynchronously. */
export function updateListing(
  sku: string,
  changes: Partial<Record<AmazonPushField, unknown>>,
  marketplace?: string,
): Promise<AmazonUpdateResult> {
  return post<AmazonUpdateResult>("update_listing", { sku, changes, marketplace }, "Amazon refused the update.");
}

/** Every SKU this seller lists under one parent ASIN. Local read, no API call. */
export function fetchVariationFamily(parentAsin: string, marketplace?: string): Promise<AmazonVariationFamily> {
  return get<AmazonVariationFamily>(
    "variation_family",
    { parent_asin: parentAsin, marketplace },
    "Could not load the variation family.",
  );
}

// ── account health ───────────────────────────────────────────────────────────

export function fetchHealthSummary(marketplace?: string): Promise<AmazonHealthSummary> {
  return get<AmazonHealthSummary>("get_health_summary", { marketplace }, "Could not load the account health.");
}

/** On-demand health sync. Blocking, and slow enough to warrant a spinner. */
export function syncHealth(marketplace?: string): Promise<unknown> {
  return post<unknown>("sync_health", { marketplace }, "Could not sync the account health from Amazon.");
}

// ── orders ───────────────────────────────────────────────────────────────────

export function fetchOrdersSyncStatus(): Promise<AmazonOrdersSyncStatus> {
  return get<AmazonOrdersSyncStatus>("get_orders_sync_status", undefined, "Could not read the order sync status.");
}

/** Pull orders updated since the watermark into Sales Orders. Backgrounded. */
export function syncOrders(marketplace?: string): Promise<AmazonQueued> {
  return post<AmazonQueued>("sync_orders", { marketplace }, "Could not start the order sync.");
}
