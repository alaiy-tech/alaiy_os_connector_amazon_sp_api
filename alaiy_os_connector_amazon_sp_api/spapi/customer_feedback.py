# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""What customers say about a product, as far as Amazon will tell anyone.

## Start with what does not exist, because it decides the shape of this module

**SP-API has no product-review-text endpoint. At any version, in any preview,
behind any role.** A seller's own product reviews are readable only by signing
into Seller Central. This is not a gap waiting on an integration; Amazon's own
team has said so repeatedly.

That matters here because the Ratings tab was specified around a sentence this
API cannot produce: *"3 reviews this week mention 'zipper'"*. No amount of
engineering against SP-API yields that, and the honest options for it are all
outside this connector — a paid third-party review service, scraping, or not
doing it.

What Amazon does expose, through the Customer Feedback API, is the *aggregate*:
which topics customers raise about an ASIN, whether they raise them warmly or
badly, and how the product's rating is trending. That is enough to say
"complaints about durability on this SKU are rising" and never enough to quote
anybody.

So every name in this module says topic or trend, and nothing returns a field
called `review`, `comment` or `text`. A caller cannot accidentally render a
review out of this, because there is no review in it.

## Three limits a consumer has to carry, not discover

  * **Weekly, not live.** Amazon refreshes these aggregates about once a week.
    A topic that surfaces today has been building for up to seven days, so
    nothing here is an early-warning signal in the sense the issue wanted, and
    `CUSTOMER_FEEDBACK_REFRESH_DAYS` is returned alongside so a screen can say
    "as of this week" rather than implying this morning.
  * **English-language marketplaces only**, a subset of the ones this app
    supports. Which subset is Amazon's business and changes — see below.
  * **Aggregate, so it cannot be attributed to an order or a buyer.** The
    seller-feedback rows this app already syncs *can* be (they carry an order
    id), and those are about the seller rather than the product. The two are
    genuinely different things and the tab must not blur them; that is why they
    live in different modules.

## No hardcoded list of supported marketplaces

Tempting, and wrong in both directions the moment Amazon changes it: too narrow
silently disables the feature for a seller who could use it, too wide makes
every sync log an error for a marketplace that was never going to answer.

So Amazon is asked, and a refusal comes back as `supported: False` carrying the
reason it gave. A caller gets one shape whether the answer is "no topics yet" or
"not in this marketplace", and can tell them apart by reading a field rather
than by matching an error message.

## Role

