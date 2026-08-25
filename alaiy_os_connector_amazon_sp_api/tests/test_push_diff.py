# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for what "Push Update to Amazon" decides to send.

Only the pure diff layer — no SP-API calls, no saved documents.

The bug these cover was that a push diffed the form against the register row's
last clean state, so it answered "what did the operator edit since the page
loaded?" instead of "what does Amazon not have?". Neither side of that
comparison is Amazon: a row is only as fresh as the last sync, and
update_listing writes the *submitted* values onto it and marks it pending, so a
change Amazon rejected still reads locally as though it landed. Re-pushing it
was impossible — the row already agreed with the form, so the button reported
nothing to push.
"""

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import listings

MP = frappe._dict(
	{"name": "A21TJRUUN4KGV", "marketplace_id": "A21TJRUUN4KGV", "currency": "INR", "language": "en_IN"}
)

# What Amazon holds. Every test states its desired side against this.
REMOTE = {
	"title": "Cotton Shirt",
	"description": "Soft cotton.",
	"price": 499.0,
	"quantity": 12,
	"condition": "new_new",
	"bullet_points": ["Breathable", "Machine washable"],
	"keywords": ["shirt", "cotton"],
	"images": [{"url": "main.jpg", "is_main": True}, {"url": "alt.jpg", "is_main": False}],
}


def _desired(**overrides):
	"""The form's values, matching Amazon except where a test says otherwise."""
	return {
		"title": REMOTE["title"],
		"description": REMOTE["description"],
		"price": REMOTE["price"],
		"quantity": REMOTE["quantity"],
		"condition": REMOTE["condition"],
		"bullet_points": list(REMOTE["bullet_points"]),
		"keywords": list(REMOTE["keywords"]),
		"images": [dict(im) for im in REMOTE["images"]],
		**overrides,
	}


