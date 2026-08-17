"use client";

import { useEffect, useState } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { toast } from "sonner";

import { amazonErrorMessage, fetchMarketplaces } from "@/lib/amazon/api";
import type { AmazonMarketplace } from "@/lib/amazon/types";

/** The sentinel for "no marketplace chosen" — Radix Select rejects a `""` value. */
export const ANY_MARKETPLACE = "__any__";

/**
 * A marketplace chooser over the `Amazon Marketplace` reference table.
 *
 * Not a generic Link field: that DocType is autonamed on `marketplace_id`, so its
 * `name` is `A21TJRUUN4KGV` and a name-matching search would never find the
 * "India" an operator types. The whole table is a few dozen rows, so it is
 * fetched once and shown as a plain select — the country to read, the id to send.
 *
 * `value` and `onChange` speak docnames (= marketplace ids), and `ANY_MARKETPLACE`
 * is the empty choice, which the filters treat as "all of them" and the settings
 * screen as "not set".
 */
export function MarketplacePicker({
  value,
  onChange,
  disabled = false,
  emptyLabel = "All marketplaces",
  className,
}: {
  value: string;
  onChange: (marketplace: string) => void;
  disabled?: boolean;
  emptyLabel?: string;
  className?: string;
}) {
  const { marketplaces, loading } = useMarketplaces();

  return (
    <Select value={value === "" ? ANY_MARKETPLACE : value} onValueChange={onChange} disabled={loading || disabled}>
      <SelectTrigger size="sm" className={className}>
        <SelectValue placeholder={loading ? "Loading..." : emptyLabel} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY_MARKETPLACE}>{emptyLabel}</SelectItem>
        {marketplaces.map((marketplace) => (
          <SelectItem key={marketplace.name} value={marketplace.name}>
            {marketplaceLabel(marketplace)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The table, fetched once per mount.
 *
 * Exported because three screens need the same rows for different jobs — the
 * filter above, the settings screen's Primary Marketplace, and resolving an id
 * back to a country name for display.
 */
export function useMarketplaces() {
  const [marketplaces, setMarketplaces] = useState<AmazonMarketplace[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    fetchMarketplaces()
      .then((rows) => {
        if (!cancelled) setMarketplaces(rows);
      })
      .catch((error) => {
        // A missing reference table is a broken install, not a user's problem —
        // but every picker on the page would silently sit empty without saying so.
        if (!cancelled) toast.error(amazonErrorMessage(error, "Could not load the Amazon marketplaces."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return { marketplaces, loading };
}

export function marketplaceLabel(marketplace: AmazonMarketplace): string {
  const country = (marketplace.country ?? "").trim();
  const domain = (marketplace.domain ?? "").trim();
  if (country && domain) return `${country} · ${domain}`;
  return country || domain || marketplace.name;
}

/** A marketplace id as something a person recognises, falling back to the id. */
export function marketplaceName(marketplaces: AmazonMarketplace[], id: string | null | undefined): string {
  if (!id) return "—";
  const match = marketplaces.find((marketplace) => marketplace.name === id);
  return match ? marketplaceLabel(match) : id;
}
