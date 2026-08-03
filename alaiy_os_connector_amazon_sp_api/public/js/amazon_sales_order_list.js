// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Sales Order list view: Amazon order-sync controls. Orders synced from Seller
// Central land as ordinary Sales Orders, so this hangs off ERPNext's own list
// rather than a DocType of ours.
//
// The buttons are added under an "Amazon" group and only for users who can
// actually run the sync — on a site that also sells through other channels,
// most people looking at this list have no business with Seller Central.

frappe.listview_settings["Sales Order"] = frappe.listview_settings["Sales Order"] || {};

(function () {
	const settings = frappe.listview_settings["Sales Order"];
	const previous_onload = settings.onload;

	settings.onload = function (listview) {
		// Never shadow ERPNext's (or another connector's) own onload.
		if (previous_onload) {
			previous_onload.call(this, listview);
		}

		const can_sync =
			frappe.user.has_role("Amazon Manager") || frappe.user.has_role("System Manager");
		if (!can_sync || listview._amazon_orders_bound) {
			return;
		}
		listview._amazon_orders_bound = true;

		frappe.realtime.on("amazon_orders_sync_complete", (data) => {
			if (data && data.success) {
				const parts = ["created", "updated", "unchanged", "skipped_unresolved"]
					.filter((k) => data[k])
					.map((k) => `${data[k]} ${k.replace("skipped_unresolved", "not imported")}`)
					.join(", ");
				frappe.show_alert(
					{
						message: __("Amazon orders synced: {0}", [parts || __("nothing new")]),
						indicator: "green",
					},
					10
				);
				// Orders still import when a SKU isn't linked — the lines book against
				// the placeholder Item — so this is advisory, not a failure.
				const unmapped = data.unmapped_skus || [];
				if (unmapped.length) {
					frappe.msgprint({
						title: __("Imported with unmapped SKUs"),
						indicator: "orange",
						message: __(
							"These SKUs aren't linked to an Item, so their order lines booked against the placeholder Item:<br><br><b>{0}</b><br><br>Set the <b>Product</b> field on each matching Amazon Listing so future orders map correctly. Orders already imported are not re-pointed automatically.",
							[unmapped.map((s) => frappe.utils.escape_html(s)).join("<br>")]
						),
					});
				}
				// The watermark stopped short because some orders didn't land. Say so
				// loudly — the alternative failure mode (silently stepping over them)
				// is exactly what this guards against.
				if (data.watermark_held_at) {
					frappe.msgprint({
						title: __("Sync position held back"),
						indicator: "orange",
						message: __(
							"Some orders were returned by Amazon but not imported, so the sync position was held at <b>{0}</b> instead of moving to the end of the window. They'll be retried on the next run.<br><br>Affected orders: <b>{1}</b><br><br>See the Error Log for why. Until they import or are resolved, each run re-reads from that point.",
							[
								frappe.utils.escape_html(data.watermark_held_at),
								(data.retry_orders || [])
									.map((i) => frappe.utils.escape_html(i))
									.join(", ") || __("see Error Log"),
							]
						),
					});
				}
				if (data.skipped_unresolved) {
					frappe.msgprint(
						__(
							"{0} order(s) could not be imported at all — the placeholder Item could not be created. Set 'Unmapped SKU Item' under Orders on the Amazon Connection.",
							[data.skipped_unresolved]
						)
					);
				}
				listview.refresh();
			} else {
				frappe.show_alert(
					{
						message: __("Amazon order sync failed: {0}", [
							frappe.utils.escape_html((data && data.error) || "unknown error"),
						]),
						indicator: "red",
					},
					10
				);
			}
		});

		listview.page.add_inner_button(
			__("Sync Orders from Amazon"),
			() => {
				frappe.call({
					method: "alaiy_os_connector_amazon_sp_api.api.sync_orders",
					callback: () => {
						frappe.show_alert({
							message: __("Order sync started — you'll be notified when it completes."),
							indicator: "blue",
						});
					},
				});
			},
			__("Amazon")
		);

		listview.page.add_inner_button(
			__("Backfill Orders from Amazon"),
			() => {
				const dialog = new frappe.ui.Dialog({
					title: __("Backfill Amazon Orders"),
					fields: [
						{
							fieldname: "date_from",
							label: __("Updated After"),
							fieldtype: "Datetime",
							reqd: 1,
						},
						{
							fieldname: "date_to",
							label: __("Updated Before"),
							fieldtype: "Datetime",
							description: __("Leave blank to run up to now."),
						},
						{
							fieldtype: "HTML",
							options: `<p class="text-muted small">${__(
								"Re-reads the range in chunks without moving the scheduled sync's watermark. Already-imported orders are left as they are."
							)}</p>`,
						},
					],
					primary_action_label: __("Start Backfill"),
					primary_action(values) {
						dialog.hide();
						frappe.call({
							method: "alaiy_os_connector_amazon_sp_api.api.backfill_orders",
							args: values,
							callback: () => {
								frappe.show_alert({
									message: __("Backfill started — you'll be notified when it completes."),
									indicator: "blue",
								});
							},
						});
					},
				});
				dialog.show();
			},
			__("Amazon")
		);
	};
})();
