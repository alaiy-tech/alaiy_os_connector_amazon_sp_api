# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""A new catalog entry is checked against the whole product type definition before it goes.

Most of what a product type requires is conditional: a definition's top-level
`required` names a handful of attributes, and its `allOf` / `if` / `then` rules name
the rest — in this marketplace, when another attribute is present. A check reading
only the top level passes a payload Amazon then rejects attribute by attribute, so
these tests are written against a definition shaped the way Amazon's are: Draft
2019-09, conditional requirements, and Amazon's own validating keywords.

And because the schema cannot see every rule Amazon applies, `create_asin` asks
Amazon to validate the payload (VALIDATION_PREVIEW) before it submits, and keeps a
rejection on the listing instead of losing it to the request's rollback.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import catalog, listings, product_types

MP = frappe._dict(marketplace_id="A21TJRUUN4KGV", currency="INR", language="en_IN")
MP_ID = MP.marketplace_id


def _text(value):
	return [{"value": value, "language_tag": "en_IN", "marketplace_id": MP_ID}]


def _text_attribute(title, **value_rules):
	return {
		"title": title,
		"type": "array",
		"selectors": ["marketplace_id", "language_tag"],
		"maxUniqueItems": 1,
		"items": {
			"type": "object",
			"required": ["language_tag", "value"],
			"properties": {
				"value": {"type": "string", **value_rules},
				"language_tag": {"type": "string"},
				"marketplace_id": {"type": "string"},
			},
			"additionalProperties": False,
		},
	}


# Shaped like a real definition, cut down to what each rule needs.
DEFINITION = {
	"$schema": "https://schemas.amazon.com/selling-partners/definitions/product-types/meta-schema/v1",
	"type": "object",
	"required": ["item_name", "brand"],
	"properties": {
		"item_name": _text_attribute("Item Name", maxUtf8ByteLength=20),
		"brand": _text_attribute("Brand Name"),
		"generic_keyword": _text_attribute("Search Keyword(s)"),
		"manufacturer": _text_attribute("Manufacturer"),
		"material": _text_attribute("Material Type"),
		"condition_type": {
			"title": "Item Condition",
			"type": "array",
			"items": {
				"type": "object",
				"required": ["value"],
				"properties": {
					"value": {"type": "string", "enum": ["new_new", "used_good"]},
					"marketplace_id": {"type": "string"},
				},
			},
		},
		"externally_assigned_product_identifier": {"title": "External Product ID", "type": "array"},
		"merchant_suggested_asin": {"title": "Merchant Suggested ASIN", "type": "array"},
		"supplier_declared_has_product_identifier_exemption": {"title": "Exemption", "type": "array"},
	},
	"allOf": [
		# Required only in this marketplace — the kind the top level never shows.
		{
			"if": {"required": ["condition_type"]},
			"then": {"required": ["manufacturer", "material"]},
		},
		# No identifier and no exemption: a barcode or an ASIN is then required.
		{
			"if": {"not": {"required": ["supplier_declared_has_product_identifier_exemption"]}},
			"then": {"required": ["externally_assigned_product_identifier", "merchant_suggested_asin"]},
		},
	],
}

COMPLETE = {
	"item_name": _text("Rope toy"),
	"brand": _text("House"),
	"condition_type": [{"value": "new_new", "marketplace_id": MP_ID}],
	"manufacturer": _text("Maker"),
	"material": _text("Cotton"),
	"supplier_declared_has_product_identifier_exemption": [{"value": True, "marketplace_id": MP_ID}],
}


def _problems(**changes):
	attributes = {**COMPLETE, **changes}
	for name, value in changes.items():
		if value is None:
			attributes.pop(name)
	return product_types.definition_problems(attributes, DEFINITION)


