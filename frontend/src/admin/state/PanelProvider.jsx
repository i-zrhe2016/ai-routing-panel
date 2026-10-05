// Single source of console state: the dashboard snapshot, the read-only
// insights snapshot, busy/flash tracking, and every mutation the console can
// perform. Workspaces stay presentational and read this through usePanel().
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { createEmptyPortForm } from "../../utils.js";
import { createConsoleApi, installClientErrorLogging, reportClientError } from "../lib/consoleApi.js";
import {
  EMPTY_PANEL,
  attentionPortCount,
  dataPlaneRunningLabel,
  normalizeDashboard,
  routingSignature,
  totalTrafficBytes,
  trafficToday,
} from "../lib/dashboard.js";

const PanelContext = createContext(null);

export function usePanel() {
  const value = useContext(PanelContext);
  if (!value) {
    throw new Error("usePanel() must be used inside <PanelProvider>");
  }
  return value;
}

function bootCsrfToken() {
  return (typeof window !== "undefined" && window.__BOOT__?.csrf_token) || "";
}

export function PanelProvider({
  children,
  api,
  pollInterval = 15000,
  insightsInterval = 60000,
  autoLoad = true,
}) {
  const apiRef = useRef(null);
  if (!apiRef.current) {
    apiRef.current = api || createConsoleApi({ csrfToken: bootCsrfToken() });
  }
  const client = apiRef.current;

  const [panel, setPanel] = useState(EMPTY_PANEL);
  const [loading, setLoading] = useState(true);
  const [flash, setFlash] = useState({ message: "", level: "info" });
  const [busy, setBusy] = useState({});
  const [insights, setInsights] = useState(null);
  const [insightsError, setInsightsError] = useState("");
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsDays, setInsightsDays] = useState(1);
  const [incidents, setIncidents] = useState([]);
  const [incidentsError, setIncidentsError] = useState("");
  const [incidentsLoading, setIncidentsLoading] = useState(false);
  const incidentRequestRef = useRef(0);
  const [diagnosis, setDiagnosis] = useState(null);
  const [pathChangedAt, setPathChangedAt] = useState(0);
  const signatureRef = useRef("");
  const readyRef = useRef(false);
  const historyRequestRef = useRef(0);
  const trafficSampleRef = useRef(null);
  const [trafficActivity, setTrafficActivity] = useState(null);

  const applyDashboard = useCallback((dashboard) => {
    const next = normalizeDashboard(dashboard);
    const now = performance.now();
    const bytes = totalTrafficBytes(next.summary);
    const portIds = next.ports.map((port) => port.id).sort((a, b) => a - b).join(",");
    const previous = trafficSampleRef.current;
    const elapsed = previous ? (now - previous.at) / 1000 : 0;
    const comparable = previous && previous.portIds === portIds && bytes >= previous.bytes && elapsed >= 1;
    setTrafficActivity(comparable ? { bytesPerSecond: (bytes - previous.bytes) / elapsed, bytes: bytes - previous.bytes } : null);
    trafficSampleRef.current = { at: now, bytes, portIds };
    const nextSignature = routingSignature(next);
    if (readyRef.current && signatureRef.current && signatureRef.current !== nextSignature) {
      setPathChangedAt(Date.now());
    }
    signatureRef.current = nextSignature;
    readyRef.current = true;
    setPanel(next);
  }, []);

  const clearFlash = useCallback(() => setFlash({ message: "", level: "info" }), []);

  const applyResponse = useCallback(
    (data) => {
      if (data?.dashboard) {
        applyDashboard(data.dashboard);
      }
      if (data?.message) {
        setFlash({ message: data.message, level: data.level || "info" });
      }
    },
    [applyDashboard],
  );

  const runAction = useCallback(
    async (key, callback) => {
      let alreadyBusy = false;
      setBusy((current) => {
        if (current[key]) {
          alreadyBusy = true;
          return current;
        }
        return { ...current, [key]: true };
      });
      if (alreadyBusy) return undefined;
      try {
        return await callback();
      } catch (error) {
        setFlash({ message: error?.message || "操作失败。", level: "error" });
        return undefined;
      } finally {
        setBusy((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
    },
    [],
  );

  const refreshDashboard = useCallback(async () => {
    try {
      const data = await client.get("/api/dashboard");
      applyDashboard(data.dashboard || {});
    } catch (error) {
      setFlash({ message: error?.message || "刷新失败。", level: "error" });
    }
  }, [applyDashboard, client]);

  const loadInsights = useCallback(
    async (days = insightsDays) => {
      const requestId = ++historyRequestRef.current;
      setInsightsLoading(true);
      setInsightsError("");
      try {
        const data = await client.get(`/api/insights?days=${days}`);
        if (requestId !== historyRequestRef.current) return;
        setInsights(data.insights || null);
        setInsightsError("");
      } catch (error) {
        if (requestId !== historyRequestRef.current) return;
        setInsights(null);
        setInsightsError(error?.message || "历史数据加载失败。");
      } finally {
        if (requestId === historyRequestRef.current) setInsightsLoading(false);
      }
    },
    [client, insightsDays],
  );

  const loadIncidents = useCallback(async () => {
    const request = ++incidentRequestRef.current;
    setIncidentsLoading(true);
    setIncidentsError("");
    try {
      const data = await client.get("/api/probe-incidents");
      if (request === incidentRequestRef.current) setIncidents(data.incidents || []);
    } catch (error) {
      if (request === incidentRequestRef.current) setIncidentsError(error?.message || "故障记录加载失败。");
    } finally {
      if (request === incidentRequestRef.current) setIncidentsLoading(false);
    }
  }, [client]);

  const loadIncidentReport = useCallback(async (id) => {
    const data = await client.get(`/api/probe-incidents/${encodeURIComponent(id)}/report`);
    return data.report || "";
  }, [client]);

  useEffect(() => {
    if (!autoLoad) return undefined;
    loadIncidents();
    if (!insightsInterval) return undefined;
    const timer = window.setInterval(loadIncidents, insightsInterval);
    return () => window.clearInterval(timer);
  }, [autoLoad, insightsInterval, loadIncidents]);

  const changeInsightsDays = useCallback(
    (days) => {
      setInsightsDays(days);
      setInsights(null);
      loadInsights(days);
    },
    [loadInsights],
  );

  useEffect(() => {
    if (!autoLoad) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await client.get("/api/dashboard");
        if (!cancelled) applyDashboard(data.dashboard || {});
      } catch (error) {
        if (!cancelled) setFlash({ message: error?.message || "加载失败。", level: "error" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    loadInsights(insightsDays);
    return () => {
      cancelled = true;
    };
    // The initial load runs once; polling is handled by the interval effects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoLoad]);

  useEffect(() => {
    if (!autoLoad || !pollInterval) return undefined;
    const timer = window.setInterval(refreshDashboard, pollInterval);
    return () => window.clearInterval(timer);
  }, [autoLoad, pollInterval, refreshDashboard]);

  useEffect(() => {
    if (!autoLoad || !insightsInterval) return undefined;
    const timer = window.setInterval(() => loadInsights(insightsDays), insightsInterval);
    return () => window.clearInterval(timer);
  }, [autoLoad, insightsDays, insightsInterval, loadInsights]);

  useEffect(() => {
    if (!autoLoad) return undefined;
    return installClientErrorLogging({ csrfToken: bootCsrfToken() });
  }, [autoLoad]);

  const [createForm, setCreateForm] = useState(createEmptyPortForm);
  const [filters, setFilters] = useState({ query: "", status: "all" });
  const [selectedPortId, setSelectedPortId] = useState(null);

  const filteredPorts = useMemo(() => {
    const query = String(filters.query || "").trim().toLowerCase();
    return panel.ports.filter((port) => {
      if (filters.status !== "all" && port.status !== filters.status) return false;
      if (!query) return true;
      return [port.listen_port, port.note, port.upstream_host, port.status_label]
        .map((item) => String(item || "").toLowerCase())
        .some((item) => item.includes(query));
    });
  }, [filters, panel.ports]);

  const selectedPort = useMemo(
    () => filteredPorts.find((port) => port.id === selectedPortId) || null,
    [filteredPorts, selectedPortId],
  );

  useEffect(() => {
    if (filteredPorts.some((port) => port.id === selectedPortId)) return;
    setSelectedPortId(filteredPorts.length ? filteredPorts[0].id : null);
  }, [filteredPorts, selectedPortId]);

  const findPortByListenPort = useCallback(
    (listenPort) => {
      const target = String(listenPort ?? "").trim();
      if (!target) return null;
      return panel.ports.find((port) => String(port.listen_port) === target) || null;
    },
    [panel.ports],
  );

  const selectPort = useCallback((portId) => setSelectedPortId(portId), []);

  // Local form edits. They live in the dashboard snapshot so the workspaces stay
  // stateless; the next dashboard poll replaces them, matching the previous
  // console behaviour.
  const updatePortForm = useCallback((portId, patch) => {
    setPanel((current) => ({
      ...current,
      ports: current.ports.map((port) =>
        port.id === portId ? { ...port, form: { ...port.form, ...patch } } : port,
      ),
    }));
  }, []);

  const actions = useMemo(() => {
    const mutate = (key, request) => runAction(key, async () => applyResponse(await request()));

    return {
      createPort: () =>
        runAction("create-port", async () => {
          const createdListenPort = String(createForm.listen_port ?? "").trim();
          let data;
          try {
            data = await client.post("/api/ports", createForm);
          } catch (error) {
            if (error?.status === 409 && createdListenPort) {
              await refreshDashboard();
              setFlash({ message: "监听端口已存在，已选中已有端口。", level: "info" });
              setCreateForm(createEmptyPortForm());
              return;
            }
            throw error;
          }
          applyResponse(data);
          setCreateForm(createEmptyPortForm());
          const created = normalizeDashboard(data.dashboard || {}).ports.find(
            (port) => data.created_port_id != null
              ? port.id === data.created_port_id
              : String(port.listen_port) === createdListenPort,
          );
          if (created) {
            setFilters({ query: "", status: "all" });
            setSelectedPortId(created.id);
          }
        }),
      updatePort: (port) => mutate(`update:${port.id}`, () => client.put(`/api/ports/${port.id}`, port.form)),
      togglePort: (port) => mutate(`toggle:${port.id}`, () => client.post(`/api/ports/${port.id}/toggle`)),
      deletePort: (port) =>
        runAction(`delete:${port.id}`, async () => {
          try {
            applyResponse(await client.del(`/api/ports/${port.id}`));
          } catch (error) {
            if (error?.status === 400 && error.message === "端口记录不存在。") {
              await refreshDashboard();
              setFlash({ message: "端口已不存在，列表已刷新。", level: "info" });
              return;
            }
            throw error;
          }
        }),
      resetTraffic: (port) => mutate(`reset:${port.id}`, () => client.post(`/api/ports/${port.id}/reset-traffic`)),
      rotateTenantToken: (port) =>
        mutate(`rotate-tenant:${port.id}`, () => client.post(`/api/ports/${port.id}/rotate-tenant-token`)),
      rotateTenantCredentials: (port) =>
        mutate(`rotate-credentials:${port.id}`, () => client.post(`/api/ports/${port.id}/rotate-tenant-credentials`)),
      rotatePortSubscription: (port) =>
        mutate(`rotate-subscription:${port.id}`, () => client.post(`/api/ports/${port.id}/rotate-subscription-token`)),
      runDnsFailoverCheck: () => mutate("dns-failover-check", () => client.post("/api/dns-failover/check")),
      switchDnsTarget: (target) =>
        mutate(`dns-failover-switch:${target}`, () => client.post("/api/dns-failover/switch", { target })),
      switchPortAiRoutingScope: (port_id, traffic_scope) =>
        mutate(`switch-ai-port-${port_id}`, () => client.post(`/api/ai-routing/ports/${port_id}/scope`, { traffic_scope })),
      switchAiRoutingScope: (traffic_scope) =>
        mutate("switch-ai-scope", () => client.post("/api/ai-routing/scope", { traffic_scope })),
      switchAiRoutingMode: (mode) =>
        mutate(`switch-ai-${mode}`, () => client.post("/api/ai-routing/switch", { mode })),
      restartDataPlane: () =>
        runAction("restart-data-plane", async () => {
          if (!panel.dataPlaneStatus?.configured) return;
          applyResponse(await client.post("/api/data-plane/restart"));
        }),
      restartAiNode: (nodeId = "") =>
        runAction(`restart-ai-node:${nodeId}`, async () => {
          const url = nodeId
            ? `/api/ai-nodes/${encodeURIComponent(nodeId)}/restart`
            : "/api/ai-node/restart";
          applyResponse(await client.post(url));
        }),
      diagnoseDataPlane: () =>
        runAction("diagnose-data-plane", async () => {
          const data = await client.post("/api/data-plane/diagnose");
          setDiagnosis(data.diagnosis || null);
          applyResponse(data);
        }),
    };
  }, [applyResponse, client, createForm, panel.dataPlaneStatus, refreshDashboard, runAction]);

  const value = useMemo(
    () => ({
      panel,
      meta: panel.meta,
      summary: panel.summary,
      subscription: panel.subscription,
      ports: panel.ports,
      filteredPorts,
      selectedPort,
      selectPort,
      updatePortForm,
      findPortByListenPort,
      dataPlaneStatus: panel.dataPlaneStatus,
      aiNodeStatus: panel.aiNodeStatus,
      aiNodes: panel.aiNodes,
      aiRoutingStatus: panel.aiRoutingStatus,
      dnsFailoverStatus: panel.dnsFailoverStatus,
      nodes: panel.nodes,
      trafficRouting: panel.trafficRouting,
      trafficActivity,
      aiDomainStats: panel.aiDomainStats,
      insights,
      insightsError,
      insightsLoading,
      insightsDays,
      changeInsightsDays,
      loadInsights,
      incidents,
      incidentsLoading,
      incidentsError,
      loadIncidents,
      loadIncidentReport,
      diagnosis,
      setDiagnosis,
      flash,
      setFlash,
      clearFlash,
      busy,
      isBusy: (key) => Boolean(busy[key]),
      loading,
      refreshDashboard,
      pathChangedAt,
      filters,
      setFilters,
      createForm,
      setCreateForm,
      attentionPortCount: attentionPortCount(panel.summary),
      totalTrafficBytes: totalTrafficBytes(panel.summary),
      trafficToday,
      dataPlaneRunningLabel: () => dataPlaneRunningLabel(panel.dataPlaneStatus),
      ...actions,
    }),
    [
      actions,
      busy,
      createForm,
      incidents,
      incidentsLoading,
      incidentsError,
      loadIncidents,
      loadIncidentReport,
      diagnosis,
      filteredPorts,
      findPortByListenPort,
      flash,
      filters,
      insights,
      insightsDays,
      insightsError,
      insightsLoading,
      loadInsights,
      loading,
      panel,
      trafficActivity,
      pathChangedAt,
      refreshDashboard,
      selectPort,
      selectedPort,
      updatePortForm,
    ],
  );

  return <PanelContext.Provider value={value}>{children}</PanelContext.Provider>;
}

export function reportRenderError(error, info) {
  reportClientError({ error, csrfToken: bootCsrfToken(), source: `react.${info?.componentStack ? "render" : "error"}` });
}
