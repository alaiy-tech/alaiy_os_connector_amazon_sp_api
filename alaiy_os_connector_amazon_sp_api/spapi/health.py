# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Account-health: report parsing, finance-event counting, and sync orchestration.

Parsers are deliberately tolerant: the V2 seller-performance report has shipped
as JSON, XML (`<Performance measure=...>`) and TSV over time, so we try each in
turn. Values are normalised to *percent* to match the policy targets in
constants.HEALTH_METRICS. Fixture-based unit tests (spec §12) pin the exact
shapes; the tolerance here keeps a sync from hard-failing on a format shift.
"""

import csv
import io
import json
import xml.etree.ElementTree as ET

import frappe
from frappe.utils import cint, flt, now_datetime

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi import reports
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient, SpApiError
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	HEALTH_METRICS,
	HEALTH_METRICS_BY_KEY,
	HEALTH_STATUS_AT_RISK,
	HEALTH_STATUS_NORMAL,
	HEALTH_STATUS_UNKNOWN,
	REPORT_SELLER_FEEDBACK,
	REPORT_SELLER_PERFORMANCE,
)

# A metric within this fraction of its policy limit is flagged "warn".
WARN_MARGIN = 0.2  # lower-is-better metrics
WARN_MARGIN_HIGH = 0.05  # higher-is-better metrics


def _to_percent(value):
	"""Normalise a rate to percent. Amazon sometimes returns fractions (0.01)
	and sometimes percentages (1.0); treat anything <= 1 as a fraction."""
	v = flt(value)
	if 0 < v <= 1:
		return v * 100.0
	return v


# --- performance report parsing ---------------------------------------------
def parse_performance(text):
	"""Return {metric_key: percent_value} from a performance report in any of
	the known encodings (JSON, XML, TSV)."""
	if not text:
		return {}
	text = text.strip()
	if not text:
		return {}

	if text[0] in "{[":
		parsed = _parse_performance_json(text)
		if parsed:
			return parsed
	if text[0] == "<":
		parsed = _parse_performance_xml(text)
		if parsed:
			return parsed
	return _parse_performance_tsv(text)


def _parse_performance_json(text):
	try:
		data = json.loads(text)
	except ValueError:
		return {}
	found = {}
	_walk_json_for_metrics(data, found)
	return found


def _walk_json_for_metrics(node, found):
	"""Recursively collect any key matching a known metric_key with a numeric
	value (or a nested rate/value field)."""
	if isinstance(node, dict):
		for key, value in node.items():
			if key in HEALTH_METRICS_BY_KEY and key not in found:
				num = _coerce_metric_value(value)
				if num is not None:
					found[key] = _to_percent(num)
			_walk_json_for_metrics(value, found)
	elif isinstance(node, list):
		for item in node:
			_walk_json_for_metrics(item, found)


def _coerce_metric_value(value):
	if isinstance(value, (int, float)):
		return value
	if isinstance(value, dict):
		for sub in ("rate", "value", "percentage", "count"):
			if sub in value and isinstance(value[sub], (int, float)):
				return value[sub]
	return None


def _parse_performance_xml(text):
	"""Parse `<Performance measure="OrderDefectRate" rate="0.005"/>`-style XML."""
	found = {}
	try:
		root = ET.fromstring(text)
	except ET.ParseError:
		return {}

	# Build a case-insensitive lookup from measure name -> metric_key.
	label_to_key = {k.lower(): k for k in HEALTH_METRICS_BY_KEY}
	for el in root.iter():
		tag = el.tag.split("}")[-1]
		if tag.lower() != "performance":
			continue
		measure = (el.get("measure") or el.get("name") or "").strip()
		key = label_to_key.get(measure.lower())
		if not key:
			continue
		raw = el.get("rate") or el.get("value") or (el.text or "").strip()
		try:
			found[key] = _to_percent(float(raw))
		except (TypeError, ValueError):
			continue
	return found


def _parse_performance_tsv(text):
	"""Fallback: a single-row TSV whose headers match metric keys/labels."""
	found = {}
	reader = csv.DictReader(io.StringIO(text), delimiter="\t")
	label_to_key = {k.lower(): k for k in HEALTH_METRICS_BY_KEY}
	label_to_key.update({m["metric_label"].lower(): m["metric_key"] for m in HEALTH_METRICS})
	for row in reader:
		for header, value in row.items():
			if not header:
				continue
			key = label_to_key.get(header.strip().lower())
			if key and key not in found and value not in (None, ""):
				try:
					found[key] = _to_percent(float(str(value).replace("%", "").strip()))
				except ValueError:
					continue
	return found


# --- feedback report parsing -------------------------------------------------
def parse_feedback(text, limit=50):
	"""Parse the seller-feedback TSV; return up to `limit` most-recent rows as
	dicts {order_id, rating, comment, feedback_date}."""
	if not text:
		return []
	reader = csv.DictReader(io.StringIO(text), delimiter="\t")
	# Header names vary by locale; match on normalised substrings.
	rows = []
	for row in reader:
		norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
		order_id = _pick(norm, ("order id", "order-id", "orderid"))
		rating = _pick(norm, ("rating", "feedback rating", "stars"))
		comment = _pick(norm, ("comments", "comment", "feedback"))
		date = _pick(norm, ("date", "feedback date", "submission date"))
		if not order_id and not rating:
			continue
		rows.append(
			{
				"order_id": order_id,
				"rating": cint(rating) if rating else None,
				"comment": comment,
				"feedback_date": date or None,
			}
		)
	# Keep the last `limit`, most recent first if a date is present.
	rows = rows[-limit:]
	return rows


def _pick(norm, candidates):
	for c in candidates:
		if c in norm and norm[c]:
			return norm[c]
	return None


# --- finance events ----------------------------------------------------------
def count_finance_events(client, posted_after, marketplace_id=None):
	"""Count A-to-Z guarantee claims + chargebacks since `posted_after` (ISO).

	Follows NextToken for a bounded number of pages.
	"""
	guarantees = 0
	chargebacks = 0
	params = {"PostedAfter": posted_after}
	if marketplace_id:
		params["MarketplaceId"] = marketplace_id

	path = "/finances/v0/financialEvents"
	for _ in range(10):  # page cap
		resp = client.get(path, params=params, context="health")
		payload = (resp or {}).get("payload", {})
		events = payload.get("FinancialEvents", {})
		guarantees += len(events.get("GuaranteeClaimEventList", []) or [])
		chargebacks += len(events.get("ChargebackEventList", []) or [])
		next_token = payload.get("NextToken")
		if not next_token:
			break
		params = {"NextToken": next_token}
	return {"guarantees": guarantees, "chargebacks": chargebacks}


# --- status computation ------------------------------------------------------
def compute_status(value, target, higher_is_better):
	"""Return 'ok' / 'warn' / 'critical' for a metric against its policy target."""
	if value is None or target is None:
		return "ok"
	value = flt(value)
	target = flt(target)
	if higher_is_better:
		if value < target:
			return "critical"
		if value < target * (1 + WARN_MARGIN_HIGH):
			return "warn"
		return "ok"
	# lower is better
	if value > target:
		return "critical"
	if value > target * (1 - WARN_MARGIN):
		return "warn"
	return "ok"


def rollup_status(statuses):
	"""Worst-metric rollup into the overall account-health state.

	DEACTIVATED is only asserted from an explicit account-status signal (not
	available in v1), so metric breaches surface as AT_RISK.
	"""
	if not statuses:
		return HEALTH_STATUS_UNKNOWN
	if any(s == "critical" for s in statuses):
		return HEALTH_STATUS_AT_RISK
	return HEALTH_STATUS_NORMAL


# --- orchestration -----------------------------------------------------------
def run_health_sync(marketplace=None, connection=None):
	"""Full account-health sync for one marketplace (defaults to primary).

	1. preflight  2. performance + feedback reports  3. finances  4. upsert.
	Returns a summary dict. Raises SpApiError with an actionable message on 403.
	"""
	from alaiy_os_connector_amazon_sp_api.spapi.client import describe_forbidden

	connection = connections.resolve(connection)
	marketplace = marketplace or connection.primary_marketplace
	if not marketplace:
		frappe.throw("No marketplace specified and no primary marketplace set on Amazon Connection.")

	mp = frappe.get_cached_doc("Amazon Marketplace", marketplace)
	client = SpApiClient(connection)

	# 1. preflight (role-free) — distinguishes token/region 403 from role 403.
	try:
		client.preflight()
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=True))
		raise

	# 2. reports
	try:
		perf_text = reports.fetch_report(REPORT_SELLER_PERFORMANCE, [mp.marketplace_id], client=client)
		feedback_text = reports.fetch_report(REPORT_SELLER_FEEDBACK, [mp.marketplace_id], client=client)
	except SpApiError as e:
		if e.is_forbidden():
			frappe.throw(describe_forbidden(e, role_free=False))
		raise

	metrics = parse_performance(perf_text)
	feedback_rows = parse_feedback(feedback_text)

	# 3. finances (last 30 days) — best effort; don't fail the whole sync.
	finances = {"guarantees": 0, "chargebacks": 0}
	posted_after = frappe.utils.add_to_date(now_datetime(), days=-30).isoformat()
	try:
		finances = count_finance_events(client, posted_after, mp.marketplace_id)
	except SpApiError:
		frappe.log_error(title="Amazon finances fetch failed", message=frappe.get_traceback())

	# 4. upsert
	synced_at = now_datetime()
	statuses = _upsert_metrics(mp.name, metrics, finances, synced_at)
	_upsert_feedback(feedback_rows)

	return {
		"marketplace": mp.name,
		"metrics_synced": len(metrics),
		"feedback_synced": len(feedback_rows),
		"overall_status": rollup_status(statuses),
		"synced_at": synced_at,
	}


def _upsert_metrics(marketplace, metrics, finances, synced_at):
	"""Upsert one Account Health Metric row per tracked metric. Returns the list
	of computed statuses for the rollup."""
	statuses = []
	for defn in HEALTH_METRICS:
		key = defn["metric_key"]
		value = metrics.get(key)
		status = compute_status(value, defn["metric_target"], defn["higher_is_better"])
		statuses.append(status)

		existing = frappe.db.get_value(
			"Account Health Metric",
			{"marketplace": marketplace, "metric_key": key},
			"name",
		)
		row = {
			"marketplace": marketplace,
			"metric_key": key,
			"metric_label": defn["metric_label"],
			"metric_value": value,
			"metric_target": defn["metric_target"],
			"higher_is_better": defn["higher_is_better"],
			"section": defn["section"],
			"health_status": status,
			"synced_at": synced_at,
		}
		# A-to-Z / chargeback counts ride on the ODR row.
		if key == "orderDefectRate":
			row["finances_guarantees"] = finances.get("guarantees", 0)
			row["finances_chargebacks"] = finances.get("chargebacks", 0)

		if existing:
			doc = frappe.get_doc("Account Health Metric", existing)
			doc.update(row)
			doc.save(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "Account Health Metric", **row}).insert(
				ignore_permissions=True
			)
	return statuses


def _upsert_feedback(rows):
	for r in rows:
		if not r.get("order_id"):
			continue
		existing = frappe.db.exists("Seller Feedback", {"order_id": r["order_id"]})
		if existing:
			continue  # feedback is immutable; dedup on order_id
		frappe.get_doc(
			{
				"doctype": "Seller Feedback",
				"order_id": r["order_id"],
				"rating": r.get("rating"),
				"comment": r.get("comment"),
				"feedback_date": r.get("feedback_date"),
			}
		).insert(ignore_permissions=True)
