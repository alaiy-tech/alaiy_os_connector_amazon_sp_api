"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@alaiy-os/ui/card";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@alaiy-os/ui/table";
import { cn } from "@alaiy-os/utils";
import { HeartPulse, MessageSquareQuote, RefreshCw, ShieldAlert, Star } from "lucide-react";
import { toast } from "sonner";

import { amazonErrorMessage, fetchConnectionStatus, fetchHealthSummary, syncHealth } from "@/lib/amazon/api";
import {
  amazonCount,
  amazonDate,
  amazonDateTime,
  metricValue,
  sectionLabel,
  targetLabel,
  textOr,
} from "@/lib/amazon/format";
import type { AmazonConnectionStatus, AmazonHealthMetric, AmazonHealthSummary } from "@/lib/amazon/types";

import { ConnectorBlocker } from "../../_components/connector-blocker";
import { ListEmpty } from "../../_components/list-empty";
import {
  ANY_MARKETPLACE,
  MarketplacePicker,
  marketplaceName,
  useMarketplaces,
} from "@/components/amazon/marketplace-picker";
import { HealthStatusBadge } from "@/components/amazon/status-badge";

/**
 * Amazon's own verdict on this seller account.
 *
 * Local rows again: the metrics are whatever the last health sync wrote, daily by
 * the scheduler or on demand from here. So the sync timestamp is not decoration —
 * a healthy-looking account whose metrics are three weeks old is telling you about
 * three weeks ago, and the header says which.
 *
 * The A-to-Z and chargeback counts ride along on the metric rows rather than being
 * metrics of their own (they are counts, not percentages against a target), so they
 * are pulled out of whichever row carries them and shown separately.
 */
