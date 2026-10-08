# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""The listing agent is asked for the product facts a product type requires.

What is asked comes from the definition and the listing's own payload; what is kept
is shaped for the product type the finished title settles on; what approval merges
never overwrites. The seller's own declarations are never asked.
"""

import json
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.listing import attributes, handlers
from alaiy_os_connector_amazon_sp_api.spapi import listings, product_types

MP = frappe._dict(name="IN", marketplace_id="A21TJRUUN4KGV", currency="INR", language="en_IN")


def _text(title, enum=None):
	value = {"type": "string", **({"enum": enum} if enum else {})}
	return {
		"title": title,
		"type": "array",
		"items": {
			"type": "object",
			"required": ["language_tag", "value"],
			"properties": {
				"value": value,
				"language_tag": {"type": "string"},
				"marketplace_id": {"type": "string"},
			},
		},
	}


DEFINITION = {
	"type": "object",
	"required": ["item_name"],
	"properties": {
		"item_name": _text("Item Name"),
		"material": _text("Material Type"),
		"color": _text("Colour"),
		"closure": _text("Closure Type", enum=["zipper", "buckle"]),
		"manufacturer": _text("Manufacturer"),
		"item_depth_width_height": {
			"title": "Item Dimensions",
			"type": "array",
			"items": {
				"type": "object",
				"required": ["depth"],
				"properties": {
					"depth": {
						"type": "object",
						"required": ["value", "unit"],
						"properties": {
							"value": {"type": "number"},
							"unit": {"type": "string", "enum": ["centimeters"]},
						},
					},
					"marketplace_id": {"type": "string"},
				},
			},
		},
	},
	"allOf": [
		{"if": {"required": ["item_name"]}, "then": {"required": ["material", "closure", "manufacturer"]}},
	],
}


def _row(**values):
	return frappe.get_doc(
		{"doctype": "Amazon Product Listing", "sku": "SKU-1", "title": "Rope toy", **values}
	)


class Patched(UnitTestCase):
	def setUp(self):
		self.row = _row()
		for target, value in (
			("_register_row", lambda sku: self.row),
			("_marketplace", lambda *a, **k: MP),
			("_gtin_exempt_brands", lambda *a, **k: frozenset()),
		):
			p = patch.object(listings, target, side_effect=value)
			p.start()
			self.addCleanup(p.stop)
		p = patch.object(product_types, "get_definition", return_value=DEFINITION)
		p.start()
		self.addCleanup(p.stop)


class TestWhatIsAsked(Patched):
	def test_the_product_facts_the_definition_requires_are_asked(self):
		asked = {entry["attribute"]: entry for entry in attributes.to_fill("SKU-1", "BACKPACK")}
		self.assertEqual(set(asked), {"material", "closure"})
		self.assertEqual(asked["closure"]["accepted"], ["zipper", "buckle"])
		self.assertEqual(asked["material"]["fill"], {"value": ""})

	def test_the_sellers_declarations_are_never_asked(self):
		self.assertNotIn("manufacturer", [e["attribute"] for e in attributes.to_fill("SKU-1", "BACKPACK")])
		self.assertFalse(attributes.asked("importer_contact_information"))
		self.assertFalse(attributes.asked("other_product_image_locator_3"))

	def test_what_extra_attributes_already_holds_is_not_asked_again(self):
		self.row = _row(
			extra_attributes=json.dumps({"material": [{"value": "Cotton", "language_tag": "en_IN"}]})
		)
		self.assertEqual([e["attribute"] for e in attributes.to_fill("SKU-1", "BACKPACK")], ["closure"])

	def test_no_product_type_asks_nothing(self):
		self.assertEqual(attributes.to_fill("SKU-1", None), [])

	def test_a_listing_on_an_existing_asin_is_not_asked(self):
		self.assertIsNone(handlers._attributes_block(_row(asin="B0TEST12345"), "BACKPACK"))

	def test_a_definition_that_cannot_be_read_asks_nothing_rather_than_failing(self):
		with patch.object(attributes, "to_fill", side_effect=RuntimeError("down")):
			self.assertIsNone(handlers._attributes_block(_row(), "BACKPACK"))


class TestWhatIsKept(Patched):
	def test_answers_are_shaped_for_the_product_type(self):
		shaped, dropped = attributes.shape(
			"SKU-1",
			"BACKPACK",
			{
				"material": "Cotton",
				"item_depth_width_height": {"depth": {"value": 30, "unit": "centimeters"}},
			},
		)
		self.assertEqual(
			shaped["material"],
			[{"value": "Cotton", "marketplace_id": MP.marketplace_id, "language_tag": "en_IN"}],
		)
		self.assertEqual(shaped["item_depth_width_height"][0]["depth"], {"value": 30, "unit": "centimeters"})
		self.assertEqual(dropped, [])

	def test_a_sellers_declaration_or_an_unknown_attribute_is_dropped(self):
		shaped, dropped = attributes.shape(
			"SKU-1", "BACKPACK", {"manufacturer": "Someone", "not_in_this_type": "x", "color": ""}
		)
		self.assertEqual(shaped, {})
		self.assertEqual(dropped, ["color", "manufacturer", "not_in_this_type"])

	def test_an_answer_in_amazons_array_form_is_taken(self):
		shaped, _dropped = attributes.shape("SKU-1", "BACKPACK", {"color": [{"value": "Red"}]})
		self.assertEqual(shaped["color"][0]["value"], "Red")

	def test_dropped_answers_are_said_in_notes(self):
		doc = frappe._dict(sku="SKU-1", product_type="BACKPACK", notes=None)
		handlers._save_attributes(doc, {"attributes": {"material": "Cotton", "manufacturer": "Someone"}})
		self.assertIn("material", json.loads(doc.attributes_json))
		self.assertIn("manufacturer", doc.notes)


class TestMerging(UnitTestCase):
	SHAPED: ClassVar[dict] = {"material": [{"value": "Cotton"}], "color": [{"value": "Red"}]}

	def test_only_what_is_missing_is_added(self):
		existing = json.dumps({"material": [{"value": "Jute"}]})
		merged = json.loads(attributes.merged_into(existing, self.SHAPED))
		self.assertEqual(merged["material"], [{"value": "Jute"}])
		self.assertEqual(merged["color"], [{"value": "Red"}])

	def test_nothing_missing_or_unreadable_text_is_left_alone(self):
		self.assertIsNone(attributes.merged_into(json.dumps(self.SHAPED), self.SHAPED))
		self.assertIsNone(attributes.merged_into("not json", self.SHAPED))
