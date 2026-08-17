"use client";

import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { RefreshCw, Settings, TriangleAlert } from "lucide-react";

/** This connector's own settings screen — no Desk, no second UI to learn. */
export const AMAZON_SETTINGS_HREF = "/os/channels/amazon/settings";
export const AMAZON_LISTINGS_HREF = "/os/channels/amazon/listings";
export const AMAZON_HEALTH_HREF = "/os/channels/amazon/health";

/**
 * Why a screen can't do its job, with the one action that fixes it.
 *
 * The register and the health metrics are local rows, so they still *render*
 * without a connection — what stops working is every action that reaches Amazon.
 * This is the banner above them, not a replacement for them: an empty register on
 * an unconnected account is a connection problem, and saying so beats an empty
 * state that reads as "you have no listings".
 */
export function ConnectorBlocker({
  message,
  onRetry,
  title = "Amazon is not connected",
}: {
  message: string;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <Alert variant="destructive">
      <TriangleAlert />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href={AMAZON_SETTINGS_HREF}>
              <Settings /> Open Amazon settings
            </Link>
          </Button>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              <RefreshCw /> Retry
            </Button>
          )}
        </div>
      </AlertDescription>
    </Alert>
  );
}
