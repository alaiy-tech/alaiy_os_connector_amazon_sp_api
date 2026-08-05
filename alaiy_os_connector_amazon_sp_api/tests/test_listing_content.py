# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for listing content: where it comes from and what must not lose it.

Only the pure transformation layer — no SP-API calls, no saved documents. The
bug these cover was silent and total: content was read from the seller's own
Listings attributes, which an offer-only listing does not have, and the empty
result was then written over the row. Every field the operator could see went
blank at once, on every sync, and nothing errored.
"""

import csv
import io

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import catalog, listings, reconcile

# amazon.in — a marketplace whose language tag is not the API default, so a
# language-blind implementation can't pass by accident.
MP = frappe._dict(
	{"name": "A21TJRUUN4KGV", "marketplace_id": "A21TJRUUN4KGV", "currency": "INR", "language": "en_IN"}
)
OTHER_MP_ID = "ATVPDKIKX0DER"


def _attr(value, marketplace_id=MP.marketplace_id, language="en_IN"):
	entry = {"value": value, "marketplace_id": marketplace_id}
	if language:
		entry["language_tag"] = language
	return entry


def _listing_row(**values):
	"""An unsaved Amazon Product Listing, so _apply_content runs against the real doc API."""
	return frappe.get_doc({"doctype": "Amazon Product Listing", "sku": "SKU-1", **values})


class TestCatalogContent(UnitTestCase):
	# --- attribute scoping ----------------------------------------------
	def test_attr_values_keeps_only_this_marketplace(self):
		attributes = {"bullet_point": [_attr("ours"), _attr("theirs", marketplace_id=OTHER_MP_ID)]}
		self.assertEqual(
			catalog._attr_values(attributes, "bullet_point", MP.marketplace_id), ["ours"]
		)

	def test_attr_values_keeps_unscoped_entries(self):
		# Some product types return values with no marketplace at all; dropping
		# them would discard the only content on offer.
		attributes = {"product_description": [_attr("unscoped", marketplace_id=None)]}
		self.assertEqual(
			catalog._attr_values(attributes, "product_description", MP.marketplace_id), ["unscoped"]
		)

	def test_attr_values_prefers_the_marketplace_language(self):
		attributes = {
			"item_name": [_attr("Deutsch", language="de_DE"), _attr("English", language="en_IN")]
		}
		self.assertEqual(
			catalog._attr_values(attributes, "item_name", MP.marketplace_id, "en_IN"), ["English"]
		)

	def test_attr_values_falls_back_when_no_entry_matches_the_language(self):
		attributes = {"item_name": [_attr("Deutsch", language="de_DE")]}
		self.assertEqual(
			catalog._attr_values(attributes, "item_name", MP.marketplace_id, "en_IN"), ["Deutsch"]
		)

	def test_attr_values_skips_blank_and_non_string_values(self):
		attributes = {
			"generic_keyword": [_attr("  "), _attr(None), _attr({"nested": 1}), _attr("real")]
		}
		self.assertEqual(
			catalog._attr_values(attributes, "generic_keyword", MP.marketplace_id), ["real"]
		)

	# --- images ---------------------------------------------------------
	def test_images_take_the_largest_of_each_variant_in_schema_order(self):
		item = {
			"images": [
				{
					"marketplaceId": MP.marketplace_id,
					"images": [
						{"variant": "PT01", "link": "pt01.jpg", "height": 500, "width": 500},
						{"variant": "MAIN", "link": "main-small.jpg", "height": 75, "width": 75},
						{"variant": "MAIN", "link": "main-big.jpg", "height": 1500, "width": 1500},
					],
				}
			]
		}
		self.assertEqual(
			catalog._images_from(item, MP.marketplace_id),
			[
				{"url": "main-big.jpg", "is_main": True},
				{"url": "pt01.jpg", "is_main": False},
			],
		)

	def test_images_skip_colour_swatches(self):
		# A swatch is not a product shot and would cost one of the eight slots.
		item = {
			"images": [
				{
					"marketplaceId": MP.marketplace_id,
					"images": [
						{"variant": "MAIN", "link": "main.jpg", "height": 100, "width": 100},
						{"variant": "SWCH", "link": "swatch.jpg", "height": 100, "width": 100},
					],
				}
			]
		}
		self.assertEqual(
			catalog._images_from(item, MP.marketplace_id), [{"url": "main.jpg", "is_main": True}]
		)

	def test_images_promote_a_main_when_the_asin_has_no_main_variant(self):
		item = {
			"images": [
				{
					"marketplaceId": MP.marketplace_id,
					"images": [{"variant": "PT02", "link": "pt02.jpg", "height": 100, "width": 100}],
				}
			]
		}
		self.assertEqual(
			catalog._images_from(item, MP.marketplace_id), [{"url": "pt02.jpg", "is_main": True}]
		)

	def test_images_are_read_from_this_marketplaces_block(self):
		item = {
			"images": [
				{
					"marketplaceId": OTHER_MP_ID,
					"images": [{"variant": "MAIN", "link": "us.jpg", "height": 100, "width": 100}],
				},
				{
					"marketplaceId": MP.marketplace_id,
					"images": [{"variant": "MAIN", "link": "in.jpg", "height": 100, "width": 100}],
				},
			]
		}
		self.assertEqual(
			catalog._images_from(item, MP.marketplace_id), [{"url": "in.jpg", "is_main": True}]
		)

	# --- whole-item normalisation ---------------------------------------
	def test_content_from_item_maps_every_field(self):
		item = {
			"asin": "B0TEST00001",
			"attributes": {
				"item_name": [_attr("Cotton Shirt")],
				"product_description": [_attr("<p>Soft cotton.</p>")],
				"bullet_point": [_attr("Breathable"), _attr("Machine washable")],
				"generic_keyword": [_attr("shirt cotton")],
			},
			"images": [
				{
					"marketplaceId": MP.marketplace_id,
					"images": [{"variant": "MAIN", "link": "main.jpg", "height": 500, "width": 500}],
				}
			],
			"summaries": [{"marketplaceId": MP.marketplace_id, "itemName": "Summary Name"}],
		}
		content = catalog.content_from_item(item, MP)
		self.assertEqual(content["title"], "Cotton Shirt")
		self.assertEqual(content["description"], "<p>Soft cotton.</p>")
		self.assertEqual(content["bullets"], ["Breathable", "Machine washable"])
		self.assertEqual(content["keywords"], ["shirt cotton"])
		self.assertEqual(content["images"], [{"url": "main.jpg", "is_main": True}])

	def test_content_from_item_falls_back_to_the_summary(self):
		# includedData=attributes can come back empty for an ASIN we can still name.
		item = {
			"asin": "B0TEST00002",
			"summaries": [
				{
					"marketplaceId": MP.marketplace_id,
					"itemName": "Summary Name",
					"mainImage": {"link": "thumb.jpg"},
				}
			],
		}
		content = catalog.content_from_item(item, MP)
		self.assertEqual(content["title"], "Summary Name")
		self.assertEqual(content["images"], [{"url": "thumb.jpg", "is_main": True}])
		self.assertIsNone(content["description"])
		self.assertEqual(content["bullets"], [])


class TestApplyContentPrecedence(UnitTestCase):
	# The attributes an offer-only listing actually returns: offer only, no
	# content whatsoever. This is the payload that used to blank the row.
	OFFER_ONLY_ATTRIBUTES = {
		"condition_type": [{"marketplace_id": MP.marketplace_id, "value": "new_new"}],
		"merchant_suggested_asin": [{"marketplace_id": MP.marketplace_id, "value": "B0TEST00001"}],
		"purchasable_offer": [{"marketplace_id": MP.marketplace_id, "our_price": []}],
	}

	CATALOG_CONTENT = {
		"title": "Catalog Title",
		"description": "Catalog description",
		"bullets": ["Catalog bullet"],
		"keywords": ["catalog keyword"],
		"images": [{"url": "catalog-main.jpg", "is_main": True}],
	}

	def test_offer_only_listing_takes_content_from_the_catalog(self):
		row = _listing_row()
		listings._apply_content(
			row,
			MP,
			{"attributes": self.OFFER_ONLY_ATTRIBUTES},
			{},
			catalog_content=self.CATALOG_CONTENT,
		)
		self.assertEqual(row.title, "Catalog Title")
		self.assertEqual(row.description, "Catalog description")
		self.assertEqual([b.bullet for b in row.bullet_points], ["Catalog bullet"])
		self.assertEqual([k.keyword for k in row.keywords], ["catalog keyword"])
		self.assertEqual([(i.image_url, i.is_main) for i in row.images], [("catalog-main.jpg", 1)])

	def test_our_own_attributes_win_over_the_catalog(self):
		# A seller who owns the ASIN's content should see their own, not the
		# catalog's rendering of it.
		item = {
			"attributes": {
				**self.OFFER_ONLY_ATTRIBUTES,
				"item_name": [_attr("Our Title")],
				"product_description": [_attr("Our description")],
				"bullet_point": [_attr("Our bullet")],
				"generic_keyword": [_attr("our keyword")],
				"main_product_image_locator": [
					{"marketplace_id": MP.marketplace_id, "media_location": "ours-main.jpg"}
				],
				"other_product_image_locator_1": [
					{"marketplace_id": MP.marketplace_id, "media_location": "ours-alt.jpg"}
				],
			}
		}
		row = _listing_row()
		listings._apply_content(row, MP, item, {}, catalog_content=self.CATALOG_CONTENT)
		self.assertEqual(row.title, "Our Title")
		self.assertEqual(row.description, "Our description")
		self.assertEqual([b.bullet for b in row.bullet_points], ["Our bullet"])
		self.assertEqual([k.keyword for k in row.keywords], ["our keyword"])
		self.assertEqual(
			[(i.image_url, i.is_main) for i in row.images],
			[("ours-main.jpg", 1), ("ours-alt.jpg", 0)],
		)

	def test_a_content_free_sync_never_blanks_what_the_row_already_has(self):
		"""The regression test for the reported bug.

		Offer-only attributes, no catalog answer. Every field must survive: the
		old code assigned unconditionally and wiped all five.
		"""
		row = _listing_row(
			title="Existing Title",
			description="Existing description",
			bullet_points=[{"bullet": "Existing bullet"}],
			keywords=[{"keyword": "existing keyword"}],
			images=[{"image_url": "existing.jpg", "is_main": 1}],
		)
		listings._apply_content(
			row, MP, {"attributes": self.OFFER_ONLY_ATTRIBUTES}, {}, catalog_content=None
		)
		self.assertEqual(row.title, "Existing Title")
		self.assertEqual(row.description, "Existing description")
		self.assertEqual([b.bullet for b in row.bullet_points], ["Existing bullet"])
		self.assertEqual([k.keyword for k in row.keywords], ["existing keyword"])
		self.assertEqual([i.image_url for i in row.images], ["existing.jpg"])

	def test_an_empty_payload_never_blanks_what_the_row_already_has(self):
		# The bulk path used to guard this case with an early return; the guard is
		# gone, so assert the behaviour it protected directly.
		row = _listing_row(title="Existing Title", description="Existing description")
		listings._apply_content(row, MP, {}, {}, catalog_content=None)
		self.assertEqual(row.title, "Existing Title")
		self.assertEqual(row.description, "Existing description")

	def test_summary_main_image_is_the_last_resort(self):
		row = _listing_row()
		listings._apply_content(
			row,
			MP,
			{"attributes": self.OFFER_ONLY_ATTRIBUTES},
			{"mainImage": {"link": "summary-thumb.jpg"}},
			catalog_content=None,
		)
		self.assertEqual([i.image_url for i in row.images], ["summary-thumb.jpg"])


class TestReportParsing(UnitTestCase):
	HEADER = "item-name\titem-description\tseller-sku\tstatus"

	def test_an_unbalanced_quote_does_not_swallow_the_following_rows(self):
		# `"Premium quality` opens a quote that never closes. Under csv's default
		# quoting this consumed the rest of the row *and* every row after it, so
		# SKU-2 vanished from the reconcile entirely.
		text = "\n".join(
			[
				self.HEADER,
				'Shirt\t"Premium quality\tSKU-1\tactive',
				"Trouser\tplain\tSKU-2\tinactive",
			]
		)
		rows = reconcile._parse_rows(text)
		self.assertEqual([r["seller-sku"] for r in rows], ["SKU-1", "SKU-2"])
		self.assertEqual([r["status"] for r in rows], ["active", "inactive"])
		self.assertEqual(rows[0]["item-description"], '"Premium quality')

	def test_default_csv_quoting_is_what_loses_the_row(self):
		# Pins the reason QUOTE_NONE is passed, so nobody "tidies" it away.
		text = "\n".join(
			[
				self.HEADER,
				'Shirt\t"Premium quality\tSKU-1\tactive',
				"Trouser\tplain\tSKU-2\tinactive",
			]
		)
		naive = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
		self.assertEqual(len(naive), 1)
		self.assertEqual(len(reconcile._parse_rows(text)), 2)

	def test_inch_marks_survive(self):
		text = "\n".join([self.HEADER, '43" LED TV\t43" screen\tSKU-TV\tactive'])
		rows = reconcile._parse_rows(text)
		self.assertEqual(rows[0]["item-name"], '43" LED TV')
		self.assertEqual(rows[0]["seller-sku"], "SKU-TV")


class TestReportContentSeeding(UnitTestCase):
	REPORT_ROW = {
		"item-name": "Report Title",
		"item-description": "Report description",
		"image-url": "report.jpg",
	}

	def test_seeds_a_row_that_has_no_content(self):
		# The variation-parent / offer-only case: title is NULL, so the list view
		# was falling back to `name` and showing the raw SKU.
		row = _listing_row()
		reconcile._seed_content(row, self.REPORT_ROW)
		self.assertEqual(row.title, "Report Title")
		self.assertEqual(row.description, "Report description")
		self.assertEqual([i.image_url for i in row.images], ["report.jpg"])

	def test_never_overwrites_content_that_is_already_there(self):
		row = _listing_row(
			title="Richer Title",
			description="Richer description",
			images=[{"image_url": "richer.jpg", "is_main": 1}],
		)
		reconcile._seed_content(row, self.REPORT_ROW)
		self.assertEqual(row.title, "Richer Title")
		self.assertEqual(row.description, "Richer description")
		self.assertEqual([i.image_url for i in row.images], ["richer.jpg"])

	def test_tolerates_a_report_row_with_no_content_columns(self):
		row = _listing_row(title="Kept")
		reconcile._seed_content(row, {})
		self.assertEqual(row.title, "Kept")
		self.assertFalse(row.get("images"))


class TestReconcileCatalogEnrichment(UnitTestCase):
	"""The report-driven reconcile is the only path that reaches a catalog past the
	Listings API's ~1000-SKU cap, so it is where content and parentage have to be
	filled. It used to fill neither, which is what left every row beyond the cap
	showing its SKU instead of a title.
	"""

	CATALOG = {
		"title": "Real Product Title",
		"description": "Real description",
		"bullets": ["Breathable"],
		"keywords": ["shirt"],
		"images": [{"url": "real.jpg", "is_main": True}],
		"parent_asin": "B0PARENT001",
		"variation_theme": "SIZE",
		"is_variation_parent": False,
	}
	# A variation parent's report row: `item-name` is the SKU-ish string, which is
	# exactly the value that must not win.
	REPORT_ROW = {
		"item-name": "PARENT-12345",
		"item-description": "thin report description",
		"image-url": "report.jpg",
	}

	def test_catalog_content_beats_the_report_columns(self):
		row = _listing_row()
		reconcile._apply_catalog(row, MP, self.CATALOG)
		reconcile._seed_content(row, self.REPORT_ROW)
		self.assertEqual(row.title, "Real Product Title")
		self.assertEqual(row.description, "Real description")
		self.assertEqual([i.image_url for i in row.images], ["real.jpg"])
		self.assertEqual([b.bullet for b in row.bullet_points], ["Breathable"])
		self.assertEqual([k.keyword for k in row.keywords], ["shirt"])

	def test_parentage_lands_on_the_reconcile_path(self):
		row = _listing_row()
		reconcile._apply_catalog(row, MP, self.CATALOG)
		self.assertEqual(row.parent_asin, "B0PARENT001")
		self.assertEqual(row.variation_theme, "SIZE")

	def test_an_enriched_row_is_marked_done(self):
		row = _listing_row()
		reconcile._apply_catalog(row, MP, self.CATALOG)
		self.assertTrue(row.catalog_synced_at)

	def test_no_catalog_answer_falls_back_to_the_report_and_retries_later(self):
		# Not marked done, so the next run tries this SKU again rather than
		# leaving it permanently untitled.
		row = _listing_row()
		reconcile._apply_catalog(row, MP, None)
		reconcile._seed_content(row, self.REPORT_ROW)
		self.assertEqual(row.title, "PARENT-12345")
		self.assertIsNone(row.get("catalog_synced_at"))
		self.assertIsNone(row.get("parent_asin"))
