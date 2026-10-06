# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Turning a photo this connector already holds into something a model can look at.

`get_product` hands the listing agent the product's photographs, because they are
the primary evidence for material, colour, pattern, construction and any spec text
printed onto the image. A URL is not evidence; these functions are what make it
one.

## Reading only

This is the half of the old agent pack's image plumbing that **enrichment** needs
— turning a photo already on the listing into something the model can look at.
Producing images (`prepare_images`, the translated gallery) is `handlers.py`;
what it produces is stored wherever `translate_image` hands back a URL, not
through this module.
"""

import base64
import os

import frappe

# Anthropic vision accepts JPEG, PNG, GIF, WEBP.
MEDIA_TYPES = {
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".gif": "image/gif",
	".webp": "image/webp",
}

# Some product-photo CDNs block requests with no browser-like User-Agent
# (confirmed: a provider's own url-source fetch was refused by one such CDN) — so
# an external image is always fetched here rather than handed over as a bare URL
# for something else to fetch.
FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlaiyOS-AmazonListing/1.0)"}


def media_type(path_or_name):
	"""Guess an image media type from a filename or URL extension, or None."""
	ext = os.path.splitext(path_or_name or "")[1].lower()
	return MEDIA_TYPES.get(ext)


def image_block_from_file(file_name):
	"""A base64 vision block from a File docname, or None."""
	mime = media_type(file_name)
	try:
		file_doc = frappe.get_doc("File", file_name)
		mime = mime or media_type(file_doc.file_name or file_doc.file_url)
		if not mime:
			return None
		content = file_doc.get_content()  # bytes for a binary/image file
		if isinstance(content, str):
			content = content.encode("utf-8", "ignore")
		return _block(content, mime)
	except Exception:
		return None


def fetch_image_bytes(image_url):
	"""Download an external image URL ourselves. Returns (bytes, media_type)."""
	import requests

	resp = requests.get(image_url, timeout=30, headers=FETCH_HEADERS)
	resp.raise_for_status()
	mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
	if not mime or not mime.startswith("image/"):
		mime = media_type(image_url) or "image/jpeg"
	return resp.content, mime


def fetch_image_block(image_url):
	"""A base64 vision block from an external image URL."""
	content, mime = fetch_image_bytes(image_url)
	return _block(content, mime)


def _stored_block(url):
	"""A vision block for an image the site holds -- in its S3 bucket or as a local
	File -- read with the site's own access, or None. A stored S3 object is private, so
	a plain HTTP GET of its URL is refused; this is how the model still sees it."""
	from alaiy_os import image_store

	try:
		stored = image_store.read(url)
	except Exception:
		return None
	if not stored:
		return None
	content, mime = stored
	return _block(content, mime or media_type(url) or "image/jpeg")


def image_block_from_url(url):
	"""A vision block for an image URL on a listing row, or None if unreadable.

	An image the site holds -- an object in its bucket, or a site-relative File like
	'/files/x.jpg', neither HTTP-fetchable on its own -- is read with the site's own
	access, and an external http(s) url is downloaded. Returns None rather than
	raising: one photo that cannot be read must not take a whole enrichment down with it.
	"""
	if not url:
		return None
	block = _stored_block(url)
	if block:
		return block
	if url.startswith("http"):
		try:
			return fetch_image_block(url)
		except Exception:
			return None
	return None


def reference_source(url):
	"""The `source` half of a vision block, for grounding a call in a real photo.

	Raises where `image_block_from_url` returns None, because a caller asking for
	a reference has nothing to fall back on — it wanted *this* photo.
	"""
	block = _stored_block(url)
	if block:
		return block["source"]
	return fetch_image_block(url)["source"]


def public_image_url(url):
	"""An absolute URL a third party -- Amazon, an image service -- can fetch for itself.

	See `alaiy_os.image_store.fetchable_url`: an object in the site's bucket comes back
	presigned, a local File is moved to the bucket on first use and presigned (or, on a
	site with no bucket, expanded against the site URL), and a supplier CDN photo passes
	straight through.
	"""
	from alaiy_os import image_store

	return image_store.fetchable_url(url)


def _block(content, mime):
	return {
		"type": "image",
		"source": {
			"type": "base64",
			"media_type": mime,
			"data": base64.b64encode(content).decode("ascii"),
		},
	}
