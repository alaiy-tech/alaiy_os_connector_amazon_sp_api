# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the sales reporting logic — the parts that need no site.

Everything here is a pure transformation: the period and bucket arithmetic, the
totals fold, the baseline shift, and the interval string the Sales API is handed.
The SQL itself is not covered — that needs a database and real Sales Orders — so
what is pinned here is the reasoning around it, which is where a silent wrong
number would come from.

Three of these fail for reasons worth spelling out.

`_totals_from` is the fold that decides what "revenue" means, and it is tested
against buckets that would come out differently under an average-of-averages: a
weighted average order value is not the mean of the per-bucket ones, and a model
quoting the wrong one has no way to tell.

`_change` is the percentage arithmetic the pack exists to keep out of a
completion. The case that matters is a zero baseline, where the honest answer is
no percentage at all — anything else invents a number.

`_interval` is where a whole day goes missing. Amazon's interval is half-open, so
an end instant of `date_to` itself drops that day, and it is the last one anyone
asked about. The DST test is the second half of the same problem: a fixed offset
across a daylight-saving change shifts every bucket boundary by an hour.
"""

from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import getdate

from alaiy_os_connector_amazon_sp_api import sales
from alaiy_os_connector_amazon_sp_api.spapi import sales as spapi_sales
from alaiy_os_connector_amazon_sp_api.spapi.constants import ORDER_STATUS_CANCEL

# Non-UTC and not a whole number of hours: a UTC-only test passes even when the
# conversion is a no-op, which is the bug worth catching.
TZ = "Asia/Kolkata"


class TestPeriod(UnitTestCase):
	def test_date_to_defaults_to_today_not_to_date_from(self):
		start, end = sales._period("2026-08-01")
		self.assertEqual(start, getdate("2026-08-01"))
		self.assertEqual(end, getdate(frappe.utils.nowdate()))

	def test_a_backwards_period_is_refused(self):
		self.assertRaises(frappe.ValidationError, sales._period, "2026-08-31", "2026-08-01")

	def test_a_missing_start_is_refused(self):
		self.assertRaises(frappe.ValidationError, sales._period, None)

	def test_the_error_names_the_parameter_that_was_actually_missing(self):
		"""compare_sales_periods validates two pairs; an error saying `date_from`
		when `baseline_from` is what is missing sends a caller to the wrong
		argument."""
		with self.assertRaises(frappe.ValidationError) as caught:
			sales._period(None, label="baseline_from")
		self.assertIn("baseline_from", str(caught.exception))

	def test_one_day_is_a_period(self):
		start, end = sales._period("2026-08-01", "2026-08-01")
		self.assertEqual(start, end)


class TestEnumValidation(UnitTestCase):
	def test_an_unknown_value_names_the_real_ones(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			sales._one_of("quarterly", sales.GRANULARITIES, "granularity")
		self.assertIn("month", str(caught.exception))

	def test_a_blank_takes_the_default_rather_than_throwing(self):
		self.assertEqual(sales._one_of(None, sales.GRANULARITIES, "granularity", default="day"), "day")

	def test_a_blank_with_no_default_is_refused(self):
		self.assertRaises(frappe.ValidationError, sales._one_of, None, sales.GRANULARITIES, "granularity")

	def test_the_sales_networks_are_not_the_listing_channels(self):
		"""The two enums must stay distinct — see the note in pack_meta."""
		self.assertEqual(set(sales.FULFILLMENT_NETWORKS), {"AFN", "MFN"})
		self.assertRaises(
			frappe.ValidationError,
			sales._one_of,
			"AMAZON",
			sales.FULFILLMENT_NETWORKS,
			"fulfillment network",
		)


class TestBucketCount(UnitTestCase):
	def test_a_year_of_days_is_allowed(self):
		sales._assert_bucket_count(getdate("2026-01-01"), getdate("2026-12-31"), "day")

	def test_two_years_of_days_is_refused_naming_a_way_out(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			sales._assert_bucket_count(getdate("2024-01-01"), getdate("2026-12-31"), "day")
		self.assertIn("month", str(caught.exception))

	def test_the_same_span_is_fine_by_month(self):
		sales._assert_bucket_count(getdate("2024-01-01"), getdate("2026-12-31"), "month")

	def test_total_is_always_one_bucket(self):
		sales._assert_bucket_count(getdate("1990-01-01"), getdate("2026-12-31"), "total")


class TestBucketBounds(UnitTestCase):
	def test_a_day_bucket_is_its_own_day(self):
		bounds = sales._bucket_bounds("2026-08-12", "day", getdate("2026-08-01"), getdate("2026-08-31"))
		self.assertEqual(bounds, (getdate("2026-08-12"), getdate("2026-08-12")))

	def test_a_week_bucket_runs_seven_days(self):
		bounds = sales._bucket_bounds("2026-08-10", "week", getdate("2026-08-01"), getdate("2026-08-31"))
		self.assertEqual(bounds, (getdate("2026-08-10"), getdate("2026-08-16")))

	def test_a_partial_first_bucket_is_clamped_to_the_period(self):
		"""A month bucket for a period starting mid-month covers half a month.

		Reporting the calendar month would invite a comparison against a whole
		one — the dates are what make the partial visible.
		"""
		bounds = sales._bucket_bounds("2026-08-01", "month", getdate("2026-08-15"), getdate("2026-09-20"))
		self.assertEqual(bounds, (getdate("2026-08-15"), getdate("2026-08-31")))

	def test_a_partial_last_bucket_is_clamped_too(self):
		bounds = sales._bucket_bounds("2026-09-01", "month", getdate("2026-08-15"), getdate("2026-09-20"))
		self.assertEqual(bounds, (getdate("2026-09-01"), getdate("2026-09-20")))

	def test_total_covers_the_whole_period_whatever_the_bucket_says(self):
		bounds = sales._bucket_bounds("2026-08-01", "total", getdate("2026-08-15"), getdate("2026-09-20"))
		self.assertEqual(bounds, (getdate("2026-08-15"), getdate("2026-09-20")))


class TestTotals(UnitTestCase):
	def _buckets(self):
		return [
			{"product_sales": 100.0, "order_total": 118.0, "units": 4, "order_count": 2},
			{"product_sales": 300.0, "order_total": 354.0, "units": 9, "order_count": 6},
		]

	def test_totals_are_the_sum_of_the_buckets(self):
		totals = sales._totals_from(self._buckets())
		self.assertEqual(totals["product_sales"], 400.0)
		self.assertEqual(totals["order_total"], 472.0)
		self.assertEqual(totals["units"], 13)
		self.assertEqual(totals["order_count"], 8)

	def test_average_order_value_is_weighted_not_an_average_of_averages(self):
		"""400/8 is 50. The mean of the two buckets' own averages is 75."""
		self.assertEqual(sales._totals_from(self._buckets())["avg_order_value"], 50.0)

	def test_no_orders_is_a_zero_average_rather_than_a_division(self):
		self.assertEqual(sales._totals_from([])["avg_order_value"], 0.0)


