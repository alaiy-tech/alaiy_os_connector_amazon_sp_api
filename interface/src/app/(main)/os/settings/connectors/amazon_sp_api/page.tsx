import { Suspense } from "react";

import { PageHeader } from "@alaiy-os/layout/page-header";
import { Skeleton } from "@alaiy-os/ui/skeleton";

import { AmazonSettings } from "./_components/amazon-settings";

export default function Page() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Amazon Settings"
        subtitle="The seller account, the app's own credentials, and where synced orders land — everything this connector needs to run."
      />
      {/* `useSearchParams` (the callback screen's verdict) opts this subtree into
          client rendering, and Next requires the boundary to be explicit. */}
      <Suspense fallback={<Skeleton className="h-56 w-full" />}>
        <AmazonSettings />
      </Suspense>
    </div>
  );
}
