# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Amazon, as the listing agent sees it.

`alaiy_os_agent_listing` owns one channel-agnostic listing agent and knows nothing
about any marketplace. Everything Amazon wants — its extra fields, its rules, its
validator, and how to read and write a listing — arrives from here, through the
`listing_channels` hook in this app's hooks.py. See that app's channels.py for the
contract.

## Why this lives in the connector

It used to be its own app, `alaiy_os_agent_amazon_listing`, which registered a
second listing agent alongside Shopify's. The two were near-duplicates of each
other and neither could be reached from Ask Alaiy. What was genuinely Amazon's in
them was this: the fields, the rules, and the doctypes. All of that is knowledge
about *the channel*, and the app that already owns the channel is this one — it
holds `Amazon Product Listing`, it speaks SP-API, and it is what a site installs
when it decides to sell on Amazon. So the enriched-listing doctypes and their
handlers moved here, and the agent became one.

    prompts/listing.md        Amazon's rules, handed to the model by get_channel_spec
    listing/fields.json       Amazon's own output fields, on top of the shared ones
    listing/validate()        the same rules again, in Python, enforced on save
    listing/handlers.py       reading a listing and writing the enriched one
    doctype/amazon_enriched_listing/   the review record itself

The rules being written twice — once as prose for the model, once as code in
`validate` — is deliberate and not duplication to remove. The prose is what gets
the listing right the first time; the code is what makes sure. A model that has
read "exactly five bullets" still occasionally writes four, and before this the
only thing between four bullets and a live listing was a JSON schema, which could
count bullets but could not check a byte budget, a banned phrase, or a keyword
already spent in the title.

## Why validate() and not a stricter schema

JSON Schema cannot express most of what Amazon actually rejects listings for. It
can say `maxItems: 5`; it cannot say "under 250 bytes in total", "none of these
sixty phrases unless the product data substantiates them", or "no keyword that
already appears in the title, because Amazon indexes those and the repeat wastes
the budget". Those are the rules that get a listing suppressed, and they are all
ordinary Python.

Defects come back through `save_listing` as a tool error, so the model reads them
and fixes the listing rather than the run dying — and nothing is written until it
passes.

## No image step, for now

The producing side — the white-background main tile, the translated gallery, the
S3 store and the background render queue — has not moved across yet, so no
`prepare_images` handler is declared. `get_channel_spec` reports
`has_image_step: false` and the agent sets `images: []` and says so. Reading the
product's existing photos is unaffected; that is `listing/images.py`.
"""

import json
import re
from pathlib import Path

import frappe

_APP = "alaiy_os_connector_amazon_sp_api"
_DIR = Path(__file__).resolve().parent
_SELF = f"{_APP}.listing.channel"

CHANNEL = "amazon"
LISTING_DOCTYPE = "Amazon Product Listing"
ENRICHED_DOCTYPE = "Amazon Enriched Listing"

BULLET_COUNT = 5
TITLE_MIN, TITLE_MAX = 100, 150
BULLET_MIN, BULLET_MAX = 150, 300

#: Amazon's backend search terms are budgeted in BYTES, not characters — a
#: listing whose keywords run over is silently truncated at the byte boundary, so
#: the last term is lost without anything reporting it.
KEYWORD_BYTE_BUDGET = 250


def channel():
	"""The adapter. Registered via `listing_channels` in hooks.py."""
	return {
		"channel": CHANNEL,
		"label": "Amazon",
		"identifier_label": "seller SKU",
		"source_doctype": LISTING_DOCTYPE,
		"enriched_doctype": ENRICHED_DOCTYPE,
		"spec": {
			"fields": json.loads((_DIR / "fields.json").read_text(encoding="utf-8")),
			"rules": (_DIR.parent / "prompts" / "listing.md").read_text(encoding="utf-8"),
		},
		"handlers": {
			"get_product": f"{_SELF}.get_product",
			"get_reference_values": f"{_SELF}.get_reference_values",
			"save_listing": f"{_SELF}.save_listing",
			"validate": f"{_SELF}.validate",
			"health": f"{_SELF}.health",
			# No "prepare_images" — see the module docstring.
		},
	}


# ── handler adapters ──────────────────────────────────────────────────────────
# Thin, and thin on purpose: handlers.py still speaks in `sku`, because that is
# what it means on this channel and what every other caller in this app passes it.
# Core never learns that Amazon calls its identifier `sku` and Shopify calls it
# `item_code` — that translation is the whole job of these three functions.


def get_product(product):
	from alaiy_os_connector_amazon_sp_api.listing import handlers

	return handlers.get_product(sku=product)


def get_reference_values():
	from alaiy_os_connector_amazon_sp_api.listing import handlers

	return handlers.get_reference_values()


def save_listing(product, listing):
	from alaiy_os_connector_amazon_sp_api.listing import handlers

	return handlers.save_listing(listing=listing, sku=product)


# ── what Amazon says is wrong with this listing ───────────────────────────────


def health(product):
	"""This listing's current status and Amazon's own open issues.

	`spapi/listings.py` writes these back after every submission, so they are
	Amazon's verdict rather than ours: an `ERROR` here is usually the reason a
	listing is not selling, and the fields each issue names are the ones worth
	rewriting.

	This is the only channel that can answer this at all — Shopify reports no
	equivalent — which is why `health` is an optional capability rather than part
	of the contract.
	"""
	if not frappe.db.exists(LISTING_DOCTYPE, product):
		frappe.throw(f"No {LISTING_DOCTYPE} found for '{product}'.")

	doc = frappe.get_doc(LISTING_DOCTYPE, product)
	issues = [
		{
			"code": row.code,
			"severity": row.severity,
			"message": row.message,
			# The attributes Amazon blames, which is the most actionable part of an
			# issue and what a rewrite should be aimed at.
			"fields": row.attribute_names,
		}
		for row in (doc.get("suppression_reasons") or [])
	]

	return {
		"product": product,
		"listing_status": doc.get("listing_status"),
		"issues": issues,
		"last_publish_error": doc.get("last_publish_error"),
		"last_published_at": str(doc.get("last_published_at") or "") or None,
		# What the last enrichment said, so a re-run can see what it is replacing
		# rather than starting from the raw listing again.
		"current_enrichment": _current_enrichment(product),
	}


def _current_enrichment(product):
	"""The existing Enriched Listing's content, or None if it was never enriched."""
	if not frappe.db.exists(ENRICHED_DOCTYPE, product):
		return None
	doc = frappe.get_doc(ENRICHED_DOCTYPE, product)
	return {
		"status": doc.get("status"),
		"title": doc.get("title"),
		"needs_review": doc.get("needs_review"),
		"notes": doc.get("notes"),
	}


