# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Single source of truth for app-identity configuration.

All values live in site_config.json (never in DocTypes, never in the browser).
Set them with, e.g.:

    bench --site <site> set-config amazon_lwa_client_id "amzn1.application-oa2-client...."
    bench --site <site> set-config amazon_lwa_client_secret "amzn1.oa2-cs...."
    bench --site <site> set-config amazon_sp_app_id "amzn1.sp.solution...."

Secret values are read here and never returned to the client; use
`get_config_status()` to show only whether each key is set.
"""

import frappe
from frappe import _

from alaiy_os_connector_sp_api.spapi.constants import (
	REGION_CONSENT_HOSTS,
	REGION_ENDPOINTS,
	SANDBOX_ENDPOINTS,
)

# Config key metadata. `required` gates the OAuth flow; `secret` keeps a value
# from ever being echoed back to the client.
CONFIG_KEYS = [
	{
		"key": "amazon_lwa_client_id",
		"label": "LWA Client ID",
		"required": True,
		"secret": False,
		"description": "Login with Amazon application client id.",
	},
	{
		"key": "amazon_lwa_client_secret",
		"label": "LWA Client Secret",
		"required": True,
		"secret": True,
		"description": "Login with Amazon application client secret.",
	},
	{
		"key": "amazon_sp_app_id",
		"label": "SP-API Application ID",
		"required": True,
		"secret": False,
		"description": "SP-API application id used on the consent screen.",
	},
	{
		"key": "app_url",
		"label": "App URL",
		"required": False,
		"secret": False,
		"description": "Base URL for building the OAuth redirect URI. Falls back to the site URL.",
	},
	{
		"key": "amazon_consent_base_url",
		"label": "Consent Base URL",
		"required": False,
		"secret": False,
		"description": "Seller Central consent host. Falls back to the region default.",
	},
	{
		"key": "amazon_app_beta",
		"label": "Draft App (beta consent)",
		"required": False,
		"secret": False,
		"description": "Set to 1 while the SP-API app is in Draft to request version=beta.",
	},
	{
		"key": "amazon_region",
		"label": "SP-API Region",
		"required": False,
		"secret": False,
		"description": "NA / EU / FE. Overrides the region on the Amazon Connection when set.",
	},
	{
		"key": "amazon_endpoint",
		"label": "SP-API Endpoint Override",
		"required": False,
		"secret": False,
		"description": "Full API base URL. Overrides the region/sandbox default (e.g. for testing).",
	},
	{
		"key": "amazon_use_sandbox",
		"label": "Use Sandbox",
		"required": False,
		"secret": False,
		"description": "Set to 1 to call the SP-API sandbox host for the region.",
	},
]

REQUIRED_KEYS = [c["key"] for c in CONFIG_KEYS if c["required"]]


def get(key, default=None):
	return frappe.conf.get(key, default)


def lwa_client_id():
	return get("amazon_lwa_client_id")


def lwa_client_secret():
	return get("amazon_lwa_client_secret")


def sp_app_id():
	return get("amazon_sp_app_id")


def app_url():
	return (get("app_url") or frappe.utils.get_url()).rstrip("/")


def app_beta():
	return bool(get("amazon_app_beta"))


# --- SP-API target resolution ------------------------------------------------
# Region and endpoint are configurable via site_config and take precedence over
# the Amazon Connection's own region field, so the whole SP-API target can be
# driven from config (region default, sandbox, or a full endpoint override).
def region_override():
	return get("amazon_region")


def endpoint_override():
	return get("amazon_endpoint")


def use_sandbox():
	return bool(get("amazon_use_sandbox"))


def resolve_region(connection_region=None):
	"""site_config amazon_region wins, else the connection's region, else NA."""
	return region_override() or connection_region or "NA"


def resolve_endpoint(connection_region=None):
	"""The API base URL actually called: explicit override > sandbox > region default."""
	override = endpoint_override()
	if override:
		return override.rstrip("/")
	region = resolve_region(connection_region)
	table = SANDBOX_ENDPOINTS if use_sandbox() else REGION_ENDPOINTS
	endpoint = table.get(region)
	return endpoint.rstrip("/") if endpoint else None


def consent_base_url(connection_region=None):
	region = resolve_region(connection_region)
	base = get("amazon_consent_base_url") or REGION_CONSENT_HOSTS.get(region)
	return base.rstrip("/") if base else None


def missing_required():
	"""Return the required keys that are not set."""
	return [k for k in REQUIRED_KEYS if not get(k)]


def is_ready():
	return not missing_required()


def assert_ready():
	"""Throw an actionable error if any required key is missing."""
	missing = missing_required()
	if missing:
		labels = ", ".join(c["label"] for c in CONFIG_KEYS if c["key"] in missing)
		frappe.throw(
			_(
				"Amazon app credentials are not configured in site_config.json. Missing: {0}. "
				"Set them with `bench set-config <key> <value>`."
			).format(labels)
		)


def get_config_status():
	"""Report which keys are set, without exposing secret values.

	Non-secret keys include their value; secret keys report only is_set.
	"""
	status = []
	for c in CONFIG_KEYS:
		value = get(c["key"])
		entry = {
			"key": c["key"],
			"label": c["label"],
			"required": c["required"],
			"secret": c["secret"],
			"is_set": bool(value),
			"description": c["description"],
		}
		if not c["secret"]:
			entry["value"] = value
		status.append(entry)
	return {"ready": is_ready(), "keys": status}
