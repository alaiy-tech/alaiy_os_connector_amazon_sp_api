// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Connect / Disconnect / Ping / Sync buttons on the Amazon Connection form.

frappe.ui.form.on("Amazon Connection", {
	refresh(frm) {
		const status = frm.doc.last_status || "not_configured";
		const color = { connected: "green", error: "red", not_configured: "orange" }[status];
		frm.dashboard.set_headline_alert(
			`<span class="indicator ${color}">Amazon: ${frappe.utils.escape_html(status)}` +
				(frm.doc.last_status_message
					? ` — ${frappe.utils.escape_html(frm.doc.last_status_message)}`
					: "") +
				`</span>`
		);

		const connected = status === "connected";

		if (!connected) {
			frm.add_custom_button(__("Connect Amazon Account"), () => {
				frappe.call({
					method: "alaiy_os_connector_sp_api.api.get_connect_url",
					callback: (r) => {
						if (r.message && r.message.url) {
							window.location.href = r.message.url;
						}
					},
				});
			}).addClass("btn-primary");
		} else {
			frm.add_custom_button(
				__("Disconnect"),
				() => {
					frappe.confirm(__("Disconnect the Amazon account?"), () => {
						frappe.call({
							method: "alaiy_os_connector_sp_api.api.disconnect",
							callback: () => frm.reload_doc(),
						});
					});
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Test Connection"),
				() => {
					frappe.call({
						method: "alaiy_os_connector_sp_api.api.ping",
						freeze: true,
						freeze_message: __("Pinging Amazon…"),
						callback: (r) => {
							frappe.show_alert({
								message: __("Status: {0}", [r.message.status]),
								indicator: r.message.status === "connected" ? "green" : "red",
							});
							frm.reload_doc();
						},
					});
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Sync Account Health"),
				() => {
					frappe.call({
						method: "alaiy_os_connector_sp_api.api.sync_health",
						freeze: true,
						freeze_message: __("Syncing account health…"),
						callback: (r) => {
							frappe.msgprint({
								title: __("Health Sync Complete"),
								message: __("Overall status: {0}. Metrics synced: {1}.", [
									r.message.overall_status,
									r.message.metrics_synced,
								]),
								indicator: "green",
							});
						},
					});
				},
				__("Actions")
			);
		}
	},
});
