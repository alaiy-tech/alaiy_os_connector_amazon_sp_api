# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Following up the writes Amazon accepted but had not yet applied.

The Listings Items API is asynchronous twice over. A write returns ACCEPTED,
meaning queued; Amazon applies it some time later; and applying it can still
fail, with issues that were not on the accept. Nothing in the write path can see
that second outcome — by the time it happens the request is long returned — so
without this module every accepted submission stays `pending` forever and a
rejected one is indistinguishable from a slow one.

That indistinguishability is the whole problem. `pending` is what a *successful*
in-flight write looks like, so a row sitting at pending is not evidence of
anything, and an operator watching a creation that silently failed has nothing to
notice it by.

There is no endpoint that takes a submissionId and answers with its fate — the
2021-08-01 API does not have one. The item itself is the answer: once Amazon has
applied the write, `getListingsItem` returns it, with its issues and (for a
creation) its new ASIN. So the follow-up is a re-read, and the only judgement
here is how long a re-read may keep coming back empty before the submission is
called lost rather than late.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from alaiy_os_connector_amazon_sp_api.spapi import listings
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiError
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	SUBMISSION_GRACE_MINUTES,
	SUBMISSION_MAX_AGE_HOURS,
	SUBMISSION_RECONCILE_BATCH,
)


def pending_submissions(limit=None):
	"""Rows waiting on an accepted submission, oldest first.

	Only rows this connector actually submitted something for: a `submission_id`
	and a `last_published_at`. A row can be `pending` for other reasons — that is
	also what an offer update writes — and re-reading those is harmless but
	pointless, while a row nothing was submitted for has nothing to wait on.

	The grace period keeps the reconciler off submissions Amazon has plausibly not
	got to yet. Re-reading one every few minutes would answer 404 every time and
	spend rate limit doing it.
	"""
	cutoff = add_to_date(now_datetime(), minutes=-SUBMISSION_GRACE_MINUTES)
	return frappe.get_all(
		"Amazon Product Listing",
		filters={
			"listing_status": "pending",
			"submission_id": ["is", "set"],
			"last_published_at": ["<=", cutoff],
		},
		fields=["name", "marketplace", "last_published_at", "submission_id"],
		order_by="last_published_at asc",
		limit=limit or SUBMISSION_RECONCILE_BATCH,
	)


def _abandon(row_name, submission_id, age_hours):
	"""Record a submission that never landed, and stop waiting on it.

	The row leaves `pending` — that status is a claim that a write is in flight,
	and after this long it is not. `incomplete` is where it goes back to, because
	that is what it was before the submission and what it evidently still is.
	"""
	message = _(
		"Amazon never applied submission {0}. It was accepted {1} hours ago and the SKU is still "
		"not a listing on the account. Check Seller Central for a rejected submission, correct "
		"what it names, and create it again."
	).format(submission_id, age_hours)
	frappe.db.set_value(
		"Amazon Product Listing",
		row_name,
		{
			"listing_status": "incomplete",
			"last_publish_error": message[:1000],
			"submission_id": None,
		},
		update_modified=False,
	)
	return {"sku": row_name, "outcome": "abandoned", "message": message}


def reconcile_submission(row):
	"""Re-read one pending row and settle what its submission did.

	Three outcomes, and the middle one is the reason this runs at all:
	  * Amazon has the listing -> sync it; the row leaves pending with the real
	    status, and a creation picks up its new ASIN here.
	  * Amazon still has nothing, inside the age limit -> leave it alone. Late is
	    not the same as failed and there is nothing to report yet.
	  * Amazon still has nothing, past the age limit -> it is not coming. Say so
	    on the row rather than let it sit at pending indefinitely.
	"""
	age_hours = (now_datetime() - row.last_published_at).total_seconds() / 3600
	try:
		result = listings.sync_listing(row.name, marketplace=row.marketplace, missing_ok=True)
	except SpApiError as e:
		# A transient read failure is not a verdict on the submission; leave the
		# row pending and let the next run ask again.
		return {"sku": row.name, "outcome": "unreadable", "message": str(e)}

	if result is not None:
		# sync_listing has written the real state; the submission is spent.
		frappe.db.set_value(
			"Amazon Product Listing",
			row.name,
			{"submission_id": None, "last_publish_error": None},
			update_modified=False,
		)
		return {
			"sku": row.name,
			"outcome": "applied",
			"listing_status": result.get("listing_status"),
		}

	if age_hours >= SUBMISSION_MAX_AGE_HOURS:
		return _abandon(row.name, row.submission_id, int(age_hours))
	return {"sku": row.name, "outcome": "waiting"}


def reconcile_pending_submissions(limit=None):
	"""Settle every pending submission that is due a re-read.

	One row's failure does not stop the rest, and each row is committed on its own
	— the same stance as a bulk publish, for the same reason: a worker killed
	half-way must leave behind what it had already established.
	"""
	counts = {"applied": 0, "waiting": 0, "abandoned": 0, "unreadable": 0, "failed": 0}
	results = []
	for row in pending_submissions(limit=limit):
		try:
			outcome = reconcile_submission(row)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			counts["failed"] += 1
			results.append({"sku": row.name, "outcome": "failed", "message": str(e)})
			frappe.log_error(
				title=f"Amazon submission reconcile failed for {row.name}"[:140],
				message=frappe.get_traceback(),
			)
			continue
		counts[outcome["outcome"]] += 1
		results.append(outcome)
	return {"success": True, "checked": len(results), **counts, "results": results}
