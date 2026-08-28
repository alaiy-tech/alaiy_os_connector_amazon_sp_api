# Amazon SP-API connector — frontend contribution

This app's slice of the Alaiy OS frontend. It is **not** a Next.js app of its
own: `src/` overlays path-for-path onto `alaiy_os/interface` at deploy time and
builds as part of that app. See
`alaiy_os/interface/CONNECTOR_TO_BASE_UI_COMPOSITION.md` for the architecture,
and devbench's `UI_COMPOSITION.md` for the mechanism.

```
src/
├── app/
│   ├── (main)/os/channels/amazon/
│   │   ├── listings/            the register: filters, selection, bulk sync/reconcile/publish
│   │   ├── listings/new/        draft a listing Amazon does not have yet, and publish it
│   │   ├── listings/[...sku]/    one SKU: offer, content, issues, family, publish
│   │   └── health/              account-health metrics + recent seller feedback
│   ├── (main)/os/settings/connectors/amazon_sp_api/
│   │                            the seller account, app credentials, order defaults
│   └── amazon-oauth/callback/   where Amazon's consent screen returns to
├── components/amazon/           shared by screens in both trees above
└── lib/amazon/                  the API layer over this app's whitelisted methods
```

## Where it sits in the sidebar

`interface.config.json` contributes its own **Amazon** group, rather than merging
into one of the base's. Amazon is a `channel` connector (`connector_meta.py`) —
this bench *sells* on it — and the base has no channels group to join: its Sales
group is sales documents, and putting a listings register beside Sales Invoices
reads as though it were one.

The routes live under `/os/channels/amazon/` even so, because the group is one
connector's name and the URL space should not be: the next channel connector
(Shopify, Unicommerce — neither ships an `interface/` yet) lands beside this one
at `/os/channels/<its own>/` without anything here moving.

Settings is the exception, and is not in this group at all. It sits at
`/os/settings/connectors/amazon_sp_api`, the namespace the base owns and indexes
under Settings → Connectors, so this connector is configured from the same place
as every other one. The screen is still this app's — those fields need Amazon's
vocabulary — which is why the base exposes a generic API and not a generic form.

## The register is local

Every row on the Listings screen is what the last sync or reconcile wrote.
Opening the screen calls Amazon **not at all**, which is deliberate: a catalog is
up to 1,000 SKUs per marketplace and SP-API's limits are measured in requests per
second.

The two ways to refill it differ in reach, not just speed:

| | reads | cap |
|---|---|---|
| **Sync from Amazon** | full per-listing detail, parentage included | 1,000 SKUs per marketplace |
| **Reconcile** | offer + status columns only | none — the Merchant Listings report |

Both are `frappe.enqueue`d and answer "queued". They emit realtime events when
they finish, but there is no socket.io route through the frontend's proxy to
listen on, so these screens say the job is running and leave Refresh to the
operator rather than polling a thousand-row list on a timer. Worth revisiting if
the base ever proxies realtime.

## Publishing a listing

The detail screen has no Save button, and its absence is the point.
`publish_listing` writes the values it submitted onto the register row and marks
it `pending`, so a publish *is* the save. A local-only save would invent a third
state — edited here, not on Amazon, and on the row indistinguishable from a
pushed change Amazon then rejected — which is exactly what `remote_snapshot`
exists to prevent.

So the flow is preview-then-submit, and the baseline is Amazon rather than the
row:

1. **Preview** (`preview_publish`) — one Listings GET, submits nothing, and
   answers with `exists`, Amazon's live state, and the subset of the form that
   differs.
2. **Publish** (`publish_listing`) — creates the offer when `exists` is false,
   otherwise submits that subset. Amazon applies it asynchronously either way, so
   the row goes `pending` until a sync confirms what actually happened.

`exists` is the field that earns the second endpoint. `compare_listing` *throws*
for a SKU Amazon does not list, which is the ordinary state of every row drafted
here — so the screens could only ever edit listings that already existed. The
preview answers `exists: false` instead, and the dialog then shows what would be
created rather than an empty diff.

A blank field means *no opinion*, never "clear this on Amazon" — see
`diff_from_remote`. Clearing content is not something these screens can express,
deliberately: an empty image set produces no patch ops at all, and reading a
blank field as a delete would let a half-rendered form wipe a live listing.

### A new listing, and publishing several at once

`listings/new` is the other half: the register could previously only be *filled*
by a sync, so the listings you could push were exactly the listings Amazon already
had. The screen finds the ASIN and product type by catalog search — both are
opaque identifiers Amazon rejects a typo in, so they are read off a match rather
than typed — and saves a row with status `incomplete`. Publishing is a separate
button, because a draft is a real register row: it can be corrected, linked to an
Item, given content, or left to go out with the next bulk publish.

