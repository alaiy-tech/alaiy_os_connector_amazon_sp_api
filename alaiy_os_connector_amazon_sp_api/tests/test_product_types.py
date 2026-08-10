# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the product-type-from-title lookup.

Two properties are worth pinning. Amazon's ordering is the only confidence
signal the response carries — there are no scores — so anything that sorts,
de-duplicates or set-ifies the list destroys information silently. And an entry
is scoped to the marketplaces whose definitions it covers, so a product type
that does not cover ours must not be offered: publishing with it would fail.

Pure transformation only — no SP-API calls.
"""

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import product_types

MP_ID = "A21TJRUUN4KGV"  # amazon.in
OTHER_MP_ID = "ATVPDKIKX0DER"


def _pt(name, display_name=None, marketplace_ids=(MP_ID,)):
	entry = {"name": name, "marketplaceIds": list(marketplace_ids)}
	if display_name is not None:
		entry["displayName"] = display_name
	return entry


class TestProductTypeSuggestions(UnitTestCase):
	def test_preserves_amazons_order(self):
		resp = {"productTypes": [_pt("SHOES"), _pt("SANDAL"), _pt("BOOT")], "productTypeVersion": "v1"}
		self.assertEqual(
			[s["product_type"] for s in product_types.suggestions_from_response(resp, MP_ID)],
			["SHOES", "SANDAL", "BOOT"],
		)

	def test_display_name_falls_back_to_the_raw_name(self):
		resp = {"productTypes": [_pt("LUGGAGE", display_name="Luggage"), _pt("SHIRT")]}
		self.assertEqual(
			product_types.suggestions_from_response(resp, MP_ID),
			[
				{"product_type": "LUGGAGE", "display_name": "Luggage"},
				{"product_type": "SHIRT", "display_name": "SHIRT"},
			],
		)

	def test_drops_types_not_defined_for_this_marketplace(self):
		resp = {"productTypes": [_pt("SHIRT"), _pt("US_ONLY", marketplace_ids=(OTHER_MP_ID,))]}
		self.assertEqual(
			[s["product_type"] for s in product_types.suggestions_from_response(resp, MP_ID)], ["SHIRT"]
		)

	def test_keeps_entries_with_no_marketplace_scope(self):
		# marketplaceIds is required by the schema, but an absent list is not a
		# statement that the type is unavailable — dropping it would discard the
		# only answer Amazon gave.
		resp = {"productTypes": [{"name": "SHIRT"}]}
		self.assertEqual(
			product_types.suggestions_from_response(resp, MP_ID),
			[{"product_type": "SHIRT", "display_name": "SHIRT"}],
		)

	def test_no_match_is_an_empty_list_not_an_error(self):
		self.assertEqual(product_types.suggestions_from_response({"productTypes": []}, MP_ID), [])
		self.assertEqual(product_types.suggestions_from_response({}, MP_ID), [])
		self.assertEqual(product_types.suggestions_from_response(None, MP_ID), [])

	def test_skips_entries_without_a_name(self):
		resp = {"productTypes": [{"displayName": "Nameless"}, _pt("SHIRT")]}
		self.assertEqual(
			[s["product_type"] for s in product_types.suggestions_from_response(resp, MP_ID)], ["SHIRT"]
		)


class TestSuggestProductTypes(UnitTestCase):
	def test_blank_title_is_rejected_before_any_call(self):
		for title in (None, "", "   "):
			with self.assertRaises(frappe.ValidationError):
				product_types.suggest_product_types(title, client=_never_called())


def _never_called():
	class _Client:
		def get(self, *args, **kwargs):
			raise AssertionError("SP-API must not be called for a blank title")

	return _Client()
