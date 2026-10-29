# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the OAuth state — the one thing tying a consent round trip to
the operator who started it.

The state used to be cached under `frappe.session.sid`, which assumed the
browser comes back to the hostname it left from. It does not. The redirect URI
is built from `app_url`, and on every split deployment here that names the other
host: the OS frontend owns the site's hostname and the Desk sits on
`desk.<host>`, or the reverse. Frappe's `sid` cookie carries no Domain
attribute, so those are two sessions — the state was minted in one, looked for
in the other, and never found. Every authorization started from the Desk form —
the only entry point that names *which* connection it is for — came back as
"OAuth state mismatch", every time.

So the two halves of the replacement are pinned here: a state is found again
from any session, and it is still worth nothing to any user but the one who
minted it. The second is what the session id used to be doing, and dropping it
along with the key would have traded a broken flow for an open one.

No site. The cache is a dict, the session is a `_dict`, and `resolve_name` is
the only thing in this module that would otherwise want a database.
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api import oauth

OPERATOR = "ops@alaiy.com"


class FakeCache:
	"""The three calls `oauth` makes, over a dict.

	Expiry is not modelled: `STATE_TTL` is handed to redis and honoured by
	redis, and a test that reimplemented it would be testing this class.
	"""

	def __init__(self):
		self.store = {}

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value

	def get_value(self, key):
		return self.store.get(key)

	def delete_value(self, key):
		self.store.pop(key, None)


class TestOauthState(UnitTestCase):
	def setUp(self):
		self.cache = FakeCache()
		self._patch(patch.object(oauth.frappe, "cache", return_value=self.cache))
		# The only database read in the flow. Answers with the id it was given,
		# so a test can tell the two sellers apart.
		self._patch(
			patch.object(
				oauth.connections,
				"resolve_name",
				side_effect=lambda connection=None: connection or "default",
			)
		)
		# A session to start from; individual tests move to another one.
		self.session(OPERATOR, "sid-desk")

	def _patch(self, patcher):
		patcher.start()
		self.addCleanup(patcher.stop)

	def session(self, user, sid):
		"""Answer the next calls as this user, on this session.

		A new session rather than a mutation, because that is what the second
		half of the round trip is: another hostname, another cookie jar, another
		`sid` entirely — and the same person behind it.
		"""
		patcher = patch.object(oauth.frappe, "session", frappe._dict(user=user, sid=sid))
		self._patch(patcher)

	def test_a_state_survives_the_hop_to_another_session(self):
		"""The regression: the callback lands on the other hostname, as it always did."""
		state = oauth.issue_state("seller-b")

		self.session(OPERATOR, "sid-frontend")

		self.assertEqual(oauth.consume_state(state), "seller-b")

	def test_two_connections_can_be_authorized_at_once(self):
		"""One key per state, so a second Connect no longer overwrites the first.

		Two tabs was not a hypothetical once a bench could hold several sellers:
		the state was keyed by session alone, so starting B's authorization
		silently spent A's.
		"""
		first = oauth.issue_state("seller-a")
		second = oauth.issue_state("seller-b")

		self.assertEqual(oauth.consume_state(second), "seller-b")
		self.assertEqual(oauth.consume_state(first), "seller-a")

	def test_a_state_is_single_use(self):
		state = oauth.issue_state("seller-b")

		self.assertEqual(oauth.consume_state(state), "seller-b")
		self.assertIsNone(oauth.consume_state(state))

	def test_another_user_cannot_spend_it(self):
		"""What replaces the session binding — and the reason keying on the state is safe."""
		state = oauth.issue_state("seller-b")

		self.session("someone-else@alaiy.com", "sid-frontend")

		self.assertIsNone(oauth.consume_state(state))

	def test_a_refused_state_is_still_spent(self):
		"""A wrong user burns the state: one guess per round trip, not unlimited."""
		state = oauth.issue_state("seller-b")
		self.session("someone-else@alaiy.com", "sid-frontend")
		oauth.consume_state(state)

		self.session(OPERATOR, "sid-desk")

		self.assertIsNone(oauth.consume_state(state))

	def test_a_state_this_bench_never_issued(self):
		oauth.issue_state("seller-b")

		self.assertIsNone(oauth.consume_state("not-a-state-of-ours"))

	def test_no_state_at_all(self):
		"""Amazon can redirect back without one; it must not read the cache for it."""
		oauth.issue_state("seller-b")

		self.assertIsNone(oauth.consume_state(None))
		self.assertIsNone(oauth.consume_state(""))


BENCH_CALLBACK = "https://bench.example.com/amazon-oauth/callback"
PORTAL_CALLBACK = "https://portal.example.com/amazon/callback"


class TestRedirectOverride(UnitTestCase):
	"""A portal on its own hostname names its own callback — and only an allowed one.

	The exchange has to name the URI the consent screen was given, byte for byte,
	or LWA refuses the code. So the override is stored with the state, and the
	callback reads it back from there rather than from anything the browser sends.
	"""

	def setUp(self):
		self.cache = FakeCache()
		for patcher in (
			patch.object(oauth.frappe, "cache", return_value=self.cache),
			patch.object(
				oauth.connections, "resolve_name", side_effect=lambda connection=None: connection or "default"
			),
			patch.object(oauth.frappe, "session", frappe._dict(user=OPERATOR, sid="sid-portal")),
			patch.object(oauth, "redirect_uri", return_value=BENCH_CALLBACK),
			patch.object(oauth.config, "allowed_redirect_uris", return_value=[PORTAL_CALLBACK]),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_no_override_is_the_bench_callback(self):
		self.assertEqual(oauth.checked_redirect_uri(), BENCH_CALLBACK)
		self.assertEqual(oauth.checked_redirect_uri(None), BENCH_CALLBACK)

	def test_an_allowed_uri_is_accepted_without_its_trailing_slash(self):
		self.assertEqual(oauth.checked_redirect_uri(PORTAL_CALLBACK + "/"), PORTAL_CALLBACK)

	def test_an_unlisted_uri_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			oauth.checked_redirect_uri("https://evil.example.com/amazon/callback")

	def test_an_unlisted_uri_is_refused_before_a_state_is_issued(self):
		with self.assertRaises(frappe.PermissionError):
			oauth.issue_state("seller-b", redirect_to="https://evil.example.com/cb")
		self.assertEqual(self.cache.store, {})

	def test_the_exchange_names_the_uri_the_state_was_issued_with(self):
		state = oauth.issue_state("seller-b", redirect_to=PORTAL_CALLBACK)
		with (
			patch.object(oauth.auth, "exchange_authorization_code", return_value={}) as exchange,
		):
			result = oauth.complete_authorization("CODE", state)
		exchange.assert_called_once_with("CODE", PORTAL_CALLBACK)
		# No refresh token in the fake payload: the flow stops there, having
		# already named the URI — which is the part under test.
		self.assertFalse(result["success"])

	def test_a_state_without_an_override_exchanges_on_the_bench_callback(self):
		state = oauth.issue_state("seller-b")
		with patch.object(oauth.auth, "exchange_authorization_code", return_value={}) as exchange:
			oauth.complete_authorization("CODE", state)
		exchange.assert_called_once_with("CODE", BENCH_CALLBACK)

	def test_consume_state_still_answers_with_the_connection(self):
		state = oauth.issue_state("seller-b", redirect_to=PORTAL_CALLBACK)
		self.assertEqual(oauth.consume_state(state), "seller-b")
