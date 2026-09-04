# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Turning a photo this connector already holds into something a model can look at.

`get_product` hands the listing agent the product's photographs, because they are
the primary evidence for material, colour, pattern, construction and any spec text
printed onto the image. A URL is not evidence; these functions are what make it
one.

## Reading only

This is the half of the old agent pack's image plumbing that **enrichment** needs.
The other half — producing images (the white-background main tile, the translated
gallery), storing them in S3, and rendering them on a background queue — has not
moved yet, and neither has the `prepare_images` step that drives it. So the
`listing_channels` adapter declares no image handler, `get_channel_spec` reports
`has_image_step: false`, and the agent sets `images: []` and says so in its notes.
That is a supported state, not a broken one.

What that costs today: a photo an *earlier* run produced and stored in S3 cannot
be read back here, because resolving one needs the store's own credentials. Source
photos are unaffected — they are ordinary CDN URLs on the listing, or Frappe
Files — and those are what enrichment actually reads. When the producing side
moves across, `image_store` comes with it and the S3 branch is restored to
`image_block_from_url` and `reference_source`.
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


def image_block_from_url(url):
	"""A vision block for an image URL on a listing row, or None if unreadable.

	Resolves a site-relative Frappe File url ('/files/x.jpg', which is not
	HTTP-fetchable on its own) by reading the File directly, and an external
	http(s) url by downloading it. Returns None rather than raising: one photo
	that cannot be read must not take a whole enrichment down with it.
	"""
	if not url:
		return None
	file_name = frappe.db.get_value("File", {"file_url": url}, "name")
	if file_name:
		return image_block_from_file(file_name)
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
	file_name = frappe.db.get_value("File", {"file_url": url}, "name")
	if file_name:
		block = image_block_from_file(file_name)
		if block:
			return block["source"]
	return fetch_image_block(url)["source"]


def public_image_url(url):
	"""An absolute URL a third party can fetch for itself.

	A supplier CDN photo is already absolute and passes straight through. One
	stored as a local Frappe File is only a site-relative path, so it is expanded
	against the site URL — which only actually resolves when the site is reachable
	from the public internet, so a local or dev site will fail for any service that
	fetches the image itself.
	"""
	if url.startswith("http://") or url.startswith("https://"):
		return url
	return frappe.utils.get_url(url)


def _block(content, mime):
	return {
		"type": "image",
		"source": {
			"type": "base64",
			"media_type": mime,
			"data": base64.b64encode(content).decode("ascii"),
		},
	}
