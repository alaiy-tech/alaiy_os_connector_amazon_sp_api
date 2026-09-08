# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""Unit tests for the fee, pricing and settled-finance readers — no site needed.

Everything here is a pure transformation over a payload shape, which is exactly
where this feature's failure mode lives. Every number these three modules
produce is money on a margin table, and a fold that is wrong stays *plausible*:
a commission double-counted still looks like a commission, and a fee filed
against the wrong SKU still looks like a fee. Nothing downstream can catch
either. So the cases pinned here are the ones where a wrong answer is
indistinguishable from a right one.

Four of them are worth spelling out.

`test_fba_rollup_is_not_double_counted` is the trap Amazon's own payload sets.
It sends the fulfilment fee as `FBAFees` with the pick-pack and weight-handling
components nested inside under `IncludedFeeDetailList` — so a summing walk that
recursed reports twice the fulfilment cost, on the marketplaces that send the
roll-up form and not on the others.

`test_results_are_matched_by_identifier_not_by_position` is the worst available
outcome in this feature: Amazon's batch results are not guaranteed to come back
in request order, and a positional match files one SKU's fees against another's.
Every figure in the table stays believable and every one of them is wrong.

`test_used_offer_is_not_the_buy_box` is the Buy Box comparison Amazon invites by
putting used-condition featured offers in the same array as new. A used offer is
cheaper and is not what a new listing competes with, so taking the first entry
makes a healthy price look beaten and recommends a cut nobody needed.

`test_refunds_are_not_netted_against_charged_fees` is the aggregate that would
under-report the cost of selling. Amazon files a refund's fee reversal as a
positive amount on its own transaction, so sweeping up every transaction type
nets a returned order's commission against a charged one — and reports a
cheaper Amazon than the seller is paying.
"""

from unittest.mock import Mock, patch

from frappe.tests import UnitTestCase

from alaiy_os_connector_amazon_sp_api.spapi import fees, finances, pricing

MARKETPLACE = "A21TJRUUN4KGV"


def _money(amount, currency="INR"):
	return {"Amount": amount, "CurrencyCode": currency}


class TestFeeFold(UnitTestCase):
	def test_commission_like_charges_land_in_the_referral_bucket(self):
		"""VariableClosingFee and PerItemFee are commission to a margin, not "other"."""
		folded = fees._fold(
			{
				"TotalFeesEstimate": _money(70.0),
				"FeeDetailList": [
					{"FeeType": "ReferralFee", "FeeAmount": _money(50.0)},
					{"FeeType": "VariableClosingFee", "FeeAmount": _money(15.0)},
					{"FeeType": "PerItemFee", "FeeAmount": _money(5.0)},
				],
			}
		)
		self.assertEqual(folded["referral_fee"], 70.0)
		self.assertEqual(folded["other_fee"], 0.0)

	def test_fba_rollup_is_not_double_counted(self):
		"""The nested components of FBAFees must not be added to the roll-up."""
		folded = fees._fold(
			{
				"TotalFeesEstimate": _money(90.0),
				"FeeDetailList": [
					{"FeeType": "ReferralFee", "FeeAmount": _money(50.0)},
					{
						"FeeType": "FBAFees",
						"FeeAmount": _money(40.0),
						"IncludedFeeDetailList": [
							{"FeeType": "FBAPickAndPack", "FeeAmount": _money(28.0)},
							{"FeeType": "FBAWeightHandling", "FeeAmount": _money(12.0)},
						],
					},
				],
			}
		)
		self.assertEqual(folded["fba_fee"], 40.0)

	def test_final_fee_wins_over_the_pre_promotion_amount(self):
		"""A SKU in a fee promotion should report what it is actually charged."""
		folded = fees._fold(
			{
				"TotalFeesEstimate": _money(30.0),
				"FeeDetailList": [
					{
						"FeeType": "ReferralFee",
						"FeeAmount": _money(50.0),
						"FeePromotion": _money(20.0),
						"FinalFee": _money(30.0),
					}
				],
			}
		)
		self.assertEqual(folded["referral_fee"], 30.0)

	def test_a_marketplace_omitting_final_fee_still_reports_the_commission(self):
		folded = fees._fold({"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(42.0)}]})
		self.assertEqual(folded["referral_fee"], 42.0)

	def test_the_total_is_amazons_own_and_not_the_sum_of_the_buckets(self):
		"""So a bucketing mistake shows up as buckets that don't add up."""
		folded = fees._fold(
			{
				"TotalFeesEstimate": _money(100.0),
				"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(50.0)}],
			}
		)
		self.assertEqual(folded["total_fee"], 100.0)
		self.assertEqual(folded["referral_fee"], 50.0)

	def test_an_unknown_fee_name_is_reported_rather_than_dropped(self):
		"""Amazon adds fee types; an unrecognised one is still money."""
		folded = fees._fold({"FeeDetailList": [{"FeeType": "SomeNewAmazonFee", "FeeAmount": _money(11.0)}]})
		self.assertEqual(folded["other_fee"], 11.0)
		self.assertEqual(folded["total_fee"], 11.0)


