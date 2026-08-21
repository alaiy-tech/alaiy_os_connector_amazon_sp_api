import { Badge } from "@alaiy-os/ui/badge";
import { cn } from "@alaiy-os/utils";

/**
 * The tinted badges these screens use for Amazon's own status vocabularies.
 *
 * `Badge`'s variants carry no "good"/"warning" pair — `destructive` is the only
 * semantic colour it ships — so the tints are spelled out here in the same shape
 * the base uses for its own status pills, once, rather than at each call site.
 */
const TINTS = {
  good: "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  warn: "border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  bad: "border-destructive/20 bg-destructive/10 text-destructive",
  /** Nothing has been read yet — distinct from "read, and it's fine". */
  unknown: "border-border bg-muted text-muted-foreground",
} as const;

type Tint = keyof typeof TINTS;

function Tinted({ tint, children, className }: { tint: Tint; children: React.ReactNode; className?: string }) {
  return (
    <Badge variant="outline" className={cn(TINTS[tint], className)}>
      {children}
    </Badge>
  );
}

/**
 * `Amazon Product Listing.listing_status`.
 *
 * `pending` is this connector's own addition to Amazon's set: the Listings API is
 * asynchronous, so a pushed change is only *accepted*, and the row sits pending
 * until a sync confirms what Amazon actually did with it. It reads as a warning
 * rather than a success for exactly that reason.
 */
const LISTING_TINTS: Record<string, Tint> = {
  active: "good",
  pending: "warn",
  incomplete: "warn",
  suppressed: "bad",
  inactive: "unknown",
};

const LISTING_LABELS: Record<string, string> = {
  active: "Active",
  inactive: "Inactive",
  suppressed: "Suppressed",
  incomplete: "Incomplete",
  pending: "Pending",
};

export function ListingStatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const key = (status ?? "").trim().toLowerCase();
  if (!key) return <span className="text-muted-foreground">—</span>;
  return (
    <Tinted tint={LISTING_TINTS[key] ?? "unknown"} className={className}>
      {LISTING_LABELS[key] ?? key}
    </Tinted>
  );
}

/** `Account Health Metric.health_status`, and the roll-up over all of them. */
const HEALTH_TINTS: Record<string, Tint> = {
  ok: "good",
  warn: "warn",
  critical: "bad",
  unknown: "unknown",
};

const HEALTH_LABELS: Record<string, string> = {
  ok: "Healthy",
  warn: "At risk",
  critical: "Critical",
  unknown: "Not synced",
};

export function HealthStatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const key = (status ?? "unknown").trim().toLowerCase();
  return (
    <Tinted tint={HEALTH_TINTS[key] ?? "unknown"} className={className}>
      {HEALTH_LABELS[key] ?? key}
    </Tinted>
  );
}

/** `Amazon Connection.last_status`. "Connected" here means the last check passed. */
const CONNECTION_TINTS: Record<string, Tint> = {
  connected: "good",
  error: "bad",
  not_configured: "unknown",
};

const CONNECTION_LABELS: Record<string, string> = {
  connected: "Connected",
  error: "Error",
  not_configured: "Not connected",
};

export function ConnectionStatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const key = (status ?? "not_configured").trim().toLowerCase();
  return (
    <Tinted tint={CONNECTION_TINTS[key] ?? "unknown"} className={className}>
      {CONNECTION_LABELS[key] ?? "Not connected"}
    </Tinted>
  );
}

/** An `Amazon Listing Issue` severity, as Amazon grades it. */
const SEVERITY_TINTS: Record<string, Tint> = {
  ERROR: "bad",
  WARNING: "warn",
  INFO: "unknown",
};

export function IssueSeverityBadge({ severity }: { severity?: string | null }) {
  const key = (severity ?? "INFO").trim().toUpperCase();
  return <Tinted tint={SEVERITY_TINTS[key] ?? "unknown"}>{key}</Tinted>;
}
