# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""A store policy narrows what the connector stores, and no policy changes nothing.

No site is needed: frappe is patched at each call the guards sit in front of, so
the assertions are on what would have been written or fetched.
"""

from datetime import datetime
import unittest
import unittest.mock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from alaiy_os_connector_amazon_sp_api import store_policy, tasks
from alaiy_os_connector_amazon_sp_api.spapi import health, inventory, listings, orders, reconcile
from alaiy_os_connector_amazon_sp_api.spapi.client import SpApiClient
from alaiy_os_connector_amazon_sp_api.store_policy import StorePolicy

KEPT = "NG-SKU-1"
DROPPED = "OTHER-SKU-9"


def _policy(may_sync=True, kept=(KEPT,)):
	return StorePolicy(may_sync=may_sync, keeps_sku=lambda sku: sku in kept)


class TestForConnection(unittest.TestCase):
	def test_no_hook_is_unrestricted(self):
		with patch.object(store_policy.frappe, "get_hooks", return_value=[]):
			self.assertIsNone(store_policy.for_connection("AMZ-1"))

	def test_hook_returning_none_is_unrestricted(self):
		with (
			patch.object(store_policy.frappe, "get_hooks", return_value=["a.b"]),
			patch.object(store_policy.frappe, "get_attr", return_value=lambda name: None),
		):
			self.assertIsNone(store_policy.for_connection("AMZ-1"))

	def test_last_hook_is_called_with_the_connection_name(self):
		got = _policy()
		hook = MagicMock(return_value=got)
		with (
			patch.object(store_policy.frappe, "get_hooks", return_value=["first.path", "last.path"]),
			patch.object(store_policy.frappe, "get_attr", return_value=hook) as get_attr,
		):
			self.assertIs(store_policy.for_connection("AMZ-1"), got)
		get_attr.assert_called_once_with("last.path")
		hook.assert_called_once_with("AMZ-1")


class TestScheduledJobs(unittest.TestCase):
	def test_may_sync(self):
		for policy, expected in ((None, True), (_policy(True), True), (_policy(False), False)):
			with patch.object(tasks.store_policy, "for_connection", return_value=policy):
				self.assertEqual(tasks._may_sync("AMZ-1"), expected)

	def test_no_sync_connection_is_skipped_by_every_job(self):
		ran = MagicMock()
		with (
			patch.object(tasks.connections, "connected_names", return_value=["AMZ-1"]),
			patch.object(tasks.store_policy, "for_connection", return_value=_policy(False)),
			patch.object(tasks.frappe, "db", MagicMock(get_value=lambda *a: "CUST")),
			patch.object(tasks.orders, "sync_orders", ran),
		):
			tasks._for_each_connection("x", ran)
			tasks.sync_orders()
		ran.assert_not_called()

	def test_unrestricted_connection_still_runs(self):
		ran = MagicMock()
		with (
			patch.object(tasks.connections, "connected_names", return_value=["AMZ-1"]),
			patch.object(tasks.store_policy, "for_connection", return_value=None),
		):
			tasks._for_each_connection("x", ran)
		ran.assert_called_once_with("AMZ-1")


class TestListings(unittest.TestCase):
	REPORT = (
		"seller-sku\tasin1\tstatus\tprice\tquantity\tfulfillment-channel\titem-name\n"
		f"{KEPT}\tB0KEPT0001\tActive\t10\t3\tDEFAULT\tKept\n"
		f"{DROPPED}\tB0DROP0001\tActive\t20\t5\tDEFAULT\tDropped\n"
	)

	def _run(self, policy):
		mp = SimpleNamespace(name="MP", currency="USD")
		created = []
		with (
			patch.object(reconcile, "_enrich_from_catalog", return_value=({}, {"enriched": 0, "enrich_deferred": 0, "enrich_missing": 0})) as enrich,
			patch.object(reconcile, "stamp_connection"),
			patch.object(reconcile, "_apply_catalog"),
			patch.object(reconcile, "now_datetime"),
			patch.object(reconcile.frappe, "db", MagicMock(exists=lambda *a: False)),
			patch.object(reconcile.frappe, "get_doc", side_effect=lambda d: created.append(d["sku"]) or MagicMock()),
		):
			summary = reconcile._apply_report(mp, self.REPORT, client=None, policy=policy)
		return summary, created, enrich

	def test_report_rows_of_other_skus_are_not_stored_or_enriched(self):
		summary, created, enrich = self._run(_policy())
		self.assertEqual(created, [KEPT])
		self.assertEqual(summary["seen"], 1)
		enriched_rows = enrich.call_args.args[1]
		self.assertEqual([r["seller-sku"] for r in enriched_rows], [KEPT])

	def test_unrestricted_report_stores_every_row(self):
		summary, created, _ = self._run(None)
		self.assertEqual(created, [KEPT, DROPPED])
		self.assertEqual(summary["seen"], 2)

	def test_reconcile_of_a_no_sync_connection_fetches_nothing(self):
		conn = MagicMock()
		conn.name = "AMZ-1"
		with (
			patch.object(reconcile.connections, "resolve", return_value=conn),
			patch.object(reconcile, "_marketplace", return_value=SimpleNamespace(name="MP")),
			patch.object(reconcile.store_policy, "for_connection", return_value=_policy(False)),
			patch.object(reconcile.reports, "fetch_report") as fetch,
		):
			summary = reconcile.reconcile_all_listings(connection="AMZ-1")
		fetch.assert_not_called()
		self.assertEqual(summary["skipped"], "no sync allowed")

	def test_upsert_from_item_skips_a_sku_the_policy_drops(self):
		db = MagicMock()
		with patch.object(listings.frappe, "db", db):
			got = listings._upsert_from_item(DROPPED, SimpleNamespace(name="MP"), {}, policy=_policy())
		self.assertIsNone(got)
		db.exists.assert_not_called()

	def test_sync_all_filters_before_the_catalog_lookup(self):
		items = [{"sku": KEPT}, {"sku": DROPPED}]
		conn = SimpleNamespace(name="AMZ-1")
		upserted = []
		with (
			patch.object(listings, "_connection", return_value=conn),
			patch.object(listings, "_marketplace", return_value=SimpleNamespace(name="MP")),
			patch.object(listings.store_policy, "for_connection", return_value=_policy()),
			patch.object(listings, "SpApiClient"),
			patch.object(listings, "_search_listings_items", return_value={"items": items}),
			patch.object(listings, "_item_asin", side_effect=lambda sku, item: f"ASIN-{sku}"),
			patch.object(listings.catalog, "fetch_content", return_value={}) as fetch_content,
			patch.object(listings, "_upsert_from_item", side_effect=lambda sku, *a, **k: upserted.append(sku) or {"listing_status": "active"}),
			patch.object(listings.frappe, "db", MagicMock()),
		):
			listings._sync_all_listings(None, None)
		fetch_content.assert_called_once_with([f"ASIN-{KEPT}"], unittest.mock.ANY, client=unittest.mock.ANY)
		self.assertEqual(upserted, [KEPT])


class _FakeSalesOrder:
	def __init__(self):
		self.items, self.taxes = [], []
		self.flags = SimpleNamespace()
		self.inserted = self.submitted = False

	def append(self, table, row):
		getattr(self, table).append(row)

	def insert(self):
		self.inserted = True

	def submit(self):
		self.submitted = True


def _order_item(sku, price, tax="0", qty=1):
	return {
		"SellerSKU": sku,
		"QuantityOrdered": qty,
		"ItemPrice": {"Amount": price},
		"ItemTax": {"Amount": tax},
		"OrderItemId": f"OI-{sku}",
		"ASIN": f"ASIN-{sku}",
		"Title": f"Title {sku}",
	}


# Buyer fields Amazon may attach to an order; none must reach a stored row.
ORDER = {
	"AmazonOrderId": "111-1",
	"OrderStatus": "Unshipped",
	"FulfillmentChannel": "MFN",
	"PurchaseDate": "2026-08-01T10:00:00Z",
	"LastUpdateDate": "2026-08-01T11:00:00Z",
	"OrderTotal": {"CurrencyCode": "USD", "Amount": "100.00"},
	"BuyerInfo": {"BuyerEmail": "buyer@example.com", "BuyerName": "Bea Buyer"},
	"ShippingAddress": {"Name": "Sam Shopper", "City": "Springfield", "PostalCode": "99999"},
}


def _config(policy):
	return {
		"connection": "AMZ-1",
		"company": "Co",
		"company_currency": "USD",
		"customer": "CUST",
		"warehouse": "WH",
		"price_list": "PL",
		"fallback_item": None if policy else "AMZ-UNMAPPED",
		"policy": policy,
		"delivery_date_for": lambda order: None,
		"unmapped_skus": set(),
	}


class TestOrders(unittest.TestCase):
	def _upsert(self, order_items, policy, status="Unshipped"):
		so = _FakeSalesOrder()
		order = dict(ORDER, OrderStatus=status)
		with (
			patch.object(orders, "get_sales_order", return_value=None),
			patch.object(orders, "fetch_order_items", return_value=order_items),
			patch.object(orders.time, "sleep"),
			patch.object(orders, "_resolve_item_code", side_effect=lambda sku: f"ITEM-{sku}"),
			patch.object(orders, "_resolve_tax_account", return_value="TAX"),
			patch.object(orders, "_from_amazon_iso", return_value=datetime(2026, 8, 1, 10, 0)),
			patch.object(orders.frappe, "new_doc", return_value=so),
		):
			outcome = orders._upsert_order_unlocked(
				order, SimpleNamespace(name="MP", currency="USD"), MagicMock(), _config(policy), "111-1"
			)
		return outcome, so

	def test_lines_of_other_skus_are_dropped_and_totals_come_from_kept_lines(self):
		items = [_order_item(KEPT, "40.00", tax="4.00"), _order_item(DROPPED, "56.00", tax="6.00")]
		outcome, so = self._upsert(items, _policy())
		self.assertEqual(outcome, "created")
		self.assertEqual([r["amazon_seller_sku"] for r in so.items], [KEPT])
		self.assertEqual(so.amazon_order_total, 44.0)
		self.assertEqual([t["tax_amount"] for t in so.taxes], [4.0])

	def test_order_without_a_kept_line_is_not_stored(self):
		outcome, so = self._upsert([_order_item(DROPPED, "56.00")], _policy())
		self.assertEqual(outcome, "unchanged")
		self.assertFalse(so.inserted)

	def test_restricted_store_never_uses_the_placeholder_item(self):
		_, so = self._upsert([_order_item(KEPT, "40.00")], _policy())
		self.assertEqual(so.items[0]["item_code"], f"ITEM-{KEPT}")

	def test_unrestricted_order_is_unchanged(self):
		items = [_order_item(KEPT, "40.00", tax="4.00"), _order_item(DROPPED, "56.00", tax="6.00")]
		outcome, so = self._upsert(items, None)
		self.assertEqual(outcome, "created")
		self.assertEqual([r["amazon_seller_sku"] for r in so.items], [KEPT, DROPPED])
		self.assertEqual(so.amazon_order_total, 100.0)
		self.assertEqual([t["tax_amount"] for t in so.taxes], [10.0])

	def test_buyer_fields_reach_no_stored_value(self):
		items = [_order_item(KEPT, "40.00"), _order_item(DROPPED, "56.00")]
		_, so = self._upsert(items, _policy())
		stored = repr(vars(so))
		for secret in ("buyer@example.com", "Bea Buyer", "Sam Shopper", "Springfield", "99999", DROPPED):
			self.assertNotIn(secret, stored)

	def test_status_update_of_a_restricted_order_leaves_the_total_alone(self):
		so = MagicMock()
		so.get.return_value = None
		with patch.object(orders, "_from_amazon_iso", return_value=None):
			orders._stamp_status(so, ORDER, {"policy": _policy()})
			fields = [c.args[0] for c in so.db_set.call_args_list]
			self.assertNotIn("amazon_order_total", fields)
			so.reset_mock()
			orders._stamp_status(so, ORDER, {})
		fields = [c.args[0] for c in so.db_set.call_args_list]
		self.assertIn("amazon_order_total", fields)

	def test_sync_of_a_no_sync_connection_fetches_nothing(self):
		conn = MagicMock()
		conn.name = "AMZ-1"
		with (
			patch.object(orders.connections, "resolve", return_value=conn),
			patch.object(orders.store_policy, "for_connection", return_value=_policy(False)),
			patch.object(orders, "fetch_orders") as fetch,
		):
			summary = orders.sync_orders(connection="AMZ-1")
		fetch.assert_not_called()
		self.assertEqual(summary["skipped"], "no sync allowed")


class TestInventoryAndHealth(unittest.TestCase):
	def _conn(self):
		conn = MagicMock()
		conn.name = "AMZ-1"
		return conn

	def test_restricted_store_syncs_no_inventory(self):
		with (
			patch.object(inventory.connections, "resolve", return_value=self._conn()),
			patch.object(inventory.store_policy, "for_connection", return_value=_policy()),
			patch.object(inventory, "fetch_summaries") as fetch,
		):
			self.assertEqual(inventory.sync_inventory(connection="AMZ-1")["skipped"], "restricted")
		fetch.assert_not_called()

	def test_restricted_store_syncs_no_health(self):
		with (
			patch.object(health.connections, "resolve", return_value=self._conn()),
			patch.object(health.store_policy, "for_connection", return_value=_policy()),
			patch.object(health, "SpApiClient") as client,
		):
			self.assertEqual(health.run_health_sync(connection="AMZ-1")["skipped"], "restricted")
		client.assert_not_called()


class TestClientLog(unittest.TestCase):
	def _log(self, policy):
		conn = SimpleNamespace(name="AMZ-1")
		client = SpApiClient(conn)
		doc = MagicMock()
		with (
			patch("alaiy_os_connector_amazon_sp_api.spapi.client.store_policy.for_connection", return_value=policy),
			patch("alaiy_os_connector_amazon_sp_api.spapi.client.frappe.get_doc", return_value=doc) as get_doc,
		):
			client._log("GET", "/orders/v0/orders", 200, None, 12, "orders", '{"BuyerEmail": "buyer@example.com"}')
		return get_doc.call_args.args[0]

	def test_restricted_log_keeps_the_row_but_not_the_body(self):
		row = self._log(_policy())
		self.assertEqual(row["response_excerpt"], "")
		self.assertEqual(row["path"], "/orders/v0/orders")
		self.assertEqual(row["status_code"], 200)

	def test_unrestricted_log_keeps_the_body(self):
		self.assertIn("BuyerEmail", self._log(None)["response_excerpt"])

	def test_unresolvable_policy_fails_closed(self):
		with patch("alaiy_os_connector_amazon_sp_api.spapi.client.store_policy.for_connection", side_effect=RuntimeError):
			client = SpApiClient(SimpleNamespace(name="AMZ-1"))
			self.assertTrue(client._is_restricted())


if __name__ == "__main__":
	unittest.main()
