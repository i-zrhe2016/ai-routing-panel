// Builds the host inventory from the dashboard snapshot, refined by the
// insights snapshot when it is available (it carries per-node detail such as
// api_server / config_path / last_error).
export function buildHostInventory(panel, insights) {
  const detail = new Map();
  for (const host of insights?.hosts || []) {
    detail.set(host.key, host);
  }

  const hosts = [];
  const dataPlane = detail.get("data_plane") || {};
  hosts.push({
    key: "data_plane",
    role: "data_plane",
    roleLabel: "普通数据面",
    label: panel.dataPlaneStatus?.label || "普通数据面",
    target: panel.dataPlaneStatus?.management_target || dataPlane.management_target || "",
    configured: Boolean(panel.dataPlaneStatus?.configured),
    reachable: Boolean(panel.dataPlaneStatus?.reachable),
    running: panel.dataPlaneStatus?.xray_running,
    supportsRestart: Boolean(panel.dataPlaneStatus?.supports_restart),
    supportsSync: Boolean(panel.dataPlaneStatus?.supports_sync),
    apiServer: dataPlane.api_server || panel.dataPlaneStatus?.api_server || "",
    configPath: dataPlane.config_path || panel.dataPlaneStatus?.config_path || "",
    accessLogPath: dataPlane.access_log_path || panel.dataPlaneStatus?.access_log_path || "",
    lastError: dataPlane.last_error || panel.dataPlaneStatus?.last_error || "",
    nodeId: "",
  });

  const aiNodes = (panel.aiNodes?.length ? panel.aiNodes : []).map((node) => node);
  if (aiNodes.length) {
    for (const node of aiNodes) {
      const nodeId = String(node.node_id || "");
      const extra = detail.get(nodeId ? `ai_node:${nodeId}` : "ai_node") || {};
      hosts.push({
        key: nodeId ? `ai_node:${nodeId}` : "ai_node",
        role: "ai_node",
        roleLabel: "AI 数据面",
        label: node.label || "AI 节点",
        target: node.management_target || extra.management_target || "",
        configured: Boolean(node.configured),
        reachable: Boolean(node.reachable),
        running: node.xray_running,
        supportsRestart: Boolean(node.supports_restart),
        supportsSync: Boolean(node.supports_sync),
        apiServer: extra.api_server || node.api_server || "",
        configPath: extra.config_path || node.config_path || "",
        accessLogPath: extra.access_log_path || node.access_log_path || "",
        lastError: extra.last_error || node.last_error || "",
        nodeId,
      });
    }
  } else {
    const extra = detail.get("ai_node") || {};
    hosts.push({
      key: "ai_node",
      role: "ai_node",
      roleLabel: "AI 数据面",
      label: panel.aiNodeStatus?.label || "AI 节点",
      target: panel.aiNodeStatus?.management_target || extra.management_target || "",
      configured: Boolean(panel.aiNodeStatus?.configured),
      reachable: Boolean(panel.aiNodeStatus?.reachable),
      running: panel.aiNodeStatus?.xray_running,
      supportsRestart: Boolean(panel.aiNodeStatus?.supports_restart),
      supportsSync: Boolean(panel.aiNodeStatus?.supports_sync),
      apiServer: extra.api_server || "",
      configPath: extra.config_path || "",
      accessLogPath: extra.access_log_path || "",
      lastError: extra.last_error || panel.aiNodeStatus?.last_error || "",
      nodeId: "",
    });
  }

  const backup = (panel.nodes || []).find((node) => node.key === "control_plane_backup");
  if (backup) {
    hosts.push({
      key: "control_plane_backup",
      role: "backup",
      roleLabel: "控制面备用入口",
      label: backup.label || "控制面备用",
      target: backup.management_target || "",
      configured: Boolean(backup.configured),
      reachable: Boolean(backup.reachable),
      running: backup.xray_running,
      supportsRestart: false,
      supportsSync: false,
      apiServer: "",
      configPath: "",
      accessLogPath: "",
      lastError: "",
      nodeId: "",
    });
  }

  return hosts;
}

export function hostTone(host) {
  if (!host.configured) return "neutral";
  if (host.running === true) return "success";
  if (host.running === false) return "danger";
  return host.reachable ? "success" : "warning";
}

export function hostStateLabel(host) {
  if (!host.configured) return "未配置";
  if (host.running === true) return "运行中";
  if (host.running === false) return "未运行";
  return host.reachable ? "可达" : "待探测";
}
