// The dashboard routing decision owns the active path. Candidate probes own
// AI traffic reachability; management-channel/SSH health is deliberately unused.
const PATHS = new Set(["normal_ai", "normal_fallback", "normal_direct", "dns_backup_relay_ai", "dns_backup_direct", "dns_backup_pending"]);
export const TOPOLOGY_STATES = { active: "当前路径", standby: "待命", blocked: "不可达", unknown: "未确认" };

export function buildTrafficTopology(value = {}) {
  const panel = value.panel || value;
  const routing = panel.trafficRouting || {};
  const waitingReport = panel.aiRoutingStatus?.status === "waiting_report";
  const reportedAiPath = routing.path === "normal_ai" || routing.path === "dns_backup_relay_ai";
  const path = waitingReport && reportedAiPath ? "unknown" : PATHS.has(routing.path) ? routing.path : "unknown";
  const normal = Boolean(panel.dataPlaneStatus?.configured);
  const dns = panel.dnsFailoverStatus || {};
  const backup = Boolean(dns.enabled && dns.configured);
  const candidates = Array.isArray(panel.aiRoutingStatus?.ai_candidates)
    ? panel.aiRoutingStatus.ai_candidates.filter((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate)) : [];
  const selected = candidates.find((candidate) => candidate.selected === true);
  const aiPath = path === "normal_ai" || path === "dns_backup_relay_ai";
  const primaryPath = path.startsWith("normal_");
  const backupPath = path === "dns_backup_relay_ai" || path === "dns_backup_direct";
  const pending = path === "dns_backup_pending";
  const defaultState = path === "unknown" ? "unknown" : "standby";
  const entry = primaryPath && normal ? "normal" : backupPath && backup ? "backup" : null;
  const completed = Boolean(entry && (!aiPath || selected?.is_reachable === true));
  const nodes = [
    { id: "client", kind: "client", label: "客户端", role: "流量来源", state: entry ? "active" : "unknown", description: "客户端经当前入口访问。图中展示路由选择，不测量客户端在线状态。" },
  ];
  if (normal) nodes.push({
    id: "normal", kind: "entry", label: "普通数据面", role: "主入口", state: entry === "normal" ? "active" : pending ? "blocked" : defaultState,
    address: dns.primary_content || panel.subscription?.server || "未报告接入地址",
    description: pending ? "服务端报告数据面故障，等待 DNS 切换。" : "状态依据服务端流量路径；管理通道状态不代表业务链路可达性。",
  });
  if (backup) nodes.push({
    id: "backup", kind: "entry", label: dns.backup_label || "控制面备用", role: "备用入口", state: entry === "backup" ? "active" : defaultState,
    address: dns.backup_content || "未报告接入地址",
    description: pending ? "备用入口已配置，DNS 尚未切换；待命不代表已完成流量接管。" : "DNS 备用入口，按服务端报告使用 AI 中继或 freedom 直出。",
  });
  candidates.forEach((candidate, position) => {
    // Ordinal is used only when the actual candidate has no report identifier.
    const rawId = candidate.index ?? position;
    const baseId = `ai-${rawId}`;
    const id = nodes.some((node) => node.id === baseId) ? `${baseId}-${position}` : baseId;
    const probe = candidate.is_reachable;
    const active = Boolean(aiPath && entry && candidate === selected && probe === true);
    nodes.push({
      id, kind: "ai", role: "AI 候选", label: candidate.label || candidate.candidate_label || `AI 候选 ${position + 1}`,
      state: probe === false ? "blocked" : probe !== true ? "unknown" : active ? "active" : defaultState,
      address: candidate.upstream_host ? `${candidate.upstream_host}${candidate.upstream_port ? `:${candidate.upstream_port}` : ""}` : candidate.candidate_label || "未报告候选地址",
      selected: candidate.selected === true,
      probeLabel: probe === true ? "候选探测可达" : probe === false ? "候选探测不可达" : "候选探测尚未确认",
      description: candidate.selected === true ? "路由报告选中的候选。只有探测明确可达且当前路径经 AI 时，才标记为当前路径。" : "已配置候选；未被选中时不会标记为当前路径。",
    });
  });
  if (normal) nodes.push({ id: "normal-direct", kind: "direct", label: "数据面直出", role: "freedom 出口", state: entry === "normal" && !aiPath ? "active" : defaultState, description: "直接从普通数据面出站，不经过 AI 候选。" });
  if (backup) nodes.push({ id: "backup-direct", kind: "direct", label: "备用直出", role: "freedom 出口", state: path === "dns_backup_direct" ? "active" : defaultState, description: "直接从备用入口出站，不经过 AI 候选。" });
  nodes.push({ id: "exit", kind: "exit", label: "目标网络", role: "访问目的地", state: completed ? "active" : "unknown", description: "路由出口示意；当前路径不代表目标网站的实时健康或边级流量测量。" });
  const edges = [];
  const add = (from, to, state) => edges.push({ id: `${from}-${to}`, from, to, state });
  if (normal) add("client", "normal", entry === "normal" ? "active" : pending ? "blocked" : defaultState);
  if (backup) add("client", "backup", entry === "backup" ? "active" : defaultState);
  for (const source of [normal && "normal", backup && "backup"].filter(Boolean)) {
    const relay = source === "normal" || dns.control_plane_backup_xray_enabled === true || path === "dns_backup_relay_ai";
    if (relay) nodes.filter((node) => node.kind === "ai").forEach((node) => add(source, node.id,
      node.state === "blocked" ? "blocked" : node.state === "unknown" ? "unknown" : node.state === "active" && entry === source ? "active" : defaultState));
    const direct = `${source}-direct`;
    add(source, direct, nodes.find((node) => node.id === direct).state);
  }
  nodes.filter((node) => node.kind === "ai" || node.kind === "direct").forEach((node) => add(node.id, "exit", node.state));
  return {
    path, nodes, edges,
    label: waitingReport && reportedAiPath ? "等待 AI 路由报告" : routing.label || "等待路由状态同步",
    scenario: waitingReport && reportedAiPath ? "节点配置已读取，当前路由与候选探测结果尚未返回。" : routing.scenario || "尚未确认当前流量路径。",
    warning: waitingReport && reportedAiPath && candidates.length
      ? `已配置 ${candidates.length} 个 AI 候选，正在等待路由报告；当前出口与链路可达性尚未确认。`
      : (aiPath || (waitingReport && reportedAiPath)) && !candidates.length
        ? "尚未配置 AI 候选，无法展示 AI 出口。"
        : aiPath && !selected
          ? "已配置 AI 候选，但路由报告尚未确认当前出口。"
          : aiPath && selected?.is_reachable !== true
            ? "选中候选的探测未确认可达，AI 链路未标记为当前路径。"
            : path === "unknown" ? "路由状态未知，未标记任何生效链路。" : pending ? "DNS 等待切换，备用入口尚未接管。" : null,
  };
}
