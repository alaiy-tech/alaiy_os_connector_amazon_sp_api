"use client";

import { useEffect, useState } from "react";

import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Input } from "@alaiy-os/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@alaiy-os/ui/input-group";
import { Label } from "@alaiy-os/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { Spinner } from "@alaiy-os/ui/spinner";
import { CircleCheck, CloudUpload, PackageSearch, Save, Search, Sparkles, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  amazonErrorMessage,
  draftListing,
  fetchConnectionStatus,
  publishListing,
  searchCatalog,
  suggestProductType,
} from "@/lib/amazon/api";
import { conditionLabel, textOr } from "@/lib/amazon/format";
import { type AmazonCatalogMatch, type AmazonProductTypeSuggestion, LISTING_CONDITIONS } from "@/lib/amazon/types";

import { ConnectorBlocker } from "../../../_components/connector-blocker";
import { ANY_MARKETPLACE, MarketplacePicker } from "@/components/amazon/marketplace-picker";

/**
 * Draft a listing this bench does not have yet, and publish it.
 *
 * The register could only ever be *filled* before — every row arrived from a sync,
 * which by definition means Amazon already had it. So the listings you could push
 * were exactly the listings that already existed, and the create half of the
 * Listings API had no way in outside the Desk form.
 *
 * The order of the two steps is Amazon's, not ours. An offer attaches to an ASIN
 * in Amazon's catalog and every Listings write must declare a product type, so
 * finding the product comes first and both values are read off the match rather
 * than typed — they are opaque identifiers, and a typo in either is a rejection
 * minutes later rather than an error here.
 *
 * Saving and publishing are separate on purpose. A draft is a real register row
 * (status `incomplete`), so it can be corrected, linked to an Item, given content
 * on the detail screen, or left to go out with the next bulk publish. Publishing
 * is what reaches Amazon, and it creates the offer only — product content belongs
 * to whoever owns the ASIN, and this seller's copy of it goes up on a later push.
 */
