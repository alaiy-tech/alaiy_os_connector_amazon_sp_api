# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""GET /amazon-oauth/callback -> exchange code for refresh token, verify, persist."""

import frappe
from frappe import _

from alaiy_os_connector_sp_api import oauth
from alaiy_os_connector_sp_api.spapi import auth
from alaiy_os_connector_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden

no_cache = 1


def get_context(context):
	oauth.require_oauth_role()
	args = frappe.form_dict

	# Amazon can redirect back with an error instead of a code.
	if args.get("error"):
		context.success = False
		context.message = args.get("error_description") or args.get("error")
		return context

	oauth.validate_state(args.get("state"))

	code = args.get("spapi_oauth_code")
	selling_partner_id = args.get("selling_partner_id")
	if not code:
		context.success = False
		context.message = _("No authorization code returned by Amazon.")
		return context

	# Exchange the one-time code for a refresh token.
	try:
		token_payload = auth.exchange_authorization_code(code, oauth.redirect_uri())
	except auth.LwaError as e:
		context.success = False
		context.message = _("Token exchange failed: {0}").format(e.message)
		return context

	refresh_token = token_payload.get("refresh_token")
	if not refresh_token:
		context.success = False
		context.message = _("Amazon did not return a refresh token.")
		return context

	connection = oauth.store_refresh_token(refresh_token, selling_partner_id)

	# Verify with a role-free preflight. Keep the token even if it fails — the
	# authorization succeeded, and a 403 here is usually a fixable region /
	# beta / role problem. We mark the connection `error` with an actionable
	# message so the operator can fix config and retry with "Test Connection"
	# instead of re-doing the whole OAuth dance.
	try:
		SpApiClient(connection).preflight()
	except SpApiError as e:
		message = describe_forbidden(e, role_free=True) if e.is_forbidden() else e.message
		connection.set_status("error", message)
		context.success = False
		context.message = message
		return context

	connection.set_status("connected", "Connected and verified")
	context.success = True
	context.message = _("Amazon account connected successfully.")
	context.selling_partner_id = selling_partner_id
	return context
