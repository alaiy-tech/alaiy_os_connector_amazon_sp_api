# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the order-mapping logic in spapi/orders.py.

Only the pure transformation layer is covered here — no SP-API calls and no
Sales Orders. These are the parts where a silent bug is expensive and invisible:
a timezone shift moves the whole sync window, a unit-rate mistake books the
wrong revenue, and a bad merge makes ERPNext reject the document outright.
"""

from unittest.mock import patch

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import orders
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	ORDER_STATUS_CANCEL,
	ORDER_STATUS_DRAFT,
	ORDER_STATUS_SUBMIT,
)

# A deliberately non-UTC, non-half-hour zone: a UTC-only test passes even when
# the conversion is a no-op, which is exactly the bug worth catching.
TZ = "America/New_York"


class TestAmazonOrderMapping(UnitTestCase):
	# --- time -----------------------------------------------------------
	def test_to_amazon_iso_converts_system_time_to_utc(self):
		with patch.object(orders, "get_system_timezone", return_value=TZ):
			# 09:15 EDT is 13:15Z
			self.assertEqual(orders._to_amazon_iso("2026-08-03 09:15:00"), "2026-08-03T13:15:00Z")

	def test_from_amazon_iso_converts_utc_to_system_time(self):
		with patch.object(orders, "get_system_timezone", return_value=TZ):
			self.assertEqual(str(orders._from_amazon_iso("2026-08-03T13:15:00Z")), "2026-08-03 09:15:00")

	def test_iso_round_trip_is_lossless(self):
		with patch.object(orders, "get_system_timezone", return_value=TZ):
			out = orders._from_amazon_iso(orders._to_amazon_iso("2026-01-15 23:59:00"))
			self.assertEqual(str(out), "2026-01-15 23:59:00")

	def test_from_amazon_iso_tolerates_missing_and_malformed(self):
		# Pending orders routinely omit ship dates; a parse failure must not
		# take the whole run down.
		self.assertIsNone(orders._from_amazon_iso(None))
		self.assertIsNone(orders._from_amazon_iso(""))
		self.assertIsNone(orders._from_amazon_iso("not-a-date"))

	# --- money ----------------------------------------------------------
	def test_money_reads_amount(self):
		self.assertEqual(orders._money({"CurrencyCode": "USD", "Amount": "12.34"}), 12.34)

	def test_money_defaults_to_zero(self):
		self.assertEqual(orders._money(None), 0)
		self.assertEqual(orders._money({}), 0)

	# --- line items -----------------------------------------------------
	def test_item_price_is_extended_not_unit(self):
		"""ItemPrice covers the whole QuantityOrdered, and PromotionDiscount
		is likewise an order-item total — so rate = (price - discount) / qty."""
		items = [
			{
				"SellerSKU": "SKU-A",
				"QuantityOrdered": 2,
				"ItemPrice": {"Amount": "20.00"},
				"PromotionDiscount": {"Amount": "4.00"},
				"OrderItemId": "OI1",
			}
		]
		with patch.object(orders, "_resolve_item_code", return_value="ITEM-A"):
			rows, unresolved = orders._line_items(items, "WH", "2026-08-10")
		self.assertEqual(unresolved, [])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["rate"], 8.0)
		self.assertEqual(rows[0]["qty"], 2)
		self.assertEqual(rows[0]["amazon_seller_sku"], "SKU-A")

	def test_zero_quantity_lines_are_dropped(self):
		"""Amazon keeps a fully cancelled line with QuantityOrdered 0."""
		items = [{"SellerSKU": "SKU-A", "QuantityOrdered": 0, "ItemPrice": {"Amount": "0"}}]
		with patch.object(orders, "_resolve_item_code", return_value="ITEM-A"):
			rows, unresolved = orders._line_items(items, "WH", "2026-08-10")
		self.assertEqual(rows, [])
		self.assertEqual(unresolved, [])

	def test_unmapped_sku_parks_the_whole_order(self):
		"""Importing a partial order would understate it, so an unresolved SKU
		fails the order rather than dropping the line."""
		items = [
			{"SellerSKU": "SKU-A", "QuantityOrdered": 1, "ItemPrice": {"Amount": "5"}},
			{"SellerSKU": "SKU-NOPE", "QuantityOrdered": 1, "ItemPrice": {"Amount": "5"}},
		]
		with patch.object(
			orders, "_resolve_item_code", side_effect=lambda sku: "ITEM-A" if sku == "SKU-A" else None
		):
			rows, unresolved = orders._line_items(items, "WH", "2026-08-10")
		self.assertIsNone(rows)
		self.assertEqual(unresolved, ["SKU-NOPE"])

	# --- duplicate merge ------------------------------------------------
	def test_duplicate_item_codes_merge_without_changing_the_total(self):
		"""ERPNext rejects two rows with the same item_code, and Amazon splits
		one SKU across order items routinely."""
		merged = orders._merge_duplicate_rows(
			[
				{"item_code": "ITEM-A", "qty": 2, "rate": 10.0},
				{"item_code": "ITEM-A", "qty": 3, "rate": 5.0},
				{"item_code": "ITEM-B", "qty": 1, "rate": 7.0},
			]
		)
		self.assertEqual(len(merged), 2)
		self.assertEqual(merged[0]["qty"], 5)
		# 2*10 + 3*5 = 35, over 5 units = 7.0
		self.assertEqual(merged[0]["rate"], 7.0)
		self.assertEqual(merged[1]["item_code"], "ITEM-B")

	# --- status buckets -------------------------------------------------
	def test_status_buckets_are_disjoint(self):
		"""An overlap would make the docstatus a coin flip on ordering."""
		draft, submit, cancel = set(ORDER_STATUS_DRAFT), set(ORDER_STATUS_SUBMIT), set(ORDER_STATUS_CANCEL)
		self.assertEqual(draft & submit, set())
		self.assertEqual(submit & cancel, set())
		self.assertEqual(draft & cancel, set())
