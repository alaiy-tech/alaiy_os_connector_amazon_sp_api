"use client";

import { useCallback, useEffect, useState } from "react";

import Link from "next/link";

import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardHeader } from "@alaiy-os/ui/card";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@alaiy-os/ui/input-group";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@alaiy-os/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@alaiy-os/ui/table";
import { cn } from "@alaiy-os/utils";
import { Boxes, CloudDownload, Link2Off, PackageSearch, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  amazonErrorMessage,
  fetchConnectionStatus,
  fetchListings,
  reconcileListings,
  syncAllListings,
} from "@/lib/amazon/api";
import { amazonCount, amazonDateTime, amazonMoney, textOr } from "@/lib/amazon/format";
import type {
  AmazonConnectionStatus,
  AmazonListingRow,
  AmazonListingStatus,
  AmazonMarketplace,
} from "@/lib/amazon/types";

import { ConnectorBlocker } from "../../_components/connector-blocker";
import { ListEmpty } from "../../_components/list-empty";
import {
  ANY_MARKETPLACE,
  MarketplacePicker,
  marketplaceName,
  useMarketplaces,
} from "@/components/amazon/marketplace-picker";
import { ListingStatusBadge } from "@/components/amazon/status-badge";

const PAGE_SIZE = 20;
const ANY_STATUS = "__any__";
const SEARCH_DEBOUNCE_MS = 400;

const STATUSES: Array<{ value: AmazonListingStatus; label: string }> = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "suppressed", label: "Suppressed" },
  { value: "incomplete", label: "Incomplete" },
  { value: "pending", label: "Pending" },
];

/**
 * The register of managed listings — local rows, not a live read.
 *
 * Which is the important thing about this screen: every row is what the last sync
 * or reconcile recorded, so the register can be complete, stale, or both, and
 * nothing here calls Amazon just by being opened. That is deliberate — a catalog
 * is up to 1,000 SKUs per marketplace and Amazon's rate limits are measured in
 * requests per second.
 *
 * The two ways to refill it differ in reach, not just in speed: **Sync from
 * Amazon** reads rich per-listing detail but stops at Amazon's 1,000-SKU cap;
 * **Reconcile** drives the Merchant Listings report, which has no cap but carries
 * only offer and status columns. Both run in the background.
 */