# ── the validator ─────────────────────────────────────────────────────────────

#: Phrases that get a listing suppressed, sent to compliance review, or worse,
#: unless the product data substantiates them. Grouped the way the rules are
#: written so a defect can name the category and mean something to a reviewer.
#:
#: Matched on word boundaries against the lowercased text. Deliberately literal:
#: an over-clever matcher that caught "greenhouse" for "green" would train the
#: model to work around the validator rather than write better copy.
RESTRICTED = {
	"medical or health": [
		"cure", "treat", "heal", "prevent", "therapy", "therapeutic",
		"anti-bacterial", "antibacterial", "antiviral", "antifungal", "pain relief",
		"clinically proven", "fda approved", "doctor recommended",
		"prescription strength", "safe for babies",
	],
	"absolute or misleading": [
		"best", "no.1", "world's best", "guaranteed", "100% guaranteed", "perfect",
		"unbreakable", "indestructible", "lifetime guarantee", "never fails",
	],
	"unsupported quality": [
		"premium quality", "superior quality", "highest quality", "luxury grade",
		"commercial grade", "military grade", "professional grade",
		"industrial strength",
	],
	"environmental": [
		"eco-friendly", "eco friendly", "compostable", "biodegradable",
		"carbon neutral", "sustainable",
	],
	"safety": [
		"non-toxic", "nontoxic", "bpa free", "bpa-free", "food grade", "child safe",
		"chemical free", "lead free",
	],
	"promotional": [
		"discount", "cheapest", "hot sale", "limited time", "new arrival",
		"trending", "bestseller", "best seller",
	],
	"shipping or fulfilment": [
		"fast shipping", "free shipping", "same day delivery", "prime eligible",
		"cash on delivery",
	],
}

#: Named in the rules because they are the ones this catalogue actually collides
#: with. Not exhaustive, and not meant to be — a competitor name that is not here
#: is still against the rules, and that is what the prose is for.
COMPETITOR_BRANDS = [
	"amazon basics", "stanley", "milton", "cello", "borosil", "pigeon",
	"tupperware", "signoraware", "camelbak", "nalgene", "hydro flask",
	"contigo", "yeti",
]

THIRD_PARTY_IP = [
	"disney", "marvel", "barbie", "hello kitty", "pokemon", "pokémon", "minions",
	"harry potter", "star wars",
]

#: The symbols Amazon rejects outright, plus the emoji planes. Cheaper and more
#: honest than a full grapheme-aware emoji library: these ranges are what a model
#: actually emits when it decorates a title.
_FORBIDDEN_CHARS = re.compile(
	"[®™"
	"\U0001f300-\U0001faff"
	"☀-➿"
	"️]"
)

_WORD = re.compile(r"[a-z0-9']+")


def validate(listing):
	"""Amazon's rules, as a list of defects. Empty means the listing may be saved.

	Written to be read by the model: each defect says what is wrong, and where the
	number matters, what the number actually is. "Bullet 3 is 412 characters" is
	something it can act on; "bullet too long" is something it will guess at.
	"""
	listing = listing or {}
	defects = []

	title = (listing.get("title") or "").strip()
	bullets = [str(b or "").strip() for b in (listing.get("bullet_points") or [])]
	description = (listing.get("description") or "").strip()
	keywords = [str(k or "").strip() for k in (listing.get("keywords") or [])]

	defects += _check_title(title)
	defects += _check_bullets(bullets)
	defects += _check_keywords(keywords, title, bullets)

	if not description:
		defects.append("`description` is empty. Amazon needs one.")

	# Restricted language applies to everything that publishes, so it is checked
	# once over the lot rather than per field — a banned phrase is no better in a
	# bullet than in the title.
	defects += _check_language(
		{
			"title": title,
			"description": description,
			**{f"bullet {i + 1}": b for i, b in enumerate(bullets)},
			**{f"keyword '{k}'": k for k in keywords},
		}
	)

	return defects


