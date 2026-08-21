"use client";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Badge } from "@alaiy-os/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { CircleCheck, CircleX, TriangleAlert } from "lucide-react";

import type { AmazonConfigStatus } from "@/lib/amazon/types";

/**
 * The app's own identity, as reported from `site_config.json`.
 *
 * Read-only on purpose, and not for lack of an endpoint: these are the *SP-API
 * application's* credentials, shared by every seller on the site, and they are
 * deliberately somewhere no request can reach — never a DocType, never the
 * browser. Anyone who can change them has a shell on the bench, which is where
 * `bench set-config` is. So this panel's whole job is to say which of them are
 * missing, since a blank one is the difference between Connect working and
 * Connect being refused.
 *
 * Secret keys report only whether they are set. `get_config_status` never sends
 * their values, so there is nothing here to leak.
 */
export function AppCredentialsCard({ config, origin }: { config: AmazonConfigStatus; origin: string | null }) {
  const required = config.keys.filter((key) => key.required);
  const optional = config.keys.filter((key) => !key.required);
  const missing = required.filter((key) => !key.is_set);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          App credentials
          {config.ready ? (
            <Badge variant="secondary">complete</Badge>
          ) : (
            <Badge variant="destructive">{missing.length} missing</Badge>
          )}
        </CardTitle>
        <CardDescription>
          The SP-API application's own identity, from the site's <code>site_config.json</code>. Shared by the whole
          site, set with <code>bench set-config</code>, and never editable from here.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {!config.ready && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Connect is unavailable until these are set</AlertTitle>
            <AlertDescription>
              <p>Run this on the bench, then reload:</p>
              <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 text-xs">
                {missing.map((key) => `bench --site <site> set-config ${key.key} "..."`).join("\n")}
              </pre>
            </AlertDescription>
          </Alert>
        )}

        <RedirectUri redirectUri={config.redirect_uri} origin={origin} />

        <KeyList title="Required" keys={required} />
        {optional.length > 0 && <KeyList title="Optional" keys={optional} />}
      </CardContent>
    </Card>
  );
}

/**
 * The redirect URI, and whether it points back at *this* app.
 *
 * It is built from `app_url` (falling back to the site URL), and Amazon will only
 * ever redirect to the URL registered in Seller Central — so these three have to
 * agree. When the composed frontend owns the site's hostname, they do, and consent
 * comes back to the OS's own callback screen. When `app_url` names the Desk host
 * instead, Amazon lands on Frappe's callback page: still correct, just a different
 * page, and worth knowing before wondering why the OS never noticed.
 *
 * The mismatch that actually breaks a deployment is neither of those: it is
 * `app_url` naming a host that serves *neither* callback. This is the panel where
 * that shows up, because the URL is right here next to the origin it should match.
 */
function RedirectUri({ redirectUri, origin }: { redirectUri: string; origin: string | null }) {
  const landsHere = Boolean(origin) && redirectUri.startsWith(`${origin}/`);

  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex items-center gap-2">
        {landsHere ? <CircleCheck className="size-4 text-emerald-600" /> : <TriangleAlert className="size-4" />}
        <span className="font-medium text-sm">OAuth redirect URI</span>
      </div>
      <code className="block break-all text-muted-foreground text-xs">{redirectUri}</code>
      <p className="text-muted-foreground text-xs">{redirectHint(landsHere, origin)}</p>
    </div>
  );
}

const REGISTER_IT = "Register this in Seller Central → Develop Apps.";

function redirectHint(landsHere: boolean, origin: string | null): string {
  if (landsHere) return `${REGISTER_IT} Consent returns to this app's own callback screen.`;
  // No origin yet means the first paint, before the effect that reads it — say the
  // part that is true regardless rather than guessing at a mismatch.
  if (!origin) return REGISTER_IT;
  return `${REGISTER_IT} It is not on this app's origin (${origin}), so consent returns to Frappe's own callback page instead — set the app_url site config key to change that.`;
}

function KeyList({ title, keys }: { title: string; keys: AmazonConfigStatus["keys"] }) {
  return (
    <div className="space-y-2">
      <div className="text-muted-foreground text-xs uppercase tracking-wide">{title}</div>
      <div className="divide-y rounded-lg border">
        {keys.map((key) => (
          <div key={key.key} className="flex items-start gap-3 p-3">
            {key.is_set ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600" />
            ) : (
              <CircleX
                className={`mt-0.5 size-4 shrink-0 ${key.required ? "text-destructive" : "text-muted-foreground"}`}
              />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-sm">{key.label}</span>
                <code className="text-muted-foreground text-xs">{key.key}</code>
                {key.secret && <Badge variant="outline">secret</Badge>}
              </div>
              <p className="text-muted-foreground text-xs">{key.description}</p>
              {/* Non-secret keys carry their value; showing it is how a wrong
                  region or a stale consent host gets spotted. */}
              {!key.secret && key.value ? <code className="mt-1 block break-all text-xs">{key.value}</code> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
