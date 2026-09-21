// Normalizes the /api/dashboard payload into the shape the console renders.
// Field names are the backend contract; nothing here invents a value that the
// control plane did not return.
import { humanBytes } from "../../shared/formatters.js";

export const EMPTY_PANEL = {
  meta: {},
  summary: {},
  subscription: {},
  dataPlaneStatus: {},
  aiNodeStatus: {},
  aiNodes: [],
  aiRoutingStatus: {},
  dnsFailoverStatus: {},
  nodes: [],
  trafficRouting: {},
  aiDomainStats: {},
  ports: [],
  commerce: { summary: {}, settings: {}, plans: [], orders: [] },
};

export function preparePort(port) {
  return {
    ...port,
    form: {
      listen_port: String(port.listen_port ?? ""),
      expires_at: port.expires_at_input || "",
      traffic_limit: port.traffic_limit_input || "",
      note: port.note || "",
    },
  };
}

export function preparePlan(plan) {
  return {
    ...plan,
    form: {
      slug: String(plan.slug || ""),
      name: String(plan.name || ""),
      description: String(plan.description || ""),
      price_fen: String(plan.price_fen ?? ""),
      duration_days: String(plan.duration_days ?? ""),
      traffic_limit: String(plan.traffic_limit_display || ""),
      enabled: Boolean(plan.enabled),
      sort_order: String(plan.sort_order ?? 0),
    },
  };
}

export function prepareOrder(order) {
  return {
    ...order,
    form: { review_note: String(order.review_note || order.rejection_reason || "") },
  };
}

export function normalizeDashboard(dashboard) {
  const payload = dashboard || {};
  const meta = payload.meta || {};
  const aiNodeStatus = meta.ai_node_status || {};
  const aiNodes = Array.isArray(meta.ai_nodes)
    ? meta.ai_nodes
    : (Array.isArray(aiNodeStatus.nodes) ? aiNodeStatus.nodes : []);
  const commerce = payload.commerce || {};
  return {
    meta,
    summary: payload.summary || {},
    subscription: payload.subscription || {},
    dataPlaneStatus: meta.data_plane_status || {},
    aiNodeStatus,
    aiNodes,
    aiRoutingStatus: meta.ai_routing_status || {},
    dnsFailoverStatus: meta.dns_failover_status || {},
    nodes: Array.isArray(meta.nodes) ? meta.nodes : [],
    trafficRouting: meta.traffic_routing || {},
    aiDomainStats: meta.ai_domain_stats || {},
    ports: (payload.ports || []).map(preparePort),
    commerce: {
      summary: commerce.summary || {},
      settings: { ...(commerce.settings || {}) },
      plans: (commerce.plans || []).map(preparePlan),
      orders: (commerce.orders || []).map(prepareOrder),
    },
  };
}

// Stable signature of the routing decision, used to flag a path change so the
// topology can call it out instead of silently re-rendering.
export function routingSignature(panel) {
  return JSON.stringify({
    path: panel.trafficRouting?.path || "unknown",
    dnsTarget: panel.dnsFailoverStatus?.current_target || "",
    aiMode: panel.aiRoutingStatus?.manual_mode || "auto",
    backupMode: panel.meta?.backup_xray_mode || "",
  });
}

export function totalTrafficBytes(summary) {
  return Number(summary?.total_bytes_received || 0) + Number(summary?.total_bytes_sent || 0);
}

export function attentionPortCount(summary) {
  return (
    Number(summary?.expired_ports || 0) +
    Number(summary?.quota_ports || 0) +
    Number(summary?.disabled_ports || 0)
  );
}

export function trafficToday(port) {
  return Number(port?.today_bytes_received || 0) + Number(port?.today_bytes_sent || 0);
}

export function portTotalBytes(port) {
  return Number(port?.total_bytes_received || 0) + Number(port?.total_bytes_sent || 0);
}

export function dataPlaneRunningLabel(status) {
  if (!status || !status.configured) return "未配置";
  if (status.xray_running === true) return "运行中";
  if (status.xray_running === false) return "未运行";
  return "未知";
}

export function aiRoutingLabel(status) {
  if (!status || !status.configured) return "未启用";
  return status.status_label || "未知";
}

export function aiNodeStatusLabel(status) {
  if (!status || !status.configured) return "未纳管";
  if (status.reachable) return "可达";
  return status.last_error ? "不可达" : "待探测";
}

// Backend status_tone -> console tone. "ok"/"bad"/"warn" come straight from the
// control plane; anything else is neutral.
export function toneFromStatus(tone) {
  if (tone === "ok" || tone === "success") return "success";
  if (tone === "bad" || tone === "danger") return "danger";
  if (tone === "warn" || tone === "warning") return "warning";
  if (tone === "info") return "info";
  return "neutral";
}

export function dnsFailoverTone(status) {
  if (!status || !status.enabled) return "warning";
  if (!status.configured) return "danger";
  if (status.current_target === "backup") return "warning";
  if (status.last_probe_status === "unhealthy") return "danger";
  return "success";
}

export function dnsFailoverSummary(status) {
  if (!status || !status.enabled) return "未启用";
  if (!status.configured) return "配置不完整";
  return status.current_target_label || "未知";
}

export function portTone(port) {
  if (port?.status === "active") return "success";
  if (port?.status === "disabled") return "danger";
  return "warning";
}

export function humanBytesLabel(value) {
  return humanBytes(value);
}
