// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Listing form: push updates to Amazon, end the listing, re-sync, and
// (for new rows) a catalog-search -> create-offer flow.

frappe.ui.form.on("Amazon Listing", {
	refresh(frm) {
		if (frm.doc.listing_status) {
			const color = {
				active: "green",
				inactive: "gray",
				suppressed: "red",
				incomplete: "orange",
				pending: "blue",
			}[frm.doc.listing_status];
			frm.dashboard.set_headline_alert(
				`<span class="indicator ${color}">${frappe.utils.escape_html(
					frm.doc.listing_status
				)}</span>`
			);
		}

		if (frm.is_new()) {
			// New row: help the operator find an ASIN, then publish an offer.
			frm.add_custom_button(__("Search Catalog"), () => amazon_catalog_search(frm));
			frm.add_custom_button(__("Publish Offer"), () => amazon_publish(frm)).addClass(
				"btn-primary"
			);
			return;
		}

		frm.add_custom_button(
			__("Push Update to Amazon"),
			() => amazon_push_update(frm),
			__("Amazon")
		).addClass("btn-primary");

		frm.add_custom_button(__("Sync from Amazon"), () => amazon_sync(frm), __("Amazon"));

		frm.add_custom_button(
			__("End Listing"),
			() => {
				frappe.confirm(
					__("End this listing on Amazon? The row will be kept as inactive."),
					() => {
						frappe.call({
							method: "alaiy_os_connector_amazon_sp_api.api.delete_listing",
							args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace },
							freeze: true,
							freeze_message: __("Ending listing…"),
							callback: () => frm.reload_doc(),
						});
					}
				);
			},
			__("Amazon")
		);
	},
});

function amazon_push_update(frm) {
	const changes = {
		price: frm.doc.price,
		quantity: frm.doc.quantity,
		condition: frm.doc.condition,
	};
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.update_listing",
		args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace, changes: JSON.stringify(changes) },
		freeze: true,
		freeze_message: __("Pushing update to Amazon…"),
		callback: (r) => {
			frappe.show_alert({
				message: __("Listing status: {0}", [r.message.listing_status]),
				indicator: r.message.listing_status === "active" ? "green" : "orange",
			});
			frm.reload_doc();
		},
	});
}

function amazon_sync(frm) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.sync_listing",
		args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace },
		freeze: true,
		freeze_message: __("Syncing from Amazon…"),
		callback: () => frm.reload_doc(),
	});
}

function amazon_publish(frm) {
	if (!frm.doc.sku || !frm.doc.asin || !frm.doc.marketplace) {
		frappe.msgprint(__("SKU, ASIN and Marketplace are required to publish an offer."));
		return;
	}
	const product_type = frm.doc.__amazon_product_type || frm.doc.product_type;
	if (!product_type) {
		frappe.msgprint(__("Product Type is required. Use Search Catalog to pick an ASIN first."));
		return;
	}
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.create_listing",
		args: {
			sku: frm.doc.sku,
			asin: frm.doc.asin,
			product_type: product_type,
			price: frm.doc.price,
			quantity: frm.doc.quantity,
			condition: frm.doc.condition,
			marketplace: frm.doc.marketplace,
			fulfillment_channel: frm.doc.fulfillment_channel,
			product: frm.doc.product,
		},
		freeze: true,
		freeze_message: __("Publishing offer…"),
		callback: (r) => {
			frappe.set_route("Form", "Amazon Listing", r.message.sku);
		},
	});
}

function amazon_catalog_search(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Search Amazon Catalog"),
		fields: [
			{ fieldname: "query", fieldtype: "Data", label: __("Keywords or ASIN"), reqd: 1 },
			{ fieldname: "marketplace", fieldtype: "Link", label: __("Marketplace"), options: "Amazon Marketplace", default: frm.doc.marketplace },
			{ fieldname: "results_html", fieldtype: "HTML" },
		],
		primary_action_label: __("Search"),
		primary_action(values) {
			frappe.call({
				method: "alaiy_os_connector_amazon_sp_api.api.search_catalog",
				args: { query: values.query, marketplace: values.marketplace },
				freeze: true,
				callback: (r) => render_catalog_results(frm, d, r.message || []),
			});
		},
	});
	d.show();
}

function render_catalog_results(frm, dialog, items) {
	if (!items.length) {
		dialog.fields_dict.results_html.$wrapper.html(`<p>${__("No matches.")}</p>`);
		return;
	}
	const rows = items
		.map(
			(it, i) => `
		<div class="amazon-cat-row" data-i="${i}" style="display:flex;gap:12px;align-items:center;padding:8px;border-bottom:1px solid var(--border-color);cursor:pointer;">
			<img src="${frappe.utils.escape_html(it.image_url || "")}" style="width:48px;height:48px;object-fit:contain;" onerror="this.style.visibility='hidden'"/>
			<div>
				<div><b>${frappe.utils.escape_html(it.title || it.asin)}</b></div>
				<div class="text-muted small">${frappe.utils.escape_html(it.asin)} · ${frappe.utils.escape_html(it.brand || "")} · ${frappe.utils.escape_html(it.product_type || "")}</div>
			</div>
		</div>`
		)
		.join("");
	dialog.fields_dict.results_html.$wrapper.html(rows);
	dialog.fields_dict.results_html.$wrapper.find(".amazon-cat-row").on("click", function () {
		const it = items[$(this).data("i")];
		frm.set_value("asin", it.asin);
		if (!frm.doc.title) frm.set_value("title", it.title);
		if (it.image_url && !frm.doc.image_urls) frm.set_value("image_urls", it.image_url);
		// Product Type isn't a stored field; stash it for Publish Offer.
		frm.doc.__amazon_product_type = it.product_type;
		if (dialog.get_value("marketplace")) frm.set_value("marketplace", dialog.get_value("marketplace"));
		dialog.hide();
		frappe.show_alert({ message: __("Selected {0}", [it.asin]), indicator: "green" });
	});
}