class TestMergeUnits(UnitTestCase):
	def test_units_land_on_their_money_bucket(self):
		merged = sales._merge_units(
			[{"bucket": "2026-08-01", "product_sales": 10}],
			[{"bucket": "2026-08-01", "units": 3}],
		)
		self.assertEqual(merged, [{"bucket": "2026-08-01", "product_sales": 10, "units": 3}])

	def test_a_money_bucket_with_no_units_is_zero_not_missing(self):
		merged = sales._merge_units([{"bucket": "2026-08-01", "product_sales": 10}], [])
		self.assertEqual(merged[0]["units"], 0)

	def test_a_unit_bucket_with_no_money_is_carried_rather_than_dropped(self):
		"""It should not happen — same WHERE, same grouping — and dropping it
		would hide units that were sold if it ever did."""
		merged = sales._merge_units([], [{"bucket": "2026-08-01", "units": 3}])
		self.assertEqual(merged, [{"bucket": "2026-08-01", "units": 3}])

	def test_buckets_come_back_in_date_order(self):
		merged = sales._merge_units(
			[{"bucket": "2026-09-01"}, {"bucket": "2026-08-01"}],
			[{"bucket": "2026-08-01", "units": 1}],
		)
		self.assertEqual([row["bucket"] for row in merged], ["2026-08-01", "2026-09-01"])


