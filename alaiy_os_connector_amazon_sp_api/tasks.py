# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Scheduled jobs. All no-op cleanly when the connection is not configured."""

import frappe

from alaiy_os_connector_amazon_sp_api.spapi import health


def _connection_ready():
	"""True only when a refresh token is stored."""
	if not frappe.db.exists("DocType", "Amazon Connection"):
		return False
	conn = frappe.get_cached_doc("Amazon Connection")
	return conn.is_connected()


def sync_health():
	"""Daily: refresh account-health metrics + feedback for the primary marketplace."""
	if not _connection_ready():
		return
	try:
		health.run_health_sync()
	except Exception:
		frappe.log_error(title="Amazon scheduled health sync failed", message=frappe.get_traceback())
		_alert_managers("Amazon account-health sync failed")


def reconcile_listings():
	"""Every 6h: rebuild active/inactive/suppressed state. Implemented in Phase 3."""
	if not _connection_ready():
		return
	# Phase 3 (listing reconcile) — intentionally not implemented in this build.
	return


def refresh_connection_status():
	"""Hourly: ping preflight and update last_status."""
	if not _connection_ready():
		return
	try:
		frappe.get_doc("Amazon Connection").ping()
	except Exception:
		frappe.log_error(
			title="Amazon connection status refresh failed", message=frappe.get_traceback()
		)


def _alert_managers(subject):
	"""Best-effort email alert to Amazon Managers on a scheduled failure."""
	try:
		recipients = _manager_emails()
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=f"{subject}. See the Error Log and SP-API Log for details.",
			)
	except Exception:
		frappe.log_error(title="Amazon manager alert failed", message=frappe.get_traceback())


def _manager_emails():
	users = frappe.get_all(
		"Has Role",
		filters={"role": "Amazon Manager", "parenttype": "User"},
		pluck="parent",
	)
	emails = []
	for user in users:
		if user in ("Administrator", "Guest"):
			continue
		email = frappe.db.get_value("User", user, "email")
		if email:
			emails.append(email)
	return emails
