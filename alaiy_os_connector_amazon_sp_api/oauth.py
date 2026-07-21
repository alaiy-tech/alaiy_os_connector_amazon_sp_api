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


def validate_state(received):
	expected = frappe.cache().get_value(_state_cache_key())
	frappe.cache().delete_value(_state_cache_key())  # single-use
	if not expected or not received or received != expected:
		frappe.throw(_("OAuth state mismatch. Please restart the connection."), frappe.PermissionError)


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
