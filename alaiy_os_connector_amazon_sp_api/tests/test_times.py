# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the one crossing between Amazon's clock and Frappe's.

Moved here with the code they cover: the conversion used to live in
`spapi.orders` and now lives in `spapi.times`, because `spapi.inventory` needs
the same thing and the original comment already promised there would be one copy.

`spapi.orders` keeps `_to_amazon_iso` / `_from_amazon_iso` as aliases, so the
orders sync is exercised through the same functions these test.

A timezone bug here does not raise. It shifts a whole sync window by hours and
every downstream number stays plausible, which is why the zone below is
deliberately neither UTC nor a half-hour offset — a UTC-only test passes even
when the conversion is a no-op.
"""

from unittest.mock import patch

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import orders, times

TZ = "America/New_York"


class TestAmazonTimeConversion(UnitTestCase):
	def test_to_amazon_iso_converts_system_time_to_utc(self):
		with patch.object(times, "get_system_timezone", return_value=TZ):
			# 09:15 EDT is 13:15Z
			self.assertEqual(times.to_amazon_iso("2026-08-03 09:15:00"), "2026-08-03T13:15:00Z")

	def test_from_amazon_iso_converts_utc_to_system_time(self):
		with patch.object(times, "get_system_timezone", return_value=TZ):
			self.assertEqual(str(times.from_amazon_iso("2026-08-03T13:15:00Z")), "2026-08-03 09:15:00")

	def test_iso_round_trip_is_lossless(self):
		with patch.object(times, "get_system_timezone", return_value=TZ):
			out = times.from_amazon_iso(times.to_amazon_iso("2026-01-15 23:59:00"))
			self.assertEqual(str(out), "2026-01-15 23:59:00")

	def test_from_amazon_iso_tolerates_missing_and_malformed(self):
		# Pending orders routinely omit ship dates; a parse failure must not
		# take the whole run down.
		self.assertIsNone(times.from_amazon_iso(None))
		self.assertIsNone(times.from_amazon_iso(""))
		self.assertIsNone(times.from_amazon_iso("not-a-date"))

	def test_orders_still_reaches_the_same_conversion(self):
		# The aliases are what the orders sync calls; if they ever stopped
		# pointing here, the window arithmetic would silently use something else.
		self.assertIs(orders._to_amazon_iso, times.to_amazon_iso)
		self.assertIs(orders._from_amazon_iso, times.from_amazon_iso)
