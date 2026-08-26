# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Registration metadata for alaiy_os's OS Agent Registry — this connector's pack.

`connector_meta.py` registers the connector: what it is, how to test it, which
sync slots it fills. This registers what an agent may *ask* it, in the same
shape and on the same schedule — one `OS Agent Registry` row whose `tools` child
rows name this app's whitelisted entry points as dotted-path handlers. The
upsert lives beside the connector one, in setup/install.py, and runs on every
`bench migrate`.

## The handlers are api.py, deliberately, not spapi/

Core resolves a handler with `frappe.get_attr` and calls `handler(**input)`, so
either layer would work mechanically. api.py is the right one because the
permission gate travels with the call: `_require_manager()` on the catalog and
listing endpoints, `frappe.has_permission("Amazon Product Listing", "read")` on
variation_family. Pointing a tool at `spapi.listings` would run the same code
with the gate removed, which is not a shortcut, it is a hole.

## Reads only

None of the three Listings writes — create_listing, update_listing,
delete_listing — is registered, and the reason is sequencing rather than taste.
`OS Agent Tool` has no `effect` field yet, so nothing in the row can tell an
orchestrator that a tool publishes; there is no per-tool toggle layer, so a site
cannot switch one off; and the Listings write path carries no idempotency key, so
a retry after a timeout can publish twice. Registering them now would hand an
agent a publish button that no one chose to grant and nothing can take away.

What that costs is small, because `compare_listing` is the whole of the useful
half: it reports exactly what a push would change, and submits nothing. The pack
can tell you what to publish without being able to publish it.

