// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Product Listing form: push updates to Amazon, end the listing, re-sync, and
// (for new rows) a catalog-search -> create-offer flow.

frappe.ui.form.on("Amazon Product Listing", {
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

		// A parent row reaches its children through the Connections tab
		// (links -> parent_listing). A *child* has no such route to its siblings,
		// so give it one. The family ASIN is the parent's on a child row, and the
		// row's own ASIN when the row *is* the parent.
		const family_asin = frm.doc.parent_asin || (frm.doc.is_variation_parent ? frm.doc.asin : null);
		if (family_asin) {
			frm.add_custom_button(
				__("Variation Family"),
				() => amazon_show_family(frm, family_asin),
				__("Amazon")
			);
		}
	},
});

function amazon_show_family(frm, parent_asin) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.variation_family",
		args: { parent_asin: parent_asin, marketplace: frm.doc.marketplace },
		freeze: true,
		callback: (r) => {
			const data = r.message || {};
			const rows = (data.children || [])
				.map((c) => {
					const here = c.sku === frm.doc.sku;
					const link = `/app/amazon-product-listing/${encodeURIComponent(c.sku)}`;
					return `<tr${here ? ' style="font-weight:600;"' : ""}>
						<td><a href="${link}">${frappe.utils.escape_html(c.sku)}</a></td>
						<td>${frappe.utils.escape_html(c.title || "")}</td>
						<td>${frappe.utils.escape_html(c.asin || "")}</td>
						<td>${frappe.utils.escape_html(c.listing_status || "")}</td>
						<td style="text-align:right;">${c.quantity == null ? "" : c.quantity}</td>
					</tr>`;
				})
				.join("");

			// The parent is listed separately because the seller often has no SKU
			// for it at all — it is not a buyable offer.
			const parent_line = data.parent_sku
				? __("Parent SKU: {0}", [frappe.utils.escape_html(data.parent_sku)])
				: __("This seller has no SKU for the parent ASIN (parents are not buyable).");

			frappe.msgprint({
				title: __("Variation Family"),
				indicator: "blue",
				message: `
					<p class="text-muted">
						${__("Parent ASIN")}: <b>${frappe.utils.escape_html(parent_asin)}</b><br>
						${data.variation_theme ? `${__("Varies by")}: <b>${frappe.utils.escape_html(data.variation_theme)}</b><br>` : ""}
						${parent_line}
					</p>
					${
						rows
							? `<table class="table table-bordered" style="margin-bottom:0;">
									<thead><tr>
										<th>${__("SKU")}</th><th>${__("Title")}</th><th>${__("ASIN")}</th>
										<th>${__("Status")}</th><th style="text-align:right;">${__("Qty")}</th>
									</tr></thead>
									<tbody>${rows}</tbody>
								</table>`
							: `<p>${__("No sibling SKUs in the register yet — run Sync All from Amazon to populate parentage.")}</p>`
					}`,
			});
		},
	});
}

// The state the operator wants live on Amazon: the form as it stands, unsaved
// edits included. What makes a field worth pushing is Amazon not having it, so
// the comparison happens server-side against the live listing — see
// spapi.listings.remote_snapshot for why the register row cannot be that
// baseline.
function amazon_desired(frm) {
	return {
		title: frm.doc.title,
		price: frm.doc.price,
		quantity: frm.doc.quantity,
		condition: frm.doc.condition,
		description: frm.doc.description,
		bullet_points: (frm.doc.bullet_points || []).map((r) => r.bullet).filter(Boolean),
		keywords: (frm.doc.keywords || []).map((r) => r.keyword).filter(Boolean),
		images: (frm.doc.images || [])
			.filter((r) => r.image_url)
			.map((r) => ({ url: r.image_url, is_main: !!r.is_main })),
	};
}

function amazon_push_update(frm) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.compare_listing",
		args: {
			sku: frm.doc.sku,
			marketplace: frm.doc.marketplace,
			desired: JSON.stringify(amazon_desired(frm)),
		},
		freeze: true,
		freeze_message: __("Checking what Amazon has…"),
		callback: (r) => amazon_review_push(frm, r.message || {}),
	});
}

