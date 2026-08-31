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