def _check_title(title):
	if not title:
		return ["`title` is empty."]

	defects = []
	if len(title) > TITLE_MAX:
		defects.append(
			f"`title` is {len(title)} characters; Amazon's limit here is {TITLE_MAX}. "
			"Cut it without dropping the product noun."
		)
	elif len(title) < TITLE_MIN:
		defects.append(
			f"`title` is only {len(title)} characters; aim for 120-150. Expand it with "
			"product features and intended applications — never with an invented spec."
		)
	if " | " not in title:
		defects.append(
			"`title` does not use ' | ' as its section delimiter. Format: "
			"'Brand Primary Product Keyword | Product Type / Material | Key Feature | "
			"Primary Application / Use Case | Variant Specification'."
		)
	return defects


def _check_bullets(bullets):
	if len(bullets) != BULLET_COUNT:
		# The single most common defect, and the one the old JSON schema caught —
		# kept here so the whole rule set lives in one place.
		return [
			f"`bullet_points` has {len(bullets)} entries; Amazon needs exactly "
			f"{BULLET_COUNT}, in the fixed order given in the rules."
		]

	defects = []
	for i, bullet in enumerate(bullets, start=1):
		if len(bullet) > BULLET_MAX:
			defects.append(f"Bullet {i} is {len(bullet)} characters; keep each under {BULLET_MAX}.")
		elif len(bullet) < BULLET_MIN:
			defects.append(
				f"Bullet {i} is only {len(bullet)} characters; aim for 180-250 so it "
				"says something specific."
			)
		head, sep, _ = bullet.partition(" - ")
		if not sep or not head or head != head.upper():
			defects.append(
				f"Bullet {i} does not open with an UPPERCASE heading followed by ' - ' "
				"(e.g. 'LARGE CAPACITY - Main compartment fits...')."
			)
	return defects


def _check_keywords(keywords, title, bullets):
	"""The byte budget, and the words already spent in the copy.

	Both are invisible failures on Amazon's side — it truncates an over-budget set
	at the byte boundary and silently indexes nothing past it, and it already
	indexes every word in the title and bullets, so a repeat there buys nothing and
	costs budget. Neither shows up as an error on the listing.
	"""
	if not keywords:
		return ["`keywords` is empty. Fill it with the search terms the copy did not already use."]

	defects = []

	used = len(" ".join(keywords).encode("utf-8"))
	if used > KEYWORD_BYTE_BUDGET:
		defects.append(
			f"`keywords` is {used} bytes; Amazon's budget is about "
			f"{KEYWORD_BYTE_BUDGET}. Drop the weakest terms — anything over the budget "
			"is silently discarded."
		)

	spent = set(_WORD.findall(title.lower()))
	for bullet in bullets:
		spent |= set(_WORD.findall(bullet.lower()))

	repeated = sorted({
		word
		for keyword in keywords
		for word in _WORD.findall(keyword.lower())
		if word in spent and len(word) > 2
	})
	if repeated:
		defects.append(
			"These words are already in the title or bullets, where Amazon indexes "
			f"them, so repeating them in `keywords` wastes the budget: {', '.join(repeated)}."
		)

	return defects


def _check_language(fields):
	"""Restricted claims, competitor names, third-party IP and forbidden symbols."""
	defects = []

	for where, text in fields.items():
		if not text:
			continue
		lowered = text.lower()

		for category, terms in RESTRICTED.items():
			hits = [term for term in terms if _contains(lowered, term)]
			if hits:
				defects.append(
					f"The {where} uses restricted {category} language: "
					f"{', '.join(sorted(hits))}. Remove it, or — if the product data "
					"genuinely substantiates the claim — keep it and say in `notes` "
					"what substantiates it."
				)

		brands = [b for b in COMPETITOR_BRANDS if _contains(lowered, b)]
		if brands:
			defects.append(f"The {where} names a competitor brand: {', '.join(sorted(brands))}.")

		ip = [name for name in THIRD_PARTY_IP if _contains(lowered, name)]
		if ip:
			defects.append(f"The {where} names third-party IP: {', '.join(sorted(ip))}.")

		found = _FORBIDDEN_CHARS.findall(text)
		if found:
			defects.append(
				f"The {where} contains characters Amazon rejects "
				f"({''.join(sorted(set(found)))}) — no emoji, no ® and no ™."
			)

	return defects


def _contains(lowered, term):
	"""Whole-term match, so 'green' does not fire on 'greenhouse'."""
	return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered) is not None
