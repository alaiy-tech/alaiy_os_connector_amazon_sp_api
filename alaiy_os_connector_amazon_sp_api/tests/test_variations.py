# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for variation parentage: reading it, and applying it to a row.

Parentage is deliberately handled differently from content. Content may be
legitimately absent, so a missing value never overwrites a stored one. Amazon
answers parentage definitively whenever `relationships` is requested, so a
successful answer is applied verbatim — a listing that has left a family must
stop claiming it. These tests pin that asymmetry, because it is the kind of thing
a later "make it consistent" refactor would flatten.
"""

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import catalog, listings

MP = frappe._dict(
	{"name": "A21TJRUUN4KGV", "marketplace_id": "A21TJRUUN4KGV", "currency": "INR", "language": "en_IN"}
)
OTHER_MP_ID = "ATVPDKIKX0DER"


def _rel(marketplace_id=MP.marketplace_id, **relationship):
	return {"marketplaceId": marketplace_id, "relationships": [relationship]}


class TestVariationParsing(UnitTestCase):
	def test_child_reports_its_parent_and_theme(self):
		item = {
			"relationships": [
				_rel(
					type="VARIATION",
					parentAsins=["B0PARENT001"],
					variationTheme={"attributes": ["color", "size"], "theme": "COLOR/SIZE"},
				)
			]
		}
		self.assertEqual(
			catalog._variation_from(item, MP.marketplace_id),
			{
				"parent_asin": "B0PARENT001",
				"is_variation_parent": False,
				"child_asin_count": 0,
				"variation_theme": "COLOR/SIZE",
			},
		)

	def test_parent_reports_its_children_and_no_parent(self):
		item = {
			"relationships": [
				_rel(
					type="VARIATION",
					childAsins=["B0CHILD001", "B0CHILD002", "B0CHILD003"],
					variationTheme={"attributes": ["size"], "theme": "SIZE"},
				)
			]
		}
		self.assertEqual(
			catalog._variation_from(item, MP.marketplace_id),
			{
				"parent_asin": None,
				"is_variation_parent": True,
				"child_asin_count": 3,
				"variation_theme": "SIZE",
			},
		)

	def test_package_hierarchy_is_not_parentage(self):
		# The same array carries case-pack relationships. Reading those as a
		# variation parent would invent families that do not exist.
		item = {"relationships": [_rel(type="PACKAGE_HIERARCHY", parentAsins=["B0CASEPACK1"])]}
		self.assertIsNone(catalog._variation_from(item, MP.marketplace_id)["parent_asin"])

	def test_standalone_asin_has_no_family(self):
		self.assertEqual(
			catalog._variation_from({"relationships": []}, MP.marketplace_id),
			{
				"parent_asin": None,
				"is_variation_parent": False,
				"child_asin_count": 0,
				"variation_theme": None,
			},
		)

	def test_relationships_are_read_from_this_marketplaces_block(self):
		item = {
			"relationships": [
				_rel(marketplace_id=OTHER_MP_ID, type="VARIATION", parentAsins=["B0USPARENT"]),
				_rel(type="VARIATION", parentAsins=["B0INPARENT"]),
			]
		}
		self.assertEqual(
			catalog._variation_from(item, MP.marketplace_id)["parent_asin"], "B0INPARENT"
		)

	def test_content_from_item_carries_the_variation_keys(self):
		item = {
			"asin": "B0CHILD001",
			"summaries": [{"marketplaceId": MP.marketplace_id, "itemName": "Shirt - Red - M"}],
			"relationships": [
				_rel(
					type="VARIATION",
					parentAsins=["B0PARENT001"],
					variationTheme={"attributes": ["color"], "theme": "COLOR"},
				)
			],
		}
		content = catalog.content_from_item(item, MP)
		self.assertEqual(content["parent_asin"], "B0PARENT001")
		self.assertEqual(content["variation_theme"], "COLOR")
		self.assertFalse(content["is_variation_parent"])


class TestApplyVariation(UnitTestCase):
	def _row(self, **values):
		return frappe.get_doc({"doctype": "Amazon Product Listing", "sku": "SKU-1", **values})

	def test_records_the_parent(self):
		row = self._row()
		listings._apply_variation(
			row,
			{"parent_asin": "B0PARENT001", "variation_theme": "SIZE", "is_variation_parent": False},
		)
		self.assertEqual(row.parent_asin, "B0PARENT001")
		self.assertEqual(row.variation_theme, "SIZE")
		self.assertEqual(row.is_variation_parent, 0)

	def test_flags_a_parent_row(self):
		row = self._row()
		listings._apply_variation(
			row, {"parent_asin": None, "variation_theme": "SIZE", "is_variation_parent": True}
		)
		self.assertEqual(row.is_variation_parent, 1)
		self.assertIsNone(row.parent_asin)

	def test_a_definitive_answer_clears_stale_parentage(self):
		# The ASIN left its family. Unlike content, this must not be sticky.
		row = self._row(parent_asin="B0OLDPARENT", variation_theme="COLOR", is_variation_parent=0)
		listings._apply_variation(
			row, {"parent_asin": None, "variation_theme": None, "is_variation_parent": False}
		)
		self.assertIsNone(row.parent_asin)
		self.assertIsNone(row.variation_theme)

	def test_a_missing_answer_changes_nothing(self):
		# Catalog look-up failed, or there was no ASIN to look up. Not the same as
		# "this ASIN has no parent".
		row = self._row(parent_asin="B0PARENT001", variation_theme="COLOR")
		listings._apply_variation(row, None)
		self.assertEqual(row.parent_asin, "B0PARENT001")
		self.assertEqual(row.variation_theme, "COLOR")
