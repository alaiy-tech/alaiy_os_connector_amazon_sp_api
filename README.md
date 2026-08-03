# Alaiy OS Connector — Amazon SP-API

> A [Frappe](https://frappeframework.com/) app that connects [AlaiyOS](https://github.com/alaiy-tech) to the [Amazon Selling Partner API](https://developer-docs.amazon.com/sp-api/) — manage listings, track account health, and keep listing state in sync, all from within Desk.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Frappe](https://img.shields.io/badge/Frappe-v16-informational)](https://frappeframework.com/)
[![Python](https://img.shields.io/badge/Python-3.14%2B-blue)](https://www.python.org/)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Connecting a Seller Account](#connecting-a-seller-account)
- [Usage](#usage)
- [Scheduled Tasks](#scheduled-tasks)
- [Security](#security)
- [Contributing](#contributing)
- [License](#license)

## Overview

`alaiy_os_connector_amazon_sp_api` registers itself with the AlaiyOS OS Connector
Registry as an Amazon **channel** connector. It handles the full OAuth handshake
with Login with Amazon, stores only the returned (encrypted) refresh token, and
exposes server-side, role-guarded entry points for catalog and account-health
operations. No SP-API calls are ever made from the browser.

## Features

- **Login with Amazon OAuth** — sellers authorize via Amazon's consent screen; the app never handles a long-lived seller secret.
- **Account health monitoring** — syncs health metrics and recent seller feedback per marketplace, with an overall status roll-up.
- **Listing management** — catalog search (ASIN + product type), create/update/delete offers, and per-SKU or full-catalog sync.
- **Order sync** — polls Seller Central and materialises orders directly as **Sales Orders**, idempotent on the Amazon order id, with a manual date-range backfill.
- **Multi-region support** — `NA` / `EU` / `FE` region groups, with sandbox and custom-endpoint overrides.
- **Auditable** — every SP-API call is captured in the **SP-API Log** DocType.
- **Scheduled sync** — daily health sync, hourly connection preflight, and a periodic listing reconcile hook.

## Architecture

| Module | Responsibility |
| --- | --- |
| `api.py` | Whitelisted entry points for Desk/JS (all access is server-side and role-guarded). |
| `oauth.py`, `www/amazon_oauth_*` | Login with Amazon consent + callback flow. |
| `app_config.py` | Resolves credentials, region, endpoint, and consent host from `site_config.json`. |
| `spapi/` | SP-API client: `auth`, `client`, `listings`, `orders`, `health`, `reports`, `constants`. |
| `tasks.py` | Scheduled jobs (health sync, connection refresh, listing reconcile, order sync). |
| `connector_meta.py` | Registration metadata for the AlaiyOS OS Connector Registry. |

### DocTypes

| DocType | Purpose |
| --- | --- |
| **Amazon Connection** (single) | Connection settings, region, primary marketplace, and connection status. |
| **Amazon Marketplace** | Reference list of Amazon marketplaces and their IDs. |
| **Amazon Listing** | Register of managed listings and their state. |
| **Amazon Listing Issue** | Issues reported by Amazon against a listing. |
| **Account Health Metric** | Synced account-health metrics per marketplace. |
| **Seller Feedback** | Recent seller feedback pulled from Amazon. |
| **SP-API Log** | Audit log of every SP-API request/response. |

Orders deliberately have **no DocType of their own** — they are created as
ERPNext **Sales Orders** carrying `amazon_order_id`, `amazon_order_status`,
`amazon_marketplace`, `amazon_fulfillment_channel`, `amazon_order_total`, and
`amazon_last_updated_at` (custom fields installed by `install.py`, plus
`amazon_order_item_id` / `amazon_seller_sku` / `amazon_asin` on Sales Order
Item — per line, since one order routinely spans several ASINs).

`install.py` also adds **`sales_channel`** to Sales Order — a deliberately
*generic* field (this connector writes `Amazon`) so every channel connector
reports origin through one field instead of adding a competing one. It sits in
the main section next to Order Type, shows as a list column, and is available
as a standard filter. Orders raised directly in AlaiyOS leave it blank.

## Requirements

- A [Frappe Bench](https://github.com/frappe/bench) v16 environment
- Python 3.14+
- An approved [Amazon SP-API application](https://developer-docs.amazon.com/sp-api/docs/registering-your-application) (Login with Amazon credentials)

## Installation

Install with the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/alaiy-tech/alaiy_os_connector_sp_api 
bench install-app alaiy_os_connector_amazon_sp_api
```

## Configuration

App identity (the SP-API *application's* own credentials) lives in
`site_config.json` — never in DocTypes and never in the browser. The seller
never pastes a long-lived secret; they authorize via Amazon's consent screen
and only the returned refresh token is stored (encrypted).

Set the keys with `bench set-config`:

```bash
bench --site <site> set-config amazon_lwa_client_id     "amzn1.application-oa2-client...."
bench --site <site> set-config amazon_lwa_client_secret "amzn1.oa2-cs...."
bench --site <site> set-config amazon_sp_app_id         "amzn1.sp.solution...."
# Optional:
bench --site <site> set-config amazon_app_beta 1                 # Draft app -> version=beta on consent (default: 1)
bench --site <site> set-config amazon_consent_base_url "https://sellercentral.amazon.com"  # else region default
bench --site <site> set-config app_url "https://erp.example.com" # else the site URL; used to build the OAuth redirect
```

| Key | Required | Purpose |
| --- | --- | --- |
| `amazon_lwa_client_id` | yes | Login with Amazon application client id |
| `amazon_lwa_client_secret` | yes | Login with Amazon application client secret |
| `amazon_sp_app_id` | yes | SP-API application id (consent screen) |
| `amazon_app_beta` | no | `1` while the app is in Draft (adds `version=beta`) |
| `amazon_consent_base_url` | no | Seller Central consent host; falls back to the region default |
| `app_url` | no | Base URL for the OAuth redirect URI; falls back to the site URL |
| `amazon_region` | no | `NA` / `EU` / `FE`; overrides the region on the Amazon Connection |
| `amazon_endpoint` | no | Full SP-API base URL; overrides the region/sandbox default |
| `amazon_use_sandbox` | no | `1` to call the SP-API sandbox host for the region |

**SP-API target resolution.** The API base URL is resolved as: `amazon_endpoint`
(if set) → the sandbox host when `amazon_use_sandbox=1` → otherwise the region
default. Region is `amazon_region` if set, else the Amazon Connection's region.
The consent host is `amazon_consent_base_url` if set, else the region default.
The resolved endpoint/consent host are shown on the Amazon Connection form and
returned by `api.get_connection_status`.

Example — an **India** (amazon.in) seller. India is in the **EU** region group
and consents on its local Seller Central:

```bash
bench --site <site> set-config amazon_region "EU"
bench --site <site> set-config amazon_consent_base_url "https://sellercentral.amazon.in"
# then set the Amazon Connection's Primary Marketplace to India (A21TJRUUN4KGV).
```

Register the redirect URL in Seller Central → Develop Apps as
`{app_url}/amazon-oauth/callback`.

## Connecting a Seller Account

1. Open the **Amazon Connection** form in Desk. It shows which required
   `site_config` keys are still missing and only enables **Connect** once all
   are set (see `alaiy_os_connector_amazon_sp_api.app_config`).
2. Set the **Region** and **Primary Marketplace**.
3. Click **Connect** — you are redirected to Amazon's consent screen.
4. After authorizing, Amazon redirects back to `{app_url}/amazon-oauth/callback`
   and the encrypted refresh token is stored.
5. Use **Test Connection** (or the OS Settings → Connectors panel) to verify.

## Usage

All operations run server-side through whitelisted methods in
[`api.py`](alaiy_os_connector_amazon_sp_api/api.py), guarded by the
`System Manager` or `Amazon Manager` role. Key entry points:

| Method | Description |
| --- | --- |
| `get_connection_status` | Current status (never exposes the token). |
| `get_connect_url` / `disconnect` | Start OAuth / clear the stored token. |
| `ping` / `test_connection` | Verify the connection via a preflight. |
| `sync_health` / `get_health_summary` | Sync and read account-health metrics + feedback. |
| `search_catalog` | Search the Amazon catalog for an ASIN + product type. |
| `create_listing` / `update_listing` / `delete_listing` | Manage offers for a SKU. |
| `sync_listing` / `sync_all_listings` | Refresh one listing, or the whole catalog (background job). |
| `sync_orders` / `backfill_orders` | Pull orders into Sales Orders (background job). |
| `get_orders_sync_status` | Watermark + count of orders synced so far. |

`sync_all_listings` runs in the background (a catalog can be up to 1,000 SKUs)
and emits the `amazon_sync_all_complete` realtime event to the caller when done.

### Order sync

Set **Default Customer** (and optionally Company, Warehouse, Price List,
Unmapped SKU Item) under **Orders** on the Amazon Connection first — the
scheduled job stays dormant until a customer is set. Buyer info is a restricted SP-API endpoint, so all
orders book against that one customer; the buyer stays traceable via
`amazon_order_id`.

- **Idempotent.** The upsert keys on `amazon_order_id`, so the overlapping poll
  windows and any backfill can re-read the same order safely.
- **The sync position never skips an order.** `Last Orders Sync At` advances to
  the end of the window only if every order in it landed. If Amazon returned an
  order that wasn't imported (an error, or no placeholder to fall back to), the
  position is held one second *behind* that order so the next run picks it up
  again. The run reports `watermark_held_at` and the order ids. A permanently
  failing order therefore pins the position and each run re-reads from it —
  deliberately, because a stuck-and-visible sync beats one that silently steps
  over orders. Fix the cause, or move **Last Orders Sync At** on by hand.
- **Amazon's status drives the docstatus.** `Pending` lands as a draft (Amazon
  withholds pricing while an order is Pending), shipped/unshipped statuses are
  submitted, and a cancellation cancels the Sales Order — unless a Delivery Note
  or Sales Invoice already exists against it, in which case the conflict is
  logged and the documents are left alone.
- **Unmapped SKUs don't block the order.** A `SellerSKU` that isn't linked to a
  catalog Item still imports: the line books against a shared placeholder
  (**Unmapped SKU Item** on the connection, or an auto-created non-stock
  `Amazon Unmapped Item`), carrying Amazon's own title and the real SKU on the
  row. The sync summary and the Error Log name every SKU that fell back, so you
  can set the **Product** field on the matching Amazon Listing afterwards.
  Already-imported orders are *not* re-pointed automatically. No Item is ever
  auto-created per SKU — that would fill the catalog with stubs that look
  sellable. An order is only refused outright if even the placeholder can't be
  resolved.
- **Scope is the order header plus line items.** Shipping charges, Amazon fees,
  and settlements are not mapped yet, so `amazon_order_total` (Amazon's own
  figure) can legitimately differ from the Sales Order grand total — the field
  is there to make that gap visible rather than hide it.

Both entry points run in the background and emit `amazon_orders_sync_complete`.
Buttons live under **Amazon** on the Sales Order list view.

## Scheduled Tasks

Defined in [`tasks.py`](alaiy_os_connector_amazon_sp_api/tasks.py); all no-op
cleanly when the connection is not configured.

| Schedule | Job | Description |
| --- | --- | --- |
| Daily | `sync_health` | Refresh account-health metrics + feedback for the primary marketplace. |
| Hourly | `refresh_connection_status` | Ping preflight and update `last_status`. |
| Every 6h | `reconcile_listings` | Reconcile the full catalog's status/price/quantity from the Merchant Listings report. |
| Every 30m | `sync_orders` | Pull orders updated since the watermark into Sales Orders. Dormant until a Default Customer is set. |

On a scheduled failure, users with the **Amazon Manager** role receive a
best-effort email alert.

## Security

- SP-API app credentials live only in `site_config.json`, never in DocTypes or the browser.
- The seller's refresh token is stored **encrypted** and never returned to the client.
- All whitelisted API methods require the `System Manager` or `Amazon Manager` role.
- Every SP-API request is recorded in the **SP-API Log** for audit.

## Contributing

This app uses `pre-commit` for code formatting and linting. Please
[install pre-commit](https://pre-commit.com/#installation) and enable it for
this repository:

```bash
cd apps/alaiy_os_connector_amazon_sp_api
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting
your code:

- [ruff](https://github.com/astral-sh/ruff)
- [eslint](https://eslint.org/)
- [prettier](https://prettier.io/)
- [pyupgrade](https://github.com/asottile/pyupgrade)

Bug reports and feature requests are welcome via
[GitHub Issues](https://github.com/alaiy-tech/alaiy_os_connector_sp_api/issues)
(templates provided). Please open a pull request.

## License

[GNU Affero General Public License v3.0](license.txt) (AGPL-3.0).
