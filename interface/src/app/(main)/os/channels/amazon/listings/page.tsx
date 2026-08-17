import Link from "next/link";

import { PageHeader } from "@alaiy-os/layout/page-header";
import { Button } from "@alaiy-os/ui/button";
import { Settings } from "lucide-react";

import { Listings } from "./_components/listings";

export default function Page() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Amazon Listings"
        subtitle="The register of managed listings, as of the last sync. Open one to see what Amazon holds and push changes to it."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link href="/os/channels/amazon/settings">
              <Settings /> Amazon settings
            </Link>
          </Button>
        }
      />
      <Listings />
    </div>
  );
}
