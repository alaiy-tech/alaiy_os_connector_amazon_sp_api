"use client";

import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Input } from "@alaiy-os/ui/input";
import { Label } from "@alaiy-os/ui/label";
import { CircleAlert, RefreshCw, ScrollText } from "lucide-react";

import { amazonCount, amazonDateTime } from "@/lib/amazon/format";
import type { AmazonOrdersSyncStatus } from "@/lib/amazon/types";

import { LinkField } from "./link-field";

export interface OrdersForm {
  customer: string;
  company: string;
  warehouse: string;
  priceList: string;
  fallbackItem: string;
  /** `datetime-local` shape ("2026-08-17T09:30"), converted on save. */
  syncFrom: string;
}

/**
 * Where synced Seller Central orders land.
 *
 * Orders have no DocType of their own — they are created as ERPNext Sales Orders
 * carrying the Amazon ids — so everything here is about which records they book
 * against. The Default Customer is load-bearing: buyer identity is a restricted
 * SP-API endpoint, so there is no real buyer to create a Customer from and every
 * order books against this one, staying traceable through its Amazon order id.
 *
 * The scheduled poll stays dormant until that customer is set, which is why an
 * empty one is called out rather than left as an empty field among five.
 */
export function OrderDefaultsCard({
  status,
  form,
  onChange,
  busy,
  syncing,
  onSyncNow,
  canSync,
}: {
  status: AmazonOrdersSyncStatus | null;
  form: OrdersForm;
  onChange: <K extends keyof OrdersForm>(key: K, value: OrdersForm[K]) => void;
  busy: boolean;
  syncing: boolean;
  onSyncNow: () => void;
  canSync: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Orders</CardTitle>
        <CardDescription>
          Amazon orders are imported as ordinary Sales Orders, idempotent on the Amazon order id. These are the records
          they book against.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {!form.customer && (
          <Alert>
            <CircleAlert />
            <AlertTitle>Order sync is dormant</AlertTitle>
            <AlertDescription>
              Set a Default Customer below and save. Until then the scheduled poll does nothing, deliberately — an order
              with nobody to book against would fail one at a time, every half hour.
            </AlertDescription>
          </Alert>
        )}

        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Default customer</Label>
            <LinkField
              doctype="Customer"
              value={form.customer}
              onChange={(value) => onChange("customer", value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">
              Every Amazon order books against this one. Amazon does not release buyer identity.
            </p>
          </div>

          <div className="space-y-2">
            <Label>Company</Label>
            <LinkField
              doctype="Company"
              value={form.company}
              onChange={(value) => onChange("company", value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">Blank uses the site's default company.</p>
          </div>

          <div className="space-y-2">
            <Label>Default warehouse</Label>
            <LinkField
              doctype="Warehouse"
              value={form.warehouse}
              onChange={(value) => onChange("warehouse", value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">
              Must be a leaf warehouse — a group one makes every stock document raised from these orders fail.
            </p>
          </div>

          <div className="space-y-2">
            <Label>Selling price list</Label>
            <LinkField
              doctype="Price List"
              value={form.priceList}
              onChange={(value) => onChange("priceList", value)}
              disabled={busy}
            />
          </div>

          <div className="space-y-2">
            <Label>Unmapped SKU item</Label>
            <LinkField
              doctype="Item"
              value={form.fallbackItem}
              onChange={(value) => onChange("fallbackItem", value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">
              Where a SellerSKU with no linked Item books. Blank auto-creates a non-stock “Amazon Unmapped Item”
              placeholder on first use; no Item is ever created per SKU.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amazon-sync-from">Sync orders from</Label>
            <Input
              id="amazon-sync-from"
              type="datetime-local"
              className="h-8"
              value={form.syncFrom}
              disabled={busy}
              onChange={(event) => onChange("syncFrom", event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              How far back the first sync reaches. Blank starts a day ago, and it is ignored once the watermark below is
              set.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
          <div className="text-sm">
            <div className="text-muted-foreground text-xs">Watermark (last completed sync)</div>
            <div>{amazonDateTime(status?.last_sync_at)}</div>
            <div className="text-muted-foreground text-xs">
              {amazonCount(status?.synced_orders ?? 0)} Sales Orders imported from Amazon so far
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/os/sales/orders">
                <ScrollText /> Sales Orders
              </Link>
            </Button>
            <Button variant="outline" size="sm" onClick={onSyncNow} disabled={busy || syncing || !canSync}>
              <RefreshCw className={syncing ? "animate-spin" : undefined} /> Sync orders now
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
