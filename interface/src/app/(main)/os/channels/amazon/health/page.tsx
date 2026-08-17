import Link from "next/link";

import { PageHeader } from "@alaiy-os/layout/page-header";
import { Button } from "@alaiy-os/ui/button";
import { Settings } from "lucide-react";

import { AccountHealth } from "./_components/account-health";

export default function Page() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Amazon Account Health"
        subtitle="Amazon's own metrics for this seller account, as of the last sync, with the recent buyer feedback behind them."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link href="/os/channels/amazon/settings">
              <Settings /> Amazon settings
            </Link>
          </Button>
        }
      />
      <AccountHealth />
    </div>
  );
}
