"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@alaiy-os/ui/dialog";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Spinner } from "@alaiy-os/ui/spinner";
import { ArrowRight, CircleCheck, CloudUpload, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { amazonErrorMessage, compareListing, updateListing } from "@/lib/amazon/api";
import { amazonMoney, conditionLabel } from "@/lib/amazon/format";
import type { AmazonCompareResult, AmazonDesiredListing, AmazonPushField } from "@/lib/amazon/types";

import { IssueSeverityBadge } from "../../../_components/status-badge";

const FIELD_LABELS: Record<AmazonPushField, string> = {
  title: "Title",
  price: "Price",
  quantity: "Quantity",
  condition: "Condition",
  description: "Description",
  bullet_points: "Bullet points",
  keywords: "Keywords",
  images: "Images",
};

/**
 * Compare, then push — never push blind.
 *
 * The baseline is what Amazon holds right now, read live when this opens, not the
 * register row. The row is only as fresh as the last sync, and a push writes the
 * *submitted* values onto it and marks it pending, so a listing Amazon rejected
 * still reads locally as though it went through. Diffing against the row would
 * answer "what changed on this form?" when the question a push has to answer is
 * "what does Amazon not have yet?".
 *
 * Two calls, two failure modes worth separating: the compare costs one Listings GET
 * and submits nothing, so it is safe to run on every open; the push is the only
 * thing here that changes a live listing.
 */
export function PushDialog({
  open,
  onOpenChange,
  sku,
  marketplace,
  desired,
  productType,
  onPushed,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sku: string;
  marketplace?: string;
  desired: AmazonDesiredListing;
  productType?: string | null;
  onPushed: () => void;
}) {
  const [comparison, setComparison] = useState<AmazonCompareResult | null>(null);
  const [comparing, setComparing] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `desired` is the form's state and changes on every keystroke — the comparison is deliberately taken once, when the dialog opens, and is what the operator is then agreeing to
  useEffect(() => {
    if (!open) {
      setComparison(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setComparing(true);
    setError(null);

    compareListing(sku, desired, marketplace)
      .then((result) => {
        if (!cancelled) setComparison(result);
      })
      .catch((caught) => {
        if (!cancelled) setError(amazonErrorMessage(caught, "Could not read the live listing from Amazon."));
      })
      .finally(() => {
        if (!cancelled) setComparing(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, sku, marketplace]);

  async function push() {
    if (!comparison) return;
    setPushing(true);
    try {
      const result = await updateListing(sku, comparison.changes, marketplace);
      const errors = (result.issues ?? []).filter((issue) => (issue.severity ?? "").toUpperCase() === "ERROR");
      if (errors.length) {
        // update_listing throws on error-severity issues, so reaching here with any
        // is unexpected — report it rather than claiming success.
        toast.warning(`Amazon accepted the submission with ${errors.length} error issue(s).`);
      } else {
        toast.success("Submitted to Amazon. The row stays pending until a sync confirms it.");
      }
      onOpenChange(false);
      onPushed();
    } catch (caught) {
      toast.error(amazonErrorMessage(caught, "Amazon refused the update."));
    } finally {
      setPushing(false);
    }
  }

  const changed = comparison?.changed ?? [];
  const remoteIssues = comparison?.remote.issues ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Push {sku} to Amazon</DialogTitle>
          <DialogDescription>
            What Amazon holds now, against this form. Only the differences are submitted.
          </DialogDescription>
        </DialogHeader>

        {!productType && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>This row has no product type</AlertTitle>
            <AlertDescription>
              Amazon rejects any create or update that does not declare one. Sync this SKU from Amazon first — a sync
              refreshes the product type from the listing summary, and never blanks it.
            </AlertDescription>
          </Alert>
        )}

        {comparing && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-muted-foreground text-sm">
              <Spinner /> Reading the live listing from Amazon...
            </div>
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Could not compare</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {comparison && !comparing && (
          <div className="space-y-4">
            {changed.length === 0 ? (
              <Alert>
                <CircleCheck />
                <AlertTitle>Nothing to push</AlertTitle>
                <AlertDescription>
                  Amazon already has every value on this form. Note that a blank field counts as “no opinion”, never as
                  a deletion — clearing content on Amazon is not something this screen can express.
                </AlertDescription>
              </Alert>
            ) : (
              <div className="divide-y rounded-lg border">
                {changed.map((field) => (
                  <DiffRow
                    key={field}
                    field={field}
                    from={comparison.remote[field as keyof typeof comparison.remote]}
                    to={comparison.changes[field]}
                    currency={comparison.remote.currency}
                    isContent={comparison.content_changed.includes(field)}
                  />
                ))}
              </div>
            )}

            {comparison.content_changed.length > 0 && (
              <Alert>
                <TriangleAlert />
                <AlertTitle>Content changes take a different route</AlertTitle>
                <AlertDescription>
                  Title, description, bullets, keywords and images are product content rather than your offer. Amazon
                  applies them only where this seller owns the ASIN's content — on a shared ASIN they can be accepted
                  and then quietly ignored, which a later sync is what reveals.
                </AlertDescription>
              </Alert>
            )}

            {remoteIssues.length > 0 && (
              <div className="space-y-2">
                <div className="text-muted-foreground text-xs uppercase tracking-wide">
                  Issues Amazon already reports on this listing
                </div>
                <div className="space-y-2">
                  {remoteIssues.map((issue, index) => (
                    <div
                      // biome-ignore lint/suspicious/noArrayIndexKey: Amazon's issues carry no stable id and repeat the same code per attribute
                      key={index}
                      className="flex items-start gap-2 rounded-md border p-2 text-sm"
                    >
                      <IssueSeverityBadge severity={issue.severity} />
                      <span className="min-w-0 flex-1">{issue.message || issue.code}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pushing}>
            Cancel
          </Button>
          <Button onClick={() => void push()} disabled={pushing || comparing || changed.length === 0 || !productType}>
            {pushing ? (
              <>
                <Spinner /> Submitting...
              </>
            ) : (
              <>
                <CloudUpload /> Push{" "}
                {changed.length > 0 ? `${changed.length} change${changed.length > 1 ? "s" : ""}` : ""}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DiffRow({
  field,
  from,
  to,
  currency,
  isContent,
}: {
  field: AmazonPushField;
  from: unknown;
  to: unknown;
  currency?: string | null;
  isContent: boolean;
}) {
  return (
    <div className="space-y-1 p-3">
      <div className="flex items-center gap-2">
        <span className="font-medium text-sm">{FIELD_LABELS[field]}</span>
        <Badge variant="outline">{isContent ? "content" : "offer"}</Badge>
      </div>
      <div className="flex flex-wrap items-start gap-2 text-sm">
        <span className="min-w-0 flex-1 text-muted-foreground line-through decoration-muted-foreground/40">
          {renderValue(field, from, currency)}
        </span>
        <ArrowRight className="mt-1 size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1">{renderValue(field, to, currency)}</span>
      </div>
    </div>
  );
}

/** Each pushable field prints differently, and "Amazon has nothing" is a value. */
function renderValue(field: AmazonPushField, value: unknown, currency?: string | null): string {
  if (value === null || value === undefined || value === "") return "not set";
  if (field === "price") return amazonMoney(Number(value), currency);
  if (field === "condition") return conditionLabel(String(value));
  if (field === "images") {
    const images = value as Array<{ url?: string }>;
    if (!Array.isArray(images) || images.length === 0) return "not set";
    return `${images.length} image${images.length > 1 ? "s" : ""} — ${images[0]?.url ?? ""}`;
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "not set" : value.map((entry) => String(entry)).join(" · ");
  }
  return String(value);
}
