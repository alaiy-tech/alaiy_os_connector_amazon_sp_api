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

from typing import ClassVar

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


class TestAttributeValue(UnitTestCase):
	"""A value someone knows, in the shape the product type's definition gives it."""

	MP = "A21TJRUUN4KGV"
	SCHEMA: ClassVar[dict] = {
		"properties": {
			"manufacturer": {
				"type": "array",
				"items": {
					"type": "object",
					"properties": {
						"value": {"type": "string"},
						"language_tag": {"type": "string"},
						"marketplace_id": {"type": "string"},
					},
				},
			},
			"country_of_origin": {
				"type": "array",
				"items": {
					"type": "object",
					"properties": {"value": {"type": "string"}, "marketplace_id": {"type": "string"}},
				},
			},
			"base_product_mfg_warranty_period": {
				"type": "array",
				"items": {
					"type": "object",
					"properties": {
						"value": {"type": "number"},
						"unit": {"type": "string"},
						"marketplace_id": {"type": "string"},
					},
				},
			},
		}
	}

	def value(self, name, value):
		return product_types.attribute_value(self.SCHEMA, name, value, self.MP, "en_IN")

	def test_a_text_attribute_is_language_tagged(self):
		self.assertEqual(
			self.value("manufacturer", "Maker"),
			[{"value": "Maker", "marketplace_id": self.MP, "language_tag": "en_IN"}],
		)

	def test_an_attribute_without_a_language_gets_none(self):
		self.assertEqual(self.value("country_of_origin", "HK"), [{"value": "HK", "marketplace_id": self.MP}])

	def test_a_structured_value_keeps_its_keys(self):
		self.assertEqual(
			self.value("base_product_mfg_warranty_period", {"value": 0, "unit": "months"}),
			[{"value": 0, "unit": "months", "marketplace_id": self.MP}],
		)

	def test_an_attribute_the_product_type_does_not_have_is_none(self):
		self.assertIsNone(self.value("contains_liquid_contents", False))
		self.assertIsNone(product_types.attribute_value(None, "manufacturer", "Maker", self.MP))


class TestListingAttributeValues(UnitTestCase):
	def test_values_are_shaped_for_the_listings_product_type(self):
		from unittest.mock import patch

		from alaiy_os_connector_amazon_sp_api.spapi import listings

		row = frappe._dict(product_type="PET_TOY", marketplace="IN")
		mp = frappe._dict(name="IN", marketplace_id=TestAttributeValue.MP, language="en_IN")
		with (
			patch.object(listings, "_register_row", return_value=row),
			patch.object(listings, "_marketplace", return_value=mp),
			patch.object(listings.product_types, "get_definition", return_value=TestAttributeValue.SCHEMA),
		):
			shaped = listings.attribute_values("SKU-1", {"manufacturer": "Maker", "unknown_attribute": "x"})
		self.assertEqual(list(shaped), ["manufacturer"])
		self.assertEqual(shaped["manufacturer"][0]["language_tag"], "en_IN")

	def test_no_product_type_yet_is_nothing(self):
		from unittest.mock import patch

		from alaiy_os_connector_amazon_sp_api.spapi import listings

		with patch.object(listings, "_register_row", return_value=frappe._dict(product_type=None)):
			self.assertEqual(listings.attribute_values("SKU-1", {"manufacturer": "Maker"}), {})
