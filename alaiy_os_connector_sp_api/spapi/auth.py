# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""LWA token exchange and short-lived access-token caching.

The seller's long-lived *refresh token* lives (encrypted) on the Amazon
Connection Single. We exchange it for a short-lived *access token* using the
app's own LWA client id/secret (from site_config), and cache the access token
in Frappe's cache keyed by a hash of the refresh token.
"""

import hashlib
import time

import frappe
import requests

from alaiy_os_connector_sp_api.spapi.constants import (
	ACCESS_TOKEN_EXPIRY_BUFFER,
	LWA_TOKEN_URL,
)


class LwaError(Exception):
	"""Raised when the LWA token endpoint rejects a grant."""

	def __init__(self, message, error_code=None, status_code=None):
		super().__init__(message)
		self.message = message
		self.error_code = error_code
		self.status_code = status_code


def _app_credentials():
	"""Return (client_id, client_secret) from site_config, or throw."""
	from alaiy_os_connector_sp_api import config

	client_id = config.lwa_client_id()
	client_secret = config.lwa_client_secret()
	if not client_id or not client_secret:
		config.assert_ready()
	return client_id, client_secret


def _post_token(payload):
	"""POST to the LWA token endpoint and return the parsed JSON, or raise LwaError."""
	client_id, client_secret = _app_credentials()
	payload = {**payload, "client_id": client_id, "client_secret": client_secret}
	try:
		resp = requests.post(LWA_TOKEN_URL, data=payload, timeout=30)
	except requests.RequestException as e:
		raise LwaError(f"Could not reach the LWA token endpoint: {e}")

	try:
		body = resp.json()
	except ValueError:
		body = {}

	if resp.status_code != 200:
		raise LwaError(
			body.get("error_description") or body.get("error") or "LWA token exchange failed",
			error_code=body.get("error"),
			status_code=resp.status_code,
		)
	return body


def exchange_authorization_code(code, redirect_uri):
	"""Exchange a one-time OAuth `spapi_oauth_code` for a refresh token.

	Returns the full token payload; the caller persists `refresh_token`.
	"""
	return _post_token(
		{
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": redirect_uri,
		}
	)


def _cache_key(refresh_token):
	digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]
	return f"spapi_access_token::{digest}"


def get_access_token(refresh_token):
	"""Return a valid access token for the given refresh token, using cache.

	Cached in Frappe cache with a TTL of (expires_in - buffer). On a miss we
	call LWA with grant_type=refresh_token.
	"""
	if not refresh_token:
		frappe.throw("No Amazon refresh token available. Reconnect the Amazon account.")

	key = _cache_key(refresh_token)
	cached = frappe.cache().get_value(key)
	if cached:
		return cached

	body = _post_token({"grant_type": "refresh_token", "refresh_token": refresh_token})
	access_token = body["access_token"]
	expires_in = int(body.get("expires_in", 3600))
	ttl = max(expires_in - ACCESS_TOKEN_EXPIRY_BUFFER, 60)
	frappe.cache().set_value(key, access_token, expires_in_sec=ttl)
	return access_token


def clear_cached_token(refresh_token):
	"""Drop the cached access token (e.g. after a 403/disconnect)."""
	if refresh_token:
		frappe.cache().delete_value(_cache_key(refresh_token))