class TestMarketplaces(UnitTestCase):
	def test_group_concat_strings_are_flattened_and_deduped(self):
		rows = [{"marketplaces": "A21TJRUUN4KGV,ATVPDKIKX0DER"}, {"marketplaces": "ATVPDKIKX0DER"}]
		self.assertEqual(sales._marketplaces(rows), ["A21TJRUUN4KGV", "ATVPDKIKX0DER"])

	def test_an_unstamped_marketplace_is_absence_not_an_empty_name(self):
		self.assertEqual(sales._marketplaces([{"marketplaces": None}, {"marketplaces": ""}]), [])


class TestBaselinePeriod(UnitTestCase):
	def test_previous_period_is_the_same_length_immediately_before(self):
		start, end = sales._baseline_period(getdate("2026-08-01"), getdate("2026-08-30"), "previous_period")
		self.assertEqual(end, getdate("2026-07-31"))
		self.assertEqual((end - start).days, 29)

	def test_previous_period_of_a_single_day_is_the_day_before(self):
		start, end = sales._baseline_period(getdate("2026-08-05"), getdate("2026-08-05"), "previous_period")
		self.assertEqual((start, end), (getdate("2026-08-04"), getdate("2026-08-04")))

	def test_previous_year_shifts_the_dates_back(self):
		start, end = sales._baseline_period(getdate("2026-08-01"), getdate("2026-08-31"), "previous_year")
		self.assertEqual((start, end), (getdate("2025-08-01"), getdate("2025-08-31")))


class TestChange(UnitTestCase):
	def test_a_rise_is_reported_both_ways(self):
		change = sales._change({"product_sales": 150}, {"product_sales": 100})["product_sales"]
		self.assertEqual(change["absolute"], 50.0)
		self.assertEqual(change["percent"], 50.0)

	def test_a_fall_is_negative(self):
		change = sales._change({"units": 40}, {"units": 50})["units"]
		self.assertEqual(change["absolute"], -10.0)
		self.assertEqual(change["percent"], -20.0)

	def test_growth_from_zero_has_no_percentage(self):
		"""There is no percentage change from nothing, and a model handed a
		number would quote it."""
		change = sales._change({"product_sales": 500}, {"product_sales": 0})["product_sales"]
		self.assertEqual(change["absolute"], 500.0)
		self.assertIsNone(change["percent"])

	def test_both_sides_are_carried_so_an_answer_can_name_them(self):
		change = sales._change({"order_count": 3}, {"order_count": 2})["order_count"]
		self.assertEqual((change["current"], change["baseline"]), (3.0, 2.0))


class TestCoverageNote(UnitTestCase):
	def test_nothing_synced_says_so_rather_than_letting_zero_speak(self):
		note = sales._coverage_note(
			{"first_order_date": None, "last_order_date": None}, getdate("2026-08-01"), getdate("2026-08-31")
		)
		self.assertIn("have ever synced", note)

	def test_a_period_before_the_data_is_no_data_not_no_sales(self):
		cov = {"first_order_date": "2026-03-01", "last_order_date": "2026-08-30"}
		note = sales._coverage_note(cov, getdate("2026-01-01"), getdate("2026-01-31"))
		self.assertIn("entirely outside", note)

	def test_a_period_straddling_the_start_says_what_is_missing(self):
		cov = {"first_order_date": "2026-03-01", "last_order_date": "2026-08-30"}
		note = sales._coverage_note(cov, getdate("2026-02-01"), getdate("2026-04-30"))
		self.assertIn("2026-03-01", note)

	def test_a_covered_period_gets_no_note(self):
		cov = {"first_order_date": "2026-03-01", "last_order_date": "2026-08-30"}
		self.assertIsNone(sales._coverage_note(cov, getdate("2026-04-01"), getdate("2026-04-30")))


