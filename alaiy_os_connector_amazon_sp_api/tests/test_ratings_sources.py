# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the ratings reads — the aggregate, and the refusals.

This is the tab where the most important thing the code does is *decline to
answer*. Amazon has no product-review-text endpoint, so the tests below are as
much about what these functions refuse to produce as about what they return.

Four cases carry the weight.

`test_no_feedback_is_not_a_zero_star_seller` is the most damaging plausible bug
in the module. A new seller with no ratings, summarised carelessly, shows 0.0
stars — the worst possible rendering of "nobody has rated you yet", on the tile
a seller looks at to decide whether they are in trouble.

`test_an_unrated_comment_moves_nothing` is the quiet one. A buyer who left words
and no stars expressed no opinion on the scale being reported, and counting them
as neutral shifts the positive share using a row that said nothing about it.

`test_an_unsupported_marketplace_is_an_answer_and_not_an_exception` is the
distinction the whole `supported` field exists for. "No topics yet" and "not
covered in this marketplace" are different facts, and a caller must be able to
tell them apart by reading a field rather than matching an error string.

`test_an_unknown_sentiment_is_neutral` is the one that would manufacture a
signal. Guessing `negative` for a label Amazon added fires a defect alert off a
vocabulary change; guessing `positive` hides a real one.
"""

from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import customer_feedback, health
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiError


class TestSellerRating(UnitTestCase):
	def test_the_average_and_the_shares(self):
		summary = health.summarise_feedback(
			[{"rating": 5}, {"rating": 5}, {"rating": 4}, {"rating": 3}, {"rating": 1}]
		)
		self.assertEqual(summary["count"], 5)
		self.assertEqual(summary["average_rating"], 3.6)
		self.assertEqual(summary["positive"], 3)
		self.assertEqual(summary["neutral"], 1)
		self.assertEqual(summary["negative"], 1)
		self.assertEqual(summary["positive_pct"], 60.0)
		self.assertEqual(summary["negative_pct"], 20.0)

	def test_amazons_own_definition_of_negative(self):
		"""1 and 2 stars, which is what Order Defect Rate is built on."""
		summary = health.summarise_feedback([{"rating": 1}, {"rating": 2}, {"rating": 3}])
		self.assertEqual(summary["negative"], 2)
		self.assertEqual(summary["neutral"], 1)
		self.assertEqual(summary["positive"], 0)

	def test_the_three_buckets_always_account_for_every_rated_row(self):
		summary = health.summarise_feedback([{"rating": n} for n in (1, 2, 3, 4, 5)])
		self.assertEqual(summary["positive"] + summary["neutral"] + summary["negative"], summary["count"])

	def test_no_feedback_is_not_a_zero_star_seller(self):
		"""See the module docstring. 0.0 is the most damaging available answer."""
		summary = health.summarise_feedback([])
		self.assertEqual(summary["count"], 0)
		self.assertIsNone(summary["average_rating"])
		self.assertIsNone(summary["positive_pct"])
		self.assertIsNone(summary["negative_pct"])

	def test_an_unrated_comment_moves_nothing(self):
		"""Words without stars express no opinion on the scale being reported."""
		summary = health.summarise_feedback([{"rating": 5}, {"comment": "arrived late", "rating": None}])
		self.assertEqual(summary["count"], 1)
		self.assertEqual(summary["positive_pct"], 100.0)

	def test_the_reference_line_travels_with_the_figures(self):
		"""So a tile draws Amazon's guidance without hardcoding it twice."""
		self.assertEqual(
			health.summarise_feedback([{"rating": 5}])["target_positive_pct"],
			health.summarise_feedback([])["target_positive_pct"],
		)


class TestSentiment(UnitTestCase):
	def test_amazons_labels_map_to_ours(self):
		self.assertEqual(customer_feedback.normalise_sentiment("POSITIVE"), "positive")
		self.assertEqual(customer_feedback.normalise_sentiment("negative"), "negative")
		self.assertEqual(customer_feedback.normalise_sentiment("Mixed"), "neutral")

	def test_an_unknown_sentiment_is_neutral(self):
		"""See the module docstring: the alternatives manufacture a signal."""
		self.assertEqual(customer_feedback.normalise_sentiment("ENTHUSIASTIC"), "neutral")
		self.assertEqual(customer_feedback.normalise_sentiment(None), "neutral")
		self.assertEqual(customer_feedback.normalise_sentiment(""), "neutral")