class TestDiffFromRemote(UnitTestCase):
	def test_a_form_that_matches_amazon_pushes_nothing(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired()), {})

	def test_only_the_fields_that_differ_are_sent(self):
		changes = listings.diff_from_remote(REMOTE, _desired(price=549))
		self.assertEqual(changes, {"price": 549.0})

	def test_a_field_the_operator_never_touched_still_pushes_when_amazon_lacks_it(self):
		"""The whole point of the change.

		A previous push wrote 549 onto the row and marked it pending; Amazon
		rejected it and still has 499. The form is clean, so a row-vs-form diff
		saw nothing to do and the listing stayed wrong forever.
		"""
		row_after_a_rejected_push = _desired(price=549)
		self.assertEqual(
			listings.diff_from_remote(REMOTE, row_after_a_rejected_push), {"price": 549.0}
		)

	def test_a_seller_central_edit_is_picked_up_as_a_difference(self):
		# Someone changed the title in Seller Central; ours is now the odd one out.
		amazon_moved_on = {**REMOTE, "title": "Cotton Shirt (Relisted)"}
		self.assertEqual(
			listings.diff_from_remote(amazon_moved_on, _desired()), {"title": "Cotton Shirt"}
		)

	# --- what counts as "no opinion" -------------------------------------
	def test_blank_scalars_are_not_a_request_to_clear(self):
		changes = listings.diff_from_remote(REMOTE, _desired(title="", description="   ", condition=None))
		self.assertEqual(changes, {})

	def test_empty_child_tables_are_not_a_request_to_clear(self):
		changes = listings.diff_from_remote(REMOTE, _desired(bullet_points=[], keywords=[], images=[]))
		self.assertEqual(changes, {})

	def test_a_missing_key_is_never_invented(self):
		# The caller may push a subset; absent fields are not compared at all.
		self.assertEqual(listings.diff_from_remote(REMOTE, {"price": 549}), {"price": 549.0})

	def test_whitespace_only_differences_are_not_changes(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(title="  Cotton Shirt  ")), {})

	# --- offer fields -----------------------------------------------------
	def test_a_price_amazon_never_answered_counts_as_different(self):
		no_offer = {**REMOTE, "price": None}
		self.assertEqual(listings.diff_from_remote(no_offer, _desired()), {"price": 499.0})

	def test_float_noise_is_not_a_price_change(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(price=499.000000001)), {})

	def test_a_sub_paisa_difference_is_not_a_price_change(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(price=499.004)), {})

	def test_a_zero_price_is_never_pushed(self):
		# Amazon takes no such price, so a blank/zero field is a form gap.
		for price in (0, "0", "", None):
			self.assertNotIn("price", listings.diff_from_remote(REMOTE, _desired(price=price)))

	def test_zero_quantity_is_pushed_because_it_means_out_of_stock(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(quantity=0)), {"quantity": 0})

	def test_a_blank_quantity_is_left_alone(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(quantity=None)), {})

	def test_a_quantity_amazon_never_answered_counts_as_different(self):
		no_availability = {**REMOTE, "quantity": None}
		self.assertEqual(listings.diff_from_remote(no_availability, _desired()), {"quantity": 12})

	def test_a_string_quantity_from_json_compares_as_a_number(self):
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(quantity="12")), {})

	# --- lists and images -------------------------------------------------
	def test_reordered_bullets_are_a_change(self):
		flipped = list(reversed(REMOTE["bullet_points"]))
		self.assertEqual(
			listings.diff_from_remote(REMOTE, _desired(bullet_points=flipped)),
			{"bullet_points": flipped},
		)

	def test_a_dropped_bullet_is_a_change(self):
		self.assertEqual(
			listings.diff_from_remote(REMOTE, _desired(bullet_points=["Breathable"])),
			{"bullet_points": ["Breathable"]},
		)

	def test_blank_rows_in_a_child_table_are_ignored(self):
		padded = ["Breathable", "  ", "Machine washable", None]
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(bullet_points=padded)), {})

	def test_image_row_order_does_not_matter_while_the_main_image_holds(self):
		# The main image is sent first whatever row it sits in, so moving rows
		# about in the grid is not a change worth a submission.
		shuffled = [{"url": "alt.jpg", "is_main": False}, {"url": "main.jpg", "is_main": True}]
		self.assertEqual(listings.diff_from_remote(REMOTE, _desired(images=shuffled)), {})

	def test_promoting_a_different_main_image_is_a_change(self):
		promoted = [{"url": "main.jpg", "is_main": False}, {"url": "alt.jpg", "is_main": True}]
		changes = listings.diff_from_remote(REMOTE, _desired(images=promoted))
		self.assertEqual(
			changes["images"], [{"url": "alt.jpg", "is_main": True}, {"url": "main.jpg", "is_main": False}]
		)

	def test_an_added_image_is_a_change(self):
		added = [*REMOTE["images"], {"url": "third.jpg", "is_main": False}]
		self.assertIn("images", listings.diff_from_remote(REMOTE, _desired(images=added)))

	def test_an_empty_remote_takes_everything_the_form_has(self):
		# Nothing on Amazon to compare against (a listing that carries no content
		# and no offer): every field the operator filled in is genuinely new.
		changes = listings.diff_from_remote({}, _desired())
		self.assertEqual(
			sorted(changes),
			["bullet_points", "condition", "description", "images", "keywords", "price", "quantity", "title"],
		)


