# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""`channel.normalize`: the defects fixed in code instead of sent back to the model.

Pure functions over a listing dict. What they guard is cost and reliability: each
defect `normalize` misses is a rejected save, a resent conversation, and another
chance for a small model to give up.
"""

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.listing import channel

TITLE = "BLUEGEARS LED Fog Light Kit | Automotive Driving Lamp | High Visibility | Night Driving | Set of 2"
BULLETS = ["DURABLE BUILD - Aluminium housing with a sealed lens for all-weather use on the road"] * 5


def listing(**values):
	return {"title": TITLE, "bullet_points": BULLETS, "description": "A lamp.", **values}


class TestNormalizeKeywords(UnitTestCase):
	def test_words_already_in_the_title_or_bullets_are_dropped(self):
		out = channel.normalize(listing(keywords=["fog lamp foglight", "housing lens bumper"]))
		self.assertEqual(out["keywords"], ["foglight", "bumper"])

	def test_the_validator_no_longer_names_repeated_words(self):
		out = channel.normalize(listing(keywords=["led fog light", "foglamp auxiliary"]))
		self.assertFalse(any("already in the title" in d for d in channel._check_keywords(out["keywords"], TITLE, BULLETS)))

	def test_short_words_are_kept(self):
		# The validator only counts words longer than two letters as spent.
		out = channel.normalize(listing(keywords=["of 2 xl"]))
		self.assertEqual(out["keywords"], ["of 2 xl"])

	def test_repeats_and_empties_are_dropped(self):
		out = channel.normalize(listing(keywords=["Foglight", "foglight", "", "fog"]))
		self.assertEqual(out["keywords"], ["Foglight"])

	def test_a_term_too_long_for_its_row_is_split(self):
		term = " ".join(f"word{n}" for n in range(40))
		out = channel.normalize(listing(keywords=[term]))
		self.assertTrue(all(len(k) <= channel.KEYWORD_ROW_MAX for k in out["keywords"]))

	def test_trimmed_to_the_byte_budget(self):
		out = channel.normalize(listing(keywords=[f"term{n} extra{n}" for n in range(60)]))
		self.assertLessEqual(len(" ".join(out["keywords"]).encode("utf-8")), channel.KEYWORD_BYTE_BUDGET)

	def test_a_listing_without_a_keyword_list_is_returned_as_is(self):
		self.assertNotIn("keywords", channel.normalize(listing()))


class TestNormalizePerfect(UnitTestCase):
	def test_perfect_becomes_ideal_everywhere_it_publishes(self):
		out = channel.normalize(listing(
			description="A perfect gift, perfect for cars.",
			bullet_points=["PERFECT FIT - Made for a perfect match"] * 5,
			keywords=[],
		))
		self.assertEqual(out["description"], "An ideal gift, ideal for cars.")
		self.assertEqual(out["bullet_points"][0], "IDEAL FIT - Made for an ideal match")

	def test_other_words_are_left_alone(self):
		out = channel.normalize(listing(description="Perfectly sized, the best lamp.", keywords=[]))
		self.assertEqual(out["description"], "Perfectly sized, the best lamp.")