class TestFeeRequest(UnitTestCase):
	def test_the_fba_schedule_is_only_requested_for_an_fba_sku(self):
		merchant = fees._request({"sku": "A", "price": 100, "currency": "INR"}, MARKETPLACE, "0-A")
		fulfilled = fees._request(
			{"sku": "B", "price": 100, "currency": "INR", "fba": True}, MARKETPLACE, "1-B"
		)
		self.assertFalse(merchant["FeesEstimateRequest"]["IsAmazonFulfilled"])
		self.assertNotIn("OptionalFulfillmentProgram", merchant["FeesEstimateRequest"])
		self.assertTrue(fulfilled["FeesEstimateRequest"]["IsAmazonFulfilled"])
		self.assertEqual(fulfilled["FeesEstimateRequest"]["OptionalFulfillmentProgram"], "FBA_CORE")

	def test_shipping_is_omitted_rather_than_sent_as_zero_when_unknown(self):
		"""A zero shipping charge understates commission where Amazon commissions it."""
		request = fees._request({"sku": "A", "price": 100, "currency": "INR"}, MARKETPLACE, "0-A")
		self.assertNotIn("Shipping", request["FeesEstimateRequest"]["PriceToEstimateFees"])

		with_shipping = fees._request(
			{"sku": "A", "price": 100, "currency": "INR", "shipping": 0}, MARKETPLACE, "0-A"
		)
		self.assertEqual(
			with_shipping["FeesEstimateRequest"]["PriceToEstimateFees"]["Shipping"]["Amount"], 0.0
		)

	def test_a_sku_without_a_price_is_refused_rather_than_quoted_at_zero(self):
		import frappe

		self.assertRaises(frappe.ValidationError, fees._request, {"sku": "A", "price": 0}, MARKETPLACE, "0-A")

	def test_an_asin_only_item_is_asked_for_by_asin(self):
		request = fees._request({"asin": "B0TEST12345", "price": 100}, MARKETPLACE, "0-B0TEST12345")
		self.assertEqual(request["IdType"], "ASIN")
		self.assertEqual(request["IdValue"], "B0TEST12345")


class TestEstimateFees(UnitTestCase):
	def _client(self, results):
		client = Mock()
		client.post.return_value = {"payload": {"FeesEstimateResultList": results}}
		return client

	def test_results_are_matched_by_identifier_not_by_position(self):
		"""Amazon does not promise request order. See the module docstring."""
		results = [
			{
				"Status": "Success",
				"FeesEstimateIdentifier": {"Identifier": "1-SKU-B", "IdValue": "SKU-B"},
				"FeesEstimate": {
					"TotalFeesEstimate": _money(80.0),
					"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(80.0)}],
				},
			},
			{
				"Status": "Success",
				"FeesEstimateIdentifier": {"Identifier": "0-SKU-A", "IdValue": "SKU-A"},
				"FeesEstimate": {
					"TotalFeesEstimate": _money(20.0),
					"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(20.0)}],
				},
			},
		]
		with patch.object(fees, "_marketplace", return_value=Mock(marketplace_id=MARKETPLACE)):
			out = fees.estimate_fees(
				[
					{"sku": "SKU-A", "price": 100, "currency": "INR"},
					{"sku": "SKU-B", "price": 400, "currency": "INR"},
				],
				client=self._client(results),
			)

		self.assertEqual(out["SKU-A"]["referral_fee"], 20.0)
		self.assertEqual(out["SKU-B"]["referral_fee"], 80.0)

	def test_one_refused_sku_does_not_cost_the_others_their_fees(self):
		results = [
			{
				"Status": "Success",
				"FeesEstimateIdentifier": {"Identifier": "0-SKU-A", "IdValue": "SKU-A"},
				"FeesEstimate": {
					"TotalFeesEstimate": _money(20.0),
					"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(20.0)}],
				},
			},
			{
				"Status": "ClientError",
				"FeesEstimateIdentifier": {"Identifier": "1-SKU-B", "IdValue": "SKU-B"},
				"Error": {"Code": "InvalidInput", "Message": "SKU is not listed"},
			},
		]
		with patch.object(fees, "_marketplace", return_value=Mock(marketplace_id=MARKETPLACE)):
			out = fees.estimate_fees(
				[
					{"sku": "SKU-A", "price": 100, "currency": "INR"},
					{"sku": "SKU-B", "price": 400, "currency": "INR"},
				],
				client=self._client(results),
			)

		self.assertEqual(out["SKU-A"]["referral_fee"], 20.0)
		self.assertEqual(out["SKU-B"]["error"], "SKU is not listed")

	def test_an_unwrapped_response_envelope_still_yields_fees(self):
		"""The gateway has shipped this endpoint both wrapped and unwrapped."""
		client = Mock()
		client.post.return_value = {
			"FeesEstimateResultList": [
				{
					"Status": "Success",
					"FeesEstimateIdentifier": {"Identifier": "0-SKU-A", "IdValue": "SKU-A"},
					"FeesEstimate": {
						"TotalFeesEstimate": _money(20.0),
						"FeeDetailList": [{"FeeType": "ReferralFee", "FeeAmount": _money(20.0)}],
					},
				}
			]
		}
		with patch.object(fees, "_marketplace", return_value=Mock(marketplace_id=MARKETPLACE)):
			out = fees.estimate_fees([{"sku": "SKU-A", "price": 100}], client=client)
		self.assertEqual(out["SKU-A"]["referral_fee"], 20.0)

	def test_nothing_to_quote_makes_no_call_at_all(self):
		client = Mock()
		self.assertEqual(fees.estimate_fees([], client=client), {})
		self.assertEqual(fees.estimate_fees(None, client=client), {})
		client.post.assert_not_called()

	def test_a_batch_is_capped_at_amazons_own_limit(self):
		items = [{"sku": f"SKU-{n}", "price": 100} for n in range(45)]
		batches = fees._batches(items)
		self.assertEqual([len(b) for b in batches], [20, 20, 5])


