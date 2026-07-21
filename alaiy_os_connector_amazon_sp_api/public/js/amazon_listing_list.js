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
								"Reached the 1,000-listing cap of the Listings API. Some listings may not be synced; a report-based sync is needed for larger catalogs."
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
	},
};