class TestSoldFilter(UnitTestCase):
	"""The filter that decides what counts as sold.

	Both holes it closes book revenue that does not exist: a draft is an Amazon
	Pending order whose pricing Amazon withheld, and an order cancelled after we
	shipped it stays submitted on purpose so its Delivery Note is not corrupted.
	"""

	def _where(self, **kw):
		where, params = sales._sold_where("Acme", "2026-08-01", "2026-08-31", **kw)
		return " AND ".join(where), params

	def test_drafts_and_locally_cancelled_orders_are_excluded(self):
		where, _params = self._where()
		self.assertIn("so.docstatus = 1", where)

	def test_orders_cancelled_on_amazon_are_excluded_by_status(self):
		where, params = self._where()
		self.assertIn("NOT IN %(cancelled)s", where)
		self.assertEqual(params["cancelled"], tuple(ORDER_STATUS_CANCEL))

	def test_the_status_test_survives_a_null(self):
		"""`NULL NOT IN (...)` is NULL, so a bare NOT IN would drop a submitted
		order whose status was never stamped."""
		where, _params = self._where()
		self.assertIn("IFNULL(so.amazon_order_status, '') NOT IN", where)

	def test_non_amazon_orders_are_excluded(self):
		where, _params = self._where()
		self.assertIn("so.amazon_order_id IS NOT NULL", where)
		self.assertIn("so.amazon_order_id != ''", where)

	def test_reads_are_scoped_to_one_company(self):
		"""Company currencies differ, and every figure sums a base_* column."""
		where, params = self._where()
		self.assertIn("so.company = %(company)s", where)
		self.assertEqual(params["company"], "Acme")

	def test_an_unasked_for_filter_adds_no_condition(self):
		where, params = self._where()
		self.assertNotIn("amazon_fulfillment_channel", where)
		self.assertNotIn("fulfillment_network", params)

	def test_the_fulfillment_network_filter_is_amazons_own_field(self):
		where, params = self._where(fulfillment_network="AFN")
		self.assertIn("so.amazon_fulfillment_channel = %(fulfillment_network)s", where)
		self.assertEqual(params["fulfillment_network"], "AFN")


class TestLogContext(UnitTestCase):
	def test_the_sales_context_is_one_the_log_doctype_accepts(self):
		"""`SP-API Log.context` is a Select, so a context the options do not list
		fails validation on insert. `client._log` swallows that, so the symptom
		is not a broken call — it is every sales call quietly filing an error log
		instead of the row it meant to write.
		"""
		options = frappe.get_meta("SP-API Log").get_field("context").options.split("\n")
		self.assertIn("sales", options)


class TestBucketSql(UnitTestCase):
	def test_every_granularity_has_an_expression(self):
		self.assertEqual(set(sales.BUCKET_SQL), set(sales.GRANULARITIES))

	def test_the_week_bucket_starts_on_monday(self):
		# MariaDB WEEKDAY() is 0 for Monday, so subtracting it lands on Monday.
		self.assertIn("WEEKDAY", sales.BUCKET_SQL["week"])

	def test_the_total_bucket_groups_on_the_period_start(self):
		"""One code path for every granularity: `total` is one group whose key is
		a constant expression, not a special case in the caller."""
		self.assertIn("%(date_from)s", sales.BUCKET_SQL["total"])


# --- the live half -----------------------------------------------------------
class TestOrderMetricsInterval(UnitTestCase):
	def test_the_end_is_the_day_after_so_the_last_day_is_included(self):
		"""Amazon's interval is half-open. Passing date_to itself as the end
		silently drops the last day anybody asked about."""
		interval = spapi_sales._interval("2026-08-01", "2026-08-31", TZ)
		start, end = interval.split("--")
		self.assertEqual(start, "2026-08-01T00:00:00+05:30")
		self.assertEqual(end, "2026-09-01T00:00:00+05:30")

	def test_a_single_day_is_a_twentyfour_hour_interval(self):
		start, end = spapi_sales._interval("2026-08-01", "2026-08-01", TZ).split("--")
		self.assertEqual(start, "2026-08-01T00:00:00+05:30")
		self.assertEqual(end, "2026-08-02T00:00:00+05:30")

	def test_each_end_carries_its_own_offset_across_a_dst_change(self):
		"""A fixed offset would shift every bucket boundary by an hour on one
		side of the change. The format allows two offsets; this uses them."""
		start, end = spapi_sales._interval("2026-03-01", "2026-03-31", "America/New_York").split("--")
		self.assertTrue(start.endswith("-05:00"))
		self.assertTrue(end.endswith("-04:00"))