class TestBuyBox(UnitTestCase):
	def test_used_offer_is_not_the_buy_box(self):
		offer = pricing._featured_offer(
			{
				"featuredBuyingOptions": [
					{
						"buyingOptionType": "Used",
						"segmentedFeaturedOffers": [{"listingPrice": {"amount": 300.0}}],
					},
					{
						"buyingOptionType": "New",
						"segmentedFeaturedOffers": [{"listingPrice": {"amount": 499.0}}],
					},
				]
			}
		)
		self.assertEqual(offer["listingPrice"]["amount"], 499.0)

	def test_no_featured_offer_is_none_and_not_a_zero_price(self):
		self.assertIsNone(pricing._featured_offer({"featuredBuyingOptions": []}))
		row = pricing._summary_row({"asin": "B0TEST12345"}, "SELLER-US")
		self.assertIsNone(row["buy_box_price"])
		self.assertIsNone(row["is_ours"])

	def test_our_own_buy_box_is_recognised_as_ours(self):
		body = {
			"asin": "B0TEST12345",
			"featuredBuyingOptions": [
				{
					"buyingOptionType": "New",
					"segmentedFeaturedOffers": [
						{
							"sellerId": "SELLER-US",
							"listingPrice": {"amount": 499.0, "currencyCode": "INR"},
						}
					],
				}
			],
		}
		self.assertTrue(pricing._summary_row(body, "SELLER-US")["is_ours"])
		self.assertFalse(pricing._summary_row(body, "SOMEONE-ELSE")["is_ours"])

	def test_shipping_is_part_of_what_a_buyer_pays(self):
		"""An offer ₹40 cheaper with ₹80 of shipping is not the cheaper offer."""
		total = pricing._shipping_total(
			{"shippingOptions": [{"price": {"amount": 40.0}}, {"price": {"amount": 40.0}}]}
		)
		self.assertEqual(total, 80.0)

	def test_an_absent_buy_box_percentage_is_unknown_and_not_zero(self):
		"""0% reads as "never won"; absent means Amazon reported no sessions."""
		rows = pricing._traffic_rows(
			'{"salesAndTrafficByAsin": ['
			'{"childAsin": "B0AAA", "trafficByAsin": {"buyBoxPercentage": 0}},'
			'{"childAsin": "B0BBB", "trafficByAsin": {}}]}'
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["trafficByAsin"]["buyBoxPercentage"], 0)
		self.assertEqual(rows[1]["trafficByAsin"], {})

	def test_a_report_that_is_not_json_costs_the_column_and_nothing_else(self):
		with patch("frappe.log_error"):
			self.assertEqual(pricing._traffic_rows("Order ID\tBuy Box\n123\t80%"), [])
		self.assertEqual(pricing._traffic_rows(""), [])


