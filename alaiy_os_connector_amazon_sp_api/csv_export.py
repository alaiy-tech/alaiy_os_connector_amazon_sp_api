# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""CSV export for callers holding rows rather than a question.

Every other read here answers in JSON, and a caller turns that into prose. When
someone wants the rows themselves — a spreadsheet of every suppressed SKU, a
price list to send on, a register page to work through offline — prose is the
wrong shape, and pasting the commas into the reply is worse. This takes JSON that
has already been read and writes a real CSV.

The file is saved as a private Frappe File and its `file_url` comes back in the
result, which is the whole of the delivery: an agent pack runs through
alaiy_os's `engine/executor.py`, which has no channel to push a file down.
(alaiy_os's own `chat/artifacts.py` does have one — a download chip drained by
the chat runner — so if this is ever called from inside a chat turn, that is the
mechanism to switch to, not a second copy of it here.)

Ported from alaiy_os_connector_nayaglobal's `agent/tools/csv_export.py`, whose
row-shape heuristic this keeps; the envelope threshold below is the one number
that had to change, because this app's envelopes carry more metadata than that
one's. The two are separate copies on purpose — neither app depends on the other,
and a shared helper would need a third app to live in.

This writes no Amazon state and reads nothing from Amazon. It is the only write
in the app's agent pack, and it writes a File.
"""

import csv
import io
import json
import re

import frappe
from frappe.utils.file_manager import save_file

# A caller retypes every row into the call, so a big export costs a big
# completion. These caps are about the context window and the file store, not
# about what a spreadsheet can hold.
MAX_ROWS = 2000
MAX_PAYLOAD_CHARS = 400_000
MAX_CELL_CHARS = 2000

# Excel and Sheets read a leading =, +, - or @ as the start of a formula, so a
# *text* cell beginning with one is written with a guarding apostrophe. Numbers
# are written as numbers and never reach here, so this cannot mangle -5.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

# How many non-list keys a dict may carry and still count as a wrapper around its
# payload list rather than a record in its own right.
#
# Five, because that is what this app's widest envelope has: `variation_family`
# returns parent_asin, parent_sku, parent_title, variation_theme and child_count
# around its `children`, and `list_listings` returns four (total, page_no,
# page_size, has_more) around its `listings`. Anything lower would export the
# summary line instead of the rows it summarises. Nothing this app returns is a
# five-scalar-field *record* that also carries a nested list of dicts — the one
# result with dicts inside it, `compare_listing`, nests them under keys that are
# dicts and not lists, so it is still read as the single row it is.
_ENVELOPE_METADATA_KEYS = 5


def _rows_from(data):
	"""A list of row dicts out of whatever shape the caller passed.

	Either a bare array of objects, or a result that wraps one — the
	`{"total":..., "listings":[...]}` of list_listings.

	A dict is only unwrapped when it looks like an envelope: a payload list plus a
	few metadata keys. A record with many fields of its own is one row, nested
	lists and all — otherwise "export this listing" would silently export nothing
	but its issue rows, which is not what was asked for.
	"""
	if isinstance(data, dict):
		rows = [v for v in data.values() if isinstance(v, list) and v]
		of_dicts = [r for r in rows if all(isinstance(item, dict) for item in r)]
		payload = (of_dicts or rows or [None])[0]
		others = sum(1 for v in data.values() if not isinstance(v, list))

		data = payload if payload and others <= _ENVELOPE_METADATA_KEYS else [data]

	if not isinstance(data, list):
		return []

	# A list of scalars is still exportable — one unnamed column beats an error.
	return [row if isinstance(row, dict) else {"value": row} for row in data]


def _header(rows, requested):
	"""Column order: what was asked for, else every key in first-seen order."""
	if requested:
		return [name.strip() for name in requested.split(",") if name.strip()]

	seen = {}
	for row in rows:
		for key in row:
			seen.setdefault(key, None)

	return list(seen)


def _cell(value):
	"""One JSON value as a CSV cell.

	Nested values become compact JSON rather than python reprs, so a listing's
	`bullet_points` stays readable in a spreadsheet.
	"""
	if value is None:
		return ""

	if isinstance(value, bool):
		return "true" if value else "false"

	if isinstance(value, (int, float)):
		return value

	if isinstance(value, (dict, list)):
		text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
	else:
		text = str(value)

	if len(text) > MAX_CELL_CHARS:
		text = text[:MAX_CELL_CHARS].rstrip() + "…"

	if text.startswith(_FORMULA_PREFIXES):
		return "'" + text

	return text


def _file_name(filename):
	"""A safe, unique `<name>.csv`.

	The hash keeps two exports of the same question from overwriting each other in
	the file store.
	"""
	stem = re.sub(r"[^A-Za-z0-9._-]+", "-", filename or "").strip("-.")
	stem = re.sub(r"\.csv$", "", stem, flags=re.IGNORECASE)[:60] or "export"

	return f"{stem}-{frappe.generate_hash(length=6)}.csv"


def _build(rows, header):
	csv_file = io.StringIO()
	writer = csv.DictWriter(
		csv_file, fieldnames=header, extrasaction="ignore", lineterminator="\r\n"
	)
	writer.writeheader()
	for row in rows:
		writer.writerow({name: _cell(row.get(name)) for name in header})

	# utf-8-sig: listing titles are full of non-ASCII, and Excel reads a BOM-less
	# UTF-8 CSV as the local codepage — mojibake in every cell that has one.
	return csv_file.getvalue().encode("utf-8-sig")


def export_csv(rows_json: str, filename: str = "export", columns: str = ""):
	"""Write rows of JSON to a CSV file the user can open in a spreadsheet.

	`rows_json` is a JSON string of the rows to export, `filename` names the file
	and `columns` optionally fixes the column order. Returns {saved, file_name,
	file_url, row_count, columns}, or {"saved": false, "error": ...} if the rows
	could not be read.

	An unreadable payload comes back as a result rather than a throw, deliberately
	and unlike the rest of this app: every one of these errors is fixable by the
	caller on its next attempt — different columns, fewer rows, valid JSON — and
	saying which is more use than a PermissionError-shaped failure. The
	model-facing description of all three arguments lives in pack_meta.py.
	"""
	if not rows_json or not rows_json.strip():
		return {"saved": False, "error": "rows_json was empty — pass the rows to export."}

	if len(rows_json) > MAX_PAYLOAD_CHARS:
		return {
			"saved": False,
			"error": (
				f"rows_json is {len(rows_json)} characters, over the "
				f"{MAX_PAYLOAD_CHARS} limit. Export fewer columns or fewer rows."
			),
		}

	try:
		data = json.loads(rows_json)
	except ValueError as error:
		return {"saved": False, "error": f"rows_json is not valid JSON: {error}"}

	rows = _rows_from(data)
	if not rows:
		return {
			"saved": False,
			"error": "found no rows in rows_json — it must be an array of objects, "
			"or an object containing one.",
		}

	truncated = len(rows) > MAX_ROWS
	rows = rows[:MAX_ROWS]

	header = _header(rows, columns)
	if not header:
		return {"saved": False, "error": "the rows carry no fields to use as columns."}

	content = _build(rows, header)
	file_name = _file_name(filename)
	file_url = save_file(file_name, content, None, None, is_private=1).file_url

	result = {
		"saved": True,
		"file_name": file_name,
		"file_url": file_url,
		"row_count": len(rows),
		"columns": header,
	}
	if truncated:
		result["truncated"] = f"only the first {MAX_ROWS} rows were written"

	return result
