# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for what creating a catalog entry decides, before it reaches Amazon.

The decision layer only — no SP-API calls, no saved documents. What is worth
pinning down here is everything that distinguishes minting an ASIN from
publishing an offer against one, because the two submissions look similar and
mean opposite things:

  * `_catalog_attributes` — carries the product's own content and, critically,
	does NOT carry merchant_suggested_asin. That attribute is what makes a
	submission an offer against someone else's catalog entry.
  * `_identifier_attributes` — a barcode or an exemption, never both, never
	neither silently.
  * `asin_create_blockers` — Amazon's requirements for the product type, read off
	its schema rather than assumed, reported rather than attempted.
  * `_extra_attributes` — the escape hatch that makes a blocked row unblockable
	is worth nothing if bad JSON reaches Amazon as an empty dict.
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import listings, product_types

MARKETPLACE = frappe._dict(marketplace_id="A21TJRUUN4KGV", currency="INR", language="en_IN")


def _row(**values):
	"""An unsaved Amazon Product Listing — a draft that has never been to Amazon."""
	return frappe.get_doc({"doctype": "Amazon Product Listing", "sku": "NG-1-2", **values})


def _attributes(row, exempt_brands=frozenset()):
	with patch.object(listings, "_gtin_exempt_brands", return_value=exempt_brands):
		return listings._catalog_attributes(MARKETPLACE, row)


class TestIdentifierAttributes(UnitTestCase):
	def test_a_barcode_is_sent_as_an_assigned_identifier(self):
		attrs = _attributes(_row(product_id="8901234567890", product_id_type="EAN"))
		self.assertEqual(
			attrs["externally_assigned_product_identifier"],
			[{"marketplace_id": MARKETPLACE.marketplace_id, "type": "ean", "value": "8901234567890"}],
		)
		self.assertNotIn("supplier_declared_has_product_identifier_exemption", attrs)

	def test_a_barcode_beats_an_exemption_the_brand_also_holds(self):
		# An exemption is a dispensation for products with no GTIN, not a way to
		# skip entering the one you have.
		attrs = _attributes(
			_row(product_id="8901234567890", brand="Naya"), exempt_brands={"naya"}
		)
		self.assertIn("externally_assigned_product_identifier", attrs)
		self.assertNotIn("supplier_declared_has_product_identifier_exemption", attrs)

	def test_an_exempt_brand_declares_the_exemption(self):
		attrs = _attributes(_row(brand="Naya"), exempt_brands={"naya"})
		self.assertEqual(
			attrs["supplier_declared_has_product_identifier_exemption"],
			[{"marketplace_id": MARKETPLACE.marketplace_id, "value": True}],
		)

	def test_brand_matching_ignores_case(self):
		self.assertIn(
			"supplier_declared_has_product_identifier_exemption",
			_attributes(_row(brand="  NAYA  "), exempt_brands={"naya"}),
		)

	def test_an_unexempt_brand_carries_no_identifier_at_all(self):
		# The case that must not silently pass: no barcode, no exemption, and
		# nothing in the payload standing in for one.
		attrs = _attributes(_row(brand="Naya"), exempt_brands={"other"})
		for name in listings._IDENTIFIER_ATTRIBUTES:
			self.assertNotIn(name, attrs)


class TestCatalogAttributes(UnitTestCase):
	def test_a_create_never_suggests_an_asin(self):
		# merchant_suggested_asin is what makes a submission an offer against an
		# existing catalog entry. Sending it here would ask for the opposite of
		# what this path is for.
		self.assertNotIn("merchant_suggested_asin", _attributes(_row(asin="B0TEST12345", title="X")))

	def test_the_products_own_content_is_carried(self):
		# The other half of the same distinction: create_listing drops content
		# because on an offer it belongs to whoever owns the ASIN. Here it is the
		# product being described, so it has to go.
		attrs = _attributes(
			_row(
				title="Cotton Shirt",
				brand="Naya",
				description="Soft cotton.",
				bullet_points=[{"bullet": "Breathable"}],
				keywords=[{"keyword": "shirt"}],
				images=[{"image_url": "main.jpg", "is_main": 1}],
			)
		)
		self.assertEqual(attrs["item_name"][0]["value"], "Cotton Shirt")
		self.assertEqual(attrs["brand"][0]["value"], "Naya")
		self.assertEqual(attrs["product_description"][0]["value"], "Soft cotton.")
		self.assertEqual(attrs["bullet_point"][0]["value"], "Breathable")
		self.assertEqual(attrs["generic_keyword"][0]["value"], "shirt")
		self.assertEqual(attrs["main_product_image_locator"][0]["media_location"], "main.jpg")

	def test_condition_defaults_rather_than_being_omitted(self):
		self.assertEqual(_attributes(_row())["condition_type"][0]["value"], "new_new")

	def test_extra_attributes_win_over_what_was_built(self):
		# Declared precedence: the operator naming an attribute explicitly is the
		# only way to correct one this builder gets wrong for their product type.
		attrs = _attributes(
			_row(title="Built", extra_attributes='{"item_name": [{"value": "Overridden"}]}')
		)
		self.assertEqual(attrs["item_name"], [{"value": "Overridden"}])


