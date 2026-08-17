"use client";

import { useEffect, useRef, useState } from "react";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Spinner } from "@alaiy-os/ui/spinner";
import { CircleCheck, CircleX, Settings } from "lucide-react";

import { amazonErrorMessage, completeOauth } from "@/lib/amazon/api";
import type { AmazonOauthResult } from "@/lib/amazon/types";

import { AMAZON_SETTINGS_HREF } from "../../../(main)/os/channels/amazon/_components/connector-blocker";

/**
 * Where Amazon's consent screen returns to.
 *
 * This route exists because of *where* it is: the redirect URI is built from
 * `app_url`, which on a deployment where the composed frontend owns the site's
 * hostname resolves to this app — and Frappe's own `/amazon-oauth/callback` www
 * page is then unreachable from a browser, since only `/api` is proxied through.
 * Same landing page, other side of the split.
 *
 * The exchange itself is not done here. Amazon's one-time code is traded for a
 * refresh token by `api.complete_oauth`, server-side, using the app's LWA secret —
 * which lives in `site_config.json` and must never reach a browser. This screen
 * only carries the query parameters across and reports the verdict.
 */
export function CallbackExchange() {
  const params = useSearchParams();
  const router = useRouter();
  const [result, setResult] = useState<AmazonOauthResult | null>(null);
  /**
   * Which code+state pair has already been spent.
   *
   * Amazon's code is single-use and so is the state, so the exchange must run at
   * most once per pair — React's development double-mount would otherwise spend
   * the code on the first pass and report a state mismatch on the second. Keyed on
   * the pair rather than a plain "have I run" flag so that it is the *code* that is
   * idempotent, which is the thing Amazon actually cares about.
   */
  const spent = useRef<string | null>(null);

  useEffect(() => {
    const code = params.get("spapi_oauth_code");
    const state = params.get("state");
    const attempt = `${code}:${state}`;
    if (spent.current === attempt) return;
    spent.current = attempt;

    completeOauth({
      spapi_oauth_code: code,
      state,
      selling_partner_id: params.get("selling_partner_id"),
      error: params.get("error"),
      error_description: params.get("error_description"),
    })
      .then(setResult)
      .catch((error) => {
        // A throw here is the role gate or a dead backend — not one of the handled
        // outcomes, which come back as `{success: false}`.
        setResult({
          success: false,
          message: amazonErrorMessage(error, "Could not complete the Amazon authorization."),
          status: null,
        });
      });
  }, [params]);

  if (!result) {
    return (
      <Shell title="Connecting your Amazon account" description="Exchanging Amazon's authorization code.">
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Spinner /> Talking to Amazon...
        </div>
      </Shell>
    );
  }

  return (
    <Shell
      title={result.success ? "Amazon connected" : "Amazon not connected"}
      description={
        result.success
          ? "The refresh token is stored, encrypted, and the connection was verified."
          : "Nothing was left half-done — the settings screen has the fix."
      }
    >
      <Alert variant={result.success ? "default" : "destructive"}>
        {result.success ? <CircleCheck /> : <CircleX />}
        <AlertTitle>{result.success ? "Connected" : "Connection failed"}</AlertTitle>
        <AlertDescription>{result.message}</AlertDescription>
      </Alert>

      {result.selling_partner_id && (
        <div>
          <div className="text-muted-foreground text-xs">Selling Partner ID</div>
          <code className="text-xs">{result.selling_partner_id}</code>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() => {
            // Carried as query parameters because the round trip left the app: the
            // settings screen has no state of ours left to read.
            const search = new URLSearchParams({
              connected: result.success ? "1" : "0",
              message: result.message,
            });
            router.replace(`${AMAZON_SETTINGS_HREF}?${search.toString()}`);
          }}
        >
          <Settings /> Back to Amazon settings
        </Button>
        {result.success && (
          <Button variant="outline" asChild>
            <Link href="/os/channels/amazon/listings">Go to listings</Link>
          </Button>
        )}
      </div>
    </Shell>
  );
}

/** A page of its own, not an OS screen: this renders outside the sidebar layout. */
function Shell({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">{children}</CardContent>
      </Card>
    </div>
  );
}
