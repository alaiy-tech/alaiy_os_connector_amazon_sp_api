// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Connect / Disconnect / Ping / Sync buttons on the Amazon Connection form,
// under the shared AlaiyOS connector card.

frappe.ui.form.on("Amazon Connection", {
	refresh(frm) {
		frm.page.set_title(__("Amazon Settings"));

		// Icon + name + live status pill, identical to every other connector's
		// settings page (see Shopify Connector Settings). The card reads
		// OS Connector Registry.connection_status, which this app's controller
		// mirrors from last_status on every set_status() — so an OAuth connect
		// or a scheduled ping moves the pill, not just a Test click.
		//
		// Guarded: the connector is installable without alaiy_os (setup/install
		// skips registration when the core's DocTypes are absent), and the card
		// ships with the core.
		if (window.alaiy_os && alaiy_os.connector_card) {
			alaiy_os.connector_card.mount(frm, "amazon_sp_api");
		}

		// Branch on whether a refresh token is actually stored (is_connected),
		// not just status === "connected": an authorized-but-erroring connection
		// still has a token and should offer Test/Disconnect, not just Connect.
		const hasToken = !!frm.doc.refresh_token;

		// Any headline here is layered on top of the card, never a second copy
		// of it: the card says *what* the status is, a headline says what to do
		// about it. Only the states with an operator next step get one.
		if (!hasToken) {
			// Connect requires app credentials in site_config. Check first so the
			// operator sees exactly what's missing instead of a mid-flow error.
			frappe.call({
				method: "alaiy_os_connector_amazon_sp_api.api.get_config_status",
				callback: (r) => {
					const cfg = r.message || {};
					if (cfg.ready) {
						frm.dashboard.set_headline_alert(
							`<span class="indicator orange">${__(
								"Not authorized with Amazon yet — use Connect Amazon Account."
							)}</span>`
						);
						frm.add_custom_button(__("Connect Amazon Account"), () => {
							frappe.call({
								method: "alaiy_os_connector_amazon_sp_api.api.get_connect_url",
								callback: (res) => {
									if (res.message && res.message.url) {
										window.location.href = res.message.url;
									}
								},
							});
						}).addClass("btn-primary");
					} else {
						const missing = (cfg.keys || [])
							.filter((k) => k.required && !k.is_set)
							.map((k) => k.label);
						frm.dashboard.add_comment(
							__(
								"App credentials missing in site_config.json: {0}. Set them with <code>bench set-config &lt;key&gt; &lt;value&gt;</code>, then reload.",
								[frappe.utils.escape_html(missing.join(", "))]
							),
							"yellow",
							true
						);
					}
				},
			});
		} else {
			// Authorized: the card's pill already reads Connected/Failed, so the
			// only thing worth adding is *why* a failure happened.
			if (frm.doc.last_status === "error" && frm.doc.last_status_message) {
				frm.dashboard.set_headline_alert(
					`<span class="indicator red">${frappe.utils.escape_html(
						frm.doc.last_status_message
					)}</span>`
				);
			}

			frm.add_custom_button(
				__("Disconnect"),
				() => {
					frappe.confirm(__("Disconnect the Amazon account?"), () => {
						frappe.call({
							method: "alaiy_os_connector_amazon_sp_api.api.disconnect",
							callback: () => frm.reload_doc(),
						});
					});
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Test Connection"),
				() => {
					// Straight to our own ping rather than core's test_connector
					// wrapper (which is what Shopify has to use): ping() already
					// mirrors the result onto the registry row, so the card's pill
					// updates either way, and going direct keeps the
					// not_configured/error distinction the wrapper flattens.
					frappe.call({
						method: "alaiy_os_connector_amazon_sp_api.api.ping",
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
						method: "alaiy_os_connector_amazon_sp_api.api.sync_health",
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
