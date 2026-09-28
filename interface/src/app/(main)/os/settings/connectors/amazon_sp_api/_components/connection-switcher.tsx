"use client";

import { useState } from "react";

import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@alaiy-os/ui/dialog";
import { Input } from "@alaiy-os/ui/input";
import { Label } from "@alaiy-os/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@alaiy-os/ui/select";
import { Spinner } from "@alaiy-os/ui/spinner";
import { Plus, Star, Store } from "lucide-react";

import { ConnectionStatusBadge } from "@/components/amazon/status-badge";
import type { AmazonConnectionSummary } from "@/lib/amazon/types";

import { REGIONS } from "./connection-card";

/**
 * Same alphabet the backend enforces, and for the same reason: a connection id
 * becomes the DocType's name, so a "/" would end a REST path early and a "%"
 * would be decoded twice on the way to the consent redirect. Checked here too so
 * the operator is told while they are typing rather than after a round trip.
 */
const CONNECTION_ID = /^[A-Za-z0-9._-]+$/;

/**
 * Which seller the rest of this screen is about.
 *
 * A bench holds several since "one bench, many sellers": the connection is a
 * choice before it is a status, and every card below — including Connect —
 * names whatever is chosen here. Without it the screen showed whichever
 * connection `resolve()` picked and gave no way to reach the others, so the
 * second seller on a bench was invisible and the first was unsaveable.
 *
 * Rendered even for a single connection, where the select has one option. The
 * alternative — hiding it until there are two — means the Add button that makes
 * the second one appears only once you already have it.
 */
export function ConnectionSwitcher({
  connections,
  selected,
  busy,
  onSelect,
  onAdd,
  onMakeDefault,
}: {
  connections: AmazonConnectionSummary[];
  selected: string | null;
  busy: boolean;
  onSelect: (connection: string) => void;
  onAdd: (values: { connection_id: string; label: string; region: string }) => Promise<void>;
  onMakeDefault: () => void;
}) {
  const current = connections.find((entry) => entry.connection === selected) ?? null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle>Amazon connections</CardTitle>
            <CardDescription>
              {connections.length === 0
                ? "No seller account on this site yet. Add one to configure it."
                : "Every card below is about the seller chosen here."}
            </CardDescription>
          </div>
          <AddConnectionDialog busy={busy} taken={connections.map((entry) => entry.connection)} onAdd={onAdd} />
        </div>
      </CardHeader>

      {connections.length > 0 && (
        <CardContent className="flex flex-wrap items-center gap-3">
          <Select value={selected ?? undefined} onValueChange={onSelect} disabled={busy}>
            <SelectTrigger className="w-72">
              <SelectValue placeholder="Choose a connection" />
            </SelectTrigger>
            <SelectContent>
              {connections.map((entry) => (
                <SelectItem key={entry.connection} value={entry.connection}>
                  <span className="flex items-center gap-2">
                    <Store className="size-3.5 text-muted-foreground" />
                    {entry.label}
                    {entry.is_default && <Star className="size-3 fill-current text-amber-500" />}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {current && <ConnectionStatusBadge status={current.status} />}

          {current?.selling_partner_id && (
            <code className="text-muted-foreground text-xs">{current.selling_partner_id}</code>
          )}

          {/* The flag decides which seller every *unnamed* call answers with —
              the scheduled syncs, the agent tools, the Desk. Worth being able to
              set from here, and worth saying what it does, because nothing else
              on this screen depends on it. */}
          {current?.is_default ? (
            <span className="flex items-center gap-1 text-muted-foreground text-xs">
              <Star className="size-3 fill-current text-amber-500" /> Default — calls that name no connection use this
              one
            </span>
          ) : (
            <Button variant="outline" size="sm" onClick={onMakeDefault} disabled={busy || !current}>
              <Star /> Make default
            </Button>
          )}
        </CardContent>
      )}
    </Card>
  );
}

/**
 * What is wrong with the id as typed, or null while there is nothing to say.
 *
 * An empty box is not an error — it is where everyone starts — so it reads as
 * "nothing yet" and the submit button stays disabled on its own.
 */
function idProblem(id: string, taken: string[]): string | null {
  if (!id) return null;
  if (!CONNECTION_ID.test(id)) return "Letters, numbers, dots, dashes and underscores only.";
  if (taken.includes(id)) return "There is already a connection with this id.";
  return null;
}

/**
 * Adding a seller.
 *
 * The id is the operator's to choose because it becomes the record's name and
 * outlives every label: order provenance, listing rows and the OAuth state all
 * carry it. A generated one would read as noise everywhere it surfaces.
 *
 * Nothing here authorizes anything. A new connection is an empty row until
 * Connect is pressed for it, which is deliberate — the consent round trip has to
 * know which seller it is for before it starts, so the row comes first.
 */
function AddConnectionDialog({
  busy,
  taken,
  onAdd,
}: {
  busy: boolean;
  taken: string[];
  onAdd: (values: { connection_id: string; label: string; region: string }) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [connectionId, setConnectionId] = useState("");
  const [label, setLabel] = useState("");
  const [region, setRegion] = useState("NA");

  const id = connectionId.trim();
  const problem = idProblem(id, taken);

  function reset() {
    setConnectionId("");
    setLabel("");
    setRegion("NA");
  }

  async function submit() {
    if (!id || problem) return;
    setSaving(true);
    try {
      await onAdd({ connection_id: id, label: label.trim() || id, region });
      reset();
      setOpen(false);
    } catch {
      // Left open, and left filled in: the caller has already said why in a
      // toast, and closing would throw away what was typed over an id that only
      // needs a character changed.
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} disabled={busy}>
        <Plus /> Add connection
      </Button>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an Amazon connection</DialogTitle>
          <DialogDescription>
            One seller account. It is created unauthorized — use Connect afterwards to run the consent round trip for
            it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="amz-connection-id">Connection ID</Label>
            <Input
              id="amz-connection-id"
              value={connectionId}
              onChange={(event) => setConnectionId(event.target.value)}
              placeholder="acme-uk"
              autoComplete="off"
            />
            <p className={problem ? "text-destructive text-xs" : "text-muted-foreground text-xs"}>
              {problem ?? "Permanent — it names the record that orders and listings are filed against."}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amz-connection-label">Label</Label>
            <Input
              id="amz-connection-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Acme UK"
              autoComplete="off"
            />
            <p className="text-muted-foreground text-xs">
              What this screen calls it. Defaults to the id, and can change later.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="amz-connection-region">Region</Label>
            <Select value={region} onValueChange={setRegion}>
              <SelectTrigger id="amz-connection-region">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REGIONS.map((entry) => (
                  <SelectItem key={entry.value} value={entry.value}>
                    {entry.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              Where this seller's marketplaces are. `amazon_region` in site_config overrides it if set.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={saving || !id || Boolean(problem)}>
            {saving ? (
              <>
                <Spinner /> Adding...
              </>
            ) : (
              "Add connection"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
