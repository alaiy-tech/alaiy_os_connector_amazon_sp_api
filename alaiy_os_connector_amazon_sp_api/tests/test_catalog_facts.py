# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the two catalog facts a cross-channel product needs.

Barcode and browse-node ancestry, both added to CATALOG_CONTENT_INCLUDED_DATA
so that a product can be recognised as the same physical thing on two channels
and grouped under the same category.

Only the pure transformation layer — no SP-API calls, no saved documents.

The failure these guard against is the quiet kind. An ASIN's identifiers live in
the *catalog*, while a listing's `externally_assigned_product_identifier`
attribute is populated only for a seller who created the ASIN. Reading the
attribute instead returns nothing for every reseller, and a catalogue matched on
nothing matches nothing while looking exactly like a matcher that ran.
"""

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import catalog, listings

MP = frappe._dict(
	{"name": "A21TJRUUN4KGV", "marketplace_id": "A21TJRUUN4KGV", "currency": "INR", "language": "en_IN"}
)
OTHER_MP_ID = "ATVPDKIKX0DER"


def _identifiers(pairs, marketplace_id=MP.marketplace_id):
	return [
		{
			"marketplaceId": marketplace_id,
			"identifiers": [{"identifierType": k, "identifier": v} for k, v in pairs],
		}
	]


def _node(name, node_id, parent=None):
	node = {"displayName": name, "classificationId": node_id}
	if parent:
		node["parent"] = parent
	return node


def _classifications(leaf, marketplace_id=MP.marketplace_id):
	return [{"marketplaceId": marketplace_id, "classifications": [leaf]}]


class TestIdentifiers(UnitTestCase):
	def test_reads_the_barcode_off_the_catalog_entry(self):
		item = {"identifiers": _identifiers([("EAN", "0819752013274")])}
		self.assertEqual(
			catalog.identifiers_from(item, MP.marketplace_id),
			[{"type": "EAN", "value": "0819752013274"}],
		)

	def test_keeps_every_identifier_not_just_the_pick(self):
		# The point of returning the list: an EAN and the UPC inside it are the
		# same barcode and differ by a leading zero. A matcher comparing only
		# the pick against a Shopify barcode holding the other form finds
		# nothing, which is the exact failure this feature exists to prevent.
		item = {"identifiers": _identifiers([("UPC", "819752013274"), ("EAN", "0819752013274")])}
		values = [e["value"] for e in catalog.identifiers_from(item, MP.marketplace_id)]
		self.assertIn("819752013274", values)
		self.assertIn("0819752013274", values)

	def test_orders_by_preference_not_by_amazons_order(self):
		item = {"identifiers": _identifiers([("UPC", "819752013274"), ("EAN", "0819752013274")])}
		first = catalog.identifiers_from(item, MP.marketplace_id)[0]
		self.assertEqual(first["type"], "EAN")

	def test_an_unknown_identifier_type_sorts_last_but_is_kept(self):
		item = {"identifiers": _identifiers([("ASIN", "B09XKQL3M2"), ("UPC", "819752013274")])}
		types = [e["type"] for e in catalog.identifiers_from(item, MP.marketplace_id)]
		self.assertEqual(types, ["UPC", "ASIN"])

	def test_ignores_another_marketplaces_identifiers(self):
		item = {"identifiers": _identifiers([("EAN", "9999999999999")], marketplace_id=OTHER_MP_ID)}
		# _pick_for_marketplace falls back to the first block when nothing
		# matches, which is right for a single-marketplace request; what must
		# not happen is a *second* marketplace's value winning over ours.
		item["identifiers"] += _identifiers([("EAN", "0819752013274")])
		self.assertEqual(
			catalog.identifiers_from(item, MP.marketplace_id)[0]["value"], "0819752013274"
		)

	def test_no_identifiers_is_an_empty_list_not_a_guess(self):
		self.assertEqual(catalog.identifiers_from({}, MP.marketplace_id), [])

	def test_drops_blank_and_duplicate_entries(self):
		item = {
			"identifiers": _identifiers(
				[("EAN", "0819752013274"), ("EAN", "0819752013274"), ("UPC", "")]
			)
		}
		self.assertEqual(len(catalog.identifiers_from(item, MP.marketplace_id)), 1)


class TestClassifications(UnitTestCase):
	def test_flattens_a_three_level_chain_root_first(self):
		leaf = _node("Laptops", "565108", _node("Computers", "541966", _node("Electronics", "172282")))
		got = catalog.classifications_from({"classifications": _classifications(leaf)}, MP.marketplace_id)
		self.assertEqual(got["category_l1"], "Electronics")
		self.assertEqual(got["category_l2"], "Computers")
		self.assertEqual(got["category_l3"], "Laptops")
		self.assertEqual(got["browse_node_id"], "565108")

	def test_a_shorter_chain_fills_from_the_top(self):
		leaf = _node("Tote Bags", "1035", _node("Bags", "1000"))
		got = catalog.classifications_from({"classifications": _classifications(leaf)}, MP.marketplace_id)
		self.assertEqual(got["category_l1"], "Bags")
		self.assertEqual(got["category_l2"], "Tote Bags")
		self.assertIsNone(got["category_l3"])

	def test_a_deeper_chain_keeps_the_top_two_and_the_leaf(self):
		leaf = _node(
			"Gaming Laptops",
			"900",
			_node("Laptops", "565108", _node("Computers", "541966", _node("Electronics", "172282"))),
		)
		got = catalog.classifications_from({"classifications": _classifications(leaf)}, MP.marketplace_id)
		self.assertEqual(got["category_l1"], "Electronics")
		self.assertEqual(got["category_l2"], "Computers")
		# The leaf, not the level that happens to be third from the top.
		self.assertEqual(got["category_l3"], "Gaming Laptops")
		self.assertEqual(got["browse_node_id"], "900")

	def test_a_self_referential_parent_terminates(self):
		# Not paranoia about Amazon: this runs inside a sync, and a cycle here
		# would spin a worker rather than fail.
		leaf = {"displayName": "Loop", "classificationId": "1"}
		leaf["parent"] = leaf
		got = catalog.classifications_from({"classifications": _classifications(leaf)}, MP.marketplace_id)
		self.assertEqual(got["category_l1"], "Loop")

	def test_no_classifications_is_all_none(self):
		got = catalog.classifications_from({}, MP.marketplace_id)
		self.assertEqual(
			got,
			{"category_l1": None, "category_l2": None, "category_l3": None, "browse_node_id": None},
		)


class TestApplyCatalogFacts(UnitTestCase):
	"""What reaches the register row, and — more importantly — what never leaves it."""

	def _row(self, **values):
		return frappe.get_doc({"doctype": "Amazon Product Listing", "sku": "SKU-1", **values})

	def test_writes_the_barcode_and_the_category(self):
		row = self._row()
		listings._apply_catalog_facts(
			row,
			{
				"product_id": "0819752013274",
				"product_id_type": "EAN",
				"category_l1": "Electronics",
				"category_l2": "Computers",
				"category_l3": "Laptops",
				"browse_node_id": "565108",
			},
		)
		self.assertEqual(row.product_id, "0819752013274")
		self.assertEqual(row.product_id_type, "EAN")
		self.assertEqual(row.amazon_category_l1, "Electronics")
		self.assertEqual(row.amazon_browse_node_id, "565108")

	def test_a_failed_catalog_lookup_leaves_the_barcode_alone(self):
		# The one that matters: product_id is what create_asin submits, and a
		# read that blanked it would leave a listing that can no longer be
		# created, with nothing in the log saying a read did it.
		row = self._row(product_id="0819752013274", product_id_type="EAN")
		listings._apply_catalog_facts(row, None)
		self.assertEqual(row.product_id, "0819752013274")

	def test_an_empty_answer_leaves_the_barcode_alone(self):
		row = self._row(product_id="0819752013274", product_id_type="EAN")
		listings._apply_catalog_facts(row, {"product_id": None, "category_l1": None})
		self.assertEqual(row.product_id, "0819752013274")

	def test_an_unmappable_identifier_type_stores_the_value_without_one(self):
		# The Select knows four types. An ASIN-typed identifier is still a real
		# value; rejecting the save over the type would lose it entirely.
		row = self._row()
		listings._apply_catalog_facts(row, {"product_id": "B09XKQL3M2", "product_id_type": "ASIN"})
		self.assertEqual(row.product_id, "B09XKQL3M2")
		self.assertFalse(row.get("product_id_type"))

	def test_a_shortened_chain_does_not_keep_the_old_leaf(self):
		# L2/L3 move with L1 or not at all. A product that went from three
		# levels to two would otherwise keep an L3 from the category it left,
		# which reads as a real leaf and is not one.
		row = self._row(
			amazon_category_l1="Electronics",
			amazon_category_l2="Computers",
			amazon_category_l3="Laptops",
		)
		listings._apply_catalog_facts(
			row, {"category_l1": "Bags", "category_l2": "Tote Bags", "category_l3": None}
		)
		self.assertEqual(row.amazon_category_l1, "Bags")
		self.assertEqual(row.amazon_category_l2, "Tote Bags")
		self.assertIsNone(row.amazon_category_l3)
