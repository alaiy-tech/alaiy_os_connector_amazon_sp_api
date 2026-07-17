// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Listing list view: colour the status and expose quick filters for
// Active / Inactive / Suppressed.

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
};
