// Copyright (c) 2026, Alaiy and contributors
// For license information, please see license.txt
//
// Amazon Product Listing form: publish to Amazon, create the catalog entry for a
// product Amazon has never listed, end the listing, re-sync, and (for new rows) a
// catalog-search -> create-offer flow.
//
// "Publish" is one button for what used to be two decisions. It previews first
// (api.preview_publish), and the preview says which the row needs: Amazon has no
// listing for the SKU, so publishing creates the offer — or it has one, and
// publishing submits the difference. The operator sees which before anything is
// submitted, and the same endpoint pair backs the bulk publish on the list view.

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
			// New row: help the operator find an ASIN, then publish an offer. This
			// one cannot go through api.publish_listing — that publishes a register
			// row, and this document has never been saved.
			frm.add_custom_button(__("Search Catalog"), () => amazon_catalog_search(frm));
			frm.add_custom_button(__("Publish Offer"), () => amazon_publish_new(frm)).addClass(
				"btn-primary"
			);
			return;
		}

		frm.add_custom_button(
			__("Publish to Amazon"),
			() => amazon_publish_row(frm),
			__("Amazon")
		).addClass("btn-primary");

		// Only for a row Amazon has no catalog entry for, and never as the primary
		// action. Publishing puts an offer on an ASIN that exists; this asks Amazon
		// to mint one, which is public and has no undo — so it appears only where
		// it is the row's only route to Amazon, and says so before it submits.
		if (!frm.doc.asin) {
			frm.add_custom_button(
				__("Create on Amazon"),
				() => amazon_create_asin(frm),
				__("Amazon")
			);
		}

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

function amazon_publish_row(frm) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.preview_publish",
		args: {
			sku: frm.doc.sku,
			marketplace: frm.doc.marketplace,
			desired: JSON.stringify(amazon_desired(frm)),
		},
		freeze: true,
		freeze_message: __("Checking what Amazon has…"),
		callback: (r) => amazon_review_publish(frm, r.message || {}),
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
function amazon_review_publish(frm, comparison) {
	const changed = comparison.changed || [];
	const changes = comparison.changes || {};
	const remote = comparison.remote || {};

	// Anything Amazon requires and the row has not got. Nothing can be submitted
	// until these are fixed, so they are the whole message rather than a warning
	// on a dialog that would only fail.
	const blockers = comparison.blockers || [];
	if (blockers.length) {
		frappe.msgprint({
			title: __("Not ready to publish"),
			indicator: "red",
			message: `<ul>${blockers
				.map((b) => `<li>${frappe.utils.escape_html(b)}</li>`)
				.join("")}</ul>`,
		});
		return;
	}

	// Amazon has no listing for this SKU: publishing creates the offer, and there
	// is no "before" to diff against.
	if (comparison.exists === false) {
		amazon_review_create(frm, comparison);
		return;
	}

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
		title: __("Publish to Amazon"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "diff" }],
		primary_action_label: __("Publish {0} Field(s)", [changed.length]),
		primary_action() {
			d.hide();
			amazon_submit_publish(frm);
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

// Creating the catalog entry, as opposed to publishing an offer against one.
//
// Preview first, always: what Amazon requires is a property of the product type
// and is read from its schema, so the blockers are the substance of the answer
// for most rows and the only way to learn them without submitting. The confirm
// step is not ceremony either — a catalog entry becomes a public ASIN other
// sellers can list against, and there is nothing to press afterwards to undo it.
function amazon_create_asin(frm) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.preview_asin_creation",
		args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace },
		freeze: true,
		freeze_message: __("Checking what Amazon requires for this product type…"),
		callback: (r) => amazon_review_asin_creation(frm, r.message || {}),
	});
}

