// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Listing list view: colour the status, expose quick filters for
// Active / Inactive / Suppressed, and a "Sync All from Amazon" button.

frappe.listview_settings["Amazon Listing"] = {
	add_fields: ["listing_status"],

	get_indicator(doc) {
		const map = {
			active: ["Active", "green", "listing_status,=,active"],
			inactive: ["Inactive", "gray", "listing_status,=,inactive"],
			suppressed: ["Suppressed", "red", "listing_status,=,suppressed"],
			incomplete: ["Incomplete", "orange", "listing_status,=,incomplete"],
			pending: ["Pending", "blue", "listing_status,=,pending"],
		};
		return map[doc.listing_status] || [doc.listing_status, "gray", ""];
	},

	onload(listview) {
		// Notify + refresh when the background bulk sync finishes.
		if (!listview._amazon_sync_bound) {
			frappe.realtime.on("amazon_sync_all_complete", (data) => {
				if (data && data.success) {
					const parts = Object.entries(data.by_status || {})
						.map(([k, v]) => `${v} ${k}`)
						.join(", ");
					frappe.show_alert(
						{
							message: __("Synced {0} listings from Amazon{1}", [
								data.synced,
								parts ? ` (${parts})` : "",
							]),
							indicator: "green",
						},
						10
					);
					if (data.truncated) {
						frappe.msgprint(
							__(
								"Reached the 1,000-listing cap of the Listings API. For larger catalogs use \"Reconcile All from Amazon\", which uses the Merchant Listings report (no cap)."
							)
						);
					}
					listview.refresh();
				} else {
					frappe.show_alert(
						{
							message: __("Amazon sync failed: {0}", [
								frappe.utils.escape_html((data && data.error) || "unknown error"),
							]),
							indicator: "red",
						},
						10
					);
				}
			});
			// Notify + refresh when the background reconciliation finishes.
			frappe.realtime.on("amazon_reconcile_complete", (data) => {
				if (data && data.success) {
					const parts = Object.entries(data.by_status || {})
						.map(([k, v]) => `${v} ${k}`)
						.join(", ");
					const extra = data.skipped_pending
						? __(" ({0} pending skipped)", [data.skipped_pending])
						: "";
					frappe.show_alert(
						{
							message: __("Reconciled {0} listings from Amazon{1}{2}", [
								data.seen,
								parts ? ` (${parts})` : "",
								extra,
							]),
							indicator: "green",
						},
						10
					);
					listview.refresh();
				} else {
					frappe.show_alert(
						{
							message: __("Amazon reconciliation failed: {0}", [
								frappe.utils.escape_html((data && data.error) || "unknown error"),
							]),
							indicator: "red",
						},
						10
					);
				}
			});
			listview._amazon_sync_bound = true;
		}

		listview.page.add_inner_button(__("Sync All from Amazon"), () => {
			frappe.confirm(
				__("Pull all listings from the primary marketplace into the register?"),
				() => {
					frappe.call({
						method: "alaiy_os_connector_amazon_sp_api.api.sync_all_listings",
						callback: () => {
							frappe.show_alert({
								message: __("Sync started — you'll be notified when it completes."),
								indicator: "blue",
							});
						},
					});
				}
			);
		});

		listview.page.add_inner_button(__("Reconcile All from Amazon"), () => {
			frappe.confirm(
				__(
					"Reconcile the full catalog (status/price/quantity) from the Merchant Listings report? This has no 1,000-listing cap and does not change description, bullet points, keywords, or images."
				),
				() => {
					frappe.call({
						method: "alaiy_os_connector_amazon_sp_api.api.reconcile_listings",
						callback: () => {
							frappe.show_alert({
								message: __(
									"Reconciliation started — you'll be notified when it completes."
								),
								indicator: "blue",
							});
						},
					});
				}
			);
		});
	},
};