class TestReviewTopics(UnitTestCase):
	def _client(self, payload):
		client = Mock()
		client.get.return_value = payload
		return client

	def _marketplace(self):
		return patch.object(
			customer_feedback, "_marketplace", return_value=Mock(marketplace_id="ATVPDKIKX0DER")
		)

	def test_topics_are_read_whatever_amazon_calls_the_fields(self):
		payload = {
			"payload": {
				"topics": [
					{"topicName": "zipper durability", "sentiment": "NEGATIVE", "rank": 1},
					{"topic": "fabric quality", "topicSentiment": "POSITIVE", "position": 2},
				]
			}
		}
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=self._client(payload))

		topics = out["B0AAA"]["topics"]
		self.assertTrue(out["B0AAA"]["supported"])
		self.assertEqual(topics[0]["topic"], "zipper durability")
		self.assertEqual(topics[0]["sentiment"], "negative")
		self.assertEqual(topics[1]["topic"], "fabric quality")
		self.assertEqual(topics[1]["rank"], 2)

	def test_nothing_in_the_result_is_shaped_like_a_review(self):
		"""The module's whole reason for being. No text, no review count."""
		payload = {"topics": [{"topicName": "zipper", "sentiment": "NEGATIVE"}]}
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=self._client(payload))

		topic = out["B0AAA"]["topics"][0]
		for forbidden in ("review", "reviews", "text", "comment", "snippet", "review_count"):
			self.assertNotIn(forbidden, topic)

	def test_a_mention_share_is_never_turned_into_a_count(self):
		""" "About 4 reviews" would read as things somebody could go and look at."""
		payload = {"topics": [{"topicName": "zipper", "mentionPercentage": 12.5}]}
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=self._client(payload))
		self.assertEqual(out["B0AAA"]["topics"][0]["mention_share"], 12.5)

	def test_an_unsupported_marketplace_is_an_answer_and_not_an_exception(self):
		"""See the module docstring on why `supported` exists."""
		client = Mock()
		client.get.side_effect = SpApiError("Not available in this marketplace", status_code=404)
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=client)

		self.assertFalse(out["B0AAA"]["supported"])
		self.assertEqual(out["B0AAA"]["topics"], [])
		self.assertEqual(out["B0AAA"]["reason"], "Not available in this marketplace")
		self.assertEqual(out["B0AAA"]["status_code"], 404)

	def test_a_covered_asin_with_nothing_to_report_is_supported_and_empty(self):
		"""The other half of the same distinction."""
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=self._client({"topics": []}))
		self.assertTrue(out["B0AAA"]["supported"])
		self.assertEqual(out["B0AAA"]["topics"], [])

	def test_a_real_failure_still_raises(self):
		"""A 500 is not "this marketplace isn't covered"."""
		client = Mock()
		client.get.side_effect = SpApiError("Internal error", status_code=500)
		with self._marketplace():
			self.assertRaises(SpApiError, customer_feedback.review_topics, ["B0AAA"], client=client)

	def test_a_403_comes_back_as_the_role_gap_it_is(self):
		client = Mock()
		client.get.side_effect = SpApiError("Forbidden", status_code=403)
		with self._marketplace():
			self.assertRaises(
				frappe.ValidationError,
				customer_feedback.review_topics,
				["B0AAA"],
				client=client,
			)

	def test_the_weekly_refresh_travels_with_the_answer(self):
		"""So a screen can date what it shows instead of implying it is live."""
		with self._marketplace():
			out = customer_feedback.review_topics(["B0AAA"], client=self._client({"topics": []}))
		self.assertEqual(out["B0AAA"]["refreshed_every_days"], 7)

	def test_a_repeated_asin_is_asked_about_once(self):
		client = self._client({"topics": []})
		with self._marketplace():
			customer_feedback.review_topics(["B0AAA", "B0AAA", " B0AAA "], client=client)
		self.assertEqual(client.get.call_count, 1)

	def test_nothing_to_ask_about_makes_no_call(self):
		client = Mock()
		self.assertEqual(customer_feedback.review_topics([], client=client), {})
		client.get.assert_not_called()