const AMAZON_PUSH_LABELS = {
	title: "Title",
	price: "Price",
	quantity: "Quantity",
	condition: "Condition",
	description: "Description",
	bullet_points: "Bullet Points",
	keywords: "Keywords",
	images: "Images",
};

// Amazon's value vs the one about to replace it, per field. The operator is the
// only one who can tell an intended edit from a drift the register introduced,
// so nothing is submitted until they have seen both sides.
function amazon_review_push(frm, comparison) {
	const changed = comparison.changed || [];
	const changes = comparison.changes || {};
	const remote = comparison.remote || {};

	if (!changed.length) {
		frappe.msgprint({
			title: __("Already in sync"),
			indicator: "green",
			message: __("Every field this row can push already matches Amazon."),
		});
		return;
	}

	const rows = changed
		.map(
			(field) => `<tr>
				<td style="white-space:nowrap;"><b>${__(AMAZON_PUSH_LABELS[field] || field)}</b></td>
				<td class="text-muted">${amazon_format_value(field, remote[field])}</td>
				<td>${amazon_format_value(field, changes[field])}</td>
			</tr>`
		)
		.join("");

	// Content edits force Amazon to validate the full product-type schema, which
	// often fails on offer-only listings.
	const changedContent = comparison.content_changed || [];
	const warning = changedContent.length
		? `<p class="text-warning small">${__(
				"This push changes product content ({0}). Amazon validates the full product-type schema for content changes, which can be rejected if required attributes are missing.",
				[changedContent.map((f) => __(AMAZON_PUSH_LABELS[f] || f)).join(", ")]
			)}</p>`
		: "";

	const d = new frappe.ui.Dialog({
		title: __("Push Update to Amazon"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "diff" }],
		primary_action_label: __("Push {0} Field(s)", [changed.length]),
		primary_action() {
			d.hide();
			amazon_send_update(frm, changes);
		},
	});
	d.fields_dict.diff.$wrapper.html(`
		<p class="text-muted">${__("Only the fields below differ from the live listing; nothing else is submitted.")}</p>
		<table class="table table-bordered">
			<thead><tr>
				<th style="width:18%;">${__("Field")}</th>
				<th style="width:41%;">${__("On Amazon")}</th>
				<th style="width:41%;">${__("Will Become")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table>
		${warning}`);
	d.show();
}

function amazon_format_value(field, value) {
	if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) {
		return `<i class="text-muted">${__("not set")}</i>`;
	}
	if (field === "images") {
		return value
			.map((im) => `${im.is_main ? "★ " : ""}${frappe.utils.escape_html(im.url || "")}`)
			.join("<br>");
	}
	if (Array.isArray(value)) {
		return value.map((v) => frappe.utils.escape_html(String(v))).join("<br>");
	}
	const text = String(value);
	// Descriptions run to thousands of characters; the diff only has to show
	// enough of one to recognise it.
	const shown = text.length > 300 ? `${text.slice(0, 300)}…` : text;
	return frappe.utils.escape_html(shown);
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
	if (!frm.doc.product_type) {
		frappe.msgprint(__("Product Type is required. Use Search Catalog to pick an ASIN first."));
		return;
	}
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.create_listing",
		args: {
			sku: frm.doc.sku,
			asin: frm.doc.asin,
			product_type: frm.doc.product_type,
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
			frappe.set_route("Form", "Amazon Product Listing", r.message.sku);
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
		if (it.product_type) frm.set_value("product_type", it.product_type);
		// The search results already carry the ASIN's brand, so a row published
		// from here shows it without waiting for the first sync to reach the catalog.
		if (it.brand) frm.set_value("brand", it.brand);
		if (dialog.get_value("marketplace")) frm.set_value("marketplace", dialog.get_value("marketplace"));
		dialog.hide();
		frappe.show_alert({ message: __("Selected {0}", [it.asin]), indicator: "green" });
	});
}
