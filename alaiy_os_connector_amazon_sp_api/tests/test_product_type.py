# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the listing's product type — the one attribute every write needs.

Amazon rejects a PATCH or PUT that does not declare a productType, so the value
has to survive on the row whether or not the current payload mentions it. It is
also the value least likely to come back: an offer-only listing and a variation
parent both return summaries without one. These cover the reading side, which is
pure; the writing side is `_upsert_from_item`, which needs a saved document.
"""

import json

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import listings


class TestProductTypeFromRawSummary(UnitTestCase):
	"""The fallback for rows last synced before `product_type` was a field."""

	def test_reads_the_stored_product_type(self):
		raw = json.dumps({"productType": "SHIRT", "summary": {"itemName": "Cotton Shirt"}})
		self.assertEqual(listings.product_type_from_raw_summary(raw), "SHIRT")

	def test_a_summary_without_one_yields_nothing(self):
		raw = json.dumps({"productType": None, "summary": {"itemName": "Cotton Shirt"}})
		self.assertIsNone(listings.product_type_from_raw_summary(raw))

	def test_empty_and_malformed_blobs_do_not_raise(self):
		# A row that has never synced has no blob at all, and nothing guarantees
		# the column holds JSON — neither may take down an update to Amazon.
		for raw in (None, "", "not json", "[]"):
			self.assertIsNone(listings.product_type_from_raw_summary(raw))
