# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Shared helpers for the SP-API OAuth self-authorize flow.

App identity (LWA client id/secret, SP app id, consent host) lives in
site_config.json — never per-seller and never in the browser. The seller
authorizes via Amazon's consent screen; we store only the returned refresh
token, encrypted, on the Amazon Connection Single.
"""

from urllib.parse import urlencode

import frappe
from frappe import _

from alaiy_os_connector_amazon_sp_api import app_config as config
from alaiy_os_connector_amazon_sp_api.spapi import auth
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden

OAUTH_ROLES = ("System Manager", "Amazon Manager")
STATE_TTL = 600  # seconds


def require_oauth_role():
	"""Only an Amazon Manager / System Manager may drive the OAuth flow."""
	roles = set(frappe.get_roles())
	if not roles.intersection(OAUTH_ROLES):
		raise frappe.PermissionError(_("You are not permitted to connect an Amazon account."))


def redirect_uri():
	return f"{config.app_url()}/amazon-oauth/callback"


def _state_cache_key():
	# Bind the CSRF state to the current session.
	return f"amazon_oauth_state::{frappe.session.sid}"


def issue_state():
	state = frappe.generate_hash(length=32)
	frappe.cache().set_value(_state_cache_key(), state, expires_in_sec=STATE_TTL)
	return state


def consume_state(received):
	"""Whether `received` is the state we issued for this session. Single-use.

	Answers rather than throws: a mismatch is the operator's stale tab or a
	back-button replay far more often than it is an attack, and both callbacks
	below report it as a failed connection with a "start again" message.
	"""
	expected = frappe.cache().get_value(_state_cache_key())
	frappe.cache().delete_value(_state_cache_key())  # single-use
	return bool(expected) and bool(received) and received == expected


def consent_url(state):
	"""Build the Amazon Seller Central consent URL."""
	config.assert_ready()

	connection = frappe.get_cached_doc("Amazon Connection")
	base = config.consent_base_url(connection.region)
	if not base:
		frappe.throw(_("No consent base URL for region {0}.").format(connection.region))

	params = {
		"application_id": config.sp_app_id(),
		"state": state,
		"redirect_uri": redirect_uri(),
	}
	# Draft apps must request the beta consent.
	if config.app_beta() or connection.app_status == "Draft":
		params["version"] = "beta"

	return f"{base}/apps/authorize/consent?{urlencode(params)}"


def store_refresh_token(refresh_token, selling_partner_id):
	"""Persist the token on the Amazon Connection Single (encrypted)."""
	connection = frappe.get_doc("Amazon Connection")
	connection.refresh_token = refresh_token
	if selling_partner_id:
		connection.selling_partner_id = selling_partner_id
	connection.connected_at = frappe.utils.now_datetime()
	connection.last_status = "connected"
	connection.last_status_message = "Connected via OAuth"
	connection.save(ignore_permissions=True)
	return connection


def complete_authorization(code, state, selling_partner_id=None, error=None, error_description=None):
	"""Finish the consent round trip: check state, exchange, persist, verify.

	Shared by the *two* places Amazon's redirect can land, because which one it
	reaches is decided by `app_url` — that is, by which side of the deployment
	owns the site's hostname:

	  * `www/amazon_oauth_callback.py`, when Frappe serves the site (the Desk
	    keeps the hostname and the frontend, if any, is on its own).
	  * the OS's own `/amazon-oauth/callback` screen, via `api.complete_oauth`,
	    when the composed frontend owns the hostname and Frappe has moved to
	    `desk.<host>` — where the www page above is no longer reachable at all.

	Both have to behave identically, so the flow lives here once and neither
	caller decides anything.

	Returns `{"success", "message", "status", "selling_partner_id"}` and raises
	for nothing an operator can act on — every outcome here is something to tell
	them, and half of them arrive as a query parameter from Amazon rather than as
	an exception. Callers add their own role gate.
	"""
	# Amazon can redirect back with a refusal instead of a code.
	if error:
		return _failed(error_description or error)

	if not consume_state(state):
		return _failed(_("OAuth state mismatch. Please start the connection again."))

	if not code:
		return _failed(_("No authorization code returned by Amazon."))

	try:
		token_payload = auth.exchange_authorization_code(code, redirect_uri())
	except auth.LwaError as e:
		return _failed(_("Token exchange failed: {0}").format(e.message))

	refresh_token = token_payload.get("refresh_token")
	if not refresh_token:
		return _failed(_("Amazon did not return a refresh token."))

	connection = store_refresh_token(refresh_token, selling_partner_id)

	# Verify with a role-free preflight. Keep the token even if it fails — the
	# authorization succeeded, and a 403 here is usually a fixable region / beta /
	# role problem. Mark the connection `error` with an actionable message so the
	# operator can fix config and retry with Test connection instead of re-doing
	# the whole OAuth dance.
	try:
		SpApiClient(connection).preflight()
	except SpApiError as e:
		message = describe_forbidden(e, role_free=True) if e.is_forbidden() else e.message
		connection.set_status("error", message)
		# A GET request rolls back in Frappe unless we commit, which would drop the
		# stored token, the error status and the SP-API Log rows. The www callback
		# is exactly that GET; committing here costs the API caller nothing.
		frappe.db.commit()
		return {
			"success": False,
			"message": message,
			"status": "error",
			"selling_partner_id": connection.selling_partner_id,
		}

	connection.set_status("connected", "Connected and verified")
	frappe.db.commit()  # persist across the GET-request rollback (see above)
	return {
		"success": True,
		"message": _("Amazon account connected successfully."),
		"status": "connected",
		"selling_partner_id": connection.selling_partner_id,
	}


def _failed(message):
	"""A refusal that never reached the point of storing anything."""
	return {"success": False, "message": message, "status": None, "selling_partner_id": None}
