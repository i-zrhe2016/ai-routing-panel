// Routing reports describe configuration/application; probes describe only the
// tested upstream. Neither is a measurement of client traffic or destination health.
const PATHS = new Set(["normal_ai", "normal_ai_pending", "normal_fallback", "normal_direct", "dns_backup_relay_ai", "dns_backup_direct", "dns_backup_unknown", "dns_backup_pending"]);
const STATES = new Set(["active", "standby", "blocked", "unknown"]);
export const TOPOLOGY_STATES = { active: "配置路径", standby: "待命", blocked: "不可达", unknown: "未确认" };
const stateOr = (state, fallback) => STATES.has(state) ? state : fallback;
const probeState = (probe) => probe === true ? "standby" : probe === false ? "blocked" : "unknown";
const address = (host, port) => typeof host === "string" && host ? `${host.includes(":") ? `[${host}]` : host}${port ? `:${port}` : ""}` : "未报告上游地址";

export function buildTrafficTopology(value = {}) {
  const panel = value.panel || value;
  const routing = panel.trafficRouting || {};
  const ai = panel.aiRoutingStatus || {};
  const dns = panel.dnsFailoverStatus || {};
  const candidates = Array.isArray(ai.ai_candidates)
    ? ai.ai_candidates.filter((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate))
      .map((candidate) => candidate.probe_management_error === true ? { ...candidate, is_reachable: null } : candidate) : [];
  const selectedCandidates = candidates.filter((candidate) => candidate.selected === true);
  const selected = selectedCandidates.length === 1 ? selectedCandidates[0] : null;
  const preserved = ai.status === "probe_error" && ai.route_preserved === true;
  const applied = (ai.status === "applied" || preserved) && ["direct", "unchanged"].includes(ai.config_apply_status)
    && typeof ai.report_generated_at === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(ai.report_generated_at)
    && Number.isFinite(Date.parse(ai.report_generated_at)) && !ai.sync_error;
  let path = PATHS.has(routing.path) ? routing.path : "unknown";
  if (path === "normal_ai" && (!applied || !selected)) path = "normal_ai_pending";
  const allTraffic = applied && ai.applied_traffic_scope === "all" && path === "normal_ai";
  const mixedTraffic = applied && ai.applied_traffic_scope === "mixed" && path === "normal_ai";
  const normal = panel.dataPlaneStatus?.configured === true;
  const backup = dns.enabled === true && dns.configured === true;
  const normalPath = path.startsWith("normal_") && normal;
  const backupPath = ["dns_backup_relay_ai", "dns_backup_direct", "dns_backup_unknown"].includes(path) && backup;
  const entry = normalPath ? "normal" : backupPath ? "backup" : null;
  const directState = allTraffic ? "standby" : normalPath ? stateOr(routing.ordinary_direct_state, "active") : "standby";
  const aiState = path === "normal_ai" && selected?.is_reachable === true
    ? stateOr(routing.ai_branch_state, "unknown") : selected?.is_reachable === false && path === "normal_ai" ? "blocked" : "unknown";
  const nodes = [{ id: "client", kind: "client", label: "客户端", role: "流量来源", state: entry ? "active" : "unknown", description: "入口依据控制面记录的 DNS 目标；客户端 DNS 缓存和连接复用可能仍使用原入口。" }];
  const edges = [];
  const add = (from, to, state, trafficClass) => edges.push({ id: `${from}-${to}`, from, to, state, trafficClass });
  if (normal) {
    nodes.push({ id: "normal", kind: "entry", label: "普通数据面", role: "主入口", state: entry === "normal" ? "active" : "standby", address: dns.primary_content || panel.subscription?.server || "未报告接入地址", description: mixedTraffic ? "按端口账号分流：已开启全部转发的账号走 AI，其余账号按域名分流。统一入口保留账号隔离。" : allTraffic ? "托管租户入口的代理 TCP/UDP 流量统一使用所选 AI 出口，保留管理与阻断规则。客户端 DIRECT 流量不经过服务器。" : "处理域名分流：普通及未分类域名使用默认直出，AI 域名按已应用的分流规则转发。管理状态不代表客户端链路健康。" });
    nodes.push({ id: "normal-direct", kind: "direct", label: "数据面直出", role: "freedom 出口", state: directState, description: mixedTraffic ? "保留按域名分流账号的普通直出，全部 AI 账号只在故障回退时使用。" : allTraffic ? "全部转发已应用，直出保留为故障回退路径。" : path === "normal_fallback" || path === "normal_direct" ? "已确认普通数据面直出，AI 域名也使用直出。" : "普通及未分类域名的默认出口；即使 AI 分流已应用，这条路径仍然保留。" });
    add("client", "normal", entry === "normal" ? "active" : path === "dns_backup_pending" || path === "unknown" ? "unknown" : "standby", "接入流量");
    add("normal", "normal-direct", directState, ["normal_fallback", "normal_direct"].includes(path) ? "全部域名" : "普通 / 未分类域名");
    add("normal-direct", "ordinary-exit", directState, "直接访问");
  }
  let relay = null;
  if (backup) {
    nodes.push({ id: "backup", kind: "entry", label: dns.backup_label || "控制面备用", role: "备用入口", state: entry === "backup" ? "active" : "standby", address: dns.backup_content || "未报告接入地址", description: "备用配置按自身默认出口转发全部域名，不沿用普通数据面的 AI 域名分流。DNS 目标不表示所有客户端已切换。" });
    add("client", "backup", entry === "backup" ? "active" : "standby", "DNS 备用接入");
    if (dns.backup_xray_mode === "relay") {
      const target = dns.backup_relay_target || {};
      const match = candidates.find((candidate) => target.upstream_host && String(candidate.upstream_host || "").toLowerCase() === String(target.upstream_host).toLowerCase() && candidate.upstream_port === target.upstream_port);
      const state = entry === "backup" && path === "dns_backup_relay_ai"
        ? target.upstream_host && match?.is_reachable === true ? stateOr(routing.ai_branch_state, "unknown") : match?.is_reachable === false ? "blocked" : "unknown"
        : probeState(match?.is_reachable);
      relay = { id: "backup-relay", kind: "relay", label: "备用中继上游", role: "全部域名中继", state, address: address(target.upstream_host, target.upstream_port), probeLabel: !match ? "没有匹配的业务探测" : match.is_reachable === true ? "匹配上游探测可达" : match.is_reachable === false ? "匹配上游探测不可达" : "匹配上游探测未确认", description: "来自备用入口实际配置的默认上游，可能与普通数据面的 AI 出口不同。上游不可达不会自动产生备用直出路径。" };
      nodes.push(relay);
      add("backup", relay.id, state, "全部域名");
      add(relay.id, "backup-exit", state, "中继后出站");
    } else if (dns.backup_xray_mode === "direct") {
      const state = path === "dns_backup_direct" && entry === "backup" ? stateOr(routing.ordinary_direct_state, "unknown") : "standby";
      nodes.push({ id: "backup-direct", kind: "direct", label: "备用直出", role: "freedom 出口", state, description: "备用配置的默认直出，全部域名从备用入口出站，不经过 AI 候选。" });
      add("backup", "backup-direct", state, "全部域名");
      add("backup-direct", "backup-exit", state, "直接访问");
    }
  }
  candidates.forEach((candidate, position) => {
    const baseId = `ai-${candidate.index ?? position}`;
    const id = nodes.some((node) => node.id === baseId) ? `${baseId}-${position}` : baseId;
    const isSelected = candidate === selected;
    const state = normalPath && path === "normal_ai" && isSelected ? aiState
      : path === "normal_ai_pending" ? "unknown" : probeState(candidate.is_reachable);
    nodes.push({ id, kind: "ai", role: "AI 候选", label: candidate.label || candidate.candidate_label || `AI 候选 ${position + 1}`, state, address: address(candidate.upstream_host, candidate.upstream_port), selected: candidate.selected === true, probeLabel: candidate.is_reachable === true ? "候选探测可达" : candidate.is_reachable === false ? "候选探测不可达" : "候选探测尚未确认", description: mixedTraffic && isSelected ? "承接已开启全部转发账号的全部 TCP/UDP 流量，以及其他账号的 AI 域名流量。" : allTraffic && isSelected ? "承接进入托管租户入口的全部代理 TCP/UDP 流量，含普通域名、未知域名及 IP 目标。" : isSelected ? "路由报告选中的 AI 出口；配置应用与业务探测共同决定图中状态。该出口只承接匹配 AI 域名规则的流量。" : "普通数据面已配置的 AI 候选，未选中时不表示承接当前流量。" });
    if (normal) add("normal", id, normalPath && isSelected && ["normal_ai", "normal_ai_pending"].includes(path) ? state : state === "blocked" ? "blocked" : state === "unknown" ? "unknown" : "standby", mixedTraffic ? "所选端口全部流量 / 其余 AI 域名" : allTraffic ? "全部代理 TCP/UDP" : "AI 域名");
    add(id, "ai-exit", state, "AI 目标访问");
  });
  const exit = (id, label, state, description) => nodes.push({ id, kind: "exit", label, role: "访问目的地", state, description });
  if (normal) exit("ordinary-exit", path === "normal_direct" || path === "normal_fallback" ? "全部域名目标" : "普通 / 未分类目标", directState, "域名流量的访问目标示意，配置路径不代表目标网站健康或正在传输的数据量。" );
  if (candidates.length) exit("ai-exit", mixedTraffic ? "按端口策略的代理目标" : allTraffic ? "全部代理目标" : "AI 域名目标", normalPath && path === "normal_ai" ? aiState : "unknown", "匹配 AI 分流规则的域名目标；不代表已观测到该目标的当前吞吐量。" );
  if (backup) exit("backup-exit", "全部域名目标", path === "dns_backup_direct" ? stateOr(routing.ordinary_direct_state, "unknown") : path === "dns_backup_relay_ai" ? relay?.state || "unknown" : "standby", "备用入口的全部域名目标；普通数据面的 AI 分流规则不会在这里重复应用。" );
  let warning = null;
  if (path === "normal_ai_pending") warning = candidates.length ? `已配置 ${candidates.length} 个 AI 候选，正在等待路由报告或配置应用确认；普通直出保留，AI 分流尚未确认。` : "尚未配置 AI 候选，无法展示 AI 出口。";
  else if (path === "normal_ai" && aiState !== "active") warning = allTraffic ? "全部转发配置已应用，但选中候选的业务探测未确认可达；等待健康周期确认。" : "AI 分流配置已应用，但选中候选的业务探测未确认可达；普通直出仍然保留。";
  else if (path === "dns_backup_relay_ai" && relay?.state !== "active") warning = "备用入口配置为全部域名中继，上游探测尚未确认可达；没有自动直出路径。";
  else if (path === "dns_backup_unknown") warning = "DNS 记录指向备用入口，但实际出口配置尚未确认。";
  else if (path === "dns_backup_pending") warning = "主入口状态未确认，DNS 记录仍指向主入口；备用入口尚未成为记录目标。";
  else if (path === "unknown") warning = "路由状态未知，未标记任何已确认路径。";
  return { path, nodes, edges, warning,
    label: path === "normal_ai_pending" && routing.path === "normal_ai" ? "普通直出 · 等待 AI 路由报告" : routing.label || "等待路由状态同步",
    scenario: path === "normal_ai_pending" ? "普通及未分类域名保留默认直出；AI 分流的应用与探测证据尚未完整。" : routing.scenario || "尚未确认当前流量路径。",
    reportTime: ai.report_generated_at_display && ai.report_generated_at_display !== "暂无" ? ai.report_generated_at_display : ai.report_generated_at || "暂无报告",
    dnsTarget: dns.enabled ? dns.current_target === "primary" ? "主入口" : dns.current_target === "backup" ? "备用入口" : "未确认" : "未启用 DNS 切换",
  };
}
