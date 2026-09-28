# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the connection-scoped settings endpoints — what the OS screen
is allowed to write, and what it is not.

These exist because the endpoints they cover replace a *generic* pair. The
platform's `alaiy_os.api.connectors` reads a DocType's metadata and saves back
whatever it rendered, and `alaiy_os_connector_shopify/api/settings.py` copied
that shape — filter the fields by fieldtype, write the rest. Amazon cannot: half
this DocType is written by the connector rather than the operator, and the one
that matters is `refresh_token`, which arrives from the consent round trip and
from nowhere else. A fieldtype filter would have let it through as a Password,
put a "leave blank to keep" box on the settings screen for it, and made a saved
form able to overwrite a seller's authorization with an empty string.

So `EDITABLE_FIELDS` is an allowlist, and these pin that it behaves like one.
The id rules get the same treatment for a smaller reason: a connection id
becomes the docname, and the two characters that break a docname break it
somewhere far from here.

No site. Every database call and the connection helpers are patched; what is
under test is this module's own arithmetic, not frappe's.
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api import api


class FakeDoc:
	"""Enough Document for the settings endpoints: a name, and fields that stick."""

	def __init__(self, name="seller-b", **values):
		self.name = name
		self.values = {"label": "Seller B", "region": "EU", "refresh_token": "Atzr|REAL", **values}
		self.saved = False

	def get(self, fieldname, default=None):
		return self.values.get(fieldname, default)

	def set(self, fieldname, value):
		self.values[fieldname] = value

	def save(self):
		self.saved = True

	def is_connected(self):
		return bool(self.values.get("refresh_token"))


class TestConnectionSettings(UnitTestCase):
	def setUp(self):
		self._patch(patch.object(api, "_require_manager"))
		self._patch(patch.object(api.frappe.db, "commit"))

	def _patch(self, patcher):
		patcher.start()
		self.addCleanup(patcher.stop)

	# --- what a save may touch ------------------------------------------------
	def test_a_save_writes_the_fields_the_screen_owns(self):
		doc = FakeDoc()
		with (
			patch.object(api.connections, "for_write", return_value=doc),
			patch.object(api, "test_connection", return_value={"success": True, "message": "ok"}),
		):
			result = api.save_connection("seller-b", {"region": "NA", "orders_company": "Acme"})

		self.assertEqual(doc.get("region"), "NA")
		self.assertEqual(doc.get("orders_company"), "Acme")
		self.assertTrue(doc.saved)
		self.assertEqual(result["connection"], "seller-b")

	def test_a_save_cannot_touch_the_refresh_token(self):
		"""The one that matters. A form post is not how a seller gets authorized."""
		doc = FakeDoc()
		with (
			patch.object(api.connections, "for_write", return_value=doc),
			patch.object(api, "test_connection", return_value={"success": True, "message": "ok"}),
		):
			api.save_connection("seller-b", {"refresh_token": "", "region": "NA"})

		self.assertEqual(doc.get("refresh_token"), "Atzr|REAL")
		self.assertEqual(doc.get("region"), "NA")

	def test_a_save_cannot_fake_a_status(self):
		"""`last_status` is what the preflight found, not what a caller claims."""
		doc = FakeDoc(last_status="error")
		with (
			patch.object(api.connections, "for_write", return_value=doc),
			patch.object(api, "test_connection", return_value={"success": False, "message": "no"}),
		):
			api.save_connection("seller-b", {"last_status": "connected", "connected_at": "2026-01-01"})

		self.assertEqual(doc.get("last_status"), "error")
		self.assertIsNone(doc.get("connected_at"))

	def test_values_are_json_from_a_form_post(self):
		"""Frappe hands a POSTed dict through as a string; the endpoint takes both."""
		doc = FakeDoc()
		with (
			patch.object(api.connections, "for_write", return_value=doc),
			patch.object(api, "test_connection", return_value={"success": True, "message": "ok"}),
		):
			api.save_connection("seller-b", '{"region": "FE"}')

		self.assertEqual(doc.get("region"), "FE")

	def test_the_config_read_never_carries_the_token(self):
		with patch.object(api.connections, "resolve", return_value=FakeDoc()):
			config = api.get_connection_config("seller-b")

		self.assertNotIn("refresh_token", config["values"])
		self.assertEqual(config["values"]["region"], "EU")
		self.assertEqual(config["connection"], "seller-b")
		self.assertEqual(config["label"], "Seller B")

	# --- adding one -----------------------------------------------------------
	def test_a_new_connection_is_never_the_default(self):
		"""Flagging it would move every unnamed call to the new seller in silence."""
		with (
			patch.object(api.frappe.db, "exists", return_value=False),
			patch.object(api.connections, "create", return_value=FakeDoc("acme-uk")) as create,
		):
			api.create_connection("acme-uk", label="Acme UK", region="EU")

		self.assertNotIn("is_default", create.call_args.kwargs)
		self.assertEqual(create.call_args.kwargs["owner_app"], "alaiy_os_connector_amazon_sp_api")
		self.assertEqual(create.call_args.kwargs["region"], "EU")

	def test_an_id_that_would_not_survive_being_a_docname(self):
		for bad in ("acme/uk", "acme uk", "acme%2Fuk", "", "   "):
			with self.subTest(connection_id=bad):
				with patch.object(api.frappe.db, "exists", return_value=False):
					with self.assertRaises(frappe.ValidationError):
						api.create_connection(bad)

	def test_an_id_that_is_already_taken(self):
		"""`connections.create` is idempotent; here that would be the wrong answer.

		The operator asked for a new seller. Handing back the existing row would
		show them somebody else's connection and call it theirs.
		"""
		with patch.object(api.frappe.db, "exists", return_value=True):
			with self.assertRaises(frappe.ValidationError):
				api.create_connection("seller-b")

	# --- the default flag -----------------------------------------------------
	def test_making_one_default_clears_the_others(self):
		"""Two flagged rows make `resolve_name` a coin toss between two sellers."""
		written = {}
		with (
			patch.object(api.connections, "resolve_name", return_value="seller-b"),
			patch.object(api.connections, "names", return_value=["seller-a", "seller-b", "seller-c"]),
			patch.object(
				api.frappe.db,
				"set_value",
				side_effect=lambda doctype, name, field, value: written.__setitem__(name, value),
			),
			patch.object(api.frappe, "get_doc", return_value=FakeDoc("seller-b", is_default=1)),
		):
			api.set_default_connection("seller-b")

		self.assertEqual(written, {"seller-a": 0, "seller-b": 1, "seller-c": 0})
