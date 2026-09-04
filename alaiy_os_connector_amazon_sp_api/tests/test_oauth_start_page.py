# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the `/amazon-oauth/start` controller — the parts that need no site.

What is pinned here is how that page *fails*, not how it succeeds. Every reason
it can fail is something the operator can act on — no connection on the site
yet, several with none named, an app credential still missing from
`site_config` — and all three arrive as a `frappe.ValidationError` carrying the
sentence that says which. Letting one out of `get_context` renders frappe's
generic website error page instead ("Server Error / There was an error building
this page", HTTP 417, because `ValidationError.http_status_code` is 417) and
discards the message, which is the only part worth reading. A website route has
no dialog to fall back on, so the page has to say it itself.

The redirect case covers the other half of the same bug. The connection has to
reach **both** `issue_state`, which binds the single-use state to one seller,
and `consent_url` — pass it to neither and a bench holding several sellers
resolves whichever is default and stores the returned refresh token against the
wrong connection.

The role gate is checked too, and for the opposite reason: `PermissionError` is
not a `ValidationError` in frappe's hierarchy, and a 403 must keep leaving this
function as one rather than being rendered as a page saying "cannot start".
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.connections import NoConnection
from alaiy_os_connector_amazon_sp_api.www import amazon_oauth_start

CONSENT_URL = "https://sellercentral.amazon.in/apps/authorize/consent?application_id=x&state=s"
NO_CONNECTION = "No Amazon connection has been set up on this site."
AMBIGUOUS = "This site has 3 Amazon connections, so the call has to name one."


class TestOauthStartPage(UnitTestCase):
	def setUp(self):
		# The controller reads the query string off frappe.form_dict and writes
		# the redirect onto frappe.local.flags. Both are request state that a test
		# runner has no request for, so they are swapped and put back.
		self._form_dict = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict()
		self.addCleanup(self._restore_form_dict)

		# Everything under test is what happens *after* the role gate; the gate
		# itself has its own case below.
		gate = patch.object(amazon_oauth_start.oauth, "require_oauth_role")
		gate.start()
		self.addCleanup(gate.stop)

		frappe.clear_messages()

	def _restore_form_dict(self):
		frappe.local.form_dict = self._form_dict

	def _get_context(self):
		return amazon_oauth_start.get_context(frappe._dict())

	def test_redirect_carries_the_connection_to_both_calls(self):
		"""The named seller reaches the state *and* the consent URL, or the wrong one is authorized."""
		frappe.local.form_dict = frappe._dict(connection="seller-b")

		with (
			patch.object(amazon_oauth_start.oauth, "issue_state", return_value="STATE") as issue_state,
			patch.object(amazon_oauth_start.oauth, "consent_url", return_value=CONSENT_URL) as consent_url,
		):
			with self.assertRaises(frappe.Redirect):
				self._get_context()

		issue_state.assert_called_once_with("seller-b")
		consent_url.assert_called_once_with("STATE", "seller-b")
		self.assertEqual(frappe.local.flags.redirect_location, CONSENT_URL)

	def test_no_connection_renders_the_message_instead_of_417(self):
		"""The regression: deleting every connection turned Connect into a bare error page."""
		with patch.object(
			amazon_oauth_start.oauth, "issue_state", side_effect=self._throwing(NO_CONNECTION)
		):
			context = self._get_context()

		self.assertEqual(context.error, NO_CONNECTION)

	def test_ambiguous_connection_renders_the_message(self):
		"""Several sellers and none named is the same class of fixable mistake."""
		with patch.object(amazon_oauth_start.oauth, "issue_state", side_effect=self._throwing(AMBIGUOUS)):
			context = self._get_context()

		self.assertEqual(context.error, AMBIGUOUS)

	def test_rendered_message_is_not_left_queued(self):
		"""`frappe.throw` records on its way out; the page shows it, so the queue is drained."""
		with patch.object(
			amazon_oauth_start.oauth, "issue_state", side_effect=self._throwing(NO_CONNECTION)
		):
			self._get_context()

		self.assertEqual(frappe.get_message_log(), [])

	def test_permission_error_still_raises(self):
		"""A 403 is not a fixable-by-the-operator case and must not become a page."""
		with patch.object(
			amazon_oauth_start.oauth,
			"require_oauth_role",
			side_effect=frappe.PermissionError("not permitted"),
		):
			with self.assertRaises(frappe.PermissionError):
				self._get_context()

	@staticmethod
	def _throwing(message):
		"""A side effect that fails the way the real code does — via `frappe.throw`.

		Raising `NoConnection` directly would skip the message log entirely, and
		draining it is one of the things being tested.
		"""

		def throw(*args, **kwargs):
			frappe.throw(message, NoConnection)

		return throw
