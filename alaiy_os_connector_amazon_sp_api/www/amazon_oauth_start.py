# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""GET /amazon-oauth/start -> redirect to Amazon's consent screen.

Takes `?connection=<id>`, because the seller this authorizes cannot be worked
out here. `connections.resolve()` refuses to guess on a bench with several of
them, and rightly so — guessing would store one seller's refresh token against
another seller's connection. The Desk form and the OS's Settings screen both
know which row their operator is looking at, so they say.
"""

import frappe

from alaiy_os_connector_amazon_sp_api import oauth

no_cache = 1


def get_context(context):
	oauth.require_oauth_role()

	connection = frappe.form_dict.get("connection")

	try:
		state = oauth.issue_state(connection)
		target = oauth.consent_url(state, connection)
	except frappe.ValidationError as exc:
		# Rendered, not re-raised. Everything that lands here is a thing the
		# operator can fix — no connection on the site yet, several with none
		# named, an app credential still missing from site_config — and each of
		# them says so in the message it throws. Letting it out of this function
		# turns all three into frappe's generic website error page ("Server
		# Error / There was an error building this page", HTTP 417), which
		# discards the one sentence that was worth reading.
		#
		# `frappe.throw` records the message on its way out, so drop it: the
		# page below is showing it, and leaving it queued would repeat it.
		message = str(exc)
		frappe.clear_messages()
		context.error = message
		context.connection = connection
		return context

	frappe.local.flags.redirect_location = target
	raise frappe.Redirect