function amazon_review_asin_creation(frm, preview) {
	const blockers = preview.blockers || [];
	if (blockers.length) {
		frappe.msgprint({
			title: __("Not ready to create"),
			indicator: "red",
			message: `<ul>${blockers
				.map((b) => `<li>${frappe.utils.escape_html(b)}</li>`)
				.join("")}</ul>`,
		});
		return;
	}

	const required = preview.required || [];
	const attributes = Object.keys(preview.attributes || {}).sort();
	const warnings = (preview.warnings || [])
		.map((w) => `<p class="text-warning small">${frappe.utils.escape_html(w)}</p>`)
		.join("");

	const d = new frappe.ui.Dialog({
		title: __("Create on Amazon"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "summary" }],
		primary_action_label: __("Create on Amazon"),
		primary_action() {
			d.hide();
			frappe.call({
				method: "alaiy_os_connector_amazon_sp_api.api.create_asin",
				args: { sku: frm.doc.sku, marketplace: frm.doc.marketplace },
				freeze: true,
				freeze_message: __("Submitting the product to Amazon…"),
				callback: (r) => {
					const result = r.message || {};
					frappe.show_alert({
						message: result.submission_id
							? __("Submitted as {0}. The ASIN appears here once Amazon has created it.", [
									frappe.utils.escape_html(result.submission_id),
								])
							: __("Submitted. The ASIN appears here once Amazon has created it."),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		},
	});
	d.fields_dict.summary.$wrapper.html(`
		<p>${__(
			"This asks Amazon to add the product to its catalog and give it a <b>new ASIN</b>. It is not an offer against an existing one, and it cannot be undone."
		)}</p>
		<p class="text-muted">${__("Submitted as product type")} <b>${frappe.utils.escape_html(
			preview.product_type || ""
		)}</b>. ${__(
			"Amazon creates the entry asynchronously — this row stays pending until the entry exists."
		)}</p>
		<p><b>${__("Attributes being submitted")}</b></p>
		<p>${attributes
			.map(
				(name) =>
					`<code>${frappe.utils.escape_html(name)}${required.includes(name) ? " *" : ""}</code>`
			)
			.join(" ")}</p>
		<p class="text-muted small">${__(
			"* required by this product type. Anything Amazon requires that no listing field holds goes in Extra Attributes on this row."
		)}</p>
		${warnings}`);
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

// What an offer-only create can carry, and what it cannot: the offer is this
// seller's, the product content belongs to whoever owns the ASIN. So a create
// sends price, quantity and condition, and any content on the row is reported as
// still to come — it goes on the next publish, through the update path, where it
// is diffed and can be visibly rejected.
function amazon_review_create(frm, preview) {
	const warnings = preview.warnings || [];
	const pending = preview.content_pending || [];
	const rows = [
		[__("ASIN"), frm.doc.asin],
		[__("Product Type"), frm.doc.product_type],
		[__("Price"), frm.doc.price],
		[__("Quantity"), frm.doc.quantity],
		[__("Condition"), frm.doc.condition],
	]
		.map(
			([label, value]) => `<tr><td style="white-space:nowrap;"><b>${label}</b></td>
				<td>${amazon_format_value(null, value)}</td></tr>`
		)
		.join("");

	const d = new frappe.ui.Dialog({
		title: __("Publish New Offer to Amazon"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "summary" }],
		primary_action_label: __("Publish Offer"),
		primary_action() {
			d.hide();
			amazon_submit_publish(frm);
		},
	});
	d.fields_dict.summary.$wrapper.html(`
		<p class="text-muted">${__(
			"Amazon has no listing for this SKU on this marketplace, so publishing creates the offer."
		)}</p>
		<table class="table table-bordered">
			<thead><tr><th style="width:30%;">${__("Field")}</th><th>${__("Will Be Published")}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>
		${
			pending.length
				? `<p class="text-warning small">${__(
						"A new offer carries the offer only. {0} stays on this row and reaches Amazon on the next publish, and only where this seller owns the ASIN's content.",
						[pending.map((f) => __(AMAZON_PUSH_LABELS[f] || f)).join(", ")]
					)}</p>`
				: ""
		}
		${warnings
			.map((w) => `<p class="text-warning small">${frappe.utils.escape_html(w)}</p>`)
			.join("")}`);
	d.show();
}

// The submit re-reads Amazon rather than replaying the previewed diff, which is
// deliberate: the preview can be minutes old by the time it is agreed to, and
// what should reach Amazon is what Amazon still lacks. It is also the one call
// that stamps the row's publish outcome, the same way the bulk publish does.
function amazon_submit_publish(frm) {
	frappe.call({
		method: "alaiy_os_connector_amazon_sp_api.api.publish_listing",
		args: {
			sku: frm.doc.sku,
			marketplace: frm.doc.marketplace,
			desired: JSON.stringify(amazon_desired(frm)),
		},
		freeze: true,
		freeze_message: __("Publishing to Amazon…"),
		callback: (r) => {
			const result = r.message || {};
			const status = result.listing_status;
			if (result.action === "unchanged") {
				frappe.show_alert({
					message: __("Amazon already had every value on this form."),
					indicator: "blue",
				});
			} else {
				const ok = status === "active" || status === "pending";
				frappe.show_alert({
					message:
						result.action === "created"
							? __("Offer created on Amazon (processing). Sync from Amazon to confirm.")
							: status === "pending"
								? __("Update submitted to Amazon (processing). Sync from Amazon to confirm.")
								: __("Listing status: {0}", [status]),
					indicator: ok ? "green" : "orange",
				});
			}
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

function amazon_publish_new(frm) {
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
