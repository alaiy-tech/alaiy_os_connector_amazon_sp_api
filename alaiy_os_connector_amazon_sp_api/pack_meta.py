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

## Where an answer starts

`list_listings` is the only tool that answers without being handed an id, and
everything else here needs one: `compare_listing` takes a SKU, `variation_family`
takes a parent ASIN, `get_listing_issues` takes a SKU or nothing at all. Before it
existed the pack could only answer about a listing someone had already named,
which is a strange shape for a pack whose most common question is "what is wrong
with my listings" — so the register reads are what make the rest reachable, not a
convenience over them.

They read Amazon Product Listing rows, so they are as fresh as each row's
`last_synced_at` and no fresher. Every one of them returns it for that reason.

## Reads only

None of the Listings writes — create_listing, update_listing, delete_listing, and
the publish_listing / publish_listings pair over them — is registered, and the
reason is sequencing rather than taste.
`OS Agent Tool` has no `effect` field yet, so nothing in the row can tell an
orchestrator that a tool publishes; there is no per-tool toggle layer, so a site
cannot switch one off; and the Listings write path carries no idempotency key, so
a retry after a timeout can publish twice. Registering them now would hand an
agent a publish button that no one chose to grant and nothing can take away.

What that costs is small, because `compare_listing` is the whole of the useful
half: it reports exactly what a push would change, and submits nothing. The pack
can tell you what to publish without being able to publish it. `preview_publish`
is the same read one step further — it also answers whether Amazon lists the SKU
at all — and it is left out for consistency: a preview whose only next step is a
tool the pack does not have would just be `compare_listing` with a longer name.

`draft_listing` is a write that reaches no API at all, and it is still not here.
A row it created is a row an agent decided this bench should sell, sitting in the
register looking exactly like one an operator drafted. The register is where a
human decides what to publish; it is not somewhere to leave suggestions.

The sync entry points are left out for a different reason: sync_all_listings,
reconcile_listings and sync_orders page through an entire catalogue or order
window, already run on the scheduler, and take a `notify_user`. They are jobs,
not tool calls, and one of them inside a turn would outlast it. `sync_listing` is
the one that would fit inside a turn, and it is still out: it is how a suppressed
listing's issues get refreshed, so registering it would let the pack answer "is
this still broken" — but it is also a write to the register, and an agent that
can refresh a row can make the register disagree with what an operator last saw
without anyone asking it to. The honest version is to report the row's
`last_synced_at` and say the answer is as of then, which is what the tool
descriptions require.

## Two sources for one question

The sales tools answer from two places, and that is the design rather than an
accident of what was easy. `get_amazon_order_metrics` is one live Sales API call
and is what Amazon says this account sold; the other six aggregate the Sales
Orders the order sync wrote. Amazon's figure is authoritative and cannot be
broken down by SKU. The local ones can be sliced any way and are only as complete
as the sync.

Registering only one of them would have been the tidier manifest and the worse
pack. Local alone answers "which SKUs" and quietly under-reports whenever the
sync is behind, with nothing in the number to say so. Amazon alone answers a
topline and then cannot say what sold. Together they check each other, which is
why the tool descriptions tell a model to report the disagreement rather than
pick a side.

None of them is a payout. Amazon's fees and shipping are not mapped by the order
sync, and `getOrderMetrics` reports ordered product sales, so *both* sides are
gross. Every description says so, because "revenue" read as "earnings" is the
one misreading here that costs someone money. Fees, refunds and settlements are
a Finances API problem with a backing doctype behind it — a slow report poll is a
job, not a tool call, which is the same line drawn below for the sync entry
points.

`get_orders_sync_status` gained the coverage window (`first_order_date`,
`last_order_date`) rather than a seventh tool being added for it. It is the same
question as "is order sync working", and without it every local figure lies by
omission: a bench whose sync reaches back to March answers "sales in January"
with a confident zero, and nothing in the zero distinguishes "you sold nothing"
from "we have no data".

`list_amazon_orders` is the one sales read that does not apply the sold filter —
it takes a `status`, so hiding cancelled orders would make that parameter a lie.
Each row carries `counts_as_sold` instead, and its description forbids adding the
page up: the rows and the totals are answering deliberately different questions.

## Except one, and it writes a file

