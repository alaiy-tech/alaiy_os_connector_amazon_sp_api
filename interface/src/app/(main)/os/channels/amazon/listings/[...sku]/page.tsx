import Link from "next/link";

import { PageHeader } from "@alaiy-os/layout/page-header";
import { Button } from "@alaiy-os/ui/button";
import { ArrowLeft } from "lucide-react";

import { ListingDetail } from "./_components/listing-detail";

/**
 * A catch-all segment, not `[sku]`, because a SellerSKU is the seller's own string
 * and Amazon allows `/` in it. Percent-encoded that would be one segment either
 * way, but a proxy that normalises `%2F` back to `/` splits it — and with a
 * catch-all both spellings arrive intact and rejoin to the same SKU.
 */
export default async function Page({ params }: { params: Promise<{ sku: string[] }> }) {
  const { sku: segments } = await params;
  // Next has already percent-decoded each segment; decoding again would corrupt a
  // SKU containing a literal `%`.
  const sku = segments.join("/");

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={sku}
        subtitle="What the register holds for this SKU. Compare against Amazon before pushing anything to it."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link href="/os/channels/amazon/listings">
              <ArrowLeft /> All listings
            </Link>
          </Button>
        }
      />
      <ListingDetail sku={sku} />
    </div>
  );
}
