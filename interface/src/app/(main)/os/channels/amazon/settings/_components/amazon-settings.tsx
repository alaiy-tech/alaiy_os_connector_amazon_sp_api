"use client";

import { useEffect, useState } from "react";

import { useSearchParams } from "next/navigation";

import { type ConnectorConfig, fetchConnectorConfig, saveAndTestConnector } from "@alaiy-os/frappe/connectors";
import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Spinner } from "@alaiy-os/ui/spinner";
import { CircleCheck, CircleX } from "lucide-react";
import { toast } from "sonner";

import {
  amazonErrorMessage,
  disconnectAmazon,
  fetchConfigStatus,
  fetchConnectionStatus,
  fetchConsentUrl,
  fetchOrdersSyncStatus,
  syncOrders,
  testConnection,
} from "@/lib/amazon/api";
import type { AmazonConfigStatus, AmazonConnectionStatus, AmazonOrdersSyncStatus } from "@/lib/amazon/types";

import { ANY_MARKETPLACE } from "../../_components/marketplace-picker";
import { AppCredentialsCard } from "./app-credentials-card";
import { ConnectionCard, type ConnectionForm } from "./connection-card";
import { OrderDefaultsCard, type OrdersForm } from "./order-defaults-card";

const CONNECTOR_ID = "amazon_sp_api";

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
      try {
        // The order-sync status is the one read that can legitimately fail on its
        // own (no ERPNext on the site, so no Sales Order to count), and the rest of
        // the screen is still worth rendering without it.
        const [connectionStatus, configStatus, connectorConfig, ordersSync] = await Promise.all([
          fetchConnectionStatus(),
          fetchConfigStatus(),
          fetchConnectorConfig(CONNECTOR_ID),
          fetchOrdersSyncStatus().catch(() => null),
        ]);
        if (cancelled) return;

        setStatus(connectionStatus);
        setConfig(configStatus);
        setOrdersStatus(ordersSync);
        applyValues(connectorConfig);
      } catch (error) {
        if (!cancelled) toast.error(amazonErrorMessage(error, "Could not load the Amazon settings."));
      } finally {
        if (!cancelled) setLoading(false);
      }
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

  if (!status || !config) {
    return (
      <Alert variant="destructive">
        <CircleX />
        <AlertTitle>Could not read the Amazon connector</AlertTitle>
        <AlertDescription>
          <p>
            The settings endpoints answered nothing usable. This needs the System Manager or Amazon Manager role, and
            the connector to be registered in the OS Connector Registry.
          </p>
          <Button variant="outline" size="sm" onClick={reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {result && (
        <Alert variant={result.success ? "default" : "destructive"}>
          {result.success ? <CircleCheck /> : <CircleX />}
          <AlertTitle>{result.success ? "Connected" : "Connection failed"}</AlertTitle>
          <AlertDescription>{result.message}</AlertDescription>
        </Alert>
      )}

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
        configReady={config.ready}
        busy={busy}
        connecting={connecting}
        onConnect={() => void connect()}
        onDisconnect={() => void disconnect()}
        onTest={() => void test()}
      />

      <AppCredentialsCard config={config} origin={origin} />

      <OrderDefaultsCard
        status={ordersStatus}
        form={orders}
        onChange={(key, value) => setOrders((current) => ({ ...current, [key]: value }))}
        busy={busy}
        syncing={syncingOrders}
        onSyncNow={() => void syncOrdersNow()}
        canSync={status.connected && Boolean(ordersStatus?.configured)}
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