`export_csv` is a write. What it writes is a private File owned by the run's user,
out of rows the model is already holding — no Amazon call, nothing in the register
touched, nothing another person's screen would show differently. That is a
different kind of thing from the publish tools above, and the reason to have it is
that "give me a spreadsheet of the suppressed ones" is a real request that prose
cannot satisfy. It is declared on File create so that a site can withhold it by
withholding that permission, which is the toggle the publish tools do not have.

## Links are not data

`get_listing_link` formats URLs out of ids and calls nothing. It is here because
this pack's most useful answers end in something it cannot do — a suppressed
listing it cannot fix, a diff it cannot publish — and Seller Central is where a
person goes to do them. A pack that reports a problem and cannot say where to fix
it is asking to be re-asked.
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
	"Answers questions about this seller's Amazon presence and their Amazon sales. "
	"Lists their own listings and what Amazon objects to about each, searches "
	"Amazon's catalog for an ASIN and product type, previews what a listing push "
	"would change without submitting it, reads a variation family, and reports "
	"synced account health and order-sync state. Reports sales over any period — "
	"revenue, units, orders and average order value, the best-selling SKUs and "
	"ASINs, how one product sells over time, one period against another, and the "
	"individual orders behind any of it — from the synced orders, alongside "
	"Amazon's own live figures for the same period. Links or exports any of it as "
	"a CSV. Reads only — it cannot publish, and its sales figures are gross "
	"merchandise value rather than a payout, because Amazon's fees are not mapped."
)

# Left at the registry's own default. A cheaper model would do for the two
# straight reads, but choosing an Amazon product type from a title is a judgment
# call that a later publish depends on, and getting it wrong is not visible until
# Amazon rejects the submission.
MODEL = "claude-sonnet-5"

# The longest real chain is now a sales one, and it is longer than the register
# one it replaces. "How did the blue kettle do last month, and is that better
# than the month before" is list_listings -> get_product_sales ->
# compare_sales_periods -> get_listing_link, and a two-source answer costs a
# further get_orders_sync_status and get_amazon_order_metrics before the reply:
# seven. Sixteen leaves that room plus a second page and one wrong turn, without
# allowing a walk through the whole register or a year of daily buckets one call
# at a time.
MAX_TURNS = 16

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
# list_listings *does* cap its own page size, so exposing it there would have been
# safe — it is omitted anyway, because a knob whose only use is asking for more
# rows to retype into the next completion is not one worth offering. `page_no` is
# there, so the pages are all still reachable.

# The five listing_status values, from the DocType's own Select. Repeated here
# because the schema has to be literal for the model to choose from it, and
# list_listings validates what arrives against that same Select — so a value that
# drifts out of this enum comes back as an error naming the real ones rather than
# as a silently empty page.
_LISTING_STATUSES = ["active", "inactive", "suppressed", "incomplete", "pending"]

# Amazon's own fulfilment vocabulary, from Sales Order.amazon_fulfillment_channel
# and from the Sales API's `fulfillmentNetwork`. Deliberately NOT the DEFAULT /
# AMAZON of `list_listings.fulfillment_channel`: the same idea, two enums, and a
# model that has just read one tool would carry its values into the other. The
# sales tools therefore call the parameter `fulfillment_network`, so a wrong value
# is a wrong name rather than a plausible one.
_FULFILLMENT_NETWORKS = ["AFN", "MFN"]

# How the local sales reads bucket a period. `total` is one bucket for the whole
# of it, which is what makes it the right granularity for a headline figure.
_SALES_GRANULARITIES = ["day", "week", "month", "total"]

