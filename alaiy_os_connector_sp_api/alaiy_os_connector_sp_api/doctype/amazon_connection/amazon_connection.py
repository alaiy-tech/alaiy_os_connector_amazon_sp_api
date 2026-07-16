# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from alaiy_os_connector_sp_api import app_config as config


class AmazonConnection(Document):
	def validate(self):
		# Endpoint shown here is the resolved SP-API target: a site_config
		# override/sandbox wins, else the region default (see app_config).
		self.endpoint = config.resolve_endpoint(self.region)
		if not self.get_password("refresh_token", raise_exception=False):
			# No token yet -> not configured (unless an error was already recorded).
			if self.last_status != "error":
				self.last_status = "not_configured"

	def is_connected(self):
		return bool(self.get_password("refresh_token", raise_exception=False))

	def set_status(self, status, message=None):
		"""Persist connection status + message without a full form save."""
		self.db_set("last_status", status, update_modified=False)
		if message is not None:
			self.db_set("last_status_message", message[:1000], update_modified=False)

	def clear_token(self, message=None):
		"""Wipe the stored refresh token and drop any cached access token."""
		from alaiy_os_connector_sp_api.spapi import auth

		token = self.get_password("refresh_token", raise_exception=False)
		if token:
			auth.clear_cached_token(token)
		self.db_set("refresh_token", "", update_modified=False)
		self.db_set("selling_partner_id", "", update_modified=False)
		self.set_status("not_configured", message or "Disconnected")

	def ping(self):
		"""Verify the connection via the role-free marketplaceParticipations call.

		Updates last_status/last_status_message and returns a summary dict.
		"""
		from alaiy_os_connector_sp_api.spapi.client import SpApiClient, SpApiError, describe_forbidden

		if not self.is_connected():
			self.set_status("not_configured", "No refresh token stored.")
			return {"status": "not_configured"}

		try:
			SpApiClient(self).preflight()
		except SpApiError as e:
			message = describe_forbidden(e, role_free=True) if e.is_forbidden() else e.message
			self.set_status("error", message)
			return {"status": "error", "message": message}

		self.set_status("connected", "OK")
		self.db_set("connected_at", now_datetime(), update_modified=False)
		return {"status": "connected"}