class TestDefinitionProblems(UnitTestCase):
	def test_a_complete_payload_has_none(self):
		self.assertEqual(_problems(), [])

	def test_a_conditional_requirement_is_found(self):
		"""What the old check missed: `manufacturer` is in no top-level list."""
		missing = [p for p in _problems(manufacturer=None) if p["missing"]]
		self.assertEqual([p["attribute"] for p in missing], ["manufacturer"])
		self.assertIn("Manufacturer", missing[0]["message"])

	def test_each_attribute_a_rule_requires_is_named_on_its_own(self):
		names = {p["attribute"] for p in _problems(manufacturer=None, material=None)}
		self.assertEqual(names, {"manufacturer", "material"})

	def test_a_top_level_requirement_is_still_found(self):
		self.assertEqual([p["attribute"] for p in _problems(brand=None)], ["brand"])

	def test_one_keyword_value_per_marketplace_and_language(self):
		"""Amazon's `maxUniqueItems` groups by selectors: two values with the same
		marketplace and language are two in one group, which Amazon refuses."""
		problems = _problems(generic_keyword=_text("rope") + _text("toy"))
		self.assertEqual(len(problems), 1)
		self.assertFalse(problems[0]["missing"])
		self.assertIn("At most 1", problems[0]["message"])

	def test_values_in_different_languages_are_different_groups(self):
		keywords = [*_text("rope"), {"value": "rassi", "language_tag": "hi_IN", "marketplace_id": MP_ID}]
		self.assertEqual(_problems(generic_keyword=keywords), [])

	def test_length_is_measured_in_utf8_bytes(self):
		# Ten characters, thirty bytes: within any character limit, over the byte one.
		problems = _problems(item_name=_text("र" * 10))
		self.assertEqual(len(problems), 1)
		self.assertIn("bytes", problems[0]["message"])

	def test_a_value_missing_a_required_key_names_the_key(self):
		problems = _problems(brand=[{"value": "House", "marketplace_id": MP_ID}])
		self.assertEqual(problems[0]["path"], "brand[0]")
		self.assertIn("language_tag", problems[0]["message"])

	def test_an_unknown_enum_value_lists_what_is_accepted(self):
		problems = _problems(condition_type=[{"value": "mint", "marketplace_id": MP_ID}])
		self.assertIn("new_new", problems[0]["message"])
		self.assertEqual(problems[0]["attribute"], "condition_type")

	def test_no_definition_is_no_problems(self):
		self.assertEqual(product_types.definition_problems({}, None), [])


def _row(**values):
	return frappe.get_doc(
		{"doctype": "Amazon Product Listing", "sku": "SKU-1", "product_type": "PET_TOY", **values}
	)


def _blockers(row, attributes):
	return listings.asin_create_blockers(row, attributes, DEFINITION)


class TestCreateBlockers(UnitTestCase):
	def test_a_conditional_requirement_blocks(self):
		row = _row(title="Rope toy")
		blockers = _blockers(row, {k: v for k, v in COMPLETE.items() if k != "material"})
		self.assertEqual(len(blockers), 1)
		self.assertIn("material", blockers[0])
		self.assertIn("Extra Attributes", blockers[0])

	def test_a_malformed_value_blocks(self):
		row = _row(title="Rope toy")
		blockers = _blockers(row, {**COMPLETE, "generic_keyword": _text("a") + _text("b")})
		self.assertEqual(len(blockers), 1)
		self.assertIn("refuses", blockers[0])

	def test_no_identifier_is_said_once_not_once_per_rule(self):
		"""The definition asks for a barcode AND an ASIN when there is neither; the
		identifier blocker already says what to do about both."""
		row = _row(title="Rope toy", brand="House")
		attributes = {
			k: v for k, v in COMPLETE.items() if k != "supplier_declared_has_product_identifier_exemption"
		}
		blockers = _blockers(row, attributes)
		self.assertEqual(len(blockers), 1)
		self.assertIn("identifier", blockers[0])

	def test_the_required_list_includes_what_the_rules_add(self):
		attributes = {k: v for k, v in COMPLETE.items() if k != "manufacturer"}
		self.assertEqual(
			listings._required_names(attributes, DEFINITION), ["item_name", "brand", "manufacturer"]
		)


class TestKeywordAttribute(UnitTestCase):
	def test_keywords_travel_as_one_value(self):
		self.assertEqual(
			listings._keyword_attribute(MP, ["rope toy", " dog ", "", None, "chew"]),
			[{"marketplace_id": MP_ID, "value": "rope toy; dog; chew", "language_tag": "en_IN"}],
		)

	def test_a_keyword_that_does_not_fit_is_left_out_whole(self):
		with patch.object(listings, "KEYWORD_MAX_LENGTH", 12):
			(entry,) = listings._keyword_attribute(MP, ["rope toy", "chew"])
		self.assertEqual(entry["value"], "rope toy")

	def test_no_keywords_is_no_attribute(self):
		self.assertEqual(listings._keyword_attribute(MP, ["", None]), [])

	def test_the_joined_value_reads_back_as_one_row_per_keyword(self):
		(entry,) = listings._keyword_attribute(MP, ["rope toy", "dog", "chew"])
		self.assertEqual(catalog.split_keywords([entry["value"]]), ["rope toy", "dog", "chew"])

	def test_values_written_one_per_keyword_read_back_unchanged(self):
		self.assertEqual(catalog.split_keywords(["rope toy", "dog"]), ["rope toy", "dog"])


