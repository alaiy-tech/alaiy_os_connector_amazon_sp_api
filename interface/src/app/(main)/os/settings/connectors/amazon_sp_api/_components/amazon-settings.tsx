"use client";

import { useEffect, useState } from "react";

import { useSearchParams } from "next/navigation";

import { type ConnectorConfig, fetchConnectorConfig, saveAndTestConnector } from "@alaiy-os/frappe/connectors";
import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Spinner } from "@alaiy-os/ui/spinner";
import { CircleCheck, CircleX, Plug, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ANY_MARKETPLACE } from "@/components/amazon/marketplace-picker";
import {
  amazonErrorMessage,
  disconnectAmazon,
  ensureConnection,
  fetchConfigStatus,
  fetchConnectionStatus,
  fetchConsentUrl,
  fetchOrdersSyncStatus,
  syncOrders,
  testConnection,
} from "@/lib/amazon/api";
import type { AmazonConfigStatus, AmazonConnectionStatus, AmazonOrdersSyncStatus } from "@/lib/amazon/types";

import { AppCredentialsCard } from "./app-credentials-card";
import { ConnectionCard, type ConnectionForm } from "./connection-card";
import { OrderDefaultsCard, type OrdersForm } from "./order-defaults-card";

const CONNECTOR_ID = "amazon_sp_api";

/** No `Amazon Connection` on this site yet — see AmazonConnectionState. */
const NO_CONNECTION = "no_connection";

/**
 * Why each of the screen's four reads failed, if it did.
 *
 * Kept apart rather than collapsed into one boolean because they fail for
 * unrelated reasons and only one of them is fatal to the whole screen. This
 * used to be a single `Promise.all` and a single alert saying the endpoints
 * "answered nothing usable", which named four causes and distinguished none of
 * them: a missing role, an unregistered connector, a site with no connection
 * and a multi-seller site all arrived as the same red card.
 */
type LoadFailures = {
  connection?: string;
  credentials?: string;
  fields?: string;
  orders?: string;
};

/**
 * Everything this connector needs to run, in the OS.
 *
 * Two different backends, for two different reasons:
 *
 *   * The **DocType fields** (region, marketplace, order defaults) go through the
 *     platform's registry-driven connector API, which reads and writes whatever
 *     settings DocType a connector registered. No endpoint of our own, and the
 *     base still knows nothing about Amazon.
 *   * The **connection itself** goes through this app's own methods, because OAuth
 *     is not a form: there is a consent redirect, a token that never comes back,
 *     and a preflight that can fail for reasons worth reading.
 *
 * Saving always tests, because that is the only call the platform API offers — and
 * it is the right default anyway: settings saved but never tested are how a
 * connector sits at "untested" while every screen quietly refuses to work.
 */