Needs the **Selling Partner Insights** role, the same one the performance
reports and the Sales API use. A 403 is a role gap and comes back saying so —
and because it is the same role Account Health already needs, a seller who has
that tab working has this.
"""

import frappe
from frappe.utils import cint, flt

from alaiy_os_connector_amazon_sp_api.spapi.client import (
	SpApiClient,
	SpApiError,
	describe_forbidden,
)
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	CUSTOMER_FEEDBACK_ITEM_TOPICS,
	CUSTOMER_FEEDBACK_ITEM_TRENDS,
	CUSTOMER_FEEDBACK_REFRESH_DAYS,
	FEEDBACK_SENTIMENT_NEGATIVE,
	FEEDBACK_SENTIMENT_NEUTRAL,
	FEEDBACK_SENTIMENT_POSITIVE,
)
from alaiy_os_connector_amazon_sp_api.spapi.listings import _marketplace

#: The statuses Amazon uses to say "this marketplace or product is not covered",
#: as opposed to "covered, and there is nothing to report". A 404 is the former:
#: the resource does not exist for this ASIN in this marketplace.
_UNSUPPORTED_STATUSES = (400, 404)

#: Sentiment labels normalised to this app's own three, so a consumer switches
#: on a value rather than on Amazon's capitalisation.
_SENTIMENTS = {
	"POSITIVE": FEEDBACK_SENTIMENT_POSITIVE,
	"NEGATIVE": FEEDBACK_SENTIMENT_NEGATIVE,
	"NEUTRAL": FEEDBACK_SENTIMENT_NEUTRAL,
	"MIXED": FEEDBACK_SENTIMENT_NEUTRAL,
}


def _first(node: dict, *names):
	"""The first of several plausible key spellings that carries a value.

	Same tolerance `spapi.packages` and `spapi.health` apply, and for the same
	reason: this API is young, its field casing is the kind of thing that shifts
	between preview and general availability, and a topic label arriving under a
	name this module does not know should cost that field rather than the topic.
	"""
	for name in names:
		value = node
		for part in name.split("."):
			if not isinstance(value, dict):
				value = None
				break
			value = value.get(part)
		if value not in (None, "", []):
			return value
	return None


def normalise_sentiment(raw) -> str:
	"""Amazon's sentiment label as one of this app's three.

	An unrecognised label becomes `neutral` rather than the nearest strong
	reading. Guessing `negative` would fire a defect alert off a word Amazon
	added; guessing `positive` would hide one. Neutral is the only answer that
	does not manufacture a signal.
	"""
	if not raw:
		return FEEDBACK_SENTIMENT_NEUTRAL
	return _SENTIMENTS.get(str(raw).strip().upper(), FEEDBACK_SENTIMENT_NEUTRAL)


def _topic(entry: dict) -> dict:
	"""One topic entry, flattened.

	`mention_share` is kept as Amazon's own proportion and never converted into
	a count of reviews. Multiplying a share by a review total to get "about 4
	reviews" is exactly the kind of derived figure this module exists to refuse:
	it would read as a count of things somebody could go and look at, and there
	is nothing to look at.
	"""
	return {
		"topic": _first(entry, "topicName", "topic", "name", "label"),
		"sentiment": normalise_sentiment(_first(entry, "sentiment", "topicSentiment", "polarity")),
		# Amazon's own ranking within the response, when it gives one. Kept
		# because "the worst topic" is a question the tab asks and this is the
		# only ordering Amazon vouches for.
		"rank": cint(_first(entry, "rank", "position") or 0) or None,
		"mention_share": flt(_first(entry, "mentionPercentage", "mentionShare", "share") or 0) or None,
		"raw": entry,
	}


def _topics_of(payload) -> list:
	"""The topic array out of a response, whichever envelope it arrived in."""
	payload = payload or {}
	for node in (payload.get("payload"), payload):
		if not isinstance(node, dict):
			continue
		found = _first(node, "topics", "reviewTopics", "itemReviewTopics")
		if isinstance(found, list):
			return [entry for entry in found if isinstance(entry, dict)]
	return []


def _unsupported(error: SpApiError) -> dict:
	"""An Amazon refusal as an answer rather than an exception.

	See the module docstring: a caller gets one shape whether Amazon said "no
	topics yet" or "not in this marketplace", and tells them apart by reading
	`supported` instead of matching on an error string.
	"""
	return {
		"supported": False,
		"topics": [],
		"reason": error.message or f"Amazon returned HTTP {error.status_code}",
		"status_code": error.status_code,
	}


def review_topics(asins, marketplace=None, client=None, connection=None) -> dict:
	"""Aggregated review topics per ASIN, keyed by ASIN.

	Returns `{asin: {supported, topics, refreshed_every_days, reason?}}` where
	each topic is `{topic, sentiment, rank, mention_share}`.

	**No review text, and no count of reviews.** Neither exists in the response
	and neither is derivable from it — see the module docstring, which is the
	long version of why this function is named the way it is.

	One call per ASIN, because that is what the endpoint takes. A caller with a
	catalogue should ask about the ASINs it cares about rather than all of them:
	at a weekly refresh, re-reading everything daily spends rate limit to be
	told the same thing six times.
	"""
	asins = [a for a in dict.fromkeys(str(a).strip() for a in (asins or [])) if a]
	if not asins:
		return {}

	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)
	out: dict = {}

	for asin in asins:
		try:
			response = client.get(
				CUSTOMER_FEEDBACK_ITEM_TOPICS.format(asin=asin),
				params={"marketplaceId": mp.marketplace_id},
				context="ratings",
			)
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			if e.status_code in _UNSUPPORTED_STATUSES:
				out[asin] = _unsupported(e)
				continue
			raise

		out[asin] = {
			"supported": True,
			"topics": [_topic(entry) for entry in _topics_of(response)],
			# Returned so a screen can date what it is showing. A topic list is
			# up to a week old and a tab that implied otherwise would be
			# claiming an early warning it does not have.
			"refreshed_every_days": CUSTOMER_FEEDBACK_REFRESH_DAYS,
		}

	return out


def _trend_point(entry: dict) -> dict:
	return {
		"period_start": _first(entry, "startDate", "periodStart", "date"),
		"period_end": _first(entry, "endDate", "periodEnd"),
		"average_rating": flt(_first(entry, "averageRating", "rating", "starRating") or 0) or None,
		"raw": entry,
	}


def review_trends(asins, marketplace=None, client=None, connection=None) -> dict:
	"""How each ASIN's review rating is moving, keyed by ASIN.

	The same aggregate, over time: `{asin: {supported, points, reason?}}` with
	each point carrying a period and an average rating.

	This is a *product* rating trend and is not the seller-rating trend the tab
	also shows. Those come from different places about different things — one is
	what buyers think of a product, the other is what they think of the
	business — and Amazon judges the account on the second. Keeping them
	separate is a requirement of the issue, not a naming preference.
	"""
	asins = [a for a in dict.fromkeys(str(a).strip() for a in (asins or [])) if a]
	if not asins:
		return {}

	mp = _marketplace(marketplace, connection)
	client = client or SpApiClient(connection)
	out: dict = {}

	for asin in asins:
		try:
			response = client.get(
				CUSTOMER_FEEDBACK_ITEM_TRENDS.format(asin=asin),
				params={"marketplaceId": mp.marketplace_id},
				context="ratings",
			)
		except SpApiError as e:
			if e.is_forbidden():
				frappe.throw(describe_forbidden(e, role_free=False))
			if e.status_code in _UNSUPPORTED_STATUSES:
				out[asin] = {**_unsupported(e), "points": []}
				continue
			raise

		payload = (response or {}).get("payload") or response or {}
		points = _first(payload, "trends", "reviewTrends", "points") or []
		out[asin] = {
			"supported": True,
			"points": [_trend_point(entry) for entry in points if isinstance(entry, dict)],
			"refreshed_every_days": CUSTOMER_FEEDBACK_REFRESH_DAYS,
		}

	return out
