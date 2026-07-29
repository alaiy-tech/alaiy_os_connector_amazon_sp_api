// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Listing form: push updates to Amazon, end the listing, re-sync, and
// (for new rows) a catalog-search -> create-offer flow.

frappe.ui.form.on("Amazon Listing", {
	refresh(frm) {
		// Snapshot the pushable fields as the baseline for "only send what changed".
		// Captured only when the form is clean (fresh load / after reload_doc), so a
		// user's unsaved edits are always diffed against the last Amazon-synced state.
		if (!frm.is_new() && !frm.is_dirty()) {
			frm.__amazon_baseline = amazon_snapshot(frm);
		}

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

// Normalised, comparable view of the fields we can push to Amazon. Child tables
// are flattened to strings so a simple !== catches adds/edits/removes/reorders.
function amazon_snapshot(frm) {
	return {
		title: frm.doc.title || "",
		price: flt(frm.doc.price),
		quantity: cint(frm.doc.quantity),
		condition: frm.doc.condition || "",
		description: frm.doc.description || "",
		bullet_points: (frm.doc.bullet_points || []).map((r) => r.bullet || "").join("\n"),
		keywords: (frm.doc.keywords || []).map((r) => r.keyword || "").join("\n"),
		images: (frm.doc.images || [])
			.map((r) => `${r.is_main ? 1 : 0}:${r.image_url || ""}`)
			.join("\n"),
	};
}

function amazon_push_update(frm) {
	const base = frm.__amazon_baseline || {};
	const now = amazon_snapshot(frm);
	const changes = {};

	// Offer + content scalars: include only when they differ from the baseline.
	if (now.title !== base.title) changes.title = frm.doc.title;
	if (now.price !== base.price) changes.price = frm.doc.price;
	if (now.quantity !== base.quantity) changes.quantity = frm.doc.quantity;
	if (now.condition !== base.condition) changes.condition = frm.doc.condition;
	if (now.description !== base.description) changes.description = frm.doc.description;
	if (now.bullet_points !== base.bullet_points) {
		changes.bullet_points = (frm.doc.bullet_points || []).map((r) => r.bullet).filter(Boolean);
	}
	if (now.keywords !== base.keywords) {
		changes.keywords = (frm.doc.keywords || []).map((r) => r.keyword).filter(Boolean);
	}
	if (now.images !== base.images) {
		changes.images = (frm.doc.images || [])
			.filter((r) => r.image_url)
			.map((r) => ({ url: r.image_url, is_main: !!r.is_main }));
	}

	const contentKeys = ["title", "description", "bullet_points", "keywords", "images"];
	const changedContent = contentKeys.filter((k) => k in changes);

	if (!Object.keys(changes).length) {
		frappe.msgprint(__("No changes to push. Edit a field, then push."));
		return;
	}

	const send = () =>
		amazon_send_update(frm, changes);

	// Content edits force Amazon to validate the full product-type schema, which
	// often fails on offer-only listings. Warn before sending those.
	if (changedContent.length) {
		frappe.confirm(
			__(
				"You're changing product content ({0}). Amazon validates the full product-type schema for content changes, which can be rejected if required attributes are missing. Send anyway?",
				[changedContent.join(", ")]
			),
			send
		);
	} else {
		send();
	}
}

function amazon_send_update(frm, changes) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.update_listing",
		args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace, changes: JSON.stringify(changes) },
		freeze: true,
		freeze_message: __("Pushing update to Amazon…"),
		callback: (r) => {
			const status = r.message.listing_status;
			const ok = status === "active" || status === "pending";
			frappe.show_alert({
				message:
					status === "pending"
						? __("Update submitted to Amazon (processing). Sync from Amazon to confirm.")
						: __("Listing status: {0}", [status]),
				indicator: ok ? "green" : "orange",
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
		if (it.image_url && !(frm.doc.images || []).length) {
			const img = frm.add_child("images");
			img.image_url = it.image_url;
			img.is_main = 1;
			frm.refresh_field("images");
		}
		// Product Type isn't a stored field; stash it for Publish Offer.
		frm.doc.__amazon_product_type = it.product_type;
		if (dialog.get_value("marketplace")) frm.set_value("marketplace", dialog.get_value("marketplace"));
		dialog.hide();
		frappe.show_alert({ message: __("Selected {0}", [it.asin]), indicator: "green" });
	});
}
