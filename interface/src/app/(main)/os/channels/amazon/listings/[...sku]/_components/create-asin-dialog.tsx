"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@alaiy-os/ui/dialog";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Spinner } from "@alaiy-os/ui/spinner";
import { PackagePlus, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { amazonErrorMessage, createAsin, previewAsinCreation } from "@/lib/amazon/api";
import type { AmazonAsinCreatePreview } from "@/lib/amazon/types";

/**
 * Create the catalog entry, having first been told exactly what would be sent.
 *
 * This is not the publish dialog with different wording. Publishing puts an offer
 * on an ASIN somebody already owns, and is correctable — a wrong price is one
 * more submission away from right. This mints a *public ASIN*: other sellers can
 * list against it, and Amazon does not offer an undo. So the dialog is built
 * around the payload rather than around the button, and the primary action stays
 * disabled until the row satisfies the product type's own schema.
 *
 * The blockers are the substance. Each one is read from what Amazon requires for
 * this product type — not a rule of ours — and names the attribute plus where to
 * put it, because the alternative is submitting and reading the rejection an hour
 * later with no idea which of forty attributes was meant.
 */
export function CreateAsinDialog({
  open,
  onOpenChange,
  sku,
  marketplace,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sku: string;
  marketplace?: string;
  onCreated: () => void;
}) {
  const [preview, setPreview] = useState<AmazonAsinCreatePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPreview(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    previewAsinCreation(sku, marketplace)
      .then((result) => {
        if (!cancelled) setPreview(result);
      })
      .catch((caught) => {
        if (!cancelled) setError(amazonErrorMessage(caught, "Could not read what Amazon requires for this product."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, sku, marketplace]);

  async function submit() {
    if (!preview?.ready) return;
    setSubmitting(true);
    try {
      const result = await createAsin(sku, marketplace);
      toast.success(
        result.submission_id
          ? `Submitted to Amazon as ${result.submission_id}. The ASIN appears on this row once Amazon has created it.`
          : "Submitted to Amazon. The ASIN appears on this row once Amazon has created it.",
      );
      onOpenChange(false);
      onCreated();
    } catch (caught) {
      toast.error(amazonErrorMessage(caught, "Amazon refused the product."));
    } finally {
      setSubmitting(false);
    }
  }

  const blockers = preview?.blockers ?? [];
  const attributeNames = Object.keys(preview?.attributes ?? {}).sort();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create {sku} on Amazon</DialogTitle>
          <DialogDescription>
            This asks Amazon to add the product to its catalog and give it a new ASIN. It is not an offer against an
            existing one, and it cannot be undone.
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-muted-foreground text-sm">
              <Spinner /> Reading what Amazon requires for {preview?.product_type ?? "this product type"}...
            </div>
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Could not check the requirements</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {blockers.length > 0 && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Not ready to create</AlertTitle>
            <AlertDescription>
              <ul className="list-inside list-disc space-y-1">
                {blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {preview && !loading && (
          <div className="space-y-4">
            {preview.ready && (
              <Alert>
                <PackagePlus />
                <AlertTitle>This creates a new public ASIN</AlertTitle>
                <AlertDescription>
                  Submitted as product type <code className="text-xs">{preview.product_type}</code>. Amazon creates the
                  catalog entry asynchronously — the row stays pending, and its ASIN appears here once the entry exists.
                </AlertDescription>
              </Alert>
            )}

            {preview.warnings.map((warning) => (
              <Alert key={warning}>
                <TriangleAlert />
                <AlertDescription>{warning}</AlertDescription>
              </Alert>
            ))}

            {attributeNames.length > 0 && (
              <div className="space-y-2">
                <div className="font-medium text-sm">Attributes being submitted</div>
                <div className="flex flex-wrap gap-1.5">
                  {attributeNames.map((name) => (
                    <code
                      key={name}
                      className="rounded bg-muted px-1.5 py-0.5 text-xs"
                      title={preview.required.includes(name) ? "Required by this product type" : undefined}
                    >
                      {name}
                      {preview.required.includes(name) ? " *" : ""}
                    </code>
                  ))}
                </div>
                <p className="text-muted-foreground text-xs">
                  * required by this product type. Anything Amazon requires that no listing field holds goes in Extra
                  Attributes on the row.
                </p>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!preview?.ready || submitting || loading}>
            {submitting ? <Spinner /> : <PackagePlus />}
            Create on Amazon
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
