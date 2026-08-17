"use client";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@alaiy-os/ui/alert-dialog";
import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Label } from "@alaiy-os/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { Spinner } from "@alaiy-os/ui/spinner";
import { Plug, PlugZap, Unplug } from "lucide-react";

import { amazonDateTime, textOr } from "@/lib/amazon/format";
import type { AmazonConnectionStatus } from "@/lib/amazon/types";

import { MarketplacePicker } from "../../_components/marketplace-picker";
import { ConnectionStatusBadge } from "../../_components/status-badge";

/** `Amazon Connection.region` — the three SP-API region groups. */
const REGIONS = [
  { value: "NA", label: "NA — North America" },
  { value: "EU", label: "EU — Europe, India, Middle East" },
  { value: "FE", label: "FE — Far East, Australia" },
];

/** Draft apps must ask for the beta consent, or Amazon refuses the authorize. */
const APP_STATUSES = [
  { value: "Draft", label: "Draft — request beta consent" },
  { value: "Published", label: "Published" },
];

export interface ConnectionForm {
  region: string;
  appStatus: string;
  primaryMarketplace: string;
}

/**
 * The seller account itself: who is connected, where, and the two buttons that
 * change that.
 *
 * There is no field for the refresh token. It is not something anyone types — the
 * seller authorizes on Amazon's own consent screen and only the token that comes
 * back is stored, encrypted, which is the whole point of the OAuth flow and the
 * reason this screen has a Connect button instead of a password box.
 */
export function ConnectionCard({
  status,
  form,
  onChange,
  configReady,
  busy,
  connecting,
  onConnect,
  onDisconnect,
  onTest,
}: {
  status: AmazonConnectionStatus;
  form: ConnectionForm;
  onChange: <K extends keyof ConnectionForm>(key: K, value: ConnectionForm[K]) => void;
  configReady: boolean;
  busy: boolean;
  connecting: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onTest: () => void;
}) {
  // site_config's `amazon_region` overrides the field below, so what is being
  // edited and what is being called can legitimately differ. Say so rather than
  // letting someone change a select that has no effect.
  const regionOverridden = Boolean(status.region) && status.region !== form.region;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Amazon account
          <ConnectionStatusBadge status={status.status} />
          {status.use_sandbox && <Badge variant="outline">sandbox</Badge>}
        </CardTitle>
        <CardDescription>
          {status.connected
            ? "Authorized. The screens below and every scheduled job use this account."
            : "Not authorized yet. Nothing in this connector can reach Amazon until it is."}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <ReadOnly label="Selling Partner ID" value={status.selling_partner_id} mono />
          <ReadOnly label="Connected at" value={amazonDateTime(status.connected_at)} />
          <ReadOnly label="SP-API endpoint in use" value={status.endpoint} mono />
          <ReadOnly label="Consent host" value={status.consent_base_url} mono />
        </div>

        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="amazon-region">Region</Label>
            <Select value={form.region || "NA"} onValueChange={(value) => onChange("region", value)} disabled={busy}>
              <SelectTrigger id="amazon-region" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REGIONS.map((region) => (
                  <SelectItem key={region.value} value={region.value}>
                    {region.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              {regionOverridden
                ? `Overridden by the amazon_region site config key — ${status.region} is what is called.`
                : "Which region group the seller's marketplaces sit in. India is EU."}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amazon-app-status">App status</Label>
            <Select
              value={form.appStatus || "Draft"}
              onValueChange={(value) => onChange("appStatus", value)}
              disabled={busy}
            >
              <SelectTrigger id="amazon-app-status" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {APP_STATUSES.map((state) => (
                  <SelectItem key={state.value} value={state.value}>
                    {state.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              A Draft SP-API app has to request <code>version=beta</code> on the consent screen.
            </p>
          </div>

          <div className="space-y-2 md:col-span-2">
            <Label>Primary marketplace</Label>
            <MarketplacePicker
              value={form.primaryMarketplace}
              onChange={(value) => onChange("primaryMarketplace", value)}
              disabled={busy}
              emptyLabel="Not set"
              className="w-full"
            />
            <p className="text-muted-foreground text-xs">
              The default for every sync that does not name one. Listings and orders are per-marketplace on Amazon's
              side, so an unset one leaves those actions with nothing to call.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t pt-4">
          <Button onClick={onConnect} disabled={busy || connecting || !configReady}>
            {connecting ? (
              <>
                <Spinner /> Opening Amazon...
              </>
            ) : (
              <>
                <PlugZap /> {status.connected ? "Re-authorize" : "Connect Amazon account"}
              </>
            )}
          </Button>

          <Button variant="outline" onClick={onTest} disabled={busy || !status.connected}>
            <Plug /> Test connection
          </Button>

          {status.connected && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" disabled={busy} className="text-destructive hover:text-destructive">
                  <Unplug /> Disconnect
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Disconnect this Amazon account?</AlertDialogTitle>
                  <AlertDialogDescription>
                    The stored refresh token is deleted and every sync stops. Listings already in the register, and
                    orders already imported, are left exactly as they are — reconnecting the same account picks up where
                    this left off.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={onDisconnect}>Disconnect</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}

          {!configReady && (
            <span className="text-muted-foreground text-xs">
              Connect needs the app credentials below to be set first.
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ReadOnly({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      {mono ? (
        <code className="block break-all text-xs">{textOr(value)}</code>
      ) : (
        <div className="text-sm">{textOr(value)}</div>
      )}
    </div>
  );
}
