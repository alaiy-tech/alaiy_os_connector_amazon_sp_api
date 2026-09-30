# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""`item_product_specs`: which of the supplier's specification table the agent sees.

Without it the agent guessed materials from photos and flagged everything else for
review, although the supplier had sent it. The filter matters as much as the read:
the table is machine-translated and half of it is trade terms, which would be wrong
in a listing.
"""

import json
from unittest import mock

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.listing import handlers

TABLE = {
	"Brand": ["Alisa bo"],
	"Item number": ["Summer cushion"],
	"Filler": ["Velvet cotton"],
	"Surface material": ["Wooden"],
	"Function": ["Ventilation"],
	"Color": [f"colour {n}" for n in range(10)],
	"Main downstream platforms": ["Aliexpress"],
	"Whether to export": ["Yes"],
	"Is it for foreign trade?": ["Yes"],
	"There are authorizable private brands": ["No"],
	"Place of import": ["China"],
}


def specs(table=TABLE, variant=None):
	values = {handlers._ITEM_ATTRIBUTES_FIELD: json.dumps(table)}
	with mock.patch.object(handlers.frappe.db, "has_column", return_value=True), \
		mock.patch.object(handlers.frappe.db, "get_value", side_effect=lambda dt, name, field, *a, **k: values.get(field)), \
		mock.patch.object(handlers, "item_variant_specs", return_value=variant or {}):
		return handlers.item_product_specs("NG-1")


class TestProductSpecs(UnitTestCase):
	def test_product_facts_are_shown(self):
		out = specs()
		self.assertEqual(out["Filler"], "Velvet cotton")
		self.assertEqual(out["Surface material"], "Wooden")
		self.assertEqual(out["Function"], "Ventilation")

	def test_the_supplier_brand_and_trade_terms_are_not(self):
		out = specs()
		for name in ("Brand", "Item number", "Main downstream platforms", "Whether to export",
					 "Is it for foreign trade?", "There are authorizable private brands", "Place of import"):
			self.assertNotIn(name, out)

	def test_a_long_value_list_is_capped(self):
		self.assertEqual(specs()["Color"], "colour 0, colour 1, colour 2, colour 3, colour 4, colour 5 (+4 more)")

	def test_a_variant_axis_is_left_to_variant_specifications(self):
		self.assertNotIn("Color", specs(variant={"Color": "chestnut brown"}))

	def test_a_malformed_table_is_nothing(self):
		values = {handlers._ITEM_ATTRIBUTES_FIELD: "not json"}
		with mock.patch.object(handlers.frappe.db, "has_column", return_value=True), \
			mock.patch.object(handlers.frappe.db, "get_value", side_effect=lambda dt, name, field, *a, **k: values.get(field)):
			self.assertEqual(handlers.item_product_specs("NG-1"), {})
