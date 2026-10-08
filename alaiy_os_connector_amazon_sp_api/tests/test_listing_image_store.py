# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Produced listing images go through the site's image store, and leave as links Amazon can fetch.

Amazon fetches every image itself, so what it is sent has to be an address it can
read: a produced image in the site's bucket is private and must be signed, and a local
File's site-relative path is not an address at all ("Invalid URL Provided"). What is
stored, compared and copied stays the plain URL; signing happens only on the way out.

The store itself is tested in `alaiy_os`; here it is replaced by stand-ins that record
what the connector asked of it.
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.listing import handlers, images
from alaiy_os_connector_amazon_sp_api.spapi import listings

MP = frappe._dict({"name": "A21TJRUUN4KGV", "marketplace_id": "A21TJRUUN4KGV"})
STORED = "https://bucket.s3.ap-south-1.amazonaws.com/images/generated/2026/10/listing-SKU-main-x.jpg"


def _signed(url):
	return f"{url}?X-Amz-Signature=fake" if url.startswith("https://bucket.") else url


class TestSendingToAmazon(UnitTestCase):
	def locations(self, urls):
		with patch("alaiy_os.image_store.fetchable_url", side_effect=_signed):
			attrs = listings._image_attributes(MP, [{"url": url} for url in urls])
		return [loc[0]["media_location"] for loc in attrs.values()]

	def test_a_stored_image_is_signed_on_the_way_out(self):
		self.assertEqual(self.locations([STORED]), [_signed(STORED)])

	def test_every_image_goes_through_the_same_step(self):
		supplier = "https://cdn.supplier.example/a.jpg"
		with patch("alaiy_os.image_store.fetchable_url", side_effect=lambda url: f"fetchable:{url}") as fetchable:
			listings._image_attributes(MP, [{"url": STORED}, {"url": "/files/b.jpg"}, {"url": supplier}])
		self.assertEqual([call.args[0] for call in fetchable.call_args_list], [STORED, "/files/b.jpg", supplier])

	def test_the_images_given_are_left_as_they_were(self):
		"""Signing is for the payload only: the urls passed in are what gets written back
		onto the listing after a submission, and a signed link expires."""
		given = [{"url": STORED}]
		with patch("alaiy_os.image_store.fetchable_url", side_effect=_signed):
			listings._image_attributes(MP, given)
		self.assertEqual(given, [{"url": STORED}])


class TestSavingAProducedImage(UnitTestCase):
	def test_the_main_image_is_filed_as_generated_and_the_rest_as_translated(self):
		with patch("alaiy_os.image_store.save", return_value=STORED) as save:
			self.assertEqual(handlers._rehost("SKU-1", "main", b"jpg", "image/jpeg"), STORED)
			handlers._rehost("SKU-1", "gallery_1", b"jpg", "image/jpeg")
		main, gallery = save.call_args_list
		self.assertEqual(main.kwargs["category"], "generated")
		self.assertEqual(gallery.kwargs["category"], "translated")
		self.assertTrue(main.args[0].startswith("listing-SKU-1-main-"))
		self.assertEqual(main.kwargs["metadata"], {"sku": "SKU-1", "role": "main"})


class TestReadingForTheModel(UnitTestCase):
	def test_a_stored_image_is_read_with_the_sites_own_access(self):
		"""A private object refuses a plain GET; the model would silently lose the photo."""
		with patch("alaiy_os.image_store.read", return_value=(b"\x89PNG", "image/png")), \
				patch.object(images, "fetch_image_block") as fetched:
			block = images.image_block_from_url(STORED)
		fetched.assert_not_called()
		self.assertEqual(block["source"]["media_type"], "image/png")

	def test_a_supplier_photo_is_still_downloaded(self):
		with patch("alaiy_os.image_store.read", return_value=None), \
				patch.object(images, "fetch_image_block", return_value={"source": "ok"}) as fetched:
			self.assertEqual(images.image_block_from_url("https://cdn.supplier.example/a.jpg"), {"source": "ok"})
		fetched.assert_called_once()

	def test_an_unreadable_stored_image_is_none_not_an_error(self):
		with patch("alaiy_os.image_store.read", side_effect=RuntimeError("gone")):
			self.assertIsNone(images.image_block_from_url("/files/gone.jpg"))