class TestOrderMetricsBucket(UnitTestCase):
	def test_a_payload_entry_becomes_inclusive_dates_again(self):
		bucket = spapi_sales._bucket(
			{
				"interval": "2026-08-01T00:00:00+05:30--2026-08-02T00:00:00+05:30",
				"unitCount": 4,
				"orderItemCount": 3,
				"orderCount": 2,
				"totalSales": {"amount": "120.50", "currencyCode": "INR"},
				"averageUnitPrice": {"amount": "30.13", "currencyCode": "INR"},
			}
		)
		self.assertEqual(bucket["period_start"], "2026-08-01")
		self.assertEqual(bucket["period_end"], "2026-08-01")
		self.assertEqual(bucket["total_sales"], 120.50)
		self.assertEqual(bucket["units"], 4)
		self.assertEqual(bucket["order_count"], 2)
		self.assertEqual(bucket["currency"], "INR")

	def test_an_interval_with_no_sales_has_no_currency_rather_than_an_invented_one(self):
		"""Amazon omits the Money object entirely rather than sending a zero."""
		bucket = spapi_sales._bucket(
			{"interval": "2026-08-01T00:00:00Z--2026-08-02T00:00:00Z", "unitCount": 0}
		)
		self.assertEqual(bucket["total_sales"], 0.0)
		self.assertIsNone(bucket["currency"])

	def test_an_unparseable_interval_keeps_its_figures(self):
		"""A format change on Amazon's side is not a reason to throw away the
		numbers; the raw string goes back untouched."""
		bucket = spapi_sales._bucket({"interval": "whenever", "unitCount": 7})
		self.assertIsNone(bucket["period_start"])
		self.assertEqual(bucket["interval"], "whenever")
		self.assertEqual(bucket["units"], 7)


class TestOrderMetricsArguments(UnitTestCase):
	"""Validation that happens before the call, so a bad ask is a sentence."""

	def _call(self, **kw):
		with patch.object(spapi_sales, "_marketplace") as marketplace:
			marketplace.return_value = frappe._dict(marketplace_id="A21TJRUUN4KGV", currency="INR")
			return spapi_sales.order_metrics(client=object(), **kw)

	def test_asin_and_sku_together_are_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			self._call,
			date_from="2026-08-01",
			asin="B0TEST00001",
			sku="KETTLE-BLUE",
		)

	def test_a_backwards_period_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, self._call, date_from="2026-08-31", date_to="2026-08-01"
		)

	def test_an_unknown_granularity_names_the_real_ones(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._call(date_from="2026-08-01", granularity="fortnight")
		self.assertIn("Month", str(caught.exception))

	def test_granularity_is_accepted_in_the_pack_schema_casing(self):
		"""The tool schema offers lowercase; Amazon wants title case."""
		with patch.object(spapi_sales, "_marketplace") as marketplace:
			marketplace.return_value = frappe._dict(marketplace_id="A21TJRUUN4KGV", currency="INR")
			client = Mock()
			client.get.return_value = {"payload": []}
			result = spapi_sales.order_metrics(
				"2026-08-01", "2026-08-31", granularity="month", client=client
			)
		# Amazon is asked in its own vocabulary, whatever casing arrived.
		self.assertEqual(client.get.call_args.kwargs["params"]["granularity"], "Month")
		self.assertEqual(result["period"]["granularity"], "Month")

	def test_a_period_wider_than_amazon_accepts_is_refused_before_the_call(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._call(date_from="2020-01-01", date_to="2026-12-31")
		self.assertIn("730", str(caught.exception))

	def test_an_unknown_fulfillment_network_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, self._call, date_from="2026-08-01", fulfillment_network="AMAZON"
		)