export function AccountHealth() {
  const { marketplaces } = useMarketplaces();

  const [summary, setSummary] = useState<AmazonHealthSummary | null>(null);
  const [connection, setConnection] = useState<AmazonConnectionStatus | null>(null);
  const [marketplace, setMarketplace] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [failed, setFailed] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how a sync re-reads.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setFailed(false);
      try {
        const [health, connectionStatus] = await Promise.all([
          fetchHealthSummary(marketplace || undefined),
          fetchConnectionStatus().catch(() => null),
        ]);
        if (cancelled) return;
        setSummary(health);
        setConnection(connectionStatus);
      } catch (error) {
        if (cancelled) return;
        setSummary(null);
        setFailed(true);
        toast.error(amazonErrorMessage(error, "Could not load the account health."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [marketplace, reloadToken]);

  /**
   * Blocking, unlike the listing jobs: a health sync is a handful of report calls
   * rather than a thousand SKUs, so it finishes inside a request and the metrics
   * below can be re-read immediately instead of appearing whenever a worker gets
   * to them.
   */
  async function sync() {
    setSyncing(true);
    try {
      await syncHealth(marketplace || undefined);
      toast.success("Synced from Amazon.");
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not sync the account health."));
    } finally {
      setSyncing(false);
    }
  }

  const sections = useMemo(() => groupBySection(summary?.metrics ?? []), [summary]);
  const finances = useMemo(() => financeCounts(summary?.metrics ?? []), [summary]);
  const connected = Boolean(connection?.connected);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {connection && !connected && (
        <ConnectorBlocker
          message={
            connection.message ||
            "No Amazon account is authorized, so these metrics cannot be refreshed. Anything below is from an earlier sync."
          }
          onRetry={reload}
        />
      )}

      <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex flex-wrap items-center gap-2">
              Account status
              <HealthStatusBadge status={summary?.overall_status} />
            </CardTitle>
            <CardDescription>
              {summary?.synced_at
                ? `Last synced ${amazonDateTime(summary.synced_at)} · ${marketplaceName(marketplaces, summary.marketplace)}`
                : "Never synced. The scheduler runs this daily, or sync it now."}
            </CardDescription>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <MarketplacePicker
              value={marketplace}
              onChange={(value) => setMarketplace(value === ANY_MARKETPLACE ? "" : value)}
              emptyLabel="Primary marketplace"
              className="w-44"
            />
            <Button variant="outline" size="sm" onClick={() => void sync()} disabled={syncing || !connected}>
              <RefreshCw className={syncing ? "animate-spin" : undefined} /> Sync now
            </Button>
          </div>
        </CardHeader>

        {(finances.guarantees !== null || finances.chargebacks !== null) && (
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <FinanceTile
              icon={ShieldAlert}
              label="A-to-Z Guarantee claims"
              value={finances.guarantees}
              hint="Buyer claims Amazon adjudicates. Any at all is worth reading."
            />
            <FinanceTile
              icon={ShieldAlert}
              label="Chargebacks"
              value={finances.chargebacks}
              hint="Payment disputes raised through the buyer's card issuer."
            />
          </CardContent>
        )}
      </Card>

      {sections.length === 0 ? (
        <Card>
          <CardContent className="px-0">
            {failed ? (
              <ListEmpty icon={HeartPulse} title="Could not load the metrics" description="Try again in a moment." />
            ) : (
              <ListEmpty
                icon={HeartPulse}
                title="No health metrics yet"
                description={
                  connected
                    ? "Nothing has been synced for this marketplace. Sync now reads them from Amazon."
                    : "Connect an Amazon account, then sync — Amazon reports these per marketplace."
                }
              />
            )}
          </CardContent>
        </Card>
      ) : (
        sections.map(([section, metrics]) => (
          <Card key={section} className="gap-0 py-0">
            <CardHeader className="border-b px-4 py-3">
              <CardTitle className="text-base">{sectionLabel(section)}</CardTitle>
            </CardHeader>
            <CardContent className="px-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Metric</TableHead>
                      <TableHead className="text-right">Value</TableHead>
                      <TableHead className="text-right">Target</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {metrics.map((metric) => (
                      <MetricRow key={`${metric.marketplace}-${metric.metric_key}`} metric={metric} />
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        ))
      )}

      <Feedback summary={summary} />
    </div>
  );
}

function MetricRow({ metric }: { metric: AmazonHealthMetric }) {
  const missed = isMissingTarget(metric);

  return (
    <TableRow>
      <TableCell className="text-sm">{textOr(metric.metric_label, metric.metric_key)}</TableCell>
      <TableCell className={cn("text-right text-sm tabular-nums", missed && "font-medium text-destructive")}>
        {metricValue(metric.metric_value)}
      </TableCell>
      <TableCell className="text-right text-muted-foreground text-sm tabular-nums">
        {targetLabel(metric.metric_target, metric.higher_is_better)}
      </TableCell>
      <TableCell>
        <HealthStatusBadge status={metric.health_status} />
      </TableCell>
    </TableRow>
  );
}

function Feedback({ summary }: { summary: AmazonHealthSummary | null }) {
  const feedback = summary?.feedback ?? [];

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b px-4 py-3">
        <CardTitle className="text-base">Recent seller feedback</CardTitle>
        <CardDescription>The 50 most recent ratings buyers left, newest first.</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {feedback.length === 0 ? (
          <ListEmpty
            icon={MessageSquareQuote}
            title="No feedback recorded"
            description="Feedback arrives with a health sync. An account with no orders yet has none."
          />
        ) : (
          <div className="divide-y">
            {feedback.map((entry, index) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: feedback rows carry no id, and one order can legitimately appear twice
                key={index}
                className="flex items-start gap-3 px-4 py-3"
              >
                <Stars rating={entry.rating} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    {entry.comment || <span className="text-muted-foreground">No comment</span>}
                  </p>
                  <div className="flex flex-wrap items-center gap-2 text-muted-foreground text-xs">
                    <span>{amazonDate(entry.feedback_date)}</span>
                    {entry.order_id && <code className="text-xs">{entry.order_id}</code>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Amazon's 1–5 scale. A rating of 1 or 2 counts against the account's health. */
function Stars({ rating }: { rating?: number | null }) {
  const value = Math.max(0, Math.min(5, Math.trunc(Number(rating) || 0)));
  return (
    <div className="flex shrink-0 items-center gap-0.5" role="img" aria-label={`${value} out of 5`}>
      {Array.from({ length: 5 }, (_, index) => (
        <Star
          // biome-ignore lint/suspicious/noArrayIndexKey: five fixed positions, never reordered
          key={index}
          className={cn("size-3.5", index < value ? "fill-amber-400 text-amber-400" : "text-muted-foreground/40")}
        />
      ))}
    </div>
  );
}

function FinanceTile({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof ShieldAlert;
  label: string;
  value: number | null;
  hint: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <Icon className={cn("mt-0.5 size-4 shrink-0", value ? "text-destructive" : "text-muted-foreground")} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{label}</span>
          <Badge variant="outline" className="tabular-nums">
            {amazonCount(value)}
          </Badge>
        </div>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </div>
    </div>
  );
}

/**
 * Amazon groups these as customer service and shipping, and the API already sorts
 * by section then label — so this preserves the order it arrived in rather than
 * imposing one, which keeps the screen matching Seller Central's own layout.
 */
function groupBySection(metrics: AmazonHealthMetric[]): Array<[string, AmazonHealthMetric[]]> {
  const groups = new Map<string, AmazonHealthMetric[]>();
  for (const metric of metrics) {
    const key = metric.section ?? "other";
    const existing = groups.get(key);
    if (existing) existing.push(metric);
    else groups.set(key, [metric]);
  }
  return [...groups.entries()];
}

/** The two counts travel on whichever metric row carried them; find the first set. */
function financeCounts(metrics: AmazonHealthMetric[]): { guarantees: number | null; chargebacks: number | null } {
  const guarantees = metrics.find(
    (metric) => metric.finances_guarantees !== null && metric.finances_guarantees !== undefined,
  );
  const chargebacks = metrics.find(
    (metric) => metric.finances_chargebacks !== null && metric.finances_chargebacks !== undefined,
  );
  return {
    guarantees: guarantees?.finances_guarantees ?? null,
    chargebacks: chargebacks?.finances_chargebacks ?? null,
  };
}

/**
 * Whether a metric is the wrong side of its target.
 *
 * Recomputed rather than read off `health_status`, because that field is Amazon's
 * three-way roll-up (which includes "warn" for approaching a target) and this is
 * only about colouring the number itself.
 */
function isMissingTarget(metric: AmazonHealthMetric): boolean {
  const value = metric.metric_value;
  const target = metric.metric_target;
  if (value === null || value === undefined || target === null || target === undefined) return false;
  return metric.higher_is_better ? value < target : value > target;
}
