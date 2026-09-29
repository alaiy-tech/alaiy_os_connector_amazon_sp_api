# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the package readers — the parsing, not the calls.

The Shipping tab's whole claim is that a seller can see fulfilment health
without auditing orders one by one. That claim rests on these transformations,
and three of the cases below are the ones where getting it wrong produces a
*reassuring* wrong answer rather than a visibly broken one.

`test_an_unmapped_status_is_unknown_and_not_in_transit` is the first. Amazon
adds package statuses; the friendly default for one this app has not seen is "in
transit", and that is a parcel which has stopped moving being reported as on its
way.

`test_a_refused_api_version_is_not_an_empty_package_list` is the second, and it
is the reason `order_packages` raises where nearly everything else in this app
degrades. Orders 2026-01-01 is the newest surface this connector calls. A
marketplace not yet moved onto it answers with an error, and an error swallowed
into `[]` tells a seller they have no late shipments — a clean fulfilment record
they have not earned.

`test_delivery_is_never_inferred_from_an_estimate` is the third. The
fulfilled-shipments report carries an estimated arrival date and no actual one.
Reading the estimate as a delivery would manufacture a second on-time delivery
rate that disagrees with the one Amazon already reports on Account Health, and
the tab's stated promise is that those two figures come from one pipeline.
"""

from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import packages
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiError


class TestStatusNormalisation(UnitTestCase):
	def test_amazons_vocabulary_maps_to_ours(self):
		self.assertEqual(packages.normalise_status("SHIPPED"), "in_transit")
		self.assertEqual(packages.normalise_status("DELIVERED"), "delivered")
		self.assertEqual(packages.normalise_status("RETURNING"), "returned")
		self.assertEqual(packages.normalise_status("LOST"), "lost")

	def test_spelling_and_case_are_tolerated(self):
		self.assertEqual(packages.normalise_status("out for delivery"), "in_transit")
		self.assertEqual(packages.normalise_status("Out-For-Delivery"), "in_transit")
		# Amazon has shipped both spellings of this one.
		self.assertEqual(packages.normalise_status("CANCELLED"), "cancelled")
		self.assertEqual(packages.normalise_status("CANCELED"), "cancelled")

	def test_an_unmapped_status_is_unknown_and_not_in_transit(self):
		"""See the module docstring. The friendly default is the wrong one."""
		self.assertEqual(packages.normalise_status("SOME_NEW_AMAZON_STATUS"), "unknown")
		self.assertEqual(packages.normalise_status(None), "unknown")
		self.assertEqual(packages.normalise_status(""), "unknown")


class TestFieldTolerance(UnitTestCase):
	def test_the_first_spelling_that_carries_a_value_wins(self):
		self.assertEqual(packages._first({"carrierName": "Delhivery"}, "carrierName"), "Delhivery")
		self.assertEqual(
			packages._first({"carrier": {"name": "Bluedart"}}, "carrierName", "carrier.name"),
			"Bluedart",
		)

	def test_an_empty_value_is_not_a_value(self):
		"""An empty string must not shadow a populated alternative spelling."""
		self.assertEqual(
			packages._first({"carrierName": "", "carrier": {"name": "Ekart"}}, "carrierName", "carrier.name"),
			"Ekart",
		)

	def test_a_dotted_path_through_a_non_dict_does_not_raise(self):
		self.assertIsNone(packages._first({"carrier": "Delhivery"}, "carrier.name"))

	def test_nothing_found_is_none(self):
		self.assertIsNone(packages._first({}, "carrierName", "carrier.name"))


class TestPackageRow(UnitTestCase):
	def test_a_package_is_read_whatever_amazon_calls_its_fields(self):
		row = packages._package_row(
			"406-1234567-1234567",
			{
				"packageNumber": "PKG-1",
				"carrier": {"name": "Delhivery", "code": "DLV"},
				"trackingId": "TRK-9",
				"status": "IN_TRANSIT",
			},
		)
		self.assertEqual(row["package_id"], "PKG-1")
		self.assertEqual(row["carrier_name"], "Delhivery")
		self.assertEqual(row["carrier_code"], "DLV")
		self.assertEqual(row["tracking_number"], "TRK-9")
		self.assertEqual(row["package_status"], "in_transit")
		self.assertEqual(row["source"], "orders_api")

	def test_the_raw_status_is_kept_beside_the_normalised_one(self):
		"""So an `unknown` can be diagnosed without re-reading Amazon."""
		row = packages._package_row("O-1", {"packageStatus": "SOME_NEW_STATUS"})
		self.assertEqual(row["package_status"], "unknown")
		self.assertEqual(row["package_status_raw"], "SOME_NEW_STATUS")

	def test_the_lines_in_a_split_shipment_are_recorded(self):
		row = packages._package_row(
			"O-1",
			{"packageItems": [{"sellerSku": "SKU-A"}, {"sellerSKU": "SKU-B"}, {"quantity": 2}]},
		)
		self.assertEqual(row["skus"], ["SKU-A", "SKU-B"])

	def test_a_package_with_no_items_has_no_skus_rather_than_a_none(self):
		self.assertEqual(packages._package_row("O-1", {})["skus"], [])


class TestEnvelope(UnitTestCase):
	def test_packages_are_found_wrapped_or_unwrapped(self):
		wrapped = {"payload": {"amazonOrderId": "O-1", "packages": [{"packageId": "P1"}]}}
		unwrapped = {"amazonOrderId": "O-1", "packages": [{"packageId": "P1"}]}
		for response in (wrapped, unwrapped):
			order_id, found = packages._packages_of(response)
			self.assertEqual(order_id, "O-1")
			self.assertEqual(len(found), 1)

	def test_an_order_with_no_packages_yields_an_empty_list(self):
		_order_id, found = packages._packages_of({"amazonOrderId": "O-1"})
		self.assertEqual(found, [])


class TestOrderPackages(UnitTestCase):
	def test_an_unshipped_order_maps_to_an_empty_list(self):
		client = Mock()
		client.get.return_value = {"payload": {"amazonOrderId": "O-1", "packages": []}}
		self.assertEqual(packages.order_packages(["O-1"], client=client), {"O-1": []})

	def test_a_refused_api_version_is_not_an_empty_package_list(self):
		"""See the module docstring: this is the one that must raise."""
		client = Mock()
		client.get.side_effect = SpApiError("Unsupported version", status_code=400)
		self.assertRaises(SpApiError, packages.order_packages, ["O-1"], client=client)

	def test_a_403_comes_back_as_the_role_gap_it_is(self):
		client = Mock()
		client.get.side_effect = SpApiError("Forbidden", status_code=403)
		self.assertRaises(frappe.ValidationError, packages.order_packages, ["O-1"], client=client)

	def test_a_repeated_order_id_is_asked_about_once(self):
		client = Mock()
		client.get.return_value = {"amazonOrderId": "O-1", "packages": []}
		packages.order_packages(["O-1", "O-1", " O-1 "], client=client)
		self.assertEqual(client.get.call_count, 1)

	def test_nothing_to_ask_about_makes_no_call(self):
		client = Mock()
		self.assertEqual(packages.order_packages([], client=client), {})
		client.get.assert_not_called()


FBA_REPORT = (
	"amazon-order-id\tshipment-id\tsku\tquantity-shipped\tcarrier\ttracking-number\t"
	"shipment-date\testimated-arrival-date\tpurchase-date\tfulfillment-center-id\n"
	"406-111\tSHIP-1\tSKU-A\t1\tAmazon Logistics\tTRK-1\t2026-09-01T10:00:00Z\t"
	"2026-09-04T10:00:00Z\t2026-08-31T09:00:00Z\tBOM7\n"
	"406-111\tSHIP-1\tSKU-B\t2\tAmazon Logistics\tTRK-1\t2026-09-01T10:00:00Z\t"
	"2026-09-04T10:00:00Z\t2026-08-31T09:00:00Z\tBOM7\n"
)


class TestFbaShipments(UnitTestCase):
	def test_two_lines_in_one_parcel_stay_two_rows(self):
		"""Grouping needs a decision this module cannot make for the caller."""
		rows = packages.parse_fba_shipments(FBA_REPORT)
		self.assertEqual(len(rows), 2)
		self.assertEqual({r["package_id"] for r in rows}, {"SHIP-1"})
		self.assertEqual([r["skus"] for r in rows], [["SKU-A"], ["SKU-B"]])

	def test_a_shipment_in_this_report_is_shipped_by_definition(self):
		rows = packages.parse_fba_shipments(FBA_REPORT)
		self.assertEqual(rows[0]["package_status"], "in_transit")
		self.assertEqual(rows[0]["carrier_name"], "Amazon Logistics")
		self.assertEqual(rows[0]["source"], "fba_report")

	def test_delivery_is_never_inferred_from_an_estimate(self):
		"""See the module docstring on the second on-time rate this would invent."""
		row = packages.parse_fba_shipments(FBA_REPORT)[0]
		self.assertIsNone(row["delivered_date"])
		self.assertIsNotNone(row["estimated_delivery_date"])

	def test_a_row_without_an_order_id_is_dropped(self):
		text = "amazon-order-id\tshipment-id\tsku\n\tSHIP-1\tSKU-A\n406-1\tSHIP-2\tSKU-B\n"
		rows = packages.parse_fba_shipments(text)
		self.assertEqual([r["order_id"] for r in rows], ["406-1"])

	def test_an_empty_report_is_no_shipments_and_not_an_error(self):
		self.assertEqual(packages.parse_fba_shipments(None), [])
		self.assertEqual(packages.parse_fba_shipments(""), [])

	def test_an_over_wide_window_is_refused_rather_than_truncated(self):
		"""A silently truncated backfill leaves months looking shipment-free."""
		with patch.object(packages, "_marketplace"):
			self.assertRaises(
				frappe.ValidationError,
				packages.fba_shipments,
				"2026-01-01",
				"2026-06-30",
				client=Mock(),
			)

	def test_a_cancelled_report_is_no_data_rather_than_a_failure(self):
		with (
			patch.object(packages, "_marketplace", return_value=Mock(marketplace_id="A21TJRUUN4KGV")),
			patch.object(packages.reports, "fetch_report", return_value=None),
		):
			self.assertEqual(packages.fba_shipments("2026-09-01", "2026-09-07", client=Mock()), [])
