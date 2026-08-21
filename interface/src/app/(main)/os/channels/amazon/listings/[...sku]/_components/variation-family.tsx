"use client";

import { useEffect, useState } from "react";

import Link from "next/link";

import { Badge } from "@alaiy-os/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@alaiy-os/ui/table";
import { cn } from "@alaiy-os/utils";

import { fetchVariationFamily } from "@/lib/amazon/api";
import { amazonCount, amazonMoney, textOr } from "@/lib/amazon/format";
import type { AmazonVariationFamily } from "@/lib/amazon/types";

import { ListingStatusBadge } from "@/components/amazon/status-badge";

/**
 * The rest of this listing's variation family, if it is in one.
 *
 * Local and free — the parentage was recorded by a sync, so this reads rows rather
 * than calling Amazon. Which is also its limitation: a row that only ever came
 * through a reconcile has no family at all, because the Merchant Listings report
 * carries no parent/child columns. An empty answer there means "not synced", not
 * "standalone", and the copy says so rather than implying the SKU has no siblings.
 */
export function VariationFamilyCard({
  parentAsin,
  marketplace,
  currentSku,
  currency,
}: {
  parentAsin: string;
  marketplace?: string;
  currentSku: string;
  currency?: string | null;
}) {
  const [family, setFamily] = useState<AmazonVariationFamily | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    fetchVariationFamily(parentAsin, marketplace)
      .then((result) => {
        if (!cancelled) setFamily(result);
      })
      .catch(() => {
        // A family is context, never the reason to open this page — a failed read
        // leaves the card saying nothing rather than shouting at the operator.
        if (!cancelled) setFamily(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [parentAsin, marketplace]);

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b px-4 py-3">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          Variation family
          {family?.variation_theme && <Badge variant="outline">{family.variation_theme}</Badge>}
        </CardTitle>
        <CardDescription>
          Parent ASIN <code className="text-xs">{parentAsin}</code>
          {family?.parent_sku ? (
            <>
              {" · parent SKU "}
              <Link
                href={`/os/channels/amazon/listings/${encodeURIComponent(family.parent_sku)}`}
                className="hover:underline"
              >
                {family.parent_sku}
              </Link>
            </>
          ) : (
            " · this seller lists no SKU for the parent, which is normal — a parent is not a buyable offer"
          )}
        </CardDescription>
      </CardHeader>

      <CardContent className="px-0">
        <FamilyBody loading={loading} family={family} currentSku={currentSku} currency={currency} />
      </CardContent>
    </Card>
  );
}

function FamilyBody({
  loading,
  family,
  currentSku,
  currency,
}: {
  loading: boolean;
  family: AmazonVariationFamily | null;
  currentSku: string;
  currency?: string | null;
}) {
  if (loading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-4/5" />
      </div>
    );
  }

  if (!family || family.children.length === 0) {
    return (
      <p className="p-4 text-muted-foreground text-sm">
        No sibling SKUs recorded. Parentage is only known for rows that have been through a listing sync.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>SKU</TableHead>
            <TableHead>Title</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Qty</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {family.children.map((child) => {
            const isCurrent = child.sku === currentSku;
            return (
              <TableRow key={child.sku} className={cn(isCurrent && "bg-muted/40")}>
                <TableCell className="font-medium text-sm">
                  {isCurrent ? (
                    <span className="flex items-center gap-2">
                      {child.sku}
                      <Badge variant="secondary">this SKU</Badge>
                    </span>
                  ) : (
                    <Link
                      href={`/os/channels/amazon/listings/${encodeURIComponent(child.sku)}`}
                      className="hover:underline"
                    >
                      {child.sku}
                    </Link>
                  )}
                </TableCell>
                <TableCell className="max-w-xs truncate text-sm" title={child.title ?? undefined}>
                  {textOr(child.title)}
                </TableCell>
                <TableCell>
                  <ListingStatusBadge status={child.listing_status} />
                </TableCell>
                <TableCell className="text-right text-sm tabular-nums">{amazonMoney(child.price, currency)}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">{amazonCount(child.quantity)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