export function Listings() {
  const { marketplaces } = useMarketplaces();

  const [rows, setRows] = useState<AmazonListingRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<AmazonListingStatus | "">("");
  const [marketplace, setMarketplace] = useState("");
  const [unmappedOnly, setUnmappedOnly] = useState(false);

  const [connection, setConnection] = useState<AmazonConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  // The connection is read alongside the rows rather than once on mount: it can be
  // disconnected from another tab or the Desk while this page is open, and every
  // action in the toolbar depends on it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how an action re-reads.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setFailed(false);
      try {
        const [connectionStatus, listingPage] = await Promise.all([
          fetchConnectionStatus().catch(() => null),
          fetchListings({ search, status, marketplace, unmappedOnly, page, pageSize: PAGE_SIZE }),
        ]);
        if (cancelled) return;
        setConnection(connectionStatus);
        setRows(listingPage.rows);
        setTotal(listingPage.total);
      } catch (error) {
        if (cancelled) return;
        setRows([]);
        setTotal(0);
        setFailed(true);
        toast.error(amazonErrorMessage(error, "Could not load the Amazon listings."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [search, status, marketplace, unmappedOnly, page, reloadToken]);

  const connected = Boolean(connection?.connected);
  const pageStart = (page - 1) * PAGE_SIZE + 1;
  const hasNextPage = page * PAGE_SIZE < total;
  const filtered = Boolean(search || status || marketplace || unmappedOnly);

  /**
   * Both bulk reads are `frappe.enqueue`d, so the answer is "queued" and the rows
   * appear whenever the worker finishes. There is no realtime channel through the
   * frontend's proxy to wait on, so the honest thing is to say it is running and
   * leave the refresh in the operator's hands rather than poll a list of a
   * thousand rows on a timer.
   */
  async function runBulk(action: "sync" | "reconcile") {
    setBusy(true);
    try {
      const target = marketplace || undefined;
      if (action === "sync") {
        await syncAllListings(target);
        toast.success("Listing sync queued. Refresh in a minute to see the rows it writes.");
      } else {
        await reconcileListings(target);
        toast.success("Reconcile queued. It reads the full catalog report, so give it longer.");
      }
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not start that job."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {connection && !connected && (
        <ConnectorBlocker
          message={
            connection.message ||
            "No Amazon account is authorized, so nothing can be synced or pushed. The rows below, if any, are from an earlier connection."
          }
          onRetry={reload}
        />
      )}

      <Card className="gap-0 py-0">
        <CardHeader className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <InputGroup className="h-8 w-full md:w-64">
            <InputGroupAddon align="inline-start">
              <Search className="size-3.5" />
            </InputGroupAddon>
            <InputGroupInput
              className="h-8"
              placeholder="Search SKU, title or ASIN..."
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </InputGroup>

          <Select
            value={status || ANY_STATUS}
            onValueChange={(value) => {
              setStatus(value === ANY_STATUS ? "" : (value as AmazonListingStatus));
              setPage(1);
            }}
          >
            <SelectTrigger size="sm" className="w-36">
              <SelectValue placeholder="Any status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY_STATUS}>Any status</SelectItem>
              {STATUSES.map((entry) => (
                <SelectItem key={entry.value} value={entry.value}>
                  {entry.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <MarketplacePicker
            value={marketplace}
            onChange={(value) => {
              setMarketplace(value === ANY_MARKETPLACE ? "" : value);
              setPage(1);
            }}
            className="w-44"
          />

          <Button
            variant={unmappedOnly ? "secondary" : "outline"}
            size="sm"
            aria-pressed={unmappedOnly}
            onClick={() => {
              setUnmappedOnly((current) => !current);
              setPage(1);
            }}
            title="Only SKUs with no catalog Item linked"
          >
            <Link2Off /> Unmapped
          </Button>

          <div className="ms-auto flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={reload} disabled={loading} aria-label="Refresh">
              <RefreshCw className={loading ? "animate-spin" : undefined} />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runBulk("sync")}
              disabled={busy || !connected}
              title="Up to 1,000 SKUs per marketplace, with full detail"
            >
              <CloudDownload /> Sync from Amazon
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runBulk("reconcile")}
              disabled={busy || !connected}
              title="The whole catalog from the Merchant Listings report — offer and status columns only"
            >
              <Boxes /> Reconcile
            </Button>
          </div>
        </CardHeader>

        <CardContent className="px-0">
          <RegisterBody
            loading={loading}
            rows={rows}
            failed={failed}
            filtered={filtered}
            connected={connected}
            marketplaces={marketplaces}
          />
        </CardContent>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-3">
          <span className="text-muted-foreground text-sm">
            {total > 0
              ? `Showing ${pageStart}–${pageStart + rows.length - 1} of ${amazonCount(total)}`
              : "Nothing to show"}
          </span>
          <Pagination className="mx-0 w-auto justify-end">
            <PaginationContent>
              <PaginationItem>
                <PaginationPrevious
                  href="#"
                  text=""
                  className={page <= 1 ? "pointer-events-none opacity-50" : undefined}
                  onClick={(event) => {
                    event.preventDefault();
                    if (page > 1) setPage(page - 1);
                  }}
                />
              </PaginationItem>
              <PaginationItem>
                <PaginationNext
                  href="#"
                  text=""
                  className={hasNextPage ? undefined : "pointer-events-none opacity-50"}
                  onClick={(event) => {
                    event.preventDefault();
                    if (hasNextPage) setPage(page + 1);
                  }}
                />
              </PaginationItem>
            </PaginationContent>
          </Pagination>
        </div>
      </Card>
    </div>
  );
}

/**
 * The card's body: loading, or empty for one of several reasons, or the table.
 *
 * A component rather than a ternary chain in place, so each of the three states
 * is reached by its own return and the empty one can keep saying *which* kind of
 * empty it is.
 */
function RegisterBody({
  loading,
  rows,
  failed,
  filtered,
  connected,
  marketplaces,
}: {
  loading: boolean;
  rows: AmazonListingRow[];
  failed: boolean;
  filtered: boolean;
  connected: boolean;
  marketplaces: AmazonMarketplace[];
}) {
  if (loading) return <TableSkeleton />;
  if (rows.length === 0) return <EmptyRegister failed={failed} filtered={filtered} connected={connected} />;

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>SKU / title</TableHead>
            <TableHead>ASIN</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead>Fulfilment</TableHead>
            <TableHead>Item</TableHead>
            <TableHead>Marketplace</TableHead>
            <TableHead>Last synced</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <ListingTableRow
              key={row.name}
              row={row}
              marketplaceLabel={marketplaceName(marketplaces, row.marketplace)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ListingTableRow({ row, marketplaceLabel }: { row: AmazonListingRow; marketplaceLabel: string }) {
  const isParent = row.is_variation_parent === 1;

  return (
    <TableRow>
      <TableCell className="max-w-xs">
        <Link
          href={`/os/channels/amazon/listings/${encodeURIComponent(row.name)}`}
          className="font-medium text-sm hover:underline"
        >
          {row.sku || row.name}
        </Link>
        <div className="truncate text-muted-foreground text-xs" title={row.title ?? undefined}>
          {textOr(row.title, "Untitled listing")}
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {isParent && (
            <Badge variant="outline" title="A family container, not a buyable offer">
              variation parent
            </Badge>
          )}
          {row.brand ? <Badge variant="outline">{row.brand}</Badge> : null}
          {row.product_type ? (
            <Badge variant="outline">{row.product_type}</Badge>
          ) : (
            // Amazon refuses any create or update that does not declare one, so a
            // row without it cannot be pushed at all — worth flagging in the list
            // rather than only on the form.
            <Badge
              variant="outline"
              className="text-muted-foreground"
              title="Amazon rejects a push without a product type"
            >
              <TriangleAlert /> no product type
            </Badge>
          )}
        </div>
      </TableCell>
      <TableCell className="font-mono text-xs">{textOr(row.asin)}</TableCell>
      <TableCell>
        <ListingStatusBadge status={row.listing_status} />
      </TableCell>
      {/* A variation parent carries no price or quantity by design; an em dash
          says that, where a 0 would claim it is free and out of stock. */}
      <TableCell className="text-right text-sm tabular-nums">{amazonMoney(row.price, row.currency)}</TableCell>
      <TableCell className={cn("text-right text-sm tabular-nums", row.quantity === 0 && "text-destructive")}>
        {isParent ? "—" : amazonCount(row.quantity)}
      </TableCell>
      <TableCell className="text-sm">{row.fulfillment_channel === "AMAZON" ? "FBA" : "Merchant"}</TableCell>
      <TableCell className="max-w-40 text-sm">
        {row.product ? (
          <Link href={`/os/products/${encodeURIComponent(row.product)}`} className="block truncate hover:underline">
            {row.product}
          </Link>
        ) : (
          <span className="text-muted-foreground">Unmapped</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">{marketplaceLabel}</TableCell>
      <TableCell className="text-muted-foreground text-xs">{amazonDateTime(row.last_synced_at)}</TableCell>
    </TableRow>
  );
}

function EmptyRegister({ failed, filtered, connected }: { failed: boolean; filtered: boolean; connected: boolean }) {
  if (failed) {
    return <ListEmpty icon={PackageSearch} title="Could not load the register" description="Try again in a moment." />;
  }
  if (filtered) {
    return (
      <ListEmpty icon={Search} title="No matches" description="No listing in the register matches these filters." />
    );
  }
  if (!connected) {
    return (
      <ListEmpty
        icon={PackageSearch}
        title="The register is empty"
        description="Connect an Amazon account, then sync — listings are recorded here as they are read."
      />
    );
  }
  return (
    <ListEmpty
      icon={PackageSearch}
      title="The register is empty"
      description="Nothing has been synced yet. Sync from Amazon reads up to 1,000 SKUs per marketplace; Reconcile reads the whole catalog."
    />
  );
}

function TableSkeleton() {
  return (
    <div className="divide-y">
      {Array.from({ length: 6 }, (_, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length placeholder, never reordered
        <div key={index} className="flex items-center gap-4 px-4 py-3">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-64" />
          </div>
          <Skeleton className="h-5 w-16 shrink-0" />
          <Skeleton className="h-3.5 w-20 shrink-0" />
          <Skeleton className="h-3.5 w-10 shrink-0" />
          <Skeleton className="h-3 w-28 shrink-0" />
        </div>
      ))}
    </div>
  );
}