class TestExtraAttributes(UnitTestCase):
	def test_an_empty_field_is_no_attributes(self):
		self.assertEqual(listings._extra_attributes(_row()), {})

	def test_invalid_json_is_refused_rather_than_dropped(self):
		# Silently reading this as {} would submit a payload missing exactly the
		# attributes the operator added the field to supply.
		with self.assertRaises(frappe.ValidationError):
			listings._extra_attributes(_row(extra_attributes="{not json"))

	def test_a_json_list_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			listings._extra_attributes(_row(extra_attributes='["country_of_origin"]'))


SCHEMA = {
	"required": ["item_name", "country_of_origin"],
	"properties": {"country_of_origin": {"title": "Country of Origin"}},
}


class TestAsinCreateBlockers(UnitTestCase):
	def _blockers(self, row, exempt_brands=frozenset(), schema=SCHEMA):
		return listings.asin_create_blockers(row, _attributes(row, exempt_brands), schema)

	def test_a_complete_row_is_ready(self):
		row = _row(
			title="Cotton Shirt",
			brand="Naya",
			product_type="SHIRT",
			extra_attributes='{"country_of_origin": [{"value": "IN"}]}',
		)
		self.assertEqual(self._blockers(row, exempt_brands={"naya"}), [])

	def test_a_row_that_already_has_an_asin_is_told_to_publish_instead(self):
		row = _row(asin="B0TEST12345", title="X", product_type="SHIRT")
		self.assertTrue(any("already has an ASIN" in b for b in self._blockers(row)))

	def test_no_product_type_stops_before_the_schema_is_consulted(self):
		# There is no required-attribute list without a product type, so reporting
		# the schema's requirements here would be reporting some other product's.
		blockers = self._blockers(_row(title="X"), schema=None)
		self.assertEqual(len(blockers), 1)
		self.assertIn("No product type", blockers[0])

	def test_a_missing_title_is_named(self):
		row = _row(brand="Naya", product_type="SHIRT")
		self.assertTrue(any("No title" in b for b in self._blockers(row, exempt_brands={"naya"})))

	def test_no_identifier_points_at_the_exemption_for_the_named_brand(self):
		row = _row(title="X", brand="Naya", product_type="SHIRT")
		blocker = next(b for b in self._blockers(row) if "identifier" in b)
		self.assertIn("Naya", blocker)
		self.assertIn("GTIN-Exempt Brands", blocker)

	def test_no_identifier_and_no_brand_asks_for_the_brand_first(self):
		# An exemption is granted per brand, so "add a brand" is the actionable
		# step; naming the exemption alone would be advice they cannot act on.
		row = _row(title="X", product_type="SHIRT")
		blocker = next(b for b in self._blockers(row) if "identifier" in b)
		self.assertIn("no brand", blocker)

	def test_a_required_attribute_the_row_cannot_supply_is_named_with_its_label(self):
		row = _row(title="X", brand="Naya", product_type="SHIRT")
		blocker = next(b for b in self._blockers(row, exempt_brands={"naya"}) if "country_of_origin" in b)
		self.assertIn("Country of Origin", blocker)
		self.assertIn("Extra Attributes", blocker)

	def test_a_product_type_with_no_definition_here_is_a_blocker(self):
		row = _row(title="X", brand="Naya", product_type="MADE_UP")
		blockers = self._blockers(row, exempt_brands={"naya"}, schema=None)
		self.assertTrue(any("no definition" in b for b in blockers))


class TestRequiredAttributes(UnitTestCase):
	def test_the_top_level_required_list_is_what_is_read(self):
		self.assertEqual(product_types.required_attributes(SCHEMA), ["item_name", "country_of_origin"])

	def test_a_schema_without_a_required_list_requires_nothing(self):
		self.assertEqual(product_types.required_attributes({"properties": {}}), [])

	def test_no_schema_at_all_requires_nothing(self):
		self.assertEqual(product_types.required_attributes(None), [])

	def test_an_attribute_label_falls_back_to_its_raw_name(self):
		# A blocker naming supplier_declared_dg_hz_regulation is still more use
		# than one that says an attribute is missing without saying which.
		self.assertEqual(product_types.attribute_title(SCHEMA, "item_name"), "item_name")
		self.assertEqual(product_types.attribute_title(SCHEMA, "country_of_origin"), "Country of Origin")


