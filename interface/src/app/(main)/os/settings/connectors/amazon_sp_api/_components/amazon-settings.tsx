"use client";

import { useEffect, useState } from "react";

import { useSearchParams } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@alaiy-os/ui/alert";
import { Button } from "@alaiy-os/ui/button";
import { Skeleton } from "@alaiy-os/ui/skeleton";
import { Spinner } from "@alaiy-os/ui/spinner";
import { CircleCheck, CircleX, Plug, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ANY_MARKETPLACE } from "@/components/amazon/marketplace-picker";
import {
  amazonErrorMessage,
  createConnection,
  disconnectAmazon,
  ensureConnection,
  fetchConfigStatus,
  fetchConnectionConfig,
  fetchConnectionStatus,
  fetchConsentUrl,
  fetchOrdersSyncStatus,
  listConnections,
  saveConnection,
  setDefaultConnection,
  syncOrders,
  testConnection,
} from "@/lib/amazon/api";
import type {
  AmazonConfigStatus,
  AmazonConnectionConfig,
  AmazonConnectionStatus,
  AmazonConnectionSummary,
  AmazonOrdersSyncStatus,
} from "@/lib/amazon/types";

import { AppCredentialsCard } from "./app-credentials-card";
import { ConnectionCard, type ConnectionForm } from "./connection-card";
import { ConnectionSwitcher } from "./connection-switcher";
import { OrderDefaultsCard, type OrdersForm } from "./order-defaults-card";

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
  /** The list of sellers — fatal in a way the others are not, since it decides
      which connection everything else is about. */
  connections?: string;
  connection?: string;
  credentials?: string;
  fields?: string;
  orders?: string;
};

/**
 * Everything this connector needs to run, in the OS — for one seller at a time.
 *
 * Every read and write below names its connection, because a bench holds several
 * since "one bench, many sellers". `ConnectionSwitcher` is where that name comes
 * from, and nothing here resolves a connection by omission: on a bench with two
 * sellers an unnamed call either refuses or quietly answers about whichever one
 * carries `is_default`, and both are wrong for a screen that is showing the
 * other.
 *
 * That is also why none of this goes through the platform's registry-driven
 * connector API any more. `alaiy_os.api.connectors` is keyed on connector_id and
 * resolves the settings record itself, so it cannot be told which seller is on
 * screen — the same wall `alaiy_os_connector_shopify/api/settings.py` hit, and
 * the same answer: this app's own endpoints, which are the platform's plus the
 * argument the platform cannot have.
 *
 * Saving still always tests. Settings saved but never tested are how a connector
 * sits at "untested" while every screen quietly refuses to work.
 */
