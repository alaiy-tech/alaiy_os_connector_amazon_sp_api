# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
What the reviewer's screens call — the endpoints behind Amazon Enriched Listing.

An enrichment lands in `Needs Review` and a person decides what happens to it.
These are the three things that decision needs: approving one or many, the house
brands to judge the Brand field against, and viewable links for the images.

They came across from the retired Amazon listing agent app, with the DocType they
belong to. Moving that DocType without them left the review flow calling an
app that no longer exists — the Approve button in the list view did nothing at
all, which is a poor way to discover a migration was half-done. They live beside
the DocType now, so the two cannot separate again.

Only these three moved. The rest of that api.py drove the old app's own desk
pages and bulk-enrichment surfaces, and those went with the app.

## Approving is what publishes

`approve_listings` is not a status flag. Each row is approved through an ordinary
`doc.save()`, so `AmazonEnrichedListing.on_update` fires and pushes the approved
content onto the Amazon Product Listing — the same path a one-at-a-time approval
takes. That is deliberate: a bulk action that wrote the status directly would
skip the publish and leave a listing marked Approved with none of its content
applied.
"""

import json

import frappe

ENRICHED_DOCTYPE = "Amazon Enriched Listing"


@frappe.whitelist()
def approve_listings(names):
	"""
	Approve many enriched listings at once — the list view's "Approve" action.

	Each listing is approved through a normal document save, so the same
	on_update hook that fires for a one-at-a-time approval pushes each one to its
	Amazon Product Listing. Returns {approved, skipped, failed, errors}:
	already-approved rows are counted as skipped, and one bad listing does not
	stop the rest (its error is reported per name instead).
	"""
	if isinstance(names, str):
		names = json.loads(names)
	if not names:
		frappe.throw("approve_listings needs at least one listing name.")

	approved, skipped, errors = 0, 0, {}
	for name in names:
		doc = frappe.get_doc(ENRICHED_DOCTYPE, name)
		doc.check_permission("write")
		if doc.status == "Approved":
			skipped += 1
			continue
		try:
			doc.status = "Approved"
			doc.save()
			frappe.db.commit()
			approved += 1
		except Exception:
			# Per row, not per batch: one listing that will not publish must not
			# take the other forty-nine with it, and the reviewer needs to know
			# which one it was.
			frappe.db.rollback()
			errors[name] = str(frappe.get_traceback().splitlines()[-1])
			frappe.log_error(
				title=f"Bulk approve failed: {name}",
				message=frappe.get_traceback(),
			)

	return {
		"approved": approved,
		"skipped": skipped,
		"failed": len(errors),
		"errors": errors,
	}


@frappe.whitelist()
def brand_context():
	"""What the form shows beside the Brand field — `{is_configured, valid_brands}`.

	`is_configured` says whether this site has any house brands at all, so the
	form can explain an empty Brand field correctly either way: nothing to assign,
	or the agent looked and none of this site's house brands fit. `valid_brands`
	is that list, for the form to show as context.

	Takes no listing: the agent classifies brand from the listing's own title and
	description, never from its category, so there is nothing to look up per row.
	"""
	from alaiy_os_connector_amazon_sp_api.listing import brand

	return {
		"is_configured": brand.is_configured(),
		"valid_brands": sorted(brand.valid_brands()),
	}


@frappe.whitelist()
def image_view_links(sku):
	"""
	Viewable links for one enriched listing's images — `{url: viewable_url}`.

	Scoped to one listing the caller may read, and only ever answers about urls
	that listing actually holds. That mattered more when this signed S3 objects —
	handing out a signed link for an arbitrary key would have been handing out
	read access — and it is kept because the property is worth keeping whatever
	the storage is.

	Today it mostly passes urls through. The image *producing* step and its S3
	store did not migrate with the doctype, so an enrichment's images are supplier
	CDN photos or local Files: absolute urls come back unchanged, and a
	site-relative File path is expanded against the site url. A row's own url is
	usually null, because no image step ran. The client can still look every row
	up here without deciding which backend each came from, which is the point of
	the endpoint.

	A sku with no enriched listing answers `{}` rather than throwing: a form may
	ask while a run is still in flight, and there is nothing to show then.
	"""
	if not frappe.db.exists(ENRICHED_DOCTYPE, sku):
		return {}

	doc = frappe.get_doc(ENRICHED_DOCTYPE, sku)
	doc.check_permission("read")

	from alaiy_os_connector_amazon_sp_api.listing import images

	links = {}
	for row in doc.images or []:
		for url in (row.source_url, row.url):
			if url and url not in links:
				links[url] = images.public_image_url(url)
	return links
