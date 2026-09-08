# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""The one crossing between Amazon's clock and Frappe's.

Frappe stores naive datetimes in system time; Amazon speaks ISO-8601 UTC. On a
site whose timezone is not UTC — every one of ours — getting this wrong does not
raise, it silently shifts a whole sync window by hours, so both directions live
here and nothing converts by hand.

`spapi.orders` wrote these first and its comment already promised there would be
only one copy of them. This module is that promise kept once a second caller
(`spapi.inventory`) needed the same conversion.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from frappe.utils import get_datetime, get_system_timezone


def to_amazon_iso(dt):
	"""System-time naive datetime -> '2026-08-03T09:15:00Z'."""
	dt = get_datetime(dt)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_amazon_iso(value):
	"""Amazon ISO-8601 UTC string -> naive system-time datetime (or None).

	Returns None rather than raising on an unparseable value: these stamp
	supplementary fields (when Amazon last touched a row), and a format Amazon
	has changed under us should cost that one field, not the sync carrying it.
	"""
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=UTC)
	return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