class TestRemoteSnapshotParts(UnitTestCase):
	"""The two halves remote_snapshot() assembles, without the HTTP round trip."""

	OFFER_ONLY_ATTRIBUTES = {
		"condition_type": [{"marketplace_id": MP.marketplace_id, "value": "used_good"}],
		"merchant_suggested_asin": [{"marketplace_id": MP.marketplace_id, "value": "B0TEST00001"}],
	}
	CATALOG_CONTENT = {
		"title": "Catalog Title",
		"description": "Catalog description",
		"bullets": ["Catalog bullet"],
		"keywords": ["catalog keyword"],
		"images": [{"url": "catalog-main.jpg", "is_main": True}],
	}

	def test_offer_reads_price_quantity_and_condition(self):
		item = {
			"offers": [{"price": {"amount": 499.0, "currency": "INR"}}],
			"fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 12}],
		}
		offer = listings._offer_from_item(item, {"conditionType": "used_good"})
		self.assertEqual(offer["price"], 499.0)
		self.assertEqual(offer["quantity"], 12)
		self.assertEqual(offer["condition"], "used_good")

	def test_a_missing_offer_block_is_none_not_zero(self):
		# Zero would read as "Amazon has ₹0.00 / no stock" and make every push
		# look like a price and quantity change.
		offer = listings._offer_from_item({}, {})
		self.assertIsNone(offer["price"])
		self.assertIsNone(offer["quantity"])
		self.assertEqual(offer["fulfillment_channel"], "DEFAULT")

	def test_offer_only_content_falls_back_to_the_catalog(self):
		# The row's content came from the catalog too, so the baseline has to use
		# the same precedence — otherwise every offer-only listing reports its
		# whole content as pending changes and pushes it on the first click.
		content = listings._content_from_item(
			{"attributes": self.OFFER_ONLY_ATTRIBUTES}, {}, self.CATALOG_CONTENT
		)
		self.assertEqual(content["title"], "Catalog Title")
		self.assertEqual(content["bullet_points"], ["Catalog bullet"])
		self.assertEqual(content["images"], [{"url": "catalog-main.jpg", "is_main": True}])

	def test_our_own_attributes_win_over_the_catalog(self):
		item = {
			"attributes": {
				**self.OFFER_ONLY_ATTRIBUTES,
				"item_name": [{"marketplace_id": MP.marketplace_id, "value": "Our Title"}],
				"main_product_image_locator": [
					{"marketplace_id": MP.marketplace_id, "media_location": "ours-main.jpg"}
				],
			}
		}
		content = listings._content_from_item(item, {}, self.CATALOG_CONTENT)
		self.assertEqual(content["title"], "Our Title")
		self.assertEqual(content["images"], [{"url": "ours-main.jpg", "is_main": True}])

	def test_content_amazon_does_not_have_is_reported_absent(self):
		# Unlike the row upsert, the snapshot must not paper over a gap: absent
		# content is what makes the operator's value a change worth pushing.
		content = listings._content_from_item({"attributes": self.OFFER_ONLY_ATTRIBUTES}, {}, None)
		self.assertIsNone(content["title"])
		self.assertIsNone(content["description"])
		self.assertEqual(content["bullet_points"], [])
		self.assertEqual(content["images"], [])

	def test_brand_is_never_pushed(self):
		"""Brand is read from Amazon, never written back to it.

		It belongs to whoever created the ASIN, and an offer update that carried
		it would be rejected — so a row whose brand differs from the catalog's
		must still produce no change, however the operator got that value there.
		"""
		remote = listings._content_from_item({}, {}, {"brand": "Catalog Brand"})
		self.assertEqual(remote["brand"], "Catalog Brand")
		self.assertEqual(listings.diff_from_remote(remote, {"brand": "Something Else"}), {})

	def test_an_offer_only_row_in_sync_with_the_catalog_pushes_nothing(self):
		"""The regression the whole precedence rule exists for.

		The operator opens a synced offer-only listing, changes the price, and
		pushes. Content must not ride along.
		"""
		remote = {
			**listings._offer_from_item(
				{
					"offers": [{"price": {"amount": 499.0}}],
					"fulfillmentAvailability": [{"quantity": 12}],
				},
				{},
			),
			**listings._content_from_item(
				{"attributes": self.OFFER_ONLY_ATTRIBUTES}, {}, self.CATALOG_CONTENT
			),
		}
		desired = {
			"title": "Catalog Title",
			"description": "Catalog description",
			"bullet_points": ["Catalog bullet"],
			"keywords": ["catalog keyword"],
			"images": [{"url": "catalog-main.jpg", "is_main": True}],
			"condition": "new_new",
			"price": 549,
			"quantity": 12,
		}
		self.assertEqual(listings.diff_from_remote(remote, desired), {"price": 549.0})
