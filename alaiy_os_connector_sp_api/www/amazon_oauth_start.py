# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""GET /amazon-oauth/start -> redirect to Amazon's consent screen."""

import frappe

from alaiy_os_connector_sp_api import oauth

no_cache = 1


def get_context(context):
	oauth.require_oauth_role()
	state = oauth.issue_state()
	target = oauth.consent_url(state)

	frappe.local.flags.redirect_location = target
	raise frappe.Redirect
