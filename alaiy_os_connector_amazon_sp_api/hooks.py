app_name = "alaiy_os_connector_amazon_sp_api"
app_title = "Alaiy Os Connector Amazon Sp Api"
app_publisher = "Alaiy"
app_description = "AlaiyOS Connector to interact with SP API"
app_email = "mail@alaiy.com"
app_license = "agpl-3.0"

# Installation
# ------------
after_install = "alaiy_os_connector_amazon_sp_api.setup.install.after_install"
after_migrate = "alaiy_os_connector_amazon_sp_api.setup.install.after_migrate"

# AlaiyOS integration
# -------------------
# Child links under this connector's own section in the main OS sidebar.
# The core builds the section itself (labelled from the OS Connector
# Registry row, with an automatic Dashboard link) and threads it in before the
# trailing Settings item — so ordering is the core's job, not ours.
alaiy_os_sidebar_connector_items = [
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "Amazon Connection", "label": "Connection", "icon": "plug"},
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "Amazon Listing", "label": "Listings", "icon": "list"},
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "Account Health Metric", "label": "Account Health", "icon": "heart-pulse"},
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "Seller Feedback", "label": "Seller Feedback", "icon": "star"},
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "Amazon Marketplace", "label": "Marketplaces", "icon": "globe"},
	{"connector_id": "amazon_sp_api", "link_type": "DocType",
	 "link_to": "SP-API Log", "label": "SP-API Logs", "icon": "activity"},
]

# Log links surfaced in the OS Settings sidebar 'Logs' section by alaiy_os_core.
alaiy_os_sidebar_log_items = [
	{"link_type": "DocType", "link_to": "SP-API Log", "label": "SP-API Logs", "icon": "activity"},
]

# Desk client scripts
# -------------------
doctype_js = {
	"Amazon Connection": "public/js/amazon_connection.js",
	"Amazon Listing": "public/js/amazon_listing.js",
}
doctype_list_js = {"Amazon Listing": "public/js/amazon_listing_list.js"}

# Desk styles (breadcrumb title clamp for the Amazon Listing form)
app_include_css = "/assets/alaiy_os_connector_amazon_sp_api/css/amazon_desk.css"

# Website routes (clean, hyphenated OAuth URLs -> www page controllers)
# ---------------------------------------------------------------------
website_route_rules = [
	{"from_route": "/amazon-oauth/start", "to_route": "amazon_oauth_start"},
	{"from_route": "/amazon-oauth/callback", "to_route": "amazon_oauth_callback"},
]

# Scheduled Tasks
# ---------------
scheduler_events = {
	"daily": [
		"alaiy_os_connector_amazon_sp_api.tasks.sync_health",
	],
	"hourly": [
		"alaiy_os_connector_amazon_sp_api.tasks.refresh_connection_status",
	],
	"cron": {
		# Every 6 hours: rebuild listing state (Phase 3).
		"0 */6 * * *": [
			"alaiy_os_connector_amazon_sp_api.tasks.reconcile_listings",
		],
	},
}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "alaiy_os_connector_amazon_sp_api",
# 		"logo": "/assets/alaiy_os_connector_amazon_sp_api/logo.png",
# 		"title": "Alaiy Os Connector Amazon Sp Api",
# 		"route": "/alaiy_os_connector_amazon_sp_api",
# 		"has_permission": "alaiy_os_connector_amazon_sp_api.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/alaiy_os_connector_amazon_sp_api/css/alaiy_os_connector_amazon_sp_api.css"
# app_include_js = "/assets/alaiy_os_connector_amazon_sp_api/js/alaiy_os_connector_amazon_sp_api.js"

# include js, css files in header of web template
# web_include_css = "/assets/alaiy_os_connector_amazon_sp_api/css/alaiy_os_connector_amazon_sp_api.css"
# web_include_js = "/assets/alaiy_os_connector_amazon_sp_api/js/alaiy_os_connector_amazon_sp_api.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "alaiy_os_connector_amazon_sp_api/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "alaiy_os_connector_amazon_sp_api/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "alaiy_os_connector_amazon_sp_api.utils.jinja_methods",
# 	"filters": "alaiy_os_connector_amazon_sp_api.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "alaiy_os_connector_amazon_sp_api.install.before_install"
# after_install = "alaiy_os_connector_amazon_sp_api.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "alaiy_os_connector_amazon_sp_api.uninstall.before_uninstall"
# after_uninstall = "alaiy_os_connector_amazon_sp_api.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "alaiy_os_connector_amazon_sp_api.utils.before_app_install"
# after_app_install = "alaiy_os_connector_amazon_sp_api.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "alaiy_os_connector_amazon_sp_api.utils.before_app_uninstall"
# after_app_uninstall = "alaiy_os_connector_amazon_sp_api.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "alaiy_os_connector_amazon_sp_api.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "alaiy_os_connector_amazon_sp_api.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"alaiy_os_connector_amazon_sp_api.tasks.all"
# 	],
# 	"daily": [
# 		"alaiy_os_connector_amazon_sp_api.tasks.daily"
# 	],
# 	"hourly": [
# 		"alaiy_os_connector_amazon_sp_api.tasks.hourly"
# 	],
# 	"weekly": [
# 		"alaiy_os_connector_amazon_sp_api.tasks.weekly"
# 	],
# 	"monthly": [
# 		"alaiy_os_connector_amazon_sp_api.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "alaiy_os_connector_amazon_sp_api.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "alaiy_os_connector_amazon_sp_api.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "alaiy_os_connector_amazon_sp_api.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "alaiy_os_connector_amazon_sp_api.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["alaiy_os_connector_amazon_sp_api.utils.before_request"]
# after_request = ["alaiy_os_connector_amazon_sp_api.utils.after_request"]

# Job Events
# ----------
# before_job = ["alaiy_os_connector_amazon_sp_api.utils.before_job"]
# after_job = ["alaiy_os_connector_amazon_sp_api.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"alaiy_os_connector_amazon_sp_api.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