export function AmazonSettings() {
  const searchParams = useSearchParams();

  const [connectionList, setConnectionList] = useState<AmazonConnectionSummary[]>([]);
  /** Which seller every call on this screen names. Null only on a bench with none. */
  const [selected, setSelected] = useState<string | null>(null);
  /** The sellers are known — so the reads below can name one instead of guessing. */
  const [listLoaded, setListLoaded] = useState(false);
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

  /**
   * Which sellers exist, and which one is on screen.
   *
   * Read on its own and before the rest, because it decides what the rest is
   * *about*. Folding it into the reads below would mean firing them with no
   * connection named — which on a multi-seller bench is a refusal, and on one
   * with a default is an answer about the wrong seller.
   */
  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how a save re-reads.
  useEffect(() => {
    let cancelled = false;

    async function loadConnections() {
      try {
        const rows = await listConnections();
        if (cancelled) return;
        setConnectionList(rows);
        // Keep the operator where they were across a reload; otherwise the
        // default, otherwise whatever exists. A bench with none stays null, and
        // the reads below then name nothing on purpose — there is nothing to name.
        setSelected((current) => {
          if (current && rows.some((row) => row.connection === current)) return current;
          return rows.find((row) => row.is_default)?.connection ?? rows[0]?.connection ?? null;
        });
        setFailures((current) => ({ ...current, connections: undefined }));
      } catch (error) {
        if (cancelled) return;
        setConnectionList([]);
        setFailures((current) => ({
          ...current,
          connections: amazonErrorMessage(error, "Could not list the Amazon connections."),
        }));
      } finally {
        if (!cancelled) setListLoaded(true);
      }
    }

    void loadConnections();
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a trigger, not a value read here — bumping it is how a save re-reads.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      // Nothing to scope the reads to yet. `loading` stays true, so the screen
      // shows its skeleton rather than a set of cards about nobody.
      if (!listLoaded) return;
      setLoading(true);
      const connection = selected ?? undefined;

      // Settled, not all: these four reads are independent, and one of them
      // throwing is not a reason to render none of the others. The app
      // credentials in particular are a site_config question, and answer nothing
      // about whether this seller is reachable.
      const [connectionStatus, configStatus, connectionConfig, ordersSync] = await Promise.allSettled([
        fetchConnectionStatus(connection),
        fetchConfigStatus(),
        fetchConnectionConfig(connection),
        fetchOrdersSyncStatus(connection),
      ]);
      if (cancelled) return;

      // Every key set, not just the failed ones: these are merged over the list
      // read's own failure, and a stale message for a read that has since
      // succeeded would sit on the screen forever.
      const failed: LoadFailures = {
        connection: undefined,
        credentials: undefined,
        fields: undefined,
        orders: undefined,
      };

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

      if (connectionConfig.status === "fulfilled") {
        applyValues(connectionConfig.value);
        setFieldsLoaded(true);
      } else {
        setFieldsLoaded(false);
        failed.fields = amazonErrorMessage(connectionConfig.reason, "Could not read this connection's settings.");
      }

      // The one read that can legitimately fail on its own: no ERPNext on the
      // site means no Sales Order to count. Its card copes with a null.
      if (ordersSync.status === "fulfilled") setOrdersStatus(ordersSync.value);
      else {
        setOrdersStatus(null);
        failed.orders = amazonErrorMessage(ordersSync.reason, "Could not read the order sync status.");
      }

      setFailures((current) => ({ connections: current.connections, ...failed }));
      setLoading(false);
    }

    function applyValues(connectionConfig: AmazonConnectionConfig) {
      const values = connectionConfig.values;
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
  }, [selected, listLoaded, reloadToken]);

  function reload() {
    setReloadToken((token) => token + 1);
  }

  /** Switching seller re-reads everything, because everything was about the last one. */
  function select(connection: string) {
    if (connection === selected) return;
    setResult(null);
    setSelected(connection);
  }

  async function addConnection(values: { connection_id: string; label: string; region: string }) {
    try {
      const created = await createConnection(values);
      setSelected(created.connection);
      reload();
      toast.success(`Added ${created.label}. Connect it to authorize the seller.`);
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not add the Amazon connection."));
      // Rethrown so the dialog stays open over what was typed.
      throw error;
    }
  }

  async function makeDefault() {
    if (!selected) return;
    setBusy(true);
    try {
      const updated = await setDefaultConnection(selected);
      toast.success(`${updated.label} is now the default connection.`);
      reload();
    } catch (error) {
      toast.error(amazonErrorMessage(error, "Could not change the default connection."));
    } finally {
      setBusy(false);
    }
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

      // A bench with no connection has nothing to write to. The first save makes
      // it, which is also the only moment it can be made without being named —
      // with no other seller on the site there is none to confuse it with. Every
      // one after that comes from the Add dialog, with an id somebody chose.
      const target = selected ?? (await ensureConnection()).connection;

      const outcome = await saveConnection(target, values);
      setSelected(target);

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
      const { url } = await fetchConsentUrl(selected ?? undefined);
      // Leaves the OS for Amazon's consent screen and comes back to
      // /amazon-oauth/callback, which authorizes the seller this state was
      // issued for. Deliberately not a new tab: the state is single-use, so a
      // stray second attempt in the original tab could only ever fail.
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
      await disconnectAmazon(selected ?? undefined);
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
      const outcome = await testConnection(selected ?? undefined);
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
      await syncOrders(connection.primaryMarketplace || undefined, selected ?? undefined);
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
  const failureList = [
    failures.connections,
    failures.connection,
    failures.credentials,
    failures.fields,
    failures.orders,
  ].filter((message): message is string => Boolean(message));

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

      <ConnectionSwitcher
        connections={connectionList}
        selected={selected}
        busy={busy || connecting}
        onSelect={select}
        onAdd={addConnection}
        onMakeDefault={() => void makeDefault()}
      />

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
