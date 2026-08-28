import Link from "next/link";

import { PageHeader } from "@alaiy-os/layout/page-header";
import { Button } from "@alaiy-os/ui/button";
import { ArrowLeft } from "lucide-react";

import { NewListing } from "./_components/new-listing";

/**
 * A static segment beside the `[...sku]` catch-all, which Next resolves first —
 * at the cost of shadowing a SellerSKU literally called `new`. Worth it: this is
 * the only route in the register that is not a SKU, and the alternative (a modal
 * on the list) hides a three-step flow behind a button.
 */
export default function Page() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="New Amazon listing"
        subtitle="Find the ASIN your offer attaches to, describe the offer, and publish it — or save it and publish later with the rest."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link href="/os/channels/amazon/listings">
              <ArrowLeft /> All listings
            </Link>
          </Button>
        }
      />
      <NewListing />
    </div>
  );
}
