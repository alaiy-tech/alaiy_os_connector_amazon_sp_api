# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""The product facts a product type asks for, filled by the listing agent.

A new catalog entry needs far more than copy. Amazon's definition of a product type
requires, mostly conditionally, dozens of facts about the product itself: material,
colour, size, closure, compartments, dimensions, target audience. Nothing else in a
sourced product's data holds them, and the agent already has what does — the
supplier's description, its specification table and the photos.

So the agent is asked for them in the same run that writes the copy:

  `to_fill`  what `get_product` shows the model: each attribute the definition
             requires that this listing's payload does not already carry, with
             Amazon's title, its accepted values and a value shaped to fill in.
  `shape`    what `save_listing` does with the answer: each value put into the
             shape the FINAL product type gives it (the type is settled from the
             finished title, after the model is done), anything that type does not
             have left out.

Approval merges the result into the listing's Extra Attributes, never over a value
already there (`AmazonEnrichedListing._sync_attributes`).

What is never asked: the seller's own declarations. Who made, imported and packed a
product, its warranty, its origin, its identifiers and its offer terms are facts
about the business, not about what is in the photo. A model asked for them would
answer anyway, and the answer would be invented.
"""

import frappe

#: Attributes the model is never asked for and whose answers are dropped. Either the
#: listing's own fields already feed them (title, brand, bullets, images, offer), or
#: they are the seller's declarations, which a product's description and photos
#: cannot answer.
NOT_ASKED = frozenset(
	{
		# Fed by the listing's own fields.
		"item_name",
		"brand",
		"product_description",
		"bullet_point",
		"generic_keyword",
		"main_product_image_locator",
		"purchasable_offer",
		"fulfillment_availability",
		"condition_type",
		"list_price",
		"skip_offer",
		# Identity: a barcode, an ASIN or an exemption, decided by the seller.
		"externally_assigned_product_identifier",
		"merchant_suggested_asin",
		"supplier_declared_has_product_identifier_exemption",
		"model_number",
		"part_number",
		"model_name",
		# The seller's declarations.
		"manufacturer",
		"rtip_manufacturer_contact_information",
		"importer_contact_information",
		"packer_contact_information",
		"country_of_origin",
		"supplier_declared_dg_hz_regulation",
		"warranty_provider_contact_information",
		"base_product_mfg_warranty_period",
		"base_product_mfg_warranty_tnc_description",
		"steps_to_avail_mfg_warranty_description",
		"warranty_description",
		"included_components",
		"batteries_required",
		"contains_liquid_contents",
		"parentage_level",
		"child_parent_sku_relationship",
		"variation_theme",
	}
)

#: Accepted values listed per attribute. Enough for every colour family or size
#: scheme in practice; past this the model is told how many more there are.
MAX_OPTIONS = 40

#: Amazon's attribute descriptions run long; this keeps the per-product prompt small.
MAX_DESCRIPTION = 240


def asked(name):
	"""Whether the model may fill this attribute at all."""
	return name not in NOT_ASKED and not name.startswith("other_product_image_locator")


def to_fill(sku, product_type=None):
	"""[{attribute, title, description, accepted, more, fill}] for the model to answer.

	`product_type` is what to read requirements from when the listing has none yet —
	a provisional answer, since the final type is settled from the finished title.
	Empty when there is no type or no definition: there is nothing to ask.

	Requirements are read for the listing's payload as it would be submitted, so an
	attribute already supplied — by a field, by Extra Attributes — is not asked again,
	and one only some products need (by marketplace, by another attribute) is asked
	only where it applies.
	"""
	from alaiy_os_connector_amazon_sp_api.spapi import listings, product_types

	row = listings._register_row(sku)
	product_type = (row.get("product_type") or product_type or "").strip()
	if not product_type:
		return []
	mp = listings._marketplace(row.get("marketplace"))
	schema = product_types.get_definition(product_type, marketplace=mp.name)
	if not schema:
		return []
	# In memory only. Images are never asked for, and building their locators would
	# move local files to the image store just to read requirements.
	row.product_type = product_type
	row.set("images", [])
	attributes = listings._catalog_attributes(mp, row)
	out = []
	for name in listings.missing_required(attributes, schema):
		if not asked(name):
			continue
		shown, total = product_types.attribute_options(schema, name, limit=MAX_OPTIONS)
		prop = (schema.get("properties") or {}).get(name) or {}
		out.append(
			{
				"attribute": name,
				"title": product_types.attribute_title(schema, name),
				"description": (prop.get("description") or "")[:MAX_DESCRIPTION],
				"accepted": shown,
				"more": max(0, total - len(shown)),
				"fill": _fill_template(product_types.attribute_example(schema, name, mp.marketplace_id)),
			}
		)
	return out


def _fill_template(example):
	"""The definition's example value without the keys `shape` adds itself.

	An array example becomes its one item: the model answers one value per attribute.
	"""
	if isinstance(example, list):
		example = example[0] if example else {}
	if isinstance(example, dict):
		return {key: value for key, value in example.items() if key not in ("marketplace_id", "language_tag")}
	return example


def shape(sku, product_type, answers):
	"""(shaped, dropped): the model's answers as the final product type takes them.

	`answers` is {attribute: value}, each value a plain value or the dict `to_fill`
	offered. An attribute the model may not fill, or that `product_type` does not
	have — the title may have moved the listing to another type since it was asked
	— is in `dropped` rather than written.
	"""
	from alaiy_os_connector_amazon_sp_api.spapi import listings, product_types

	if not isinstance(answers, dict) or not answers or not product_type:
		return {}, sorted(answers) if isinstance(answers, dict) else []
	row = listings._register_row(sku)
	mp = listings._marketplace(row.get("marketplace"))
	schema = product_types.get_definition(product_type, marketplace=mp.name)
	shaped, dropped = {}, []
	for name, value in answers.items():
		if value in (None, "", [], {}) or not asked(name):
			dropped.append(name)
			continue
		if isinstance(value, list):
			# The model answered in Amazon's own array form; one value is what is asked.
			value = value[0] if value else None
		entry = product_types.attribute_value(schema, name, value, mp.marketplace_id, mp.get("language"))
		if entry is None:
			dropped.append(name)
		else:
			shaped[name] = entry
	return shaped, sorted(dropped)


def merged_into(extra_attributes, shaped):
	"""`extra_attributes` (JSON text) with each of `shaped` it lacks, or None to leave it.

	Never over a value already there: one an operator wrote, or a seller declaration,
	is theirs. None also for text that is not a JSON object, which would hide what
	someone typed if it were rewritten.
	"""
	try:
		blob = frappe.parse_json(extra_attributes) if (extra_attributes or "").strip() else {}
	except Exception:
		return None
	if not isinstance(blob, dict):
		return None
	missing = {name: value for name, value in (shaped or {}).items() if name not in blob}
	if not missing:
		return None
	blob.update(missing)
	return frappe.as_json(blob, indent=1)
