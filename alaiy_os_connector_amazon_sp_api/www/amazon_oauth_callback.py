# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""GET /amazon-oauth/callback -> exchange code for refresh token, verify, persist.

Only the rendering is here. The flow itself is `oauth.complete_authorization`,
shared with `api.complete_oauth` — which is the same landing page inside the OS
frontend, for the deployments where the frontend owns the site's hostname and
this www page is not reachable from a browser at all.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import oauth

no_cache = 1


def get_context(context):
	oauth.require_oauth_role()
	args = frappe.form_dict

	result = oauth.complete_authorization(
		args.get("spapi_oauth_code"),
		args.get("state"),
		selling_partner_id=args.get("selling_partner_id"),
		error=args.get("error"),
		error_description=args.get("error_description"),
	)

	context.success = result["success"]
	context.message = result["message"]
	context.selling_partner_id = result["selling_partner_id"]
	return context