# The date pair every sales read takes. Repeated rather than shared by reference
# because each tool's `date_to` sentence differs in what it defaults to.
def _period_schema(default_note):
	return {
		"date_from": {
			"type": "string",
			"description": "First day of the period, inclusive, as YYYY-MM-DD.",
		},
		"date_to": {
			"type": "string",
			"description": f"Last day of the period, inclusive, as YYYY-MM-DD. {default_note}",
		},
	}


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
# behaviour you want: three of these tools are live Amazon calls and the rest read
# doctypes this connector owns and its sync fills. A disabled Amazon connector does
# not make "your account health" a stale answer, it makes it a meaningless one, and
# a pack that half-answers is worse than one that says the connector is off.
#
# That includes the two tools that need no connector of their own — get_listing_link
# formats a string and export_csv writes a file out of rows already in hand, and
# nayaglobal's pack leaves the equivalent pair ungated. Here they carry it anyway,
# because the gate is per-tool but its effect is per-pack: factory.build_runnable
# throws on the first tool whose connector is off, so exempting these two could
# never make them reachable. It would only advertise a capability the pack does not
# have — a CSV of Amazon rows nothing can read is not a working tool.
#
# `required_permissions` is declared only where a doctype permission is what the
# code actually checks. The catalog and compare endpoints gate on the
# `Amazon Manager` / `System Manager` ROLE, which this field — a list of
# {doctype, ptype} — cannot express, and api/agent_settings.py distinguishes
# "declared" from "satisfied" on purpose, so leaving them undeclared says the true
# thing where a plausible-looking proxy would say a false one. get_pending_submissions
# is that same role gate, so it declares nothing either. get_health_summary and
# get_orders_sync_status read through `frappe.get_all` / `db.count`, which ignore
# permissions, so there is no quiet under-permissioning for them.
#
# The register reads are the exception that proves the rule: list_listings,
# get_listing_issues and get_listing_link all gate on
# `has_permission("Amazon Product Listing", "read")` and export_csv on
# `has_permission("File", "create")`, so declaring those says exactly what the code
# does — and export_csv's is the one permission a site can revoke to take a tool
# away from this pack.
#
# The five local sales reads follow the register reads, not the live ones: they
# read Sales Orders and call no Amazon API, so `{Sales Order, read}` is what the
# endpoint actually checks and what it declares. That makes it a second revocable
# switch — a site that withholds Sales Order read from the pack's Run As User
# loses the sales half and keeps the listings half, and factory.py refuses the
# whole pack loudly rather than returning zeros. `get_amazon_order_metrics` is
# live, so it takes `_require_manager()` like every other live call and declares
# nothing, for the same reason search_catalog does not.
TOOLS = [
	{
		"tool_id": "list_listings",
		"description": (
			"List this seller's OWN Amazon listings — the local register the sync "
			"fills — returning {total, page_no, page_size, has_more, listings}. Each "
			"row carries {sku, title, asin, listing_status, fulfillment_channel, "
			"price, currency, quantity, marketplace, parent_asin, "
			"is_variation_parent, last_synced_at}.\n\n"
			"This is the only tool that answers without being handed an id, so it is "
			"where you start whenever the question names no SKU: \"how many "
			"listings do I have\", \"which of them are suppressed\", \"find my "
			"listing for the blue kettle\". Not to be confused with "
			"search_catalog, which searches ALL of Amazon's catalog — this searches "
			"only what this seller lists.\n\n"
			"`status` filters on listing_status and `search` matches SKU, title or "
			"ASIN as a substring, so a half-remembered fragment is enough. Rows come "
			"back ordered by SKU and `total` is the count before paging: say \"5 of "
			"212\" rather than letting one page imply it is all of them. Ask for the "
			"next page only while `has_more` is true AND the question needs more rows "
			"than you have.\n\n"
			"Local data, not a live Amazon read. It is as fresh as each row's "
			"`last_synced_at`, and it records what was last *submitted* — a change "
			"Amazon rejected still reads here as though it applied, and "
			"compare_listing is what asks Amazon about one SKU. A `total` of 0 with "
			"no filters means nothing has ever synced to this bench, which is a "
			"sync answer rather than an Amazon one."
		),
		"handler": f"{_API}.list_listings",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"status": {
					"type": "string",
					"enum": _LISTING_STATUSES,
					"description": (
						"Optional. Only listings in this state. `suppressed` and "
						"`incomplete` are the two that mean Amazon is not selling "
						"it; `pending` means a write is in flight."
					),
				},
				"fulfillment_channel": {
					"type": "string",
					"enum": ["DEFAULT", "AMAZON"],
					"description": (
						"Optional. DEFAULT is merchant-fulfilled, AMAZON is FBA."
					),
				},
				"search": {
					"type": "string",
					"description": (
						"Optional. Substring match on SKU, title or ASIN."
					),
				},
				"page_no": {
					"type": "integer",
					"description": "1-based page number. Defaults to 1, 20 rows a page.",
					"minimum": 1,
				},
			},
		},
		"required_permissions": [
			{"doctype": "Amazon Product Listing", "ptype": "read"},
		],
	},
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
		"tool_id": "get_listing_issues",
		"description": (
			"What Amazon says is wrong with a listing: the issue rows recorded for "
			"one SKU, or across every listing when `sku` is omitted. Returns "
			"{issues, count, skus_affected, truncated}, each issue carrying {sku, "
			"title, asin, listing_status, code, severity, message, attribute_names, "
			"last_synced_at}.\n\n"
			"`severity` is ERROR, WARNING or INFO. `message` is Amazon's own wording "
			"and `attribute_names` names the fields it objects to — quote both rather "
			"than paraphrasing, because the attribute name is the actionable half of "
			"the answer.\n\n"
			"Report `skus_affected` and not `count` when asked how many listings have "
			"problems: one suppressed SKU usually carries several issues, so the row "
			"count reads as several times the real number.\n\n"
			"These rows are replaced wholesale each time a SKU is read or written, so "
			"they are the issues Amazon reported as of each row's `last_synced_at` — "
			"a listing fixed since still carries its old ones, and one broken since "
			"carries none. Say as of when. An empty list means none were recorded at "
			"that point, which is not a promise about now.\n\n"
			"You cannot fix any of this. Finish with get_listing_link's "
			"`seller_central_url` for the SKU, which is the page where a person can."
		),
		"handler": f"{_API}.get_listing_issues",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"sku": {
					"type": "string",
					"description": (
						"Optional. This seller's own SKU — the name of the "
						"`Amazon Product Listing` record, not an ASIN. Leave it out "
						"to read issues across every listing."
					),
				},
				"severity": {
					"type": "string",
					"enum": ["ERROR", "WARNING", "INFO"],
					"description": (
						"Optional. ERROR is what stops a listing selling; WARNING "
						"and INFO usually do not."
					),
				},
			},
		},
		"required_permissions": [
			{"doctype": "Amazon Product Listing", "ptype": "read"},
		],
	},
	{
		"tool_id": "get_pending_submissions",
		"description": (
			"Listings whose last write Amazon accepted but has not confirmed "
			"applying. Takes no arguments. Returns [{name, marketplace, "
			"last_published_at, submission_id}], oldest first — `name` is the SKU.\n\n"
			"This is the answer to \"did my publish go through\". A row here is a "
			"write still in flight, and its listing_status reads `pending`, which is "
			"also exactly what a healthy in-flight write looks like — which is why "
			"the list is worth more than the status. An empty list is the good "
			"answer: nothing is waiting.\n\n"
			"Two things to carry into any answer. Rows only appear after a "
			"15-minute grace period, so a publish from a minute ago is deliberately "
			"absent and that absence is not evidence it landed. And a row that has "
			"been here for hours is the interesting case: a scheduled job re-reads "
			"these every 15 minutes and gives up after 24, moving the SKU back to "
			"`incomplete`.\n\n"
			"You cannot publish, retry or reconcile any of it. Report what is "
			"waiting and since when."
		),
		"handler": f"{_API}.get_pending_submissions",
		"parameters_schema": {"type": "object", "properties": {}},
		"required_permissions": [],
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
			"Whether Amazon order sync is configured, how far it has got, and which "
			"dates it covers. Takes no arguments. Returns {configured, last_sync_at, "
			"synced_orders, first_order_date, last_order_date}.\n\n"
			"Use it to answer \"are my Amazon orders coming in\" and to explain a "
			"gap: `configured` false means nothing has ever synced and no amount "
			"of waiting will change that. `synced_orders` counts orders ever "
			"synced, not orders in any period.\n\n"
			"`first_order_date` and `last_order_date` are the coverage window, and "
			"they are what every sales figure depends on. Check them before "
			"answering about any period that might start before the sync did: a zero "
			"for a month the sync never reached means there is no data, not that "
			"nothing sold, and those are opposite answers. The sales tools each "
			"carry the same window and a `coverage.note` when the period runs past "
			"it — pass that note on rather than reporting the zero alone."
		),
		"handler": f"{_API}.get_orders_sync_status",
		"parameters_schema": {"type": "object", "properties": {}},
		"required_permissions": [],
	},
	{
		"tool_id": "get_sales_summary",
		"description": (
			"What this seller sold on Amazon over a period: revenue, units, orders "
			"and average order value, bucketed by day, week, month, or as one total. "
			"Returns {period, currency, totals, coverage, buckets}.\n\n"
			"This is where a sales question starts when it names a period rather than "
			"a product — \"how did we do last month\", \"sales this quarter\", "
			"\"are we growing\". Use `granularity: total` for a single headline "
			"figure and a dated granularity only when the shape over time is the "
			"question; a bucket with no orders is absent rather than zero.\n\n"
			"Two revenue figures, and they are not interchangeable. `product_sales` "
			"is items only and is the one to quote. `order_total` adds the tax "
			"Amazon reported. NEITHER is a payout: Amazon's fees and shipping are "
			"not mapped by the sync at all, so both are gross merchandise value and "
			"an answer must not call either one profit, earnings or takings. "
			"`get_amazon_order_metrics` is Amazon's own figure for the same period "
			"and is what to check this against.\n\n"
			"Local data, computed from the Sales Orders the order sync wrote, so it "
			"is only as complete as that sync. Read `coverage` before trusting a "
			"small number: `coverage.note` is set whenever the period reaches past "
			"what has synced, and it says so in words to pass on. "
			"`totals.orders_at_fallback_rate` counts orders whose currency "
			"conversion could not be resolved and was left at 1.0 — if it is above "
			"zero, say the total is approximate."
		),
		"handler": f"{_API}.get_sales_summary",
		"parameters_schema": {
			"type": "object",
			"properties": {
				**_period_schema("Defaults to today."),
				"granularity": {
					"type": "string",
					"enum": _SALES_GRANULARITIES,
					"description": (
						"How to bucket the period. Defaults to day. Use `total` for "
						"one figure covering the whole period."
					),
				},
				"fulfillment_network": {
					"type": "string",
					"enum": _FULFILLMENT_NETWORKS,
					"description": (
						"Optional. AFN is fulfilled by Amazon (FBA), MFN by the "
						"seller. Not the same values as list_listings' "
						"`fulfillment_channel`."
					),
				},
			},
			"required": ["date_from"],
		},
		"required_permissions": [
			{"doctype": "Sales Order", "ptype": "read"},
		],
	},
	{
		"tool_id": "get_top_selling_products",
		"description": (
			"Rank this seller's best-selling SKUs or ASINs over a period. Returns "
			"{period, ranking, currency, totals, rows}, each row carrying "
			"{amazon_seller_sku or amazon_asin, item_code, item_name, units, "
			"order_count, product_sales, share_of_product_sales}.\n\n"
			"The answer to \"what sells best\", \"top ten products\", \"which "
			"SKUs make the money\". Rank `by` revenue or units — they routinely "
			"disagree, and if the question does not say which, revenue is the "
			"default and worth naming in the answer. `group_by` sku is one of this "
			"seller's offers; asin groups every SKU of the same product together, "
			"which is what to use when they list a product several times.\n\n"
			"`share_of_product_sales` is each row against the period's whole "
			"revenue, not against the ranked rows, so a top ten adding to 0.4 means "
			"those ten are 40% of everything sold. `totals.products` is how many "
			"distinct ones sold in the period: say \"top 10 of 340\" rather than "
			"letting the list imply it is all of them.\n\n"
			"`product_sales` is gross merchandise value from synced orders — items "
			"only, no Amazon fees, not a payout. Lines whose SKU or ASIN was never "
			"recorded cannot be ranked and are reported as "
			"`totals.unattributed_product_sales`; mention it if it is large."
		),
		"handler": f"{_API}.get_top_selling_products",
		"parameters_schema": {
			"type": "object",
			"properties": {
				**_period_schema("Defaults to today."),
				"by": {
					"type": "string",
					"enum": ["revenue", "units"],
					"description": "What to rank on. Defaults to revenue.",
				},
				"group_by": {
					"type": "string",
					"enum": ["sku", "asin"],
					"description": (
						"Rank this seller's own SKUs, or group every SKU of one "
						"product under its ASIN. Defaults to sku."
					),
				},
				"limit": {
					"type": "integer",
					"description": "How many rows. Defaults to 10, capped at 50.",
					"minimum": 1,
				},
				"fulfillment_network": {
					"type": "string",
					"enum": _FULFILLMENT_NETWORKS,
					"description": "Optional. AFN is FBA, MFN is merchant-fulfilled.",
				},
			},
			"required": ["date_from"],
		},
		"required_permissions": [
			{"doctype": "Sales Order", "ptype": "read"},
		],
	},
	{
		"tool_id": "get_product_sales",
		"description": (
			"How ONE product sold over a period, bucketed. Pass exactly one of `sku` "
			"or `asin`. Returns {product, period, currency, totals, buckets} with "
			"units, orders and revenue per bucket.\n\n"
			"This is what connects a listing to its sales: `list_listings` finds the "
			"SKU, this says whether it sells. Use it for \"how is this SKU doing\", "
			"\"is this product growing\", \"did the price change help\". Pass "
			"`asin` to cover every SKU of the same product at once, `sku` for one "
			"specific offer.\n\n"
			"Defaults to month buckets, which is usually the right shape for a trend; "
			"a bucket with no sales is absent, so gaps in the series are real. An "
			"empty `buckets` with no `coverage_note` means this product genuinely "
			"sold nothing in the period — a real answer, and worth pairing with the "
			"listing's status from `list_listings`, because a suppressed listing "
			"selling nothing is a different finding from a live one selling nothing."
		),
		"handler": f"{_API}.get_product_sales",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"sku": {
					"type": "string",
					"description": (
						"This seller's own SKU — the name of the "
						"`Amazon Product Listing` record. Pass this OR `asin`."
					),
				},
				"asin": {
					"type": "string",
					"description": (
						"Amazon's product id. Covers every SKU of that product. "
						"Pass this OR `sku`."
					),
				},
				**_period_schema("Defaults to today."),
				"granularity": {
					"type": "string",
					"enum": _SALES_GRANULARITIES,
					"description": "How to bucket the period. Defaults to month.",
				},
			},
			"required": ["date_from"],
		},
		"required_permissions": [
			{"doctype": "Sales Order", "ptype": "read"},
		],
	},
	{
		"tool_id": "compare_sales_periods",
		"description": (
			"One period's sales against another's, with the differences already "
			"worked out. Returns {current, baseline, currency, change, coverage}; "
			"`change` carries {current, baseline, absolute, percent} for revenue, "
			"units, orders and average order value.\n\n"
			"Use this rather than calling get_sales_summary twice and subtracting. "
			"\"Versus last month\", \"how does this compare to last year\", \"are "
			"we up or down\" are all this tool, and the arithmetic is the part that "
			"goes wrong in prose.\n\n"
			"`compare_to: previous_period` measures against the same number of days "
			"immediately before, so a 30-day window meets a 30-day window. "
			"`previous_year` shifts the same dates back 365 days, which is the right "
			"comparison for anything seasonal. Give explicit `baseline_from` and "
			"`baseline_to` instead when the question names both periods.\n\n"
			"A null `percent` is an answer, not a gap: it means the baseline was "
			"zero, and there is no percentage change from nothing. Report the "
			"absolute figure and say the baseline was zero — never write \"infinite\" "
			"or invent a percentage. Always name the baseline dates from `baseline`, "
			"because \"up 12%\" without saying against what is not a finding. Gross "
			"merchandise value on both sides, not a payout."
		),
		"handler": f"{_API}.compare_sales_periods",
		"parameters_schema": {
			"type": "object",
			"properties": {
				**_period_schema("Required here — a comparison needs both ends."),
				"compare_to": {
					"type": "string",
					"enum": ["previous_period", "previous_year"],
					"description": (
						"Which baseline to measure against. Defaults to "
						"previous_period. Ignored when baseline_from is given."
					),
				},
				"baseline_from": {
					"type": "string",
					"description": (
						"Optional. First day of an explicit baseline period, "
						"YYYY-MM-DD. Use when the question names both periods."
					),
				},
				"baseline_to": {
					"type": "string",
					"description": "Optional. Last day of the explicit baseline period.",
				},
				"fulfillment_network": {
					"type": "string",
					"enum": _FULFILLMENT_NETWORKS,
					"description": "Optional, and applied to both periods. AFN is FBA.",
				},
			},
			"required": ["date_from", "date_to"],
		},
		"required_permissions": [
			{"doctype": "Sales Order", "ptype": "read"},
		],
	},
	{
		"tool_id": "list_amazon_orders",
		"description": (
			"The individual Amazon orders behind the sales figures, paged. Returns "
			"{total, page_no, page_size, has_more, orders}, each order carrying "
			"{amazon_order_id, sales_order, transaction_date, amazon_order_status, "
			"fulfillment_network, units, order_currency, amazon_order_total, "
			"product_sales, order_total, counts_as_sold}.\n\n"
			"Use it to drill into a summary — \"show me those orders\", \"what did "
			"we sell on the 3rd\", \"every order for this SKU\" — and when someone "
			"wants a spreadsheet of orders, because this is the row-shaped result "
			"`export_csv` needs. Report `total` alongside the page.\n\n"
			"Unlike the summary tools this one shows EVERY order in the window, "
			"including cancelled ones and still-Pending ones. `counts_as_sold` is "
			"which rows the sales totals were built from: a false row is excluded "
			"from every figure the other tools return, and adding these rows up by "
			"hand will not reproduce those figures. Do not try — call "
			"get_sales_summary for a total.\n\n"
			"`product_sales` and `order_total` are in the company currency; "
			"`amazon_order_total` is Amazon's own figure in `order_currency`, which "
			"can differ per order. A Pending order's totals are near zero because "
			"Amazon withholds pricing until it confirms, not because it was cheap."
		),
		"handler": f"{_API}.list_amazon_orders",
		"parameters_schema": {
			"type": "object",
			"properties": {
				**_period_schema("Defaults to today."),
				"status": {
					"type": "string",
					"description": (
						"Optional. Amazon's own OrderStatus, exactly as it spells "
						"it: Pending, Unshipped, PartiallyShipped, Shipped, "
						"Canceled, Unfulfillable."
					),
				},
				"fulfillment_network": {
					"type": "string",
					"enum": _FULFILLMENT_NETWORKS,
					"description": "Optional. AFN is FBA, MFN is merchant-fulfilled.",
				},
				"sku": {
					"type": "string",
					"description": (
						"Optional. Only orders containing this seller SKU. An exact "
						"SKU, not a search term."
					),
				},
				"page_no": {
					"type": "integer",
					"description": "1-based page number. Defaults to 1, 20 rows a page.",
					"minimum": 1,
				},
			},
			"required": ["date_from"],
		},
		"required_permissions": [
			{"doctype": "Sales Order", "ptype": "read"},
		],
	},
	{
		"tool_id": "get_amazon_order_metrics",
		"description": (
			"Amazon's OWN sales figures for this account, read live from Amazon. "
			"Returns {period, currency, totals, buckets} with total_sales, units, "
			"order_items, order_count and avg_unit_price per bucket.\n\n"
			"This is the authoritative topline and the one to quote when someone "
			"asks what they sold. Everything else in this pack computes sales from "
			"the orders that synced to this site; this is what Amazon says, whether "
			"or not anything synced at all, so it also answers when "
			"get_orders_sync_status shows no local coverage.\n\n"
			"It will not match get_sales_summary exactly, and the difference is "
			"worth reporting rather than resolving. This answers for the "
			"connection's primary marketplace alone, while the local tools cover "
			"every marketplace that has synced — get_sales_summary's "
			"`period.marketplaces` lists which, and more than one there explains most "
			"of a gap on its own. Beyond that, Amazon buckets by its own day boundary "
			"in the marketplace time zone; the local figures bucket by purchase date "
			"in this site's time zone, exclude orders the sync has not reached, and "
			"drop cancellations at a different moment. When both are in hand, quote "
			"this as the figure and use the local tools for the breakdown by SKU, "
			"which this cannot give.\n\n"
			"Optionally filters to one `asin` or one `sku`, never both. Costs one "
			"live Amazon call and needs the Selling Partner Insights role on the "
			"SP-API app — if it comes back saying the role is missing, that is the "
			"answer, and the local sales tools still work. Amazon accepts at most "
			"730 days in one call. This is ordered product sales, still not a payout: "
			"Amazon's fees are not deducted here either."
		),
		"handler": f"{_API}.get_amazon_order_metrics",
		"parameters_schema": {
			"type": "object",
			"properties": {
				**_period_schema("Defaults to today."),
				"granularity": {
					"type": "string",
					"enum": _SALES_GRANULARITIES,
					"description": (
						"How Amazon should bucket the period. Defaults to day. Use "
						"`total` for one figure covering the whole period."
					),
				},
				"fulfillment_network": {
					"type": "string",
					"enum": _FULFILLMENT_NETWORKS,
					"description": "Optional. AFN is fulfilled by Amazon, MFN by the seller.",
				},
				"asin": {
					"type": "string",
					"description": "Optional. Amazon's product id. Pass this OR `sku`.",
				},
				"sku": {
					"type": "string",
					"description": "Optional. This seller's own SKU. Pass this OR `asin`.",
				},
			},
			"required": ["date_from"],
		},
		"required_permissions": [],
	},
	{
		"tool_id": "get_listing_link",
		"description": (
			"Give the user a link to a listing — the buyer's Amazon product page, "
			"the Seller Central page for their own offer, or both. Use it when they "
			"ask to see, open, share or fix a listing, and when your answer is that "
			"something needs changing; not to decorate an ordinary answer.\n\n"
			"Pass `sku` whenever you have one: the SKU is looked up in the register "
			"to find its ASIN, so it returns both links. Pass `asin` for a product "
			"from search_catalog that this seller does not list — that gives the "
			"buyer page only, because an ASIN does not identify one of their offers "
			"and several sellers share it.\n\n"
			"Returns {sku, found, title, listing_status, asin, marketplace, country, "
			"product_url, seller_central_url, note}. Give the URLs back exactly as "
			"they come, and never assemble an Amazon address yourself.\n\n"
			"A null URL is an answer, not a failure: `product_url` is null when the "
			"SKU has no ASIN yet — a listing Amazon has never confirmed has no buyer "
			"page — and `seller_central_url` is null when you passed no SKU. `note` "
			"says which link is missing and why; pass that on. `found: false` means "
			"this bench has no row for that SKU at all.\n\n"
			"`seller_central_url` is the one that matters whenever the answer is "
			"\"something is wrong with this listing\": it opens the page where a "
			"person can change it, which you cannot."
		),
		"handler": f"{_API}.get_listing_link",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"sku": {
					"type": "string",
					"description": (
						"This seller's own SKU — the name of the "
						"`Amazon Product Listing` record. Gives both links."
					),
				},
				"asin": {
					"type": "string",
					"description": (
						"Amazon's product id, e.g. from search_catalog. Gives the "
						"buyer's product page only. Pass this OR `sku`; passing the "
						"SKU is better whenever the seller lists the product."
					),
				},
			},
		},
		"required_permissions": [
			{"doctype": "Amazon Product Listing", "ptype": "read"},
		],
	},
	{
		"tool_id": "export_csv",
		"description": (
			"Write rows you already have to a CSV file the user can open in a "
			"spreadsheet. Use this ONLY when the user asks for a CSV, spreadsheet, "
			"Excel file, export or download — never to format an ordinary "
			"answer.\n\n"
			"`rows_json` is a JSON string of the rows: either an array of objects "
			"('[{\"sku\": \"KETTLE-BLUE\", \"price\": 24.99}]') or a whole tool "
			"result that contains one — the '{\"total\": 212, \"listings\": "
			"[...]}' list_listings returned, or get_listing_issues' "
			"'{\"issues\": [...]}'. A single object becomes a single row. Pass data "
			"you already have from the other tools; never invent rows to fill it.\n\n"
			"It exports exactly what you pass and nothing more, so a CSV of one page "
			"is a CSV of one page: page through list_listings first when the user "
			"asked for all of them, and say how many rows you wrote against the "
			"`total` you were told.\n\n"
			"Returns {saved, file_name, file_url, row_count, columns}. Tell the user "
			"the CSV is ready, name the columns and the row count, and give them "
			"`file_url` as the download link. Returns {\"saved\": false, "
			"\"error\": ...} if the rows could not be read — relay the error "
			"instead of retrying with the same arguments."
		),
		"handler": f"{_API}.export_csv",
		"parameters_schema": {
			"type": "object",
			"properties": {
				"rows_json": {
					"type": "string",
					"description": (
						"The rows to export, as a JSON string: an array of objects, "
						"or a tool result containing one."
					),
				},
				"filename": {
					"type": "string",
					"description": (
						'Names the file, no extension needed (e.g. '
						'"suppressed-listings").'
					),
				},
				"columns": {
					"type": "string",
					"description": (
						"Optional. Fixes which columns appear and in what order, "
						'comma separated ("sku,title,listing_status,price"). By '
						"default every key in the rows becomes a column."
					),
				},
			},
			"required": ["rows_json"],
		},
		"required_permissions": [
			{"doctype": "File", "ptype": "create"},
		],
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

	`export_csv` is a write and does not move that line, because of what it writes:
	a private File, owned by whoever the run reads as, out of rows that run already
	read. Under the Administrator fallback that is an Administrator-owned private
	file — visible to the operator who asked, and to nobody the reads were not
	already visible to. A site that names a narrower Run As User gets a narrower
	file owner and the File create gate starts biting, which is the same
	relationship every other tool here has with that field.

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
