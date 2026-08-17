import { formatCurrency } from "@alaiy-os/utils";

/**
 * Presentation helpers shared by every Amazon screen.
 *
 * All of them take "the field is missing" as an ordinary input, because it is:
 * a variation parent carries no price or quantity, a reconcile-only row has no
 * parentage, and an offer-only listing has no product type. An em dash is the
 * answer in each case — never a zero, which would read as a real value.
 */

const EMPTY = "—";

/**
 * A text field's value, or `fallback` when there isn't one.
 *
 * Blank counts as absent, which is the whole reason this exists rather than a
 * `??`: Frappe stores an unset Data field as `""` about as often as `null`, and
 * both mean the same thing on screen. Every "Untitled listing" and every em dash
 * in these screens comes through here so that they agree on it.
 */
export function textOr(value: string | null | undefined, fallback: string = EMPTY): string {
  const text = (value ?? "").trim();
  return text === "" ? fallback : text;
}

/**
 * A listing's price in its own currency.
 *
 * The register stores the marketplace's currency per row rather than the
 * company's, so this deliberately does *not* fall back to the session default
 * (`useCompany`): a JP listing priced in JPY shown with an Indian company's ₹ is
 * worse than showing no symbol at all.
 */
export function amazonMoney(amount: number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined) return EMPTY;
  const value = Number(amount);
  if (!Number.isFinite(value)) return EMPTY;

  const code = (currency ?? "").trim().toUpperCase();
  if (!code) return plainNumber(value);
  try {
    return formatCurrency(value, { currency: code });
  } catch {
    // A code Intl rejects (a typo upstream, or a currency it doesn't know) is
    // not worth a RangeError taking the page down.
    return `${plainNumber(value)} ${code}`;
  }
}

function plainNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

export function amazonCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : EMPTY;
}

/**
 * A Frappe datetime, in the reader's own timezone.
 *
 * Frappe sends naive strings in the *site's* timezone ("2026-08-17 09:30:00"),
 * which `new Date()` reads as local time. That is wrong whenever the two differ,
 * but it is wrong by a fixed offset and in the same direction everywhere in the
 * OS; inventing a correction here would make these screens disagree with the
 * rest of it. So: parsed as the base does, and never used for arithmetic.
 */
export function amazonDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function amazonDate(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** "new_open_box" → "New open box". Amazon's condition codes, made readable. */
export function conditionLabel(condition: string | null | undefined): string {
  const raw = (condition ?? "").trim();
  if (!raw) return EMPTY;
  const words = raw.split("_").filter(Boolean);
  // Amazon repeats the grade in the code ("new_new", "refurbished_refurbished");
  // saying it twice in the UI is just noise.
  const deduped = words.filter((word, index) => word !== words[index - 1]);
  const text = deduped.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "customer_service" → "Customer service", for the health metric sections. */
export function sectionLabel(section: string | null | undefined): string {
  const raw = (section ?? "").trim();
  if (!raw) return "Other";
  const text = raw.split("_").filter(Boolean).join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * A health metric's value against its target.
 *
 * Every metric Amazon reports here is a percentage, and which side of the target
 * is good varies per metric — hence `higher_is_better` travelling with it.
 */
export function metricValue(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  const number = Number(value);
  if (!Number.isFinite(number)) return EMPTY;
  return `${number.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

export function targetLabel(target: number | null | undefined, higherIsBetter: 0 | 1 | undefined): string {
  if (target === null || target === undefined) return EMPTY;
  return `${higherIsBetter ? "≥" : "≤"} ${metricValue(target)}`;
}