class TestPriceAttribute(UnitTestCase):
	def test_the_mrp_goes_beside_the_price(self):
		(offer,) = listings._price_attribute(MP, 228, 274)
		self.assertEqual(offer["our_price"], [{"schedule": [{"value_with_tax": 228.0}]}])
		self.assertEqual(offer["maximum_retail_price"], [{"schedule": [{"value_with_tax": 274.0}]}])
		self.assertEqual(offer["audience"], "ALL")

	def test_no_mrp_sends_none(self):
		for mrp in (None, 0):
			(offer,) = listings._price_attribute(MP, 228, mrp)
			self.assertNotIn("maximum_retail_price", offer)

	def test_a_price_update_keeps_the_rows_mrp(self):
		"""A price update replaces `purchasable_offer` whole; without the MRP in it,
		the update would remove the MRP from the listing."""
		(patch_op,) = listings._build_patches(MP, {"price": 228}, mrp=274)
		self.assertIn("maximum_retail_price", patch_op["value"][0])


class TestBrandAttribute(UnitTestCase):
	def test_brand_is_language_tagged(self):
		with patch.object(listings, "_gtin_exempt_brands", return_value=frozenset()):
			attrs = listings._catalog_attributes(MP, _row(brand="House"))
		self.assertEqual(
			attrs["brand"], [{"marketplace_id": MP_ID, "value": "House", "language_tag": "en_IN"}]
		)


class TestCreateAsin(UnitTestCase):
	"""Amazon validates first; a rejection, at either step, is kept."""

	def _create(self, *responses):
		client = MagicMock()
		client.put.side_effect = list(responses)
		row = _row(title="Rope toy", brand="House")
		with (
			patch.object(listings, "_connection", return_value=frappe._dict(selling_partner_id="SELLER")),
			patch.object(listings, "_register_row", return_value=row),
			patch.object(listings, "_marketplace", return_value=MP),
			patch.object(listings.product_types, "get_definition", return_value=DEFINITION),
			patch.object(listings, "_catalog_attributes", return_value=dict(COMPLETE)),
			patch.object(listings, "SpApiClient", return_value=client),
			patch.object(listings, "_record_publish") as recorded,
			patch.object(listings, "_record_submitted_creation", return_value={"action": "submitted"}),
			patch.object(frappe, "db", MagicMock()) as db,
		):
			try:
				result = listings.create_asin("SKU-1")
			except (frappe.ValidationError, listings.SpApiError) as e:
				result = e
		return result, client, recorded, db

	def test_a_payload_amazon_refuses_to_validate_is_never_submitted(self):
		invalid = {
			"status": "INVALID",
			"issues": [{"code": "90183", "message": "Not the expected value.", "severity": "ERROR"}],
		}
		result, client, recorded, db = self._create(invalid)
		self.assertIsInstance(result, frappe.ValidationError)
		self.assertEqual(client.put.call_count, 1)
		self.assertEqual(client.put.call_args.kwargs["params"]["mode"], "VALIDATION_PREVIEW")
		self.assertIn("90183", recorded.call_args.kwargs["error"])
		db.commit.assert_called_once()

	def test_a_valid_payload_is_then_submitted_for_real(self):
		result, client, recorded, db = self._create(
			{"status": "VALID", "issues": []}, {"status": "ACCEPTED", "issues": []}
		)
		self.assertEqual(result, {"action": "submitted"})
		self.assertEqual(client.put.call_count, 2)
		self.assertNotIn("mode", client.put.call_args.kwargs["params"])
		recorded.assert_not_called()
		db.commit.assert_not_called()

	def test_an_http_refusal_is_kept_and_nothing_more_is_sent(self):
		refused = listings.SpApiError("Bad request", status_code=400)
		result, client, recorded, db = self._create(refused)
		self.assertIsInstance(result, listings.SpApiError)
		self.assertEqual(client.put.call_count, 1)
		self.assertIn("Bad request", recorded.call_args.kwargs["error"])
		db.commit.assert_called_once()

	def test_a_rejected_submission_is_kept_too(self):
		result, _client, recorded, db = self._create(
			{"status": "VALID", "issues": []}, {"status": "INVALID", "issues": []}
		)
		self.assertIsInstance(result, frappe.ValidationError)
		recorded.assert_called_once()
		db.commit.assert_called_once()
