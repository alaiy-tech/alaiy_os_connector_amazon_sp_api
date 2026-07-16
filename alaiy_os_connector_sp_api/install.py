# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Base data provisioning: app roles and the marketplace list.

Idempotent, so it is safe to run on both after_install and after_migrate.
"""

import frappe

from alaiy_os_connector_sp_api.spapi.constants import DEFAULT_MARKETPLACES

APP_ROLES = ("Amazon Manager", "Amazon Viewer")


def ensure_base_data():
	"""Create app roles and seed marketplaces if missing."""
	_create_roles()
	_seed_marketplaces()
	frappe.db.commit()


def _create_roles():
	for role_name in APP_ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": 1,
				}
			).insert(ignore_permissions=True)


def _seed_marketplaces():
	for marketplace_id, country, code, region, currency, domain in DEFAULT_MARKETPLACES:
		if frappe.db.exists("Amazon Marketplace", marketplace_id):
			continue
		frappe.get_doc(
			{
				"doctype": "Amazon Marketplace",
				"marketplace_id": marketplace_id,
				"country": country,
				"country_code": code,
				"region": region,
				# Only link the currency if that Currency record exists on the site.
				"currency": currency if frappe.db.exists("Currency", currency) else None,
				"domain": domain,
			}
		).insert(ignore_permissions=True)
