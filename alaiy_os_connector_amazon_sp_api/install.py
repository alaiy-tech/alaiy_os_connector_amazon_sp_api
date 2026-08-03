# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Base data provisioning: app roles and the marketplace list.

Idempotent, so it is safe to run on both after_install and after_migrate.
"""

import frappe

from alaiy_os_connector_amazon_sp_api.spapi.constants import DEFAULT_MARKETPLACES

APP_ROLES = ("Amazon Manager", "Amazon Viewer")


def ensure_base_data():
	"""Create app roles and seed marketplaces if missing."""
	_create_roles()
	_seed_marketplaces()
	_ensure_order_custom_fields()
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


def _ensure_order_custom_fields():
	"""Amazon provenance fields on Sales Order / Sales Order Item.

	`amazon_order_id` is the sync's idempotency key, so it is indexed and
	unique — a duplicate here would mean two Sales Orders for one Amazon order,
	which is the exact failure the whole poll design is built to avoid. The
	status fields are allow_on_submit because these orders are submitted
	almost immediately and Amazon keeps updating their status afterwards.

	No-op when ERPNext isn't installed: the connector's listing half works
	perfectly well on a site without it.
	"""
	if not frappe.db.exists("DocType", "Sales Order"):
		return

	custom_fields = {
		"Sales Order": [
			{
				"fieldname": "amazon_section",
				"label": "Amazon",
				"fieldtype": "Section Break",
				"insert_after": "po_date",
				"collapsible": 1,
			},
			{
				"fieldname": "amazon_order_id",
				"label": "Amazon Order ID",
				"fieldtype": "Data",
				"read_only": 1,
				"unique": 1,
				"search_index": 1,
				"in_standard_filter": 1,
				"insert_after": "amazon_section",
				"description": "Set by the Seller Central order sync. The key it upserts on.",
			},
			{
				"fieldname": "amazon_order_status",
				"label": "Amazon Order Status",
				"fieldtype": "Data",
				"read_only": 1,
				"allow_on_submit": 1,
				"insert_after": "amazon_order_id",
			},
			{
				"fieldname": "amazon_fulfillment_channel",
				"label": "Amazon Fulfillment Channel",
				"fieldtype": "Data",
				"read_only": 1,
				"allow_on_submit": 1,
				"insert_after": "amazon_order_status",
				"description": "AFN = fulfilled by Amazon, MFN = fulfilled by the seller.",
			},
			{
				"fieldname": "column_break_amazon",
				"fieldtype": "Column Break",
				"insert_after": "amazon_fulfillment_channel",
			},
			{
				"fieldname": "amazon_marketplace",
				"label": "Amazon Marketplace",
				"fieldtype": "Link",
				"options": "Amazon Marketplace",
				"read_only": 1,
				"insert_after": "column_break_amazon",
			},
			{
				"fieldname": "amazon_order_total",
				"label": "Amazon Order Total",
				"fieldtype": "Currency",
				"options": "currency",
				"read_only": 1,
				"allow_on_submit": 1,
				"insert_after": "amazon_marketplace",
				"description": "Amazon's own total for this order. Shipping and fees are not yet mapped, so this can legitimately differ from the grand total — it is here to make that gap visible.",
			},
			{
				"fieldname": "amazon_last_updated_at",
				"label": "Amazon Last Updated At",
				"fieldtype": "Datetime",
				"read_only": 1,
				"allow_on_submit": 1,
				"insert_after": "amazon_order_total",
			},
		],
		"Sales Order Item": [
			{
				"fieldname": "amazon_order_item_id",
				"label": "Amazon Order Item ID",
				"fieldtype": "Data",
				"read_only": 1,
				"search_index": 1,
				"insert_after": "item_code",
			},
			{
				"fieldname": "amazon_seller_sku",
				"label": "Amazon Seller SKU",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "amazon_order_item_id",
			},
		],
	}
	for fields in custom_fields.values():
		for field in fields:
			field.setdefault("module", "Alaiy Os Connector Amazon Sp Api")

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(custom_fields, update=True)


def _seed_marketplaces():
	for marketplace_id, country, code, region, currency, domain, language in DEFAULT_MARKETPLACES:
		if frappe.db.exists("Amazon Marketplace", marketplace_id):
			# Backfill the default language on rows seeded before the field existed.
			if not frappe.db.get_value("Amazon Marketplace", marketplace_id, "language"):
				frappe.db.set_value("Amazon Marketplace", marketplace_id, "language", language)
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
				"language": language,
			}
		).insert(ignore_permissions=True)