export function AmazonSettings() {
  const searchParams = useSearchParams();

  const [status, setStatus] = useState<AmazonConnectionStatus | null>(null);
  const [config, setConfig] = useState<AmazonConfigStatus | null>(null);
  const [ordersStatus, setOrdersStatus] = useState<AmazonOrdersSyncStatus | null>(null);
  const [connection, setConnection] = useState<ConnectionForm>({
    region: "NA",
    appStatus: "Draft",
    primaryMarketplace: "",
  });
  const [orders, setOrders] = useState<OrdersForm>({
    customer: "",
    company: "",
    warehouse: "",
    priceList: "",
    fallbackItem: "",
    syncFrom: "",
  });

  const [failures, setFailures] = useState<LoadFailures>({});
  /** The DocType fields are loaded — so saving them writes values, not blanks. */
  const [fieldsLoaded, setFieldsLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [syncingOrders, setSyncingOrders] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [origin, setOrigin] = useState<string | null>(null);

  // Read once on mount rather than in render: the first paint is the server's, and
  // there is no window there to ask.
  useEffect(() => setOrigin(window.location.origin), []);

  /**
   * The callback screen sends the operator back here with its verdict, because
   * this is where the fix for a failed authorize lives. It is a query parameter
   * and not a store because the round trip left the app entirely.
   */
  useEffect(() => {
    const outcome = searchParams.get("connected");
    if (!outcome) return;
    const message = searchParams.get("message");
    if (outcome === "1") {
      setResult({ success: true, message: message || "Amazon account connected." });
    } else {
      setResult({ success: false, message: message || "The Amazon authorization did not complete." });
    }
  }, [searchParams]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how a save re-reads.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);

      // Settled, not all: these four reads are independent, and one of them
      // throwing is not a reason to render none of the others. The DocType
      // fields in particular go through the platform's connector API, which can
      // refuse for reasons that say nothing about whether Amazon is reachable.
      const [connectionStatus, configStatus, connectorConfig, ordersSync] = await Promise.allSettled([
        fetchConnectionStatus(),
        fetchConfigStatus(),
        fetchConnectorConfig(CONNECTOR_ID),
        fetchOrdersSyncStatus(),
      ]);
      if (cancelled) return;

      const failed: LoadFailures = {};

      if (connectionStatus.status === "fulfilled") setStatus(connectionStatus.value);
      else {
        setStatus(null);
        failed.connection = amazonErrorMessage(connectionStatus.reason, "Could not read the Amazon connection.");
      }

      if (configStatus.status === "fulfilled") setConfig(configStatus.value);
      else {
        setConfig(null);
        failed.credentials = amazonErrorMessage(configStatus.reason, "Could not read the app credentials.");
      }

      if (connectorConfig.status === "fulfilled") {
        applyValues(connectorConfig.value);
        setFieldsLoaded(true);
      } else {
        setFieldsLoaded(false);
        failed.fields = amazonErrorMessage(connectorConfig.reason, "Could not read this connector's settings fields.");
      }

      // The one read that can legitimately fail on its own: no ERPNext on the
      // site means no Sales Order to count. Its card copes with a null.
      if (ordersSync.status === "fulfilled") setOrdersStatus(ordersSync.value);
      else {
        setOrdersStatus(null);
        failed.orders = amazonErrorMessage(ordersSync.reason, "Could not read the order sync status.");
      }

      setFailures(failed);
      setLoading(false);
    }

    function applyValues(connectorConfig: ConnectorConfig) {
      const values = connectorConfig.values;
      setConnection({
        region: asText(values.region) || "NA",
        appStatus: asText(values.app_status) || "Draft",
        primaryMarketplace: asText(values.primary_marketplace),
      });
      setOrders({
        customer: asText(values.orders_customer),
        company: asText(values.orders_company),
        warehouse: asText(values.orders_warehouse),
        priceList: asText(values.orders_selling_price_list),
        fallbackItem: asText(values.orders_fallback_item),
        syncFrom: toInputDateTime(asText(values.orders_sync_from)),
      });
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  function reload() {
    setReloadToken((token) => token + 1);
  }

  async function save() {
    setBusy(true);
    try {
      const values: Record<string, unknown> = {
        region: connection.region,
        app_status: connection.appStatus,
        primary_marketplace: connection.primaryMarketplace || null,
        orders_customer: orders.customer || null,
        orders_company: orders.company || null,
        orders_warehouse: orders.warehouse || null,
        orders_selling_price_list: orders.priceList || null,
        orders_fallback_item: orders.fallbackItem || null,
        orders_sync_from: toFrappeDateTime(orders.syncFrom),
      };

      // A bench with no connection has nothing for the platform's save to write
      // to, and the platform cannot create the first row: `Amazon Connection` is
      // named `field:connection_id`, which only this app knows. So the first
      // save is what makes the connection, which is also the only moment it is
      // unambiguous — with no other seller on the site there is none to confuse
      // it with.
      if (status?.status === NO_CONNECTION) await ensureConnection();

      const outcome = await saveAndTestConnector(CONNECTOR_ID, values);

      if (outcome.success) {
        setResult(outcome);
        toast.success("Saved. Amazon accepted the connection.");
      } else if (!status?.connected) {
        // The test the platform API always runs can only report "not connected"
        // until the account is authorized. That is not a failed save, and showing
        // it as one would have every first-time setup look broken.
        setResult(null);
        toast.success("Saved. Connect the Amazon account to finish.");
      } else {
        setResult(outcome);
        toast.error(outcome.message || "Saved, but the connection test failed.");
      }
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not save the Amazon settings."));
    } finally {
      setBusy(false);
    }
  }

  async function connect() {
    setConnecting(true);
    setResult(null);
    try {
      const { url } = await fetchConsentUrl();
      // Leaves the OS for Amazon's consent screen and comes back to
      // /amazon-oauth/callback. Deliberately not a new tab: the state is
      // single-use and session-bound, so a stray second attempt in the original
      // tab could only ever fail.
      window.location.assign(url);
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not start the Amazon authorization."));
      setConnecting(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setResult(null);
    try {
      await disconnectAmazon();
      toast.success("Disconnected. The stored token is gone.");
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not disconnect the Amazon account."));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await testConnection();
      setResult(outcome);
      if (outcome.success) toast.success("Connected.");
      else toast.error(outcome.message || "The connection test failed.");
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not test the Amazon connection."));
    } finally {
      setBusy(false);
    }
  }

  async function syncOrdersNow() {
    setSyncingOrders(true);
    try {
      await syncOrders(connection.primaryMarketplace || undefined);
      toast.success("Order sync queued. It runs in the background.");
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not start the order sync."));
    } finally {
      setSyncingOrders(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  // Each read that failed, said in its own words. A screen where three of the
  // four worked shows the three and names the one that did not, rather than
  // replacing all of it with a guess at what went wrong.
  const failureList = [failures.connection, failures.credentials, failures.fields, failures.orders].filter(
    (message): message is string => Boolean(message),
  );

  return (
    <div className="flex flex-col gap-4">
      {failureList.length > 0 && (
        <Alert variant="destructive">
          <CircleX />
          <AlertTitle>
            {failureList.length === 1 ? "Part of this screen did not load" : "Parts of this screen did not load"}
          </AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 pl-4">
              {failureList.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
            <Button variant="outline" size="sm" onClick={reload}>
              <RefreshCw /> Try again
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {result && (
        <Alert variant={result.success ? "default" : "destructive"}>
          {result.success ? <CircleCheck /> : <CircleX />}
          <AlertTitle>{result.success ? "Connected" : "Connection failed"}</AlertTitle>
          <AlertDescription>{result.message}</AlertDescription>
        </Alert>
      )}

      {status?.status === NO_CONNECTION && (
        <Alert>
          <Plug />
          <AlertTitle>No Amazon connection on this site yet</AlertTitle>
          <AlertDescription>
            Nothing is stored for this connector. Set the region and marketplace below and save, or connect the account
            straight away — either one creates the connection this site will use.
          </AlertDescription>
        </Alert>
      )}

      {status && (
        <ConnectionCard
          status={status}
          form={connection}
          onChange={(key, value) =>
            setConnection((current) => ({
              ...current,
              // The picker's "not set" choice is a sentinel, not a marketplace.
              [key]: value === ANY_MARKETPLACE ? "" : value,
            }))
          }
          configReady={Boolean(config?.ready)}
          busy={busy}
          connecting={connecting}
          onConnect={() => void connect()}
          onDisconnect={() => void disconnect()}
          onTest={() => void test()}
        />
      )}

      {config && <AppCredentialsCard config={config} origin={origin} />}

      {/* Both of these edit the connector's own DocType fields, so neither is
          rendered when those did not load: an empty form over real stored values
          is one Save away from erasing them. */}
      {fieldsLoaded && (
        <>
          <OrderDefaultsCard
            status={ordersStatus}
            form={orders}
            onChange={(key, value) => setOrders((current) => ({ ...current, [key]: value }))}
            busy={busy}
            syncing={syncingOrders}
            onSyncNow={() => void syncOrdersNow()}
            canSync={Boolean(status?.connected && ordersStatus?.configured)}
          />

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void save()} disabled={busy}>
              {busy ? (
                <>
                  <Spinner /> Working...
                </>
              ) : (
                "Save and test"
              )}
            </Button>
            <span className="text-muted-foreground text-xs">
              Saves the region, marketplace and order defaults, then re-runs the connection test.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  // A Password field arrives as `{_type, _set}`; there is none on this form, but
  // reading one as "[object Object]" is not a failure mode worth leaving open.
  if (typeof value === "object") return "";
  return String(value);
}

/** Frappe's "YYYY-MM-DD HH:mm:ss" → what `<input type="datetime-local">` takes. */
function toInputDateTime(value: string): string {
  if (!value) return "";
  return value.replace(" ", "T").slice(0, 16);
}

/** …and back. Null rather than "" so a cleared field really clears the field. */
function toFrappeDateTime(value: string): string | null {
  if (!value) return null;
  const [date, time = "00:00"] = value.split("T");
  return `${date} ${time.length === 5 ? `${time}:00` : time}`;
}
