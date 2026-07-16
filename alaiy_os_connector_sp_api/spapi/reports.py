# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Reports API 2021-06-30: request -> poll -> download document.

Amazon delivers bulk data (performance, feedback, merchant listings) as
reports rather than direct queries. This module drives the full lifecycle and
returns decoded text; parsing is left to health.py / listings.py.
"""

import gzip
import io
import time

import frappe
import requests

from alaiy_os_connector_sp_api.spapi.client import SpApiClient, SpApiError
from alaiy_os_connector_sp_api.spapi.constants import (
	REPORT_POLL_INTERVAL,
	REPORT_POLL_TIMEOUT,
)

REPORTS_BASE = "/reports/2021-06-30"


class ReportCancelled(Exception):
	"""Report finished as CANCELLED — treated as 'no data', not an error."""


def create_report(client, report_type, marketplace_ids, *, data_start=None, data_end=None):
	"""Request a report; returns the reportId."""
	body = {"reportType": report_type, "marketplaceIds": marketplace_ids}
	if data_start:
		body["dataStartTime"] = data_start
	if data_end:
		body["dataEndTime"] = data_end
	resp = client.post(f"{REPORTS_BASE}/reports", body=body, context="health")
	return resp.get("reportId")


def poll_report(client, report_id):
	"""Poll until the report leaves the processing states.

	Returns the reportDocumentId on DONE. Raises ReportCancelled on CANCELLED
	and SpApiError on FATAL or timeout.
	"""
	deadline = time.monotonic() + REPORT_POLL_TIMEOUT
	while True:
		resp = client.get(f"{REPORTS_BASE}/reports/{report_id}", context="health")
		status = resp.get("processingStatus")
		if status == "DONE":
			return resp.get("reportDocumentId")
		if status == "CANCELLED":
			raise ReportCancelled(report_id)
		if status == "FATAL":
			raise SpApiError(
				f"Report {report_id} ended FATAL", path=f"{REPORTS_BASE}/reports/{report_id}"
			)
		if time.monotonic() >= deadline:
			raise SpApiError(
				f"Report {report_id} did not finish within {REPORT_POLL_TIMEOUT}s "
				f"(last status: {status})",
				path=f"{REPORTS_BASE}/reports/{report_id}",
			)
		time.sleep(REPORT_POLL_INTERVAL)


def download_document(client, document_id):
	"""Fetch a report document and return its decoded text.

	The document URL is a pre-signed S3 link (no auth header); content may be
	GZIP-compressed per `compressionAlgorithm`.
	"""
	meta = client.get(f"{REPORTS_BASE}/documents/{document_id}", context="health")
	url = meta.get("url")
	if not url:
		raise SpApiError("Report document had no download URL", path=f"documents/{document_id}")

	resp = requests.get(url, timeout=120)
	resp.raise_for_status()
	content = resp.content

	if meta.get("compressionAlgorithm") == "GZIP":
		with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
			content = gz.read()

	# Amazon reports are usually UTF-8 or Latin-1 (feedback can contain accents).
	for encoding in ("utf-8", "latin-1"):
		try:
			return content.decode(encoding)
		except UnicodeDecodeError:
			continue
	return content.decode("utf-8", errors="replace")


def fetch_report(report_type, marketplace_ids, *, data_start=None, data_end=None, client=None):
	"""Convenience: run the full request->poll->download and return text.

	Returns None when the report is CANCELLED (no data). Raises on FATAL/timeout.
	"""
	client = client or SpApiClient()
	report_id = create_report(
		client, report_type, marketplace_ids, data_start=data_start, data_end=data_end
	)
	try:
		document_id = poll_report(client, report_id)
	except ReportCancelled:
		frappe.logger("amazon_seller").info(f"Report {report_type} cancelled (no data)")
		return None
	return download_document(client, document_id)
