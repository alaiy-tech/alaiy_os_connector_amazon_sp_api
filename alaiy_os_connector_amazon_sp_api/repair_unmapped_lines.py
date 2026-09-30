# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Putting the SKU back on Sales Order lines a merge bug took it off.

Until `_merge_duplicate_rows` was keyed the way ERPNext keys its own duplicate
check, every unmapped SKU on an order collapsed into the one placeholder line
they all share: quantities summed across unrelated products, rate averaged, and
`amazon_seller_sku` / `amazon_asin` / `amazon_order_item_id` blanked because the
merged rows disagreed. The order balanced, and the line described nothing.

The SKUs were not lost — `_order_item_rows` writes each one into the line's
description ("<title> | SKU: … | ASIN: …", joined with "; " when rows merged), so
what is recoverable is recoverable from there.

What "repaired" can mean depends on the line, and this refuses to blur the three:

  a draft order          left alone. The sync rebuilds a draft's lines from a
                         fresh getOrderItems on every run, so re-running it is a
                         real repair — several correct lines, not one patched
                         one. Anything written here would be overwritten anyway.

  one SKU on the line    repaired. The quantity and the rate are that SKU's
                         already, so writing its ids back makes the row true.

  several SKUs on one    reported, never written. The row is a blend: one
  line                   quantity covering products sold at different prices,
                         and no single SKU is the honest answer. Splitting it
                         would mean cancelling and amending a submitted order,
                         which is a decision about accounting records, not a
                         backfill.

Only the three provenance fields are touched, through `db_set` — they are
read-only fields carrying what Amazon said, and nothing financial moves. A
submitted order's items are otherwise untouched.

Run it (from the frappe-bench directory) — reporting first, which is the default:

    bench --site <site> execute \
      alaiy_os_connector_amazon_sp_api.repair_unmapped_lines.run \
      --kwargs "{'days': 7}"

    bench --site <site> execute \
      alaiy_os_connector_amazon_sp_api.repair_unmapped_lines.run \
      --kwargs "{'days': 7, 'dry_run': False}"

Both print the same table. Only the second writes.
"""

import re

import frappe
from frappe.utils import add_to_date, now_datetime

from alaiy_os_connector_amazon_sp_api.spapi.constants import UNMAPPED_ITEM_CODE

#: "… | SKU: ABC-123 | ASIN: B0XYZ", the shape `_order_item_rows` writes. Stops at
#: the separator so a title containing "SKU:" cannot swallow the rest of the line.
_SKU = re.compile(r"SKU:\s*([^|;]+)")
_ASIN = re.compile(r"ASIN:\s*([^|;]+)")


def run(days=7, dry_run=True, marketplace=None):
	"""Report — and optionally repair — placeholder lines with no SKU on them.

	`days` counts back from now over the order's transaction date. `dry_run` is the
	default and writes nothing; pass `False` to write the ids of the lines that can
	carry one honestly.
	"""
	placeholders = _placeholder_items()
	lines = _candidate_lines(days, placeholders, marketplace)

	repaired, blended, drafts = [], [], []
	for line in lines:
		skus = _SKU.findall(line.description or "")
		asins = _ASIN.findall(line.description or "")
		entry = {
			"sales_order": line.parent,
			"amazon_order_id": line.amazon_order_id,
			"line": line.name,
			"qty": line.qty,
			"rate": line.rate,
			"skus": [s.strip() for s in skus],
			"asins": [a.strip() for a in asins],
		}
		if line.docstatus == 0:
			drafts.append(entry)
			continue
		if len(entry["skus"]) != 1:
			blended.append(entry)
			continue
		if not dry_run:
			values = {"amazon_seller_sku": entry["skus"][0]}
			if len(entry["asins"]) == 1:
				values["amazon_asin"] = entry["asins"][0]
			frappe.db.set_value("Sales Order Item", line.name, values, update_modified=False)
		repaired.append(entry)

	if not dry_run and repaired:
		frappe.db.commit()

	_print(days, dry_run, repaired, blended, drafts)
	return {
		"dry_run": bool(dry_run),
		"examined": len(lines),
		"repaired": repaired,
		"blended": blended,
		"drafts": drafts,
	}


def _placeholder_items():
	"""Every Item an unmapped line could have been booked against on this site.

	The shared `Amazon Unmapped Item`, plus whatever each connection names in
	`orders_fallback_item` — a site that configured its own placeholder has lines
	on that one instead, and a repair that only knew the constant would report a
	clean week on a site where nothing was clean.
	"""
	configured = frappe.get_all(
		"Amazon Connection", filters={"orders_fallback_item": ["is", "set"]},
		pluck="orders_fallback_item",
	)
	return list({UNMAPPED_ITEM_CODE, *configured})


def _candidate_lines(days, placeholders, marketplace):
	"""Placeholder lines with no SKU on them, on Amazon orders inside the window.

	A line that already carries its SKU is not a candidate whatever else is true of
	it: this only ever fills a blank, so re-running it changes nothing the second
	time.
	"""
	since = add_to_date(now_datetime(), days=-int(days)).date()
	filters = {
		"item_code": ["in", placeholders],
		"amazon_seller_sku": ["is", "not set"],
		"docstatus": ["<", 2],
	}
	order_filters = {"transaction_date": [">=", since], "amazon_order_id": ["is", "set"]}
	if marketplace:
		order_filters["amazon_marketplace"] = marketplace

	orders = frappe.get_all("Sales Order", filters=order_filters,
				fields=["name", "amazon_order_id", "docstatus"])
	if not orders:
		return []
	by_name = {row.name: row for row in orders}
	filters["parent"] = ["in", list(by_name)]

	lines = frappe.get_all(
		"Sales Order Item", filters=filters,
		fields=["name", "parent", "item_code", "qty", "rate", "description"],
		order_by="parent asc, idx asc", limit_page_length=0,
	)
	for line in lines:
		line.amazon_order_id = by_name[line.parent].amazon_order_id
		line.docstatus = by_name[line.parent].docstatus
	return lines


def _print(days, dry_run, repaired, blended, drafts):
	head = "Would repair" if dry_run else "Repaired"
	print(f"\nAmazon placeholder lines with no SKU, last {days} day(s)\n")

	print(f"{head}: {len(repaired)} line(s) carrying exactly one SKU")
	for entry in repaired:
		print(f"  {entry['sales_order']}  {entry['amazon_order_id']}  qty={entry['qty']}  "
			f"-> {entry['skus'][0]}")

	print(f"\nCannot repair: {len(blended)} line(s) blending several products")
	for entry in blended:
		found = ", ".join(entry["skus"]) or "no SKU in the description"
		print(f"  {entry['sales_order']}  {entry['amazon_order_id']}  qty={entry['qty']}  "
			f"rate={entry['rate']}  [{found}]")
	if blended:
		print("  These rows are one quantity over products sold at different prices.")
		print("  Only cancelling and amending the order can split them honestly.")

	print(f"\nLeft to the sync: {len(drafts)} draft line(s)")
	for entry in drafts:
		print(f"  {entry['sales_order']}  {entry['amazon_order_id']}")
	if drafts:
		print("  Re-run sync_orders over this window — a draft's lines are rebuilt from")
		print("  Amazon on every run, which rebuilds them as several correct lines.")

	if dry_run:
		print("\nNothing was written. Re-run with {'dry_run': False} to write the SKUs above.\n")
	else:
		print("")
