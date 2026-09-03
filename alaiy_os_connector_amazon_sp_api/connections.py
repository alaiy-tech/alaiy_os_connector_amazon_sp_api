# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Which Amazon Connection a call is about.

Amazon Connection used to be a Single: one seller per site, and every caller
could say `frappe.get_cached_doc("Amazon Connection")` and be right. It is now
a normal DocType so one bench can hold many sellers — which self-serve needs,
where every seller shares a site.

Seven benches are already running the single-connection shape, so `resolve()`
answers the old question too. A caller that names no connection gets:

  1. the one flagged `is_default`, if any — which the upgrade patch sets on the
     row it migrates out of tabSingles;
  2. the only connection, when there is exactly one, which is every
     single-seller bench and is why none of them had to change;

and otherwise a refusal. That last case is the important one: on a bench with
several sellers, an unnamed call is a bug, and guessing which seller it meant
would answer with someone else's data. It has to be louder than that.

There is deliberately no third rule falling back to the connection *named*
"default". It looks harmless — that is what the patch calls the migrated row —
but on a multi-tenant bench it would quietly hand an unnamed call the first
seller's data instead of refusing, which is the whole failure this is meant to
prevent. Rules 1 and 2 already cover every upgraded bench between them.
"""

import frappe
from frappe import _

DOCTYPE = "Amazon Connection"

# What the upgrade patch names the connection it migrates from tabSingles, and
# what a fresh single-seller install gets.
DEFAULT_ID = "default"


class NoConnection(frappe.ValidationError):
	"""No connection could be resolved. Distinct from 'not connected yet'."""


def exists(connection=None) -> bool:
	"""True when `resolve` would return a document, without raising."""
	try:
		resolve_name(connection)
		return True
	except Exception:
		return False


def names() -> list[str]:
	"""Every connection on this site, oldest first."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return []
	return frappe.get_all(DOCTYPE, pluck="name", order_by="creation asc")


def resolve_name(connection=None) -> str:
	"""The name of the connection this call is about."""
	if connection:
		# A Document, not an id — callers pass either.
		name = getattr(connection, "name", connection)
		if not frappe.db.exists(DOCTYPE, name):
			frappe.throw(_("No Amazon connection {0}.").format(name), NoConnection)
		return name

	default = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
	if default:
		return default

	all_names = names()
	if len(all_names) == 1:
		return all_names[0]

	if not all_names:
		frappe.throw(
			_("No Amazon connection has been set up on this site."), NoConnection
		)

	# Several, none marked default. Picking one would answer with the wrong
	# seller's data, which is worse than failing.
	frappe.throw(
		_(
			"This site has {0} Amazon connections, so the call has to name one. "
			"Mark one as the default connection, or pass its id."
		).format(len(all_names)),
		NoConnection,
	)


def resolve(connection=None):
	"""The connection document this call is about."""
	if connection is not None and not isinstance(connection, str):
		# Already a document; hand it straight back so a caller that loaded it
		# for writing does not get a cached copy in its place.
		return connection
	return frappe.get_cached_doc(DOCTYPE, resolve_name(connection))


def for_write(connection=None):
	"""Like `resolve`, but never cached — for a caller about to save."""
	return frappe.get_doc(DOCTYPE, resolve_name(connection))


def connected_names() -> list[str]:
	"""
	Connections with a refresh token stored.

	What the scheduled jobs iterate: a site with three sellers should sync all
	three, and one with a half-finished connection should skip it rather than
	fail the whole run.
	"""
	ready = []
	for name in names():
		doc = frappe.get_cached_doc(DOCTYPE, name)
		if doc.is_connected():
			ready.append(name)
	return ready


def create(
	connection_id: str,
	*,
	region: str = "NA",
	label: str = None,
	owner_app: str = None,
	is_default: bool = False,
):
	"""
	Make a connection. Idempotent on `connection_id`.

	Used by the OAuth flow and by any app that manages sellers on this bench;
	`owner_app` records which, so a multi-tenant bench can tell its rows from
	ones created by hand in the desk.

	`is_default` is off unless asked for, and deliberately so. Flagging the
	first connection would look harmless on a single-seller bench and be a
	disclosure on a multi-tenant one: every later call that named no connection
	would quietly answer with the first seller's data instead of refusing. A
	bench with exactly one connection already resolves it without the flag, so
	nothing needs it.
	"""
	if frappe.db.exists(DOCTYPE, connection_id):
		return frappe.get_doc(DOCTYPE, connection_id)

	doc = frappe.new_doc(DOCTYPE)
	doc.connection_id = connection_id
	doc.label = label or connection_id
	doc.region = region
	doc.owner_app = owner_app
	doc.is_default = 1 if is_default else 0
	doc.insert(ignore_permissions=True)
	return doc
