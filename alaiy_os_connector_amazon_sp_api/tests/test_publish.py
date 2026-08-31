# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for what "Publish" decides, before it reaches Amazon.

Only the pure decision layer — no SP-API calls, no saved documents. The branch
itself (create where Amazon has no listing, update where it has) is one `if` over
`remote_snapshot(missing_ok=True)`; what is worth pinning down is everything
feeding it, because each piece is what a *bulk* publish leans on and a bulk
publish has no operator watching one row.

  * `desired_from_row` — a selection of rows has no form behind it, so the row is
	the intent. It must read a row whose optional fields were never set, which is
	exactly what a freshly drafted listing is.
  * `_create_blockers` — Amazon's two requirements for an offer. Reported rather
	than attempted, so a row that cannot go says why instead of collecting a
	rejection minutes later.
  * `_create_warnings` — the offers Amazon accepts and nobody can buy.
  * `_publish_error` — one line per failed row, because that line is the whole
	report a bulk publish leaves on the row.
  * `_raise_on_rejected_submission` — the other end of the same run: whether
	Amazon took the write at all. A refusal read as a success stamps the row
	`pending` and there is nothing left to notice it by.
"""

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import listings


def _row(**values):
	"""An unsaved Amazon Product Listing — a draft, before it has ever been published."""
	return frappe.get_doc({"doctype": "Amazon Product Listing", "sku": "SKU-1", **values})


class TestDesiredFromRow(UnitTestCase):
	def test_reads_scalars_and_child_tables(self):
		row = _row(
			title="Cotton Shirt",
			price=499,
			quantity=12,
			condition="new_new",
			description="Soft cotton.",
			bullet_points=[{"bullet": "Breathable"}, {"bullet": "Machine washable"}],
			keywords=[{"keyword": "shirt"}],
			images=[{"image_url": "main.jpg", "is_main": 1}, {"image_url": "alt.jpg", "is_main": 0}],
		)
		self.assertEqual(
			listings.desired_from_row(row),
			{
				"title": "Cotton Shirt",
				"price": 499,
				"quantity": 12,
				"condition": "new_new",
				"description": "Soft cotton.",
				"bullet_points": ["Breathable", "Machine washable"],
				"keywords": ["shirt"],
				"images": [{"url": "main.jpg", "is_main": 1}, {"url": "alt.jpg", "is_main": 0}],
			},
		)

	def test_a_row_with_nothing_set_reads_as_no_opinion(self):
		# A BaseDocument has no __getattr__, so an unset field is an
		# AttributeError rather than a None — and a drafted row is mostly unset
		# fields. Every value here has to come through .get().
		self.assertEqual(
			listings.desired_from_row(_row()),
			{
				"title": None,
				"price": None,
				"quantity": None,
				"condition": None,
				"description": None,
				"bullet_points": [],
				"keywords": [],
				"images": [],
			},
		)

	def test_a_row_amazon_already_matches_publishes_nothing(self):
		# The whole point of diffing the row against Amazon rather than pushing
		# it: re-publishing a selection must be a no-op for the rows that are
		# already right, or a bulk publish could never be run twice.
		remote = {
			"title": "Cotton Shirt",
			"description": "Soft cotton.",
			"price": 499.0,
			"quantity": 12,
			"condition": "new_new",
			"bullet_points": ["Breathable"],
			"keywords": ["shirt"],
			"images": [{"url": "main.jpg", "is_main": True}],
		}
		row = _row(
			title="Cotton Shirt",
			description="Soft cotton.",
			price=499,
			quantity=12,
			condition="new_new",
			bullet_points=[{"bullet": "Breathable"}],
			keywords=[{"keyword": "shirt"}],
			images=[{"image_url": "main.jpg", "is_main": 1}],
		)
		self.assertEqual(listings.diff_from_remote(remote, listings.desired_from_row(row)), {})

	def test_only_what_amazon_lacks_is_published(self):
		remote = {"price": 499.0, "quantity": 12, "condition": "new_new"}
		row = _row(price=549, quantity=12, condition="new_new")
		self.assertEqual(
			listings.diff_from_remote(remote, listings.desired_from_row(row)), {"price": 549.0}
		)


class TestCreateBlockers(UnitTestCase):
	def test_a_row_with_both_requirements_is_ready(self):
		self.assertEqual(listings._create_blockers(_row(asin="B0TEST12345", product_type="SHIRT")), [])

	def test_a_missing_asin_is_named(self):
		blockers = listings._create_blockers(_row(product_type="SHIRT"))
		self.assertEqual(len(blockers), 1)
		self.assertIn("ASIN", blockers[0])

	def test_a_missing_product_type_is_named(self):
		blockers = listings._create_blockers(_row(asin="B0TEST12345"))
		self.assertEqual(len(blockers), 1)
		self.assertIn("product type", blockers[0])

	def test_a_bare_draft_reports_both(self):
		self.assertEqual(len(listings._create_blockers(_row())), 2)


class TestCreateWarnings(UnitTestCase):
	def test_a_priced_and_stocked_offer_warns_about_nothing(self):
		self.assertEqual(listings._create_warnings(_row(price=499, quantity=3)), [])

	def test_no_price_is_a_warning_not_a_blocker(self):
		# Amazon takes the offer; nobody can buy it. That is the operator's call
		# to make, so it is said rather than refused.
		warnings = listings._create_warnings(_row(quantity=3))
		self.assertEqual(len(warnings), 1)
		self.assertIn("price", warnings[0].lower())

	def test_zero_quantity_publishes_out_of_stock(self):
		warnings = listings._create_warnings(_row(price=499, quantity=0))
		self.assertEqual(len(warnings), 1)
		self.assertIn("out of stock", warnings[0].lower())


class TestPublishError(UnitTestCase):
	def test_a_thrown_message_survives_as_one_line(self):
		# frappe.throw's message reaches the handler wrapped in markup and split
		# over lines; the row has one Small Text field to say it in.
		error = Exception("Amazon rejected the listing:<br>\n  [4001] Missing attribute")
		self.assertEqual(
			listings._publish_error(error), "Amazon rejected the listing: [4001] Missing attribute"
		)

	def test_an_empty_message_falls_back_to_the_class(self):
		# "" on the row would read as "no error" — which is what the row says
		# when the publish worked.
		self.assertEqual(listings._publish_error(TimeoutError()), "TimeoutError")


class TestRejectedSubmission(UnitTestCase):
	"""A Listings write is judged by `status` as well as by `issues`.

	Both halves matter and they fail differently. ERROR issues say what Amazon
	objected to, so they are what an operator gets shown; a bare INVALID says only
	that the submission was refused, and letting it through as a success is worse
	than an unhelpful message — the row would be stamped `pending` for a write
	that will never be applied.
	"""

	def test_an_accepted_submission_with_no_issues_passes(self):
		listings._raise_on_rejected_submission({"status": "ACCEPTED", "submissionId": "s-1"}, [], "listing")

	def test_accepted_with_warnings_only_still_passes(self):
		# Amazon accepts plenty of listings it has remarks about; only ERROR
		# severity is a refusal.
		issues = [{"code": "8541", "message": "Image is small.", "severity": "WARNING"}]
		listings._raise_on_rejected_submission({"status": "ACCEPTED"}, issues, "listing")

	def test_error_issues_are_reported_with_code_and_message(self):
		issues = [{"code": "4001", "message": "Missing attribute.", "severity": "ERROR"}]
		with self.assertRaises(frappe.ValidationError) as caught:
			listings._raise_on_rejected_submission({"status": "INVALID"}, issues, "listing")
		self.assertIn("[4001] Missing attribute.", str(caught.exception))

	def test_invalid_without_issues_is_still_a_rejection(self):
		# The regression this guards: reading `issues` alone, this response is
		# indistinguishable from a clean accept.
		with self.assertRaises(frappe.ValidationError) as caught:
			listings._raise_on_rejected_submission({"status": "INVALID", "submissionId": "s-9"}, [], "update")
		self.assertIn("s-9", str(caught.exception))

	def test_a_response_without_a_status_falls_back_to_the_issues(self):
		# DELETE answers without a submission status; nothing to judge but issues.
		listings._raise_on_rejected_submission({}, [], "deletion")
		with self.assertRaises(frappe.ValidationError):
			listings._raise_on_rejected_submission(
				{}, [{"code": "5000", "message": "No.", "severity": "ERROR"}], "deletion"
			)