Selection on the register makes that bulk publish. It selects the page, not the
result set, because the register pages server-side and the rows not fetched are
not there to select; it clears whenever the filters or the page move, so a button
saying "3 selected" never publishes rows the operator can no longer see.

The job is backgrounded like the two bulk reads, and it has the same problem: no
realtime channel through the proxy to wait on. The answer here is not a toast that
guesses — the outcome is on the rows. `last_publish_error` is why a row did not
go, shown on the row itself and cleared by the next publish that succeeds, so
Refresh is a real report rather than a hope.

A create carries the offer only — price, quantity, condition. Title, description,
bullets, keywords and images belong to whoever owns the ASIN, so the dialog names
them as `content_pending` rather than pretending they went.

The one field that saves on its own is the **catalog Item** link, because it
means nothing to Amazon — it is what stops the next order for that SellerSKU
booking against the unmapped-SKU placeholder. Written through
`/api/resource`; no push involved.

## The OAuth flow, and why there are two callbacks

Amazon's redirect URI is built from `app_url` (falling back to the site URL) —
which decides *which side of the deployment* Amazon comes back to:

```
frontend owns the hostname          Frappe owns the hostname
(serve_root: the commerce shape)    (the additive shape)

  app_url = the OS's origin           app_url = the site, i.e. the Desk
  ↓                                   ↓
  /amazon-oauth/callback              /amazon-oauth/callback
  → this app's page (here)            → the www page in the Frappe app
```

With `serve_root`, nginx sends everything but `/app` to Next, so Frappe's www
callback is **not reachable from a browser at all** and this page is the only one
that can answer. Without it, the www page answers as it always did. Both call
`oauth.complete_authorization`, so there is one implementation of the flow and
neither page decides anything.

The screens never touch a token. `Connect` fetches a consent URL from
`api.get_consent_url` (which issues the single-use, session-bound state) and
navigates to Amazon; the one-time code comes back here and is traded for a
refresh token by `api.complete_oauth`, server-side, using the LWA secret that
lives in `site_config.json` and must never reach a browser.

The Settings screen shows the resolved redirect URI and says whether it is on
this app's origin — the mismatch that actually breaks a deployment is `app_url`
naming a host that serves *neither* callback, and this is where that becomes
visible.

## What the backend had to grow

Additions to `api.py`, and one refactor:

| | why |
|---|---|
| `get_consent_url` | `/amazon-oauth/start` is a www page the OS cannot reach; this is the same thing as data |
| `complete_oauth` | the callback above, server-side |
| `get_config_status` → `redirect_uri` | so a screen can say where consent will land |
| `oauth.complete_authorization` | extracted from the www callback, now shared by both |
| `preview_publish` | `compare_listing` throws for a SKU Amazon does not list; a screen has to be able to ask |
| `publish_listing` | one call for create-or-update, so no screen has to decide which |
| `publish_listings` | the same decision per row, backgrounded, for a selection |
| `draft_listing` | the register could only be filled by a sync; this is how a listing starts here |
| `Amazon Product Listing.last_published_at` / `.last_publish_error` | a bulk publish leaves no run document, so the row is the report |

Everything else these screens do was already whitelisted. The register and the
marketplace table are read through the platform's `/api/resource` proxy, and the
DocType fields on the Settings screen go through the platform's own
registry-driven connector API (`alaiy_os.api.connectors`) — which is generic over
`OS Connector Registry`, so the base still knows nothing about Amazon.

## Known edges

- **`LinkField` is duplicated.** The same component exists in the NayaGlobal
  connector. It is a platform-shaped thing (a Link picker over
  `@alaiy-os/frappe/link`) living in two connectors because the platform does not
  export one yet; the second copy is the argument for promoting it.
- **Saving always tests.** `alaiy_os.api.connectors` offers no save-only call, so
  saving the region on an account that was never authorized runs a connection
  test that can only fail. The screen reads that case and reports "Saved.
  Connect the Amazon account to finish." rather than a red failure.
- **No orders screen.** Orders are ordinary Sales Orders, so they belong on the
  base's own list. Settings carries the defaults they book against, the sync
  watermark and a manual sync; date-range backfill is still Desk-only
  (`api.backfill_orders`).

## Working on it

There is no `npm run dev` here. Compose a workspace and run the base's:

```bash
cd devbench
python3 devbench.py compose commerce
cd builds/commerce && npm run dev   # http://localhost:3000/os/channels/amazon/listings
```

`tsconfig.json` in this directory is for your editor only; the typecheck that
counts is `npx tsc --noEmit` inside a composed workspace.