The sync entry points are left out for a different reason: sync_all_listings,
reconcile_listings and sync_orders page through an entire catalogue or order
window, already run on the scheduler, and take a `notify_user`. They are jobs,
not tool calls, and one of them inside a turn would outlast it.
"""

import json
from pathlib import Path

_APP = "alaiy_os_connector_amazon_sp_api"
_APP_DIR = Path(__file__).resolve().parent

# The OS Agent Registry primary key, and what OS Agent Run records per run.
PACK_ID = "amazon_sp_api"
PACK_NAME = "Amazon (SP-API)"
PACK_ICON = "shopping-cart"

# The OS Connector Registry id from connector_meta. Every tool row carries it, so
# factory.py refuses to build this pack while the connector is disabled — see the
# note on the tool list.
CONNECTOR_ID = "amazon_sp_api"

DESCRIPTION = (
	"Answers questions about this seller's Amazon presence: searches Amazon's "
	"catalog for an ASIN and product type, previews what a listing push would "
	"change without submitting it, reads a variation family, and reports synced "
	"account health and order-sync state. Reads only — it cannot publish."
)

# Left at the registry's own default. A cheaper model would do for the two
# straight reads, but choosing an Amazon product type from a title is a judgment
# call that a later publish depends on, and getting it wrong is not visible until
# Amazon rejects the submission.
MODEL = "claude-sonnet-5"

# search_catalog -> suggest_product_type -> compare_listing, plus the reply, is
# four. Ten leaves room for a second search on different keywords without
# allowing a long chain of speculative catalog queries.
MAX_TURNS = 10

_API = f"{_APP}.api"

# `marketplace` is a parameter on most of these endpoints and is deliberately
# absent from every schema below. It names an `Amazon Marketplace` record, which
# autonames on Amazon's own marketplaceId (`A21TJRUUN4KGV`), and there is no tool
# here that lists them — so a model asked to supply one would invent one. Omitted,
# every call falls through to the connection's primary marketplace, which is what
# the Desk surfaces do too. The day this connector serves two marketplaces the
# note's answer is two registry rows and two packs, not a parameter.
#
# `page_size` on search_catalog is omitted for the same class of reason: the
# schema is advice to the model, not a limit the executor enforces, and
# `cint(page_size) or 10` has no cap of its own. Left off, it is 10.

# What a push may carry, from spapi.listings._PUSH_FIELDS. Named here rather than
# imported because importing it would pull the SP-API client into the migrate that
# validates this row.
_DESIRED_SCHEMA = {
	"type": "object",
	"description": (
		"The intended state to compare against Amazon. Pass {} to just read what "
		"Amazon currently holds. Any other key is ignored."
	),
	"properties": {
		"title": {"type": "string"},
		"price": {
			"type": "number",
			"description": "A price of 0 or blank reads as 'leave it alone'.",
		},
		"quantity": {
			"type": "integer",
			"description": (
				"0 is meaningful — it is how a seller goes out of stock — so it "
				"counts as a change. Only an absent value is skipped."
			),
		},
		"condition": {"type": "string"},
		"description": {"type": "string"},
		"bullet_points": {"type": "array", "items": {"type": "string"}},
		"keywords": {"type": "array", "items": {"type": "string"}},
		"images": {"type": "array", "items": {"type": "string"}},
	},
}


# ── the tools ─────────────────────────────────────────────────────────────────
# Every row carries `connector`, unlike a core-hosted pack's local reads. That
# makes factory.py throw while the connector is disabled, and here that is the
# behaviour you want: three of these tools are live Amazon calls, and the other
# three read doctypes this connector owns and its sync fills. A disabled Amazon
# connector does not make "your account health" a stale answer, it makes it a
# meaningless one, and a pack that half-answers is worse than one that says the
# connector is off.
#
# `required_permissions` is declared only where a doctype permission is what the
# code actually checks. The catalog and compare endpoints gate on the
# `Amazon Manager` / `System Manager` ROLE, which this field — a list of
# {doctype, ptype} — cannot express, and api/agent_settings.py distinguishes
# "declared" from "satisfied" on purpose, so leaving them undeclared says the true
# thing where a plausible-looking proxy would say a false one. get_health_summary
# and get_orders_sync_status read through `frappe.get_all` / `db.count`, which
# ignore permissions, so there is no quiet under-permissioning for them either.
TOOLS = [
	{
		"tool_id": "search_catalog",
		"description": (
			"Search Amazon's catalog for a product, returning [{asin, title, "
			"brand, image_url, product_type}] — up to 10, best match first. "
			"`query` is either keywords or a bare 10-character ASIN, which is "
			"looked up directly.\n\n"
			"This is the first call when someone names a product rather than a "
			"SKU: it gives you both the ASIN an offer would attach to and the "
			"product type Amazon already holds for it, which every listings "
			"write has to declare. An empty list means Amazon's catalog has no "
			"match for those keywords — try once more with different words, then "
			"say so. For a product not in the catalog at all, "
			"suggest_product_type answers from the title instead. Costs one live "
			"Amazon call; the client handles throttling itself."
		),
		"handler": f"{_API}.search_catalog",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"query": {
					"type": "string",
					"description": (
						"Search keywords, or a bare 10-character ASIN for a "
						"direct look-up."
					),
				},
			},
			"required": ["query"],
		},
		"required_permissions": [],
	},
	{
		"tool_id": "suggest_product_type",
		"description": (
			"Ask Amazon which product type a product title belongs to, returning "
			"[{product_type, display_name}] best match first.\n\n"
			"Unlike search_catalog this needs no ASIN, so it answers for products "
			"that are not in Amazon's catalog yet — which is exactly when a "
			"product type is hardest to come by and most needed, since every "
			"listings write must declare one. It returns a list because a title "
			"is often ambiguous: present the candidates and say which you would "
			"choose and why, rather than reporting one as decided. Costs one live "
			"Amazon call."
		),
		"handler": f"{_API}.suggest_product_type",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"title": {
					"type": "string",
					"description": "The product title to classify.",
				},
			},
			"required": ["title"],
		},
		"required_permissions": [],
	},
	{
		"tool_id": "compare_listing",
		"description": (
			"Preview what a listing push would change, without submitting "
			"anything. Returns {sku, marketplace, listing_status, remote, "
			"changes, changed, content_changed}: `remote` is what Amazon holds "
			"for this SKU right now, and `changes` is the subset of `desired` "
			"that Amazon does not already have.\n\n"
			"Read-only — one Amazon read plus a catalog look-up, and no "
			"submission. Pass `desired` as {} to use it purely as \"what does "
			"Amazon currently hold for this SKU\", which also gives you its ASIN, "
			"price, quantity and listing status.\n\n"
			"Two things to carry into any answer. `remote` is Amazon's live state, "
			"not the local register row — the row records what was last "
			"submitted, so a change Amazon rejected still reads locally as though "
			"it went through, and where the two disagree the disagreement is "
			"itself the finding. And a blank or empty value in `desired` means "
			"\"no opinion\", never \"clear this on Amazon\": nothing here can "
			"delete content, so never describe a diff as removing something.\n\n"
			"There is no tool that pushes what this returns. Report the diff and "
			"say publishing is a separate step."
		),
		"handler": f"{_API}.compare_listing",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"sku": {
					"type": "string",
					"description": (
						"This seller's own SKU — the name of the "
						"`Amazon Product Listing` record. Not an ASIN."
					),
				},
				"desired": _DESIRED_SCHEMA,
			},
			"required": ["sku", "desired"],
		},
		"required_permissions": [],
	},
	{
		"tool_id": "variation_family",
		"description": (
			"Every SKU this seller lists under one parent ASIN — the variation "
			"family, as recorded by the listing sync.\n\n"
			"`parent_asin` is an ASIN, not a SKU. Reads local records and hits no "
			"Amazon API, so it is as fresh as the last listing sync. Use it to "
			"answer \"which sizes/colours of this am I listing\" and to find the "
			"sibling SKUs of one you already have. Empty if that ASIN has no "
			"family recorded here, which is also what you get for a standalone "
			"listing."
		),
		"handler": f"{_API}.variation_family",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"parent_asin": {
					"type": "string",
					"description": (
						"The parent ASIN of the variation family. An ASIN, not a "
						"SKU."
					),
				},
			},
			"required": ["parent_asin"],
		},
		"required_permissions": [
			{"doctype": "Amazon Product Listing", "ptype": "read"},
		],
	},
	{
		"tool_id": "get_health_summary",
		"description": (
			"This seller's Amazon account health as the last sync recorded it. "
			"Takes no arguments. Returns {overall_status, marketplace, metrics, "
			"feedback, synced_at}: `metrics` carries each policy metric with its "
			"value, Amazon's target, whether higher is better and an ok/warn/"
			"critical status; `feedback` is up to 50 most-recent seller feedback "
			"rows.\n\n"
			"This is synced data, not a live Amazon read, and `synced_at` is when. "
			"Quote it alongside any figure from here — \"your return rate was 4% "
			"as of Tuesday\" is true where \"your return rate is 4%\" is not. An "
			"overall status of unknown means no metrics have synced yet, which is "
			"a configuration answer rather than a health one."
		),
		"handler": f"{_API}.get_health_summary",
		"parameters_schema": {"type": "object", "properties": {}},
		"required_permissions": [],
	},
	{
		"tool_id": "get_orders_sync_status",
		"description": (
			"Whether Amazon order sync is configured and how far it has got. "
			"Takes no arguments. Returns {configured, last_sync_at, "
			"synced_orders}: whether a customer is set for Amazon orders, the "
			"watermark the scheduled sync has reached, and how many Sales Orders "
			"carry an Amazon order id.\n\n"
			"Use it to answer \"are my Amazon orders coming in\" and to explain a "
			"gap: `configured` false means nothing has ever synced and no amount "
			"of waiting will change that. `synced_orders` counts orders ever "
			"synced, not orders in any period."
		),
		"handler": f"{_API}.get_orders_sync_status",
		"parameters_schema": {"type": "object", "properties": {}},
		"required_permissions": [],
	},
]


def read_text(relpath):
	"""Read a file relative to this app's package directory."""
	return (_APP_DIR / relpath).read_text(encoding="utf-8")


