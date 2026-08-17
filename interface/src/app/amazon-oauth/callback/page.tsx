import { Suspense } from "react";

import { Spinner } from "@alaiy-os/ui/spinner";
import type { Metadata } from "next";

import { CallbackExchange } from "./_components/callback-exchange";

export const metadata: Metadata = {
  title: "Connecting Amazon",
};

/**
 * `/amazon-oauth/callback` — the URL registered in Seller Central.
 *
 * Outside the `(main)/os` route group on purpose: Amazon sends the browser here
 * directly, and a sidebar around a page whose only job is to finish and get out of
 * the way would be noise. It is not behind the OS's session redirect either (the
 * proxy only matches `/os` and `/auth/login`), which is fine — the endpoint it
 * calls is role-gated server-side, and an unauthenticated hit gets that refusal
 * rather than a token.
 */
export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Spinner />
        </div>
      }
    >
      <CallbackExchange />
    </Suspense>
  );
}