class TestSettledFees(UnitTestCase):
	def test_amazons_settled_name_for_commission_is_the_referral_bucket(self):
		self.assertEqual(finances._bucket_for("Commission"), "referral_fee")
		self.assertEqual(finances._bucket_for("ReferralFee"), "referral_fee")

	def test_a_fee_name_amazon_adds_next_quarter_still_lands_in_fba(self):
		"""Prefix-matched on purpose — see SETTLED_FEE_FBA_PREFIX."""
		self.assertEqual(finances._bucket_for("FBADisposalFee"), "fba_fee")
		self.assertEqual(finances._bucket_for("FBASomethingNew"), "fba_fee")
		self.assertEqual(finances._bucket_for("GiftwrapChargeback"), "other_fee")

	def test_only_the_fees_branch_is_read(self):
		"""Adding Sales or Tax to a fee total charges the seller for their revenue."""
		folded = finances._fees_from(
			{
				"breakdowns": [
					{
						"breakdownType": "Sales",
						"breakdownAmount": {"currencyAmount": 499.0, "currencyCode": "INR"},
					},
					{
						"breakdownType": "Tax",
						"breakdownAmount": {"currencyAmount": 90.0, "currencyCode": "INR"},
					},
					{
						"breakdownType": "Fees",
						"breakdowns": [
							{
								"breakdownType": "Commission",
								"breakdownAmount": {"currencyAmount": -50.0, "currencyCode": "INR"},
							},
							{
								"breakdownType": "FBAPerUnitFulfillmentFee",
								"breakdownAmount": {"currencyAmount": -40.0, "currencyCode": "INR"},
							},
						],
					},
				]
			}
		)
		self.assertEqual(folded["referral_fee"], 50.0)
		self.assertEqual(folded["fba_fee"], 40.0)
		self.assertEqual(folded["other_fee"], 0.0)

	def test_fees_are_positive_magnitudes(self):
		"""Amazon signs money out negative; every consumer here subtracts."""
		self.assertEqual(finances._magnitude({"currencyAmount": -42.5}), 42.5)
		self.assertEqual(finances._magnitude(None), 0.0)

	def _transaction(self, transaction_type, order_id, sku, commission, units=1):
		return {
			"transactionType": transaction_type,
			"relatedIdentifiers": [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": order_id}],
			"totalAmount": {"currencyAmount": 499.0, "currencyCode": "INR"},
			"items": [
				{
					"contexts": [
						{
							"contextType": "ProductContext",
							"sku": sku,
							"asin": "B0TEST12345",
							"quantityShipped": units,
							"fulfillmentNetwork": "AFN",
						}
					],
					"breakdowns": [
						{
							"breakdownType": "Fees",
							"breakdowns": [
								{
									"breakdownType": "Commission",
									"breakdownAmount": {
										"currencyAmount": commission,
										"currencyCode": "INR",
									},
								}
							],
						}
					],
				}
			],
		}

	def _settled(self, transactions):
		with patch.object(finances, "list_transactions", return_value=transactions):
			return finances.settled_fees("2026-08-01T00:00:00Z")

	def test_refunds_are_not_netted_against_charged_fees(self):
		result = self._settled(
			[
				self._transaction("Shipment", "ORDER-1", "SKU-A", -50.0),
				self._transaction("Refund", "ORDER-1", "SKU-A", 50.0),
			]
		)
		self.assertEqual(result["by_sku"]["SKU-A"]["referral_fee"], 50.0)
		self.assertEqual(result["transactions"], 1)

	def test_an_order_settling_in_two_parts_is_counted_once(self):
		result = self._settled(
			[
				self._transaction("Shipment", "ORDER-1", "SKU-A", -30.0),
				self._transaction("Shipment", "ORDER-1", "SKU-A", -20.0),
			]
		)
		row = result["by_sku"]["SKU-A"]
		self.assertEqual(row["referral_fee"], 50.0)
		self.assertEqual(row["orders"], ["ORDER-1"])
		self.assertEqual(row["units"], 2)

	def test_fees_with_no_product_context_are_reported_not_dropped(self):
		"""Unattributed fees are real money; losing them understates the cost."""
		order_level = {
			"transactionType": "Shipment",
			"relatedIdentifiers": [],
			"items": [
				{
					"contexts": [{"contextType": "MarketplaceContext"}],
					"breakdowns": [
						{
							"breakdownType": "Fees",
							"breakdowns": [
								{
									"breakdownType": "ShippingChargeback",
									"breakdownAmount": {"currencyAmount": -15.0},
								}
							],
						}
					],
				}
			],
		}
		result = self._settled([order_level])
		self.assertEqual(result["by_sku"], {})
		self.assertEqual(result["unattributed"]["other_fee"], 15.0)
		self.assertEqual(result["unattributed"]["total_fee"], 15.0)

	def test_every_settled_row_says_it_is_an_actual(self):
		result = self._settled([self._transaction("Shipment", "ORDER-1", "SKU-A", -50.0)])
		self.assertEqual(result["by_sku"]["SKU-A"]["basis"], "actual")
