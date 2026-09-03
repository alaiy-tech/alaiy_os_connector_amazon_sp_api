# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Scheduled jobs. All no-op cleanly when the connection is not configured."""

import frappe

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi import health, orders, reconcile, submissions


def _connection_ready():
	"""True when at least one seller on this site has a refresh token stored."""
	if not frappe.db.exists("DocType", "Amazon Connection"):
		return False
	return bool(connections.connected_names())



def _for_each_connection(label, run):
	"""
	Run a scheduled job once per connected seller.

	A single-seller bench iterates a list of one, so it behaves exactly as it
	did when Amazon Connection was a Single. On a bench with several, one
	seller's failure is logged against that seller and the rest still run —
	the alternative is one broken connection silently stopping everyone's sync.
	"""
	for name in connections.connected_names():
		try:
			run(name)
		except Exception:
			frappe.log_error(
				title=f"Amazon scheduled {label} failed ({name})",
				message=frappe.get_traceback(),
			)
			_alert_managers(f"Amazon {label} failed for {name}")


def sync_health():
	"""Daily: refresh account-health metrics + feedback for the primary marketplace."""
	_for_each_connection("health sync", lambda name: health.run_health_sync(connection=name))


def reconcile_listings():
	"""Every 6h: reconcile the full catalog's status/price/quantity from the
	Merchant Listings report (no 1000-SKU cap; see spapi.reconcile)."""
	_for_each_connection(
		"listing reconciliation",
		lambda name: reconcile.reconcile_all_listings(connection=name),
	)


def sync_orders():
	"""Every 10m: pull orders updated since the watermark into Sales Orders.

	No-ops when no default customer is configured — an unconfigured site would
	otherwise email its managers every ten minutes about a feature it isn't
	using.
	"""
	for name in connections.connected_names():
		# A seller with no default customer has not finished configuring order
		# import; skipping is why an unconfigured site does not email its
		# managers every ten minutes about a feature it is not using.
		if not frappe.db.get_value("Amazon Connection", name, "orders_customer"):
			continue
		try:
			orders.sync_orders(connection=name)
		except Exception:
			# Per connection, so one seller's failure does not stop the rest.
			frappe.log_error(
				title=f"Amazon scheduled order sync failed ({name})",
				message=frappe.get_traceback(),
			)
			_alert_managers(f"Amazon order sync failed for {name}")


def reconcile_submissions():
	"""Every 15m: settle writes Amazon accepted but had not yet applied.

	Cheap when idle — the query returns nothing unless something is actually in
	flight — and the cadence is set by how long a creation may sit unexplained,
	not by API cost. See spapi.submissions for why a re-read is the only way to
	learn a submission's fate.
	"""
	if not _connection_ready():
		return
	try:
		submissions.reconcile_pending_submissions()
	except Exception:
		frappe.log_error(
			title="Amazon submission reconciliation failed", message=frappe.get_traceback()
		)
		_alert_managers("Amazon submission reconciliation failed")


def refresh_connection_status():
	"""Hourly: ping preflight and update last_status, for every seller."""
	for name in connections.connected_names():
		try:
			connections.for_write(name).ping()
		except Exception:
			frappe.log_error(
				title=f"Amazon connection status refresh failed ({name})",
				message=frappe.get_traceback(),
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
