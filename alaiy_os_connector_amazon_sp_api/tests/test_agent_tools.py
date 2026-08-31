# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the pack's non-Amazon tools: the manifest, the CSV, the links.

Three things here, and they fail for three different reasons.

The manifest test is the cheap one that matters most. `pack_meta.TOOLS` is prose
plus dotted paths, and nothing type-checks either: a renamed endpoint leaves a row
whose handler imports at migrate time and throws at run time, which surfaces as an
agent that answers "that tool could not be imported" to a person who asked about
their listings. Resolving every handler here catches it in the repo instead.

The CSV tests pin the envelope heuristic. `_rows_from` has to tell a *wrapper*
around a list of rows from a *record* that happens to contain one, with no schema
to consult — and both shapes come out of this app's own reads. Getting it wrong is
silent: the export succeeds and holds the wrong thing.

The link tests cover only what needs no site — a hand-made marketplace row with a
`www.` in it, which is the one input that would otherwise produce
`https://www.www.amazon.com/dp/...`. The rest of links.py reads Amazon Connection.
"""

import inspect
import json

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api import csv_export, links, pack_meta


class TestPackManifest(UnitTestCase):
	def test_every_handler_resolves(self):
		for tool in pack_meta.TOOLS:
			with self.subTest(tool=tool["tool_id"]):
				self.assertTrue(callable(frappe.get_attr(tool["handler"])))

	def test_schema_arguments_exist_on_the_handler(self):
		"""A property the handler has no parameter for is a TypeError at run time.

		The executor calls `handler(**input)`, so a schema key the endpoint does not
		accept is not a mis-hint the model can recover from — it is the call
		failing.
		"""
		for tool in pack_meta.TOOLS:
			handler = frappe.get_attr(tool["handler"])
			accepted = set(inspect.signature(handler).parameters)
			declared = set(tool["parameters_schema"].get("properties", {}))
			with self.subTest(tool=tool["tool_id"]):
				self.assertEqual(declared - accepted, set())

	def test_registry_rows_carry_json_and_the_connector(self):
		for tool in pack_meta.TOOLS:
			row = pack_meta.as_registry_tool(tool)
			with self.subTest(tool=row["tool_id"]):
				self.assertEqual(row["connector"], pack_meta.CONNECTOR_ID)
				self.assertEqual(json.loads(row["parameters_schema"])["type"], "object")
				if row["required_permissions"]:
					for perm in json.loads(row["required_permissions"]):
						self.assertEqual(set(perm), {"doctype", "ptype"})

	def test_tool_ids_are_unique(self):
		ids = [tool["tool_id"] for tool in pack_meta.TOOLS]
		self.assertEqual(len(ids), len(set(ids)))


class TestCsvRowShapes(UnitTestCase):
	def test_list_listings_envelope_gives_up_its_rows(self):
		"""Four metadata keys around the payload, which is what set the threshold."""
		result = {
			"total": 212,
			"page_no": 1,
			"page_size": 20,
			"has_more": True,
			"listings": [{"sku": "A", "price": 1}, {"sku": "B", "price": 2}],
		}
		self.assertEqual(
			csv_export._rows_from(result),
			[{"sku": "A", "price": 1}, {"sku": "B", "price": 2}],
		)

	def test_variation_family_envelope_gives_up_its_children(self):
		"""Five metadata keys — the widest envelope this app returns."""
		result = {
			"parent_asin": "B0PARENT001",
			"parent_sku": "PARENT",
			"parent_title": "Kettle",
			"variation_theme": "COLOR",
			"child_count": 2,
			"children": [{"sku": "BLUE"}, {"sku": "RED"}],
		}
		self.assertEqual(csv_export._rows_from(result), [{"sku": "BLUE"}, {"sku": "RED"}])

	def test_issues_envelope_gives_up_its_issues(self):
		result = {
			"issues": [{"sku": "A", "code": "90220"}],
			"count": 1,
			"skus_affected": 1,
			"truncated": False,
		}
		self.assertEqual(csv_export._rows_from(result), [{"sku": "A", "code": "90220"}])

	def test_a_compare_result_is_one_row_not_its_nested_values(self):
		"""compare_listing nests dicts, not lists, so it stays a single record."""
		result = {
			"sku": "KETTLE-BLUE",
			"marketplace": "A21TJRUUN4KGV",
			"listing_status": "active",
			"remote": {"price": 24.99},
			"changes": {"price": 22.5},
			"changed": True,
			"content_changed": False,
		}
		self.assertEqual(csv_export._rows_from(result), [result])

	def test_a_bare_array_passes_through(self):
		rows = [{"name": "A"}, {"name": "B"}]
		self.assertEqual(csv_export._rows_from(rows), rows)

	def test_a_list_of_scalars_becomes_one_column(self):
		self.assertEqual(csv_export._rows_from(["A", "B"]), [{"value": "A"}, {"value": "B"}])

	def test_header_is_first_seen_key_order_across_rows(self):
		rows = [{"sku": "A", "price": 1}, {"sku": "B", "quantity": 3}]
		self.assertEqual(csv_export._header(rows, ""), ["sku", "price", "quantity"])

	def test_requested_columns_win_and_keep_their_order(self):
		rows = [{"sku": "A", "price": 1}]
		self.assertEqual(csv_export._header(rows, "price, sku"), ["price", "sku"])


class TestCsvCells(UnitTestCase):
	def test_a_text_cell_starting_with_a_formula_character_is_guarded(self):
		self.assertEqual(csv_export._cell("=SUM(A1:A2)"), "'=SUM(A1:A2)")

	def test_a_negative_number_is_not_guarded(self):
		"""The guard is for text. Quoting -5 would make it unusable as a number."""
		self.assertEqual(csv_export._cell(-5), -5)

	def test_nested_values_become_compact_json(self):
		self.assertEqual(csv_export._cell(["a", "b"]), '["a","b"]')

	def test_none_is_an_empty_cell_not_the_word_none(self):
		self.assertEqual(csv_export._cell(None), "")

	def test_a_long_cell_is_truncated_rather_than_written_whole(self):
		cell = csv_export._cell("x" * (csv_export.MAX_CELL_CHARS + 50))
		self.assertEqual(len(cell), csv_export.MAX_CELL_CHARS + 1)
		self.assertTrue(cell.endswith("…"))

	def test_a_bad_payload_is_reported_rather_than_thrown(self):
		result = csv_export.export_csv("not json at all")
		self.assertFalse(result["saved"])
		self.assertIn("not valid JSON", result["error"])

	def test_an_empty_payload_is_reported(self):
		self.assertFalse(csv_export.export_csv("   ")["saved"])


class TestLinkDomains(UnitTestCase):
	def test_a_hand_made_www_prefix_is_not_doubled(self):
		self.assertEqual(links._domain(frappe._dict(domain="www.amazon.in")), "amazon.in")

	def test_a_seeded_domain_is_left_alone(self):
		self.assertEqual(links._domain(frappe._dict(domain="amazon.com")), "amazon.com")

	def test_no_marketplace_is_an_empty_domain_not_a_throw(self):
		self.assertEqual(links._domain(None), "")

	def test_a_blank_domain_is_empty(self):
		self.assertEqual(links._domain(frappe._dict(domain="  ")), "")

	def test_a_link_needs_a_sku_or_an_asin(self):
		self.assertRaises(frappe.ValidationError, links.listing_link)
