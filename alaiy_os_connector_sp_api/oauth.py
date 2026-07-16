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

from alaiy_os_connector_sp_api.spapi.constants import REGION_CONSENT_HOSTS

OAUTH_ROLES = ("System Manager", "Amazon Manager")
STATE_TTL = 600  # seconds


def require_oauth_role():
	"""Only an Amazon Manager / System Manager may drive the OAuth flow."""
	roles = set(frappe.get_roles())
	if not roles.intersection(OAUTH_ROLES):
		raise frappe.PermissionError(_("You are not permitted to connect an Amazon account."))


def redirect_uri():
	app_url = frappe.conf.get("app_url") or frappe.utils.get_url()
	return f"{app_url.rstrip('/')}/amazon-oauth/callback"


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
	sp_app_id = frappe.conf.get("amazon_sp_app_id")
	if not sp_app_id:
		frappe.throw(_("amazon_sp_app_id is not configured in site_config.json."))

	connection = frappe.get_cached_doc("Amazon Connection")
	base = frappe.conf.get("amazon_consent_base_url") or REGION_CONSENT_HOSTS.get(
		connection.region or "NA"
	)

	params = {
		"application_id": sp_app_id,
		"state": state,
		"redirect_uri": redirect_uri(),
	}
	# Draft apps must request the beta consent.
	beta = frappe.conf.get("amazon_app_beta")
	if beta or connection.app_status == "Draft":
		params["version"] = "beta"

	return f"{base.rstrip('/')}/apps/authorize/consent?{urlencode(params)}"


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
