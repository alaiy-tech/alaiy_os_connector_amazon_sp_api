# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Registration metadata for the AlaiyOS OS Connector Registry.

alaiy_os_core reads these fields to render the connector in the OS Settings
'Connectors' panel and to drive its Test-connection button. `settings_doctype`
points at the connector's single settings DocType (here, Amazon Connection).
"""

connector_meta = {
	"connector_id": "amazon_sp_api",
	"connector_name": "Amazon (SP-API)",
	"connector_app": "alaiy_os_connector_amazon_sp_api",
	"connector_type": "channel",
	"description": "Amazon Selling Partner API connector: listings, orders, account health, and listing state.",
	"icon": "shopping-cart",
	"settings_doctype": "Amazon Connection",
	"test_method": "alaiy_os_connector_amazon_sp_api.api.test_connection",
	# The registry's sync slots are generic — each is named by its *_label, the
	# same way the Shopify connector hangs its order pull off `categories`.
	"sync_categories_method": "alaiy_os_connector_amazon_sp_api.api.sync_orders",
	"sync_categories_label": "Orders",
	"sync_items_method": None,
	"sync_status_method": "alaiy_os_connector_amazon_sp_api.api.get_orders_sync_status",
	"is_enabled": 1,
	"connection_status": "untested",
}
