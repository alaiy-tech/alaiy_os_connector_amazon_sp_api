# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for FBA inventory: five quantities, and not collapsing them.

Only the pure transformation layer — no SP-API calls, no saved documents.

The bug worth guarding against here is not a crash. `totalQuantity` and
`fulfillableQuantity` are both plausible answers to "how much stock is there",
they agree for most SKUs, and they diverge exactly when it matters: on the SKU
with a shipment in transit, which is the one a seller is about to reorder. A
days-of-cover figure computed on the wrong one reads healthy right up to the
stockout.
"""

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import inventory


def _entry(**details):
	return {
		"sellerSku": "CT-TOTE-BLK-001",
		"asin": "B09XKQL3M2",
		"fnSku": "X001ABCDEF",
		"condition": "NewItem",
		"productName": "Canvas Tote Bag (Black)",
		"totalQuantity": details.pop("totalQuantity", 0),
		"lastUpdatedTime": details.pop("lastUpdatedTime", None),
		"inventoryDetails": details,
	}


class TestSummaryFrom(UnitTestCase):
	def test_fulfillable_is_what_is_sellable_today(self):
		got = inventory.summary_from(_entry(fulfillableQuantity=12))
		self.assertEqual(got["fulfillable_qty"], 12)

	def test_inbound_sums_the_three_stages_and_is_not_stock(self):
		got = inventory.summary_from(
			_entry(
				fulfillableQuantity=4,
				inboundWorkingQuantity=100,
				inboundShippedQuantity=250,
				inboundReceivingQuantity=50,
			)
		)
		self.assertEqual(got["inbound_qty"], 400)
		# The whole point: 400 units on the way do not make this SKU stocked.
		self.assertEqual(got["fulfillable_qty"], 4)

	def test_total_is_reported_verbatim_and_is_not_fulfillable(self):
		# Amazon's total includes inbound, reserved and unfulfillable. Kept as
		# given, never treated as the answer.
		got = inventory.summary_from(
			_entry(
				totalQuantity=420,
				fulfillableQuantity=4,
				inboundShippedQuantity=400,
				reservedQuantity={"totalReservedQuantity": 6},
				unfulfillableQuantity={"totalUnfulfillableQuantity": 10},
			)
		)
		self.assertEqual(got["total_qty"], 420)
		self.assertEqual(got["fulfillable_qty"], 4)
		self.assertEqual(got["reserved_qty"], 6)
		self.assertEqual(got["unfulfillable_qty"], 10)

	def test_reads_the_nested_totals_not_their_breakdowns(self):
		got = inventory.summary_from(
			_entry(
				researchingQuantity={
					"totalResearchingQuantity": 3,
					"researchingQuantityBreakdown": [{"name": "researchingQuantityInShortTerm", "quantity": 3}],
				}
			)
		)
		self.assertEqual(got["researching_qty"], 3)

	def test_a_missing_breakdown_key_is_zero_not_none(self):
		# Amazon omits a bucket rather than sending zero, and a None here would
		# propagate into arithmetic downstream.
		got = inventory.summary_from(_entry())
		for key in (
			"fulfillable_qty",
			"inbound_qty",
			"reserved_qty",
			"unfulfillable_qty",
			"researching_qty",
			"total_qty",
		):
			self.assertEqual(got[key], 0, key)

	def test_an_unparseable_timestamp_costs_the_stamp_and_nothing_else(self):
		got = inventory.summary_from(_entry(fulfillableQuantity=7, lastUpdatedTime="not a date"))
		self.assertIsNone(got["last_updated_at"])
		self.assertEqual(got["fulfillable_qty"], 7)


class TestPaging(UnitTestCase):
	def test_reads_the_wrapped_shape(self):
		summaries, token = inventory._page(
			{"pagination": {"nextToken": "abc"}, "payload": {"inventorySummaries": [_entry()]}}
		)
		self.assertEqual(len(summaries), 1)
		self.assertEqual(token, "abc")

	def test_reads_an_unwrapped_shape_too(self):
		# Both shapes exist across gateway versions; checking the second costs
		# two dict lookups and saves a sync that silently returns one page.
		summaries, token = inventory._page(
			{"inventorySummaries": [_entry()], "pagination": {"nextToken": "xyz"}}
		)
		self.assertEqual(len(summaries), 1)
		self.assertEqual(token, "xyz")

	def test_no_token_ends_the_walk(self):
		summaries, token = inventory._page({"payload": {"inventorySummaries": []}})
		self.assertEqual(summaries, [])
		self.assertIsNone(token)


class TestRowKey(UnitTestCase):
	def test_the_key_starts_with_the_connection(self):
		# A key of SKU alone is the bug this app already carries elsewhere: two
		# sellers on one bench with the same SKU would share a stock row, and
		# the second sync would overwrite the first.
		a = inventory._row_key("seller-a", "A21TJRUUN4KGV", "TOTE-001")
		b = inventory._row_key("seller-b", "A21TJRUUN4KGV", "TOTE-001")
		self.assertNotEqual(a, b)

	def test_the_same_sku_in_two_marketplaces_is_two_rows(self):
		a = inventory._row_key("seller-a", "A21TJRUUN4KGV", "TOTE-001")
		b = inventory._row_key("seller-a", "ATVPDKIKX0DER", "TOTE-001")
		self.assertNotEqual(a, b)
