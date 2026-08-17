"use client";

import { useCallback, useEffect, useState } from "react";

import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@alaiy-os/ui/alert-dialog";
import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Input } from "@alaiy-os/ui/input";
import { Label } from "@alaiy-os/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Textarea } from "@alaiy-os/ui/textarea";
import {
  ArrowLeft,
  CircleSlash,
  CloudDownload,
  CloudUpload,
  ExternalLink,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import {
  amazonErrorMessage,
  deleteListing,
  fetchConnectionStatus,
  fetchListing,
  setListingProduct,
  syncListing,
} from "@/lib/amazon/api";
import { amazonDateTime, conditionLabel } from "@/lib/amazon/format";
import {
  type AmazonDesiredListing,
  type AmazonListingDoc,
  type AmazonPushImage,
  bulletTexts,
  keywordTexts,
  pushImages,
} from "@/lib/amazon/types";

import { ConnectorBlocker } from "../../../_components/connector-blocker";
import { marketplaceName, useMarketplaces } from "../../../_components/marketplace-picker";
import { IssueSeverityBadge, ListingStatusBadge } from "../../../_components/status-badge";
import { LinkField } from "../../../settings/_components/link-field";
import { ImageListEditor } from "./image-list-editor";
import { PushDialog } from "./push-dialog";
import { StringListEditor } from "./string-list-editor";
import { VariationFamilyCard } from "./variation-family";

/** Amazon's condition codes, as `Amazon Product Listing.condition` lists them. */
const CONDITIONS = [
  "new_new",
  "new_open_box",
  "new_oem",
  "used_like_new",
  "used_very_good",
  "used_good",
  "used_acceptable",
  "collectible_like_new",
  "collectible_very_good",
  "collectible_good",
  "collectible_acceptable",
  "refurbished_refurbished",
];

/** Amazon's own caps, so the editors show the limit before a rejection does. */
const MAX_BULLETS = 5;
const MAX_KEYWORDS = 5;

interface Form {
  title: string;
  price: string;
  quantity: string;
  condition: string;
  description: string;
  bullets: string[];
  keywords: string[];
  images: AmazonPushImage[];
}

/**
 * One listing: what the register holds, and the two directions it can move.
 *
 * **Down** is *Sync from Amazon* — one Listings GET that overwrites this row with
 * Amazon's answer. **Up** is *Push*, which compares first and submits only the
 * difference.
 *
 * There is no Save button, and its absence is the design: `update_listing` writes
 * the values it submitted onto the row itself, so a push *is* the save. A local-only
 * save would create a third state — edited here, not on Amazon, indistinguishable
 * on the row from a pushed change Amazon then rejected — which is precisely the
 * confusion `remote_snapshot` exists to avoid. The one exception is the Item link,
 * which means nothing to Amazon and saves on its own.
 */
export function ListingDetail({ sku }: { sku: string }) {
  const { marketplaces } = useMarketplaces();

  const [doc, setDoc] = useState<AmazonListingDoc | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pushOpen, setPushOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how an action re-reads.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setNotFound(false);
      try {
        const [row, connection] = await Promise.all([fetchListing(sku), fetchConnectionStatus().catch(() => null)]);
        if (cancelled) return;
        setDoc(row);
        // The form is reset from the document on every read, deliberately: a sync
        // that just overwrote the row must not leave stale edits sitting on top of
        // it, claiming to be Amazon's state.
        setForm(formFrom(row));
        setConnected(connection ? connection.connected : null);
      } catch (error) {
        if (cancelled) return;
        setDoc(null);
        setForm(null);
        setNotFound(true);
        toast.error(amazonErrorMessage(error, `Could not load the listing for ${sku}.`));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [sku, reloadToken]);

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((current) => (current ? { ...current, [key]: value } : current));
  }

  async function syncFromAmazon() {
    setBusy(true);
    try {
      await syncListing(sku, doc?.marketplace ?? undefined);
      toast.success("Refreshed from Amazon.");
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not sync this listing from Amazon."));
    } finally {
      setBusy(false);
    }
  }

  async function endListing() {
    setBusy(true);
    try {
      await deleteListing(sku, doc?.marketplace ?? undefined);
      toast.success("Listing ended on Amazon. The row stays, inactive.");
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not end this listing."));
    } finally {
      setBusy(false);
    }
  }

  async function linkProduct(product: string) {
    setBusy(true);
    try {
      const updated = await setListingProduct(sku, product || null);
      setDoc(updated);
      toast.success(product ? `Linked to ${product}.` : "Item link cleared.");
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not link that Item."));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (notFound || !doc || !form) {
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>No such listing</AlertTitle>
        <AlertDescription>
          <p>
            Nothing in the register is named <code>{sku}</code>. It may have been renamed on Amazon, or never synced.
          </p>
          <Button variant="outline" size="sm" asChild>
            <Link href="/os/channels/amazon/listings">
              <ArrowLeft /> Back to the register
            </Link>
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const isParent = doc.is_variation_parent === 1;
  const issues = doc.suppression_reasons ?? [];

  return (
    <div className="flex flex-col gap-4">
      {connected === false && (
        <ConnectorBlocker
          message="No Amazon account is authorized. This row can be read and its Item link changed, but nothing can be synced or pushed."
          onRetry={reload}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{doc.sku || doc.name}</span>
            <ListingStatusBadge status={doc.listing_status} />
            {isParent && <Badge variant="outline">variation parent</Badge>}
            {doc.fulfillment_channel === "AMAZON" && <Badge variant="outline">FBA</Badge>}
          </CardTitle>
          <CardDescription>{doc.title || "Untitled listing"}</CardDescription>
        </CardHeader>

        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="ASIN">
              {doc.asin ? (
                <span className="flex items-center gap-1">
                  <code className="text-xs">{doc.asin}</code>
                  <AsinLink asin={doc.asin} marketplace={doc.marketplace} marketplaces={marketplaces} />
                </span>
              ) : (
                "—"
              )}
            </Field>
            <Field label="Product type">
              {doc.product_type ? (
                <code className="text-xs">{doc.product_type}</code>
              ) : (
                <span className="text-destructive text-xs">Missing — a push will be rejected</span>
              )}
            </Field>
            <Field label="Marketplace">{marketplaceName(marketplaces, doc.marketplace)}</Field>
            <Field label="Last synced">{amazonDateTime(doc.last_synced_at)}</Field>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t pt-4">
            <Button onClick={() => setPushOpen(true)} disabled={busy || !connected}>
              <CloudUpload /> Compare and push
            </Button>
            <Button variant="outline" onClick={() => void syncFromAmazon()} disabled={busy || !connected}>
              <CloudDownload /> Sync from Amazon
            </Button>
            <Button variant="outline" onClick={reload} disabled={busy} aria-label="Reload the row">
              <RefreshCw />
            </Button>

            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="outline"
                  disabled={busy || !connected}
                  className="ms-auto text-destructive hover:text-destructive"
                >
                  <CircleSlash /> End listing
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>End {doc.sku} on Amazon?</AlertDialogTitle>
                  <AlertDialogDescription>
                    The offer is withdrawn on Amazon and buyers can no longer order it. The register row stays, marked
                    inactive, so the history and the Item link survive — but re-listing means a new submission.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void endListing()}>End listing</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </CardContent>
      </Card>

      {issues.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Issues Amazon reports</CardTitle>
            <CardDescription>
              Recorded by the last sync or push. An ERROR is why a listing is suppressed or incomplete.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {issues.map((issue, index) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: Amazon's issues carry no stable id and repeat the same code per attribute
                key={index}
                className="flex items-start gap-2 rounded-md border p-3 text-sm"
              >
                <IssueSeverityBadge severity={issue.severity} />
                <div className="min-w-0 flex-1">
                  <p>{issue.message || issue.code}</p>
                  {issue.attribute_names && (
                    <p className="text-muted-foreground text-xs">Attributes: {issue.attribute_names}</p>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Offer</CardTitle>
          <CardDescription>
            {isParent
              ? "This row is a family container rather than a buyable offer, so Amazon holds no price or quantity for it."
              : "Your price, availability and condition. Edits here are submitted by Compare and push — there is no separate save."}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5 md:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="amazon-price">Price</Label>
            <Input
              id="amazon-price"
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              className="h-8 text-right tabular-nums"
              value={form.price}
              disabled={busy || isParent}
              onChange={(event) => set("price", event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              In {doc.currency || "the marketplace's currency"}. Blank or zero leaves Amazon's price alone.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amazon-quantity">Quantity</Label>
            <Input
              id="amazon-quantity"
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              className="h-8 text-right tabular-nums"
              value={form.quantity}
              disabled={busy || isParent}
              onChange={(event) => set("quantity", event.target.value)}
            />
            {/* Zero is a real instruction here, unlike a zero price — it is how a
                seller goes out of stock — so it is pushed rather than skipped. */}
            <p className="text-muted-foreground text-xs">Zero is pushed: it is how a SKU goes out of stock.</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amazon-condition">Condition</Label>
            <Select
              value={form.condition || "new_new"}
              onValueChange={(value) => set("condition", value)}
              disabled={busy || isParent}
            >
              <SelectTrigger id="amazon-condition" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CONDITIONS.map((condition) => (
                  <SelectItem key={condition} value={condition}>
                    {conditionLabel(condition)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2 md:col-span-3">
            <Label>Catalog Item</Label>
            <LinkField
              doctype="Item"
              value={doc.product ?? ""}
              onChange={(value) => void linkProduct(value)}
              disabled={busy}
              placeholder="Unmapped"
            />
            <p className="text-muted-foreground text-xs">
              Local only — it never reaches Amazon. It is what stops the next order for this SellerSKU booking against
              the unmapped-SKU placeholder. Saved as soon as it is picked.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Content</CardTitle>
          <CardDescription>
            The product's own description, as opposed to your offer. Amazon applies these only where this seller owns
            the ASIN's content.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="amazon-title">Title</Label>
            <Textarea
              id="amazon-title"
              rows={2}
              value={form.title}
              disabled={busy}
              onChange={(event) => set("title", event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="amazon-description">Description</Label>
            <Textarea
              id="amazon-description"
              rows={5}
              value={form.description}
              disabled={busy}
              onChange={(event) => set("description", event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Bullet points</Label>
            <StringListEditor
              values={form.bullets}
              onChange={(values) => set("bullets", values)}
              disabled={busy}
              placeholder="A selling point, one per bullet"
              addLabel="Add bullet"
              max={MAX_BULLETS}
            />
          </div>

          <div className="space-y-2">
            <Label>Search keywords</Label>
            <StringListEditor
              values={form.keywords}
              onChange={(values) => set("keywords", values)}
              disabled={busy}
              placeholder="A search term buyers would use"
              addLabel="Add keyword"
              max={MAX_KEYWORDS}
            />
          </div>

          <div className="space-y-2">
            <Label>Images</Label>
            <ImageListEditor images={form.images} onChange={(images) => set("images", images)} disabled={busy} />
          </div>
        </CardContent>
      </Card>

      {doc.parent_asin && (
        <VariationFamilyCard
          parentAsin={doc.parent_asin}
          marketplace={doc.marketplace ?? undefined}
          currentSku={doc.name}
          currency={doc.currency}
        />
      )}

      <PushDialog
        open={pushOpen}
        onOpenChange={setPushOpen}
        sku={doc.name}
        marketplace={doc.marketplace ?? undefined}
        desired={desiredFrom(form)}
        productType={doc.product_type}
        onPushed={reload}
      />
    </div>
  );
}

function formFrom(doc: AmazonListingDoc): Form {
  return {
    title: doc.title ?? "",
    // Kept as strings so that "not set" survives round-tripping through the form:
    // a numeric 0 and an empty field mean different things to a push.
    price: doc.price === null || doc.price === undefined ? "" : String(doc.price),
    quantity: doc.quantity === null || doc.quantity === undefined ? "" : String(doc.quantity),
    condition: doc.condition ?? "new_new",
    description: doc.description ?? "",
    bullets: bulletTexts(doc),
    keywords: keywordTexts(doc),
    images: pushImages(doc),
  };
}

/**
 * The form as `compare_listing` wants it.
 *
 * Empty scalars are left out entirely rather than sent as `""`. The Python side
 * reads a blank as "no opinion" either way, but omitting them keeps the request
 * honest about what the operator actually expressed.
 */
function desiredFrom(form: Form): AmazonDesiredListing {
  const desired: AmazonDesiredListing = {};
  if (form.title.trim()) desired.title = form.title.trim();
  if (form.description.trim()) desired.description = form.description.trim();
  if (form.condition) desired.condition = form.condition;
  if (form.price.trim() !== "" && Number.isFinite(Number(form.price))) desired.price = Number(form.price);
  if (form.quantity.trim() !== "" && Number.isFinite(Number(form.quantity))) {
    desired.quantity = Math.trunc(Number(form.quantity));
  }
  const bullets = form.bullets.map((entry) => entry.trim()).filter(Boolean);
  if (bullets.length) desired.bullet_points = bullets;
  const keywords = form.keywords.map((entry) => entry.trim()).filter(Boolean);
  if (keywords.length) desired.keywords = keywords;
  const images = form.images.filter((image) => image.url.trim() !== "");
  if (images.length) desired.images = images;
  return desired;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

/**
 * A link out to the listing on Amazon, when the marketplace's domain is known.
 *
 * `/dp/<asin>` on the marketplace's own domain — the one URL shape that is stable
 * across Amazon's storefronts. Omitted rather than guessed when the reference row
 * has no domain, since a wrong storefront shows a different seller's page.
 */
function AsinLink({
  asin,
  marketplace,
  marketplaces,
}: {
  asin: string;
  marketplace?: string | null;
  marketplaces: Array<{ name: string; domain?: string | null }>;
}) {
  const domain = marketplaces.find((entry) => entry.name === marketplace)?.domain?.trim();
  if (!domain) return null;
  return (
    <a
      href={`https://${domain}/dp/${encodeURIComponent(asin)}`}
      target="_blank"
      rel="noreferrer noopener"
      className="text-muted-foreground hover:text-foreground"
      aria-label={`Open ${asin} on ${domain}`}
      title={`Open on ${domain}`}
    >
      <ExternalLink className="size-3.5" />
    </a>
  );
}
