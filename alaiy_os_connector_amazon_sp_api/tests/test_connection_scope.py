# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for listing a SKU as the seller whose register row it is.

The listing code predates sellers sharing a bench, so it asks for "the"
connection in twenty-odd places — `_connection()`, `_marketplace()`, a bare
`SpApiClient()` — several calls below the entry point that knows which SKU it
is working on. On a bench where every buyer brings their own Amazon account,
each of those has to mean the row's seller, or one buyer's publish goes out on
another's account.

`connections.using` scopes an unnamed call to one connection, and
`listings.on_row_connection` opens that scope from the row. Pinned here: the
scope reaches an unnamed resolve, it nests and unwinds (exceptions included),
and a row nobody assigned to a seller resolves exactly as it always did.

No site. `frappe.db` is a fake answering the three look-ups involved.
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api import connections
from alaiy_os_connector_amazon_sp_api.spapi import listings


class FakeDb:
	"""Two sellers, `seller-a` flagged default, and a register keyed by SKU."""

	def __init__(self, listing_connections):
		self.listing_connections = listing_connections

	def exists(self, doctype, name=None):
		if doctype == "DocType":
			return True
		return doctype == connections.DOCTYPE and name in ("seller-a", "seller-b")

	def get_value(self, doctype, filters, fieldname=None, *args, **kwargs):
		if doctype == connections.DOCTYPE and filters == {"is_default": 1}:
			return "seller-a"
		if doctype == "Amazon Product Listing":
			return self.listing_connections.get(filters)
		return None


class TestConnectionScope(UnitTestCase):
	def setUp(self):
		frappe.local.amazon_connection = None
		self.db = FakeDb({"SKU-B": "seller-b", "SKU-OLD": None})
		patcher = patch.object(connections.frappe, "db", self.db)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_an_unnamed_call_outside_a_scope_is_the_default(self):
		self.assertEqual(connections.resolve_name(), "seller-a")

	def test_an_unnamed_call_inside_a_scope_is_that_connection(self):
		with connections.using("seller-b"):
			self.assertEqual(connections.resolve_name(), "seller-b")
		self.assertEqual(connections.resolve_name(), "seller-a")

	def test_a_named_call_inside_a_scope_still_wins(self):
		with connections.using("seller-b"):
			self.assertEqual(connections.resolve_name("seller-a"), "seller-a")

	def test_an_empty_scope_changes_nothing(self):
		with connections.using("seller-b"):
			with connections.using(None):
				self.assertEqual(connections.resolve_name(), "seller-b")
		with connections.using(""):
			self.assertEqual(connections.resolve_name(), "seller-a")

	def test_scopes_nest_and_unwind_on_an_exception(self):
		with connections.using("seller-a"):
			with self.assertRaises(ValueError):
				with connections.using("seller-b"):
					raise ValueError
			self.assertEqual(connections.scoped(), "seller-a")
		self.assertIsNone(connections.scoped())

	def test_a_row_is_worked_on_as_its_own_seller(self):
		seen = []

		@listings.on_row_connection
		def probe(sku, marketplace=None):
			seen.append(connections.resolve_name())

		probe("SKU-B")
		probe(sku="SKU-B")
		self.assertEqual(seen, ["seller-b", "seller-b"])
		self.assertIsNone(connections.scoped())

	def test_a_row_without_a_seller_resolves_as_it_always_did(self):
		seen = []

		@listings.on_row_connection
		def probe(sku):
			seen.append(connections.resolve_name())

		probe("SKU-OLD")
		probe("SKU-NOT-IN-THE-REGISTER")
		self.assertEqual(seen, ["seller-a", "seller-a"])

	def test_stamp_fills_only_a_blank(self):
		blank = frappe._dict(connection=None)
		owned = frappe._dict(connection="seller-a")
		with connections.using("seller-b"):
			listings.stamp_connection(blank)
			listings.stamp_connection(owned)
		self.assertEqual(blank.connection, "seller-b")
		self.assertEqual(owned.connection, "seller-a")

	def test_stamp_outside_a_scope_leaves_the_row_alone(self):
		row = frappe._dict(connection=None)
		listings.stamp_connection(row)
		self.assertIsNone(row.connection)