# A required attribute of each kind: one a listing field feeds, and two that
# nothing on the form does — the enum one being where a bare "add it" is least
# actionable, since the title an operator sees says "country-of-origin" and the
# value Amazon wants is "IN".
ADVICE_SCHEMA = {
	"required": ["brand", "country_of_origin", "supplier_declared_dg_hz_regulation"],
	"properties": {
		"brand": {"title": "Brand Name"},
		"country_of_origin": {
			"title": "country-of-origin",
			"type": "array",
			"items": {
				"type": "object",
				"required": ["value", "marketplace_id"],
				"properties": {
					"value": {"type": "string", "enum": ["IN", "CN", "US"]},
					"marketplace_id": {"type": "string"},
				},
			},
		},
		"supplier_declared_dg_hz_regulation": {
			"title": "Dangerous Goods Regulations",
			"type": "array",
			"items": {
				"type": "object",
				"required": ["value"],
				"properties": {"value": {"type": "string", "enum": ["not_applicable", "ghs"]}},
			},
		},
	},
}


class TestBlockerAdvice(UnitTestCase):
	"""A blocker is only worth its message if the message names the right place.

	`brand` has a field on the form. Telling an operator to hand-write JSON for it
	is not merely unhelpful — it is a wrong instruction that *works*, leaving two
	sources for one value with the form's copy silently losing.
	"""

	def _blockers(self, row):
		return listings.asin_create_blockers(row, _attributes(row), ADVICE_SCHEMA)

	def test_a_field_backed_attribute_points_at_the_field(self):
		row = _row(title="X", product_type="PET_ACTIVITY_STRUCTURE")
		blocker = next(b for b in self._blockers(row) if "Brand Name" in b)
		self.assertIn("Fill in Brand on this row", blocker)
		self.assertNotIn("Extra Attributes", blocker)

	def test_an_attribute_with_no_field_still_points_at_extra_attributes(self):
		row = _row(title="X", product_type="PET_ACTIVITY_STRUCTURE")
		blocker = next(b for b in self._blockers(row) if "Dangerous Goods" in b)
		self.assertIn("Extra Attributes", blocker)

	def test_accepted_values_are_named_for_an_enumerated_attribute(self):
		# "country-of-origin" as a label tells an operator nothing about what to
		# type. The enum does.
		row = _row(title="X", product_type="PET_ACTIVITY_STRUCTURE")
		blocker = next(b for b in self._blockers(row) if "country_of_origin" in b)
		self.assertIn("IN", blocker)

	def test_a_long_enum_is_capped_and_says_how_many_more(self):
		schema = {
			"required": ["country_of_origin"],
			"properties": {
				"country_of_origin": {
					"title": "country-of-origin",
					"type": "array",
					"items": {
						"type": "object",
						"required": ["value"],
						"properties": {"value": {"enum": [f"C{i}" for i in range(50)]}},
					},
				}
			},
		}
		row = _row(title="X", brand="Naya", product_type="X")
		blocker = next(
			b
			for b in listings.asin_create_blockers(row, _attributes(row, {"naya"}), schema)
			if "country_of_origin" in b
		)
		self.assertIn("42 more", blocker)


class TestSuggestedExtraAttributes(UnitTestCase):
	def test_the_stub_is_shaped_like_the_schema_and_carries_the_marketplace(self):
		example = product_types.attribute_example(
			ADVICE_SCHEMA, "country_of_origin", MARKETPLACE.marketplace_id
		)
		self.assertEqual(example, [{"value": "IN", "marketplace_id": MARKETPLACE.marketplace_id}])

	def test_only_required_keys_of_the_item_shape_are_offered(self):
		# supplier_declared_dg_hz_regulation's item requires `value` alone, so a
		# marketplace_id in the stub would be noise the operator has to delete.
		example = product_types.attribute_example(
			ADVICE_SCHEMA, "supplier_declared_dg_hz_regulation", MARKETPLACE.marketplace_id
		)
		self.assertEqual(example, [{"value": "not_applicable"}])

	def test_a_field_backed_attribute_is_never_suggested_as_json(self):
		# The other half of the same rule: brand is a field, so it must not appear
		# in a JSON stub that would become a second source for it.
		row = _row(title="X", product_type="PET_ACTIVITY_STRUCTURE")
		with patch.object(listings, "_gtin_exempt_brands", return_value=frozenset()):
			stub = listings._suggested_extra_attributes(
				MARKETPLACE, row, listings._catalog_attributes(MARKETPLACE, row), ADVICE_SCHEMA
			)
		self.assertNotIn("brand", stub)
		self.assertIn("country_of_origin", stub)

	def test_what_the_row_already_holds_survives_the_merge(self):
		# Pasting the stub back must not discard an attribute entered earlier.
		row = _row(
			title="X",
			product_type="PET_ACTIVITY_STRUCTURE",
			extra_attributes='{"item_type_keyword": [{"value": "dog crate"}]}',
		)
		with patch.object(listings, "_gtin_exempt_brands", return_value=frozenset()):
			stub = listings._suggested_extra_attributes(
				MARKETPLACE, row, listings._catalog_attributes(MARKETPLACE, row), ADVICE_SCHEMA
			)
		self.assertEqual(stub["item_type_keyword"], [{"value": "dog crate"}])