def build_pack_meta():
	"""The OS Agent Registry row this app asserts, as a dict of its fields.

	`run_as_user` is absent on purpose. The executor pins it when set and falls
	back to Administrator when it is not, and Administrator holds System Manager,
	so `_require_manager()` passes — the gates on api.py only bite once a site
	names a narrower user here. That is the right default for a read-only pack and
	the wrong one the moment a write tool is added, so the two decisions belong
	together.

	`output_format` stays Text: this pack answers a person. It gets an
	`output_schema` when another pack consumes its result — a typed handoff is for
	pack-to-pack, and prose is right at the leaf.
	"""
	return {
		"agent_id": PACK_ID,
		"agent_name": PACK_NAME,
		"description": DESCRIPTION,
		"icon": PACK_ICON,
		"model": MODEL,
		"max_turns": MAX_TURNS,
		"system_prompt": read_text("prompts/pack.md"),
		"output_format": "Text",
		"tools": TOOLS,
	}


def as_registry_tool(tool):
	"""One manifest tool as its OS Agent Tool child row, with the JSON as text."""
	return {
		"tool_id": tool["tool_id"],
		"description": tool["description"],
		"handler": tool["handler"],
		"connector": CONNECTOR_ID,
		"parameters_schema": json.dumps(tool["parameters_schema"], indent=1),
		"required_permissions": (
			json.dumps(tool["required_permissions"], indent=1)
			if tool["required_permissions"]
			else None
		),
	}
