// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Product Listing list view: colour the status, expose quick filters for
// Active / Inactive / Suppressed, "Sync All from Amazon", and — under Actions,
// on a selection — "Publish to Amazon".
//
// Publishing a selection is one job rather than one call per row: each listing
// costs several Amazon calls, so a page of twenty outlives a request. The rows
// themselves are the report the job leaves behind (last_published_at /
// last_publish_error), and the realtime summary below is the notification.

frappe.listview_settings["Amazon Product Listing"] = {
	add_fields: ["listing_status", "last_publish_error"],

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

	// The Actions menu is rebuilt on every render, so the bulk action is added
	// here rather than in onload. add_actions_menu_item ignores a label it
	// already has, so re-adding it is free.
	refresh(listview) {
		listview.page.add_actions_menu_item(__("Publish to Amazon"), () =>
			amazon_publish_selection(listview)
		);
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
					// A capped enrichment pass has to announce itself: otherwise the
					// rows still showing a SKU instead of a title look like a bug
					// rather than a queue that the next run drains.
					if (data.enrich_deferred) {
						frappe.msgprint(
							__(
								"Filled titles, descriptions and variation parentage for {0} SKUs from the catalog. {1} more are queued — they are picked up by the next reconcile, or run this again to continue.",
								[data.enriched || 0, data.enrich_deferred]
							)
						);
					}
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
			// Notify + refresh when a bulk publish finishes. Unlike a sync, some
			// rows can fail while others land, so the failures are named rather
			// than counted — each one is a listing still not on Amazon.
			frappe.realtime.on("amazon_publish_complete", (data) => {
				if (!data) return;
				const parts = [
					data.created ? __("{0} created", [data.created]) : null,
					data.updated ? __("{0} updated", [data.updated]) : null,
					data.unchanged ? __("{0} already in sync", [data.unchanged]) : null,
					data.failed ? __("{0} failed", [data.failed]) : null,
				].filter(Boolean);
				frappe.show_alert(
					{
						message: __("Publish finished: {0}", [
							parts.join(", ") || __("nothing to do"),
						]),
						indicator: data.failed ? "orange" : "green",
					},
					10
				);
				const failures = (data.results || []).filter((r) => r.action === "failed");
				if (failures.length) {
					frappe.msgprint({
						title: __("Some listings were not published"),
						indicator: "orange",
						message: `<table class="table table-bordered">
							<thead><tr><th style="width:30%;">${__("SKU")}</th><th>${__("Why")}</th></tr></thead>
							<tbody>${failures
								.map(
									(r) =>
										`<tr><td>${frappe.utils.escape_html(r.sku)}</td>
										<td>${frappe.utils.escape_html(r.error || "")}</td></tr>`
								)
								.join("")}</tbody>
						</table>
						<p class="text-muted small">${__(
							"Each row also carries its own reason in Last Publish Error."
						)}</p>`,
					});
				}
				listview.refresh();
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

// Publish every checked row: creates the offer for the SKUs Amazon has no
// listing for, and submits the difference for the ones it does. Which of the two
// a row needs is decided per row, server-side, against Amazon — nothing here has
// to know, and that is what lets one selection mix drafts with live listings.
function amazon_publish_selection(listview) {
	const skus = listview.get_checked_items(true);
	if (!skus.length) {
		frappe.msgprint(__("Select the listings to publish first."));
		return;
	}
	frappe.confirm(
		__(
			"Publish {0} listing(s) to Amazon? Listings Amazon does not have are created; the rest receive whatever the row has that Amazon does not. Nothing is deleted.",
			[skus.length]
		),
		() => {
			frappe.call({
				method: "alaiy_os_connector_amazon_sp_api.api.publish_listings",
				args: { skus: JSON.stringify(skus) },
				freeze: true,
				freeze_message: __("Queueing publish…"),
				callback: () => {
					frappe.show_alert({
						message: __(
							"Publishing {0} listing(s) — you'll be notified when it completes.",
							[skus.length]
						),
						indicator: "blue",
					});
				},
			});
		}
	);
}