export function NewListing() {
  const router = useRouter();

  const [connected, setConnected] = useState<boolean | null>(null);
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [matches, setMatches] = useState<AmazonCatalogMatch[] | null>(null);
  const [suggestions, setSuggestions] = useState<AmazonProductTypeSuggestion[] | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  const [sku, setSku] = useState("");
  const [asin, setAsin] = useState("");
  const [productType, setProductType] = useState("");
  const [title, setTitle] = useState("");
  const [brand, setBrand] = useState("");
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [condition, setCondition] = useState("new_new");
  const [marketplace, setMarketplace] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchConnectionStatus()
      .then((status) => {
        if (cancelled) return;
        setConnected(status.connected);
        setConnectionMessage(status.message ?? null);
      })
      .catch(() => {
        if (!cancelled) setConnected(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function runSearch() {
    const term = query.trim();
    if (!term) return;
    setSearching(true);
    setSuggestions(null);
    try {
      setMatches(await searchCatalog(term, marketplace || undefined));
    } catch (error) {
      setMatches(null);
      toast.error(amazonErrorMessage(error, "Could not search the Amazon catalog."));
    } finally {
      setSearching(false);
    }
  }

  /** Take the identifiers off the match; leave anything already typed alone. */
  function useMatch(match: AmazonCatalogMatch) {
    setAsin(match.asin);
    if (match.product_type) setProductType(match.product_type);
    if (match.title && !title) setTitle(match.title);
    if (match.brand && !brand) setBrand(match.brand);
    setMatches(null);
    setSuggestions(null);
  }

  /**
   * The fallback when catalog search found nothing usable: Amazon will classify a
   * title even for a product it has no ASIN for. It answers a list because a
   * title is genuinely ambiguous, so the operator picks.
   */
  async function runSuggest() {
    const text = title.trim();
    if (!text) {
      toast.error("Enter a title first — the suggestion is made from it.");
      return;
    }
    setSuggesting(true);
    try {
      const found = await suggestProductType(text, marketplace || undefined);
      setSuggestions(found);
      if (!found.length) toast.info("Amazon recognised no product type for that title.");
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not look up the product type."));
    } finally {
      setSuggesting(false);
    }
  }

  const trimmedSku = sku.trim();
  const readyToPublish = Boolean(trimmedSku && asin.trim() && productType.trim());

  async function save(publish: boolean) {
    if (!trimmedSku) {
      toast.error("A SKU is required — it is how this listing is named, here and on Amazon.");
      return;
    }
    setSaving(true);
    try {
      await draftListing({
        sku: trimmedSku,
        asin: asin.trim() || undefined,
        product_type: productType.trim() || undefined,
        title: title.trim() || undefined,
        brand: brand.trim() || undefined,
        price: price.trim() === "" ? undefined : Number(price),
        quantity: quantity.trim() === "" ? undefined : Number(quantity),
        condition,
        marketplace: marketplace || undefined,
      });

      if (!publish) {
        toast.success(`${trimmedSku} saved. Publish it when you are ready.`);
        router.push(`/os/channels/amazon/listings/${encodeURIComponent(trimmedSku)}`);
        return;
      }

      // Published as a second call rather than one combined endpoint: the row is
      // saved either way, so a rejection from Amazon leaves the work on the
      // register instead of asking for it again.
      const result = await publishListing(trimmedSku, undefined, marketplace || undefined);
      toast.success(
        result.action === "created"
          ? `Offer created on Amazon. ${trimmedSku} stays pending until a sync confirms it.`
          : `${trimmedSku} published (${result.action}).`,
      );
      router.push(`/os/channels/amazon/listings/${encodeURIComponent(trimmedSku)}`);
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not create the listing."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {connected === false && (
        <ConnectorBlocker
          message={
            connectionMessage ||
            "No Amazon account is authorized. You can still draft a listing here, but nothing can be published or searched for until one is connected."
          }
          onRetry={() => router.refresh()}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>1. Find the product on Amazon</CardTitle>
          <CardDescription>
            An offer attaches to an ASIN that already exists in Amazon's catalog, and every write has to declare that
            ASIN's product type. Search by keywords or paste an ASIN.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <InputGroup className="h-9 w-full sm:w-96">
              <InputGroupAddon align="inline-start">
                <Search className="size-3.5" />
              </InputGroupAddon>
              <InputGroupInput
                className="h-9"
                placeholder="Keywords, or a 10-character ASIN"
                value={query}
                disabled={!connected}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void runSearch();
                  }
                }}
              />
            </InputGroup>
            <Button onClick={() => void runSearch()} disabled={searching || !connected || !query.trim()}>
              {searching ? <Spinner /> : <Search />} Search
            </Button>
            <MarketplacePicker
              value={marketplace}
              onChange={(value) => setMarketplace(value === ANY_MARKETPLACE ? "" : value)}
              emptyLabel="Primary marketplace"
              className="w-52"
            />
          </div>

          {matches !== null && <CatalogResults matches={matches} onUse={useMatch} />}

          {asin.trim() ? (
            <Alert>
              <CircleCheck />
              <AlertTitle>Attaching to {asin.trim()}</AlertTitle>
              <AlertDescription>
                {textOr(productType, "No product type yet")} · {textOr(brand, "no brand")}
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>2. Your offer</CardTitle>
          <CardDescription>
            The SKU is yours and permanent — it names this listing here and on Amazon, and it is what an order comes
            back with. The rest is the offer: what you charge, how many you have, and what condition they are in.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="sku">SKU</Label>
            <Input
              id="sku"
              value={sku}
              onChange={(event) => setSku(event.target.value)}
              placeholder="Your own seller SKU"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="What the register shows for this row"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="asin">ASIN</Label>
            <Input
              id="asin"
              value={asin}
              onChange={(event) => setAsin(event.target.value)}
              placeholder="From the search above"
              className="font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="product-type">Product type</Label>
            <div className="flex gap-2">
              <Input
                id="product-type"
                value={productType}
                onChange={(event) => setProductType(event.target.value)}
                placeholder="e.g. SHIRT"
                className="font-mono"
              />
              <Button
                variant="outline"
                onClick={() => void runSuggest()}
                disabled={suggesting || !connected}
                title="Ask Amazon to classify the title — for a product its catalog has no ASIN for"
              >
                {suggesting ? <Spinner /> : <Sparkles />}
              </Button>
            </div>
            {suggestions?.length ? (
              <div className="flex flex-wrap gap-1 pt-1">
                {suggestions.map((suggestion) => (
                  <Button
                    key={suggestion.product_type}
                    variant="outline"
                    size="sm"
                    onClick={() => setProductType(suggestion.product_type)}
                  >
                    {suggestion.display_name}
                  </Button>
                ))}
              </div>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="price">Price</Label>
            <Input
              id="price"
              type="number"
              inputMode="decimal"
              value={price}
              onChange={(event) => setPrice(event.target.value)}
              placeholder="0.00"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="quantity">Quantity</Label>
            <Input
              id="quantity"
              type="number"
              inputMode="numeric"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              placeholder="0"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="condition">Condition</Label>
            <Select value={condition} onValueChange={setCondition}>
              <SelectTrigger id="condition" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LISTING_CONDITIONS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {conditionLabel(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Alert>
        <TriangleAlert />
        <AlertTitle>Publishing creates the offer, not the product page</AlertTitle>
        <AlertDescription>
          Description, bullet points, keywords and images belong to whoever owns the ASIN's content. Add them on the
          listing after saving — they go up on a later publish, and only where Amazon accepts this seller as the content
          contributor.
        </AlertDescription>
      </Alert>

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={() => void save(false)} disabled={saving || !trimmedSku}>
          {saving ? <Spinner /> : <Save />} Save draft
        </Button>
        <Button
          onClick={() => void save(true)}
          disabled={saving || !readyToPublish || !connected}
          title={readyToPublish ? undefined : "A SKU, an ASIN and a product type are what Amazon needs to publish"}
        >
          {saving ? <Spinner /> : <CloudUpload />} Save and publish
        </Button>
      </div>
    </div>
  );
}

function CatalogResults({
  matches,
  onUse,
}: {
  matches: AmazonCatalogMatch[];
  onUse: (match: AmazonCatalogMatch) => void;
}) {
  if (matches.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed p-4 text-muted-foreground text-sm">
        <PackageSearch className="size-4" />
        No match in Amazon's catalog. Try different keywords — or set the product type by hand below, which is what a
        product Amazon has never listed needs.
      </div>
    );
  }

  return (
    <div className="divide-y rounded-lg border">
      {matches.map((match) => (
        <div key={match.asin} className="flex items-center gap-3 p-3">
          {/* Amazon's own CDN image for the ASIN. A plain <img>: the URL is
              external and not a domain the base's next/image is configured for. */}
          {match.image_url ? (
            // biome-ignore lint/performance/noImgElement: an Amazon CDN URL, not a bundled asset
            <img src={match.image_url} alt="" className="size-12 shrink-0 object-contain" />
          ) : (
            <div className="size-12 shrink-0 rounded bg-muted" />
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-sm">{textOr(match.title, match.asin)}</div>
            <div className="flex flex-wrap items-center gap-1 pt-1">
              <Badge variant="outline" className="font-mono">
                {match.asin}
              </Badge>
              {match.brand ? <Badge variant="outline">{match.brand}</Badge> : null}
              {match.product_type ? (
                <Badge variant="outline">{match.product_type}</Badge>
              ) : (
                <Badge variant="outline" className="text-muted-foreground">
                  <TriangleAlert /> no product type
                </Badge>
              )}
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={() => onUse(match)}>
            Use this
          </Button>
        </div>
      ))}
    </div>
  );
}
