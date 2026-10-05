import { useState } from "react";
import { useConfirm } from "../components/ConfirmDialog.jsx";
import TrafficTopology from "../components/TrafficTopology.jsx";
import { DataTable, MetricCard, Panel, StatusPill } from "../components/ui.jsx";
import { aiRoutingLabel, toneFromStatus } from "../lib/dashboard.js";
import { buildTrafficTopology } from "../lib/topology.js";
import { usePanel } from "../state/PanelProvider.jsx";

const MODE_LABELS = {
  auto: "自动探测",
  primary: "人工固定主 AI",
  backup: "人工固定备用 AI",
  forced_fallback: "应急直出",
};
const APPLY_LABELS = {
  direct: "已直接应用",
  unchanged: "配置一致，无需重写",
  delegated: "已委托同步，结果待确认",
  unmanaged: "由外部管理，应用结果待确认",
  not_needed: "无需更新配置",
};
const CACHE_LABELS = { available: "数据库可用", legacy: "仅有历史 AI 分类", unavailable: "分类数据库不可用" };
const CLASSIFICATION_LABELS = { ai: "AI 域名", not_ai: "普通域名", non_ai: "普通域名", unknown: "待分类" };
const SOURCE_LABELS = { builtin: "内置分类", codex: "Codex 分类器", openai: "OpenAI 分类器", cache: "历史缓存", database: "历史数据库", llm: "AI 分类器", ai: "AI 分类器", pending: "待分类", history: "历史分类" };
const DOMAIN_ROUTE_LABELS = { fallback: "普通数据面回退", direct: "普通数据面直出", ai: "AI 节点路由", ai_route: "AI 节点路由", unknown: "路由待确认", per_port: "按端口转发" };

function candidateAddress(candidate) {
  if (!candidate) return "地址待确认";
  return candidate.candidate_label || `${candidate.upstream_host || "地址待确认"}:${candidate.upstream_port || "—"}`;
}

function candidateLabel(candidate) {
  return candidate.label || (candidate.index === 0 ? "主 AI 节点" : candidate.index === 1 ? "备用 AI 节点" : `AI 候选 ${candidate.index + 1}`);
}

function candidateStatus(candidate) {
  if (candidate?.probe_management_error === true) return { label: "探测异常", tone: "warning" };
  if (candidate?.is_reachable === null || candidate?.is_reachable === undefined) return { label: "待探测", tone: "warning" };
  return candidate.is_reachable ? { label: "可达", tone: "success" } : { label: "不可达", tone: "danger" };
}

function modeForCandidate(candidate) {
  return candidate.index === 0 ? "primary" : candidate.index === 1 ? "backup" : null;
}

function effectiveOutlet(panel) {
  const graph = buildTrafficTopology(panel);
  const outlet = graph.nodes.find((node) => node.state === "active" && (node.kind === "ai" || node.kind === "relay"))
    || (["normal_fallback", "normal_direct", "dns_backup_direct"].includes(graph.path)
      ? graph.nodes.find((node) => node.state === "active" && node.kind === "direct") : null);
  if (!outlet) return { label: "出口待确认", detail: graph.warning || graph.scenario };
  if (outlet.kind === "ai" || outlet.kind === "relay") return { label: outlet.label, detail: outlet.address };
  return {
    label: outlet.id === "normal-direct" ? "普通数据面" : "控制面备用",
    detail: outlet.id === "normal-direct" ? "freedom 直出 · 按当前流量路径回退或直出" : "备用入口 freedom 直出",
  };
}

function domainRoute(row) {
  const route = row.traffic_route || {};
  return `${DOMAIN_ROUTE_LABELS[route.mode] || route.mode || "路由待确认"}${route.outbound_tag ? ` · ${route.outbound_tag}` : ""}`;
}

export default function RoutingWorkspace() {
  const panel = usePanel();
  const confirm = useConfirm();
  const routing = panel.aiRoutingStatus || {};
  const candidates = Array.isArray(routing.ai_candidates)
    ? routing.ai_candidates.filter((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate)) : [];
  const manualMode = routing.manual_mode || "auto";
  const allTraffic = routing.traffic_scope === "all";
  const portScopes = Array.isArray(routing.port_scopes) ? routing.port_scopes : [];
  const [scopeBusy, setScopeBusy] = useState(false);
  const routingBusy = scopeBusy || portScopes.some((port) => panel.isBusy(`switch-ai-port-${port.id}`)) || ["scope", "auto", "primary", "backup", "forced_fallback"].some((mode) => panel.isBusy(`switch-ai-${mode}`));
  async function switchScope() {
    if (routingBusy) return;
    setScopeBusy(true);
    try { await panel.switchAiRoutingScope(allTraffic ? "classified" : "all"); }
    finally { setScopeBusy(false); }
  }
  async function switchPortScope(port, scope) {
    if (routingBusy) return;
    setScopeBusy(true);
    try { await panel.switchPortAiRoutingScope(port.id, scope); }
    finally { setScopeBusy(false); }
  }
  const selectedCandidate = candidates.find((candidate) => candidate.selected === true) || null;
  const healthyCount = candidates.filter((candidate) => candidate.is_reachable === true).length;
  const routeTone = toneFromStatus(routing.status_tone);
  const routeTarget = effectiveOutlet(panel);
  const cache = routing.classification_cache || {};
  const domains = Array.isArray(routing.recent_domains) ? routing.recent_domains : [];
  const applied = routing.config_apply_status === "direct" || routing.config_apply_status === "unchanged";
  const changeEvidence = typeof routing.config_changed === "boolean"
    ? routing.config_changed ? "已生成配置变更" : "无配置变更" : "配置变更待确认";
  const retryEvidence = typeof routing.config_retried === "boolean"
    ? routing.config_retried ? "已触发配置重试" : "未触发配置重试" : "配置重试待确认";

  function requestSwitch(mode, candidate) {
    const title = mode === "forced_fallback"
      ? "启用应急直出？"
      : mode === "auto"
        ? "恢复自动探测？"
        : `确认切换到${candidate?.label || "该 AI 节点"}？`;
    const body = mode === "forced_fallback"
      ? "动态 AI 路由会被移除，进入服务器的代理流量回到普通数据面的 freedom 直出；保留转发范围偏好。恢复 AI 节点路由需要手动恢复自动探测。"
      : mode === "auto"
        ? "控制面将恢复按全部候选的优先级与可达性自动选择，不再固定当前人工目标。"
        : `${candidateAddress(candidate)} · ${candidateStatus(candidate).label}。此操作固定 AI 目标，健康探测仍会继续；不可达时回退普通数据面，恢复后继续使用固定目标。`;
    confirm.ask({
      title,
      body,
      tone: mode === "forced_fallback" ? "danger" : "primary",
      confirmLabel: "确认切换",
      onConfirm: () => panel.switchAiRoutingMode(mode),
    });
  }

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <h1>路由决策与故障切换</h1>
          <p>查看当前生效路径、业务探测和历史域名分类，管理自动选择与人工策略。</p>
        </div>
        <StatusPill tone={routeTone} label={aiRoutingLabel(routing)} />
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="当前出口" value={routeTarget.label} note={routeTarget.detail} accent />
        <MetricCard label="可达候选" value={`${healthyCount} / ${candidates.length}`} note="上游探测可达" tone={healthyCount === candidates.length && candidates.length ? "success" : "warning"} />
        <MetricCard label="路由策略" value={MODE_LABELS[manualMode] || manualMode} note={`最近人工操作：${routing.manual_updated_at_display || "暂无"}`} tone={manualMode === "auto" ? "success" : "warning"} />
        <MetricCard label="最近报告" value={routing.report_generated_at_display || "—"} note="控制面最近一次路由报告" />
      </section>

      <Panel title="决策与应用详情" description={routing.route_status_reason || "等待控制面报告路由决策原因。"}>
        <div className="cc-metric-grid">
          <MetricCard label="选中目标" value={selectedCandidate ? candidateLabel(selectedCandidate) : "暂无"} note={selectedCandidate ? `${candidateAddress(selectedCandidate)} · ${candidateStatus(selectedCandidate).label}` : "等待候选选择结果"} />
          <MetricCard label="配置应用" value={APPLY_LABELS[routing.config_apply_status] || "应用状态待确认"} note={`${changeEvidence} · ${retryEvidence}`} tone={applied ? "success" : "warning"} />
          <MetricCard label="健康探测间隔" value={routing.health_interval_seconds ? `${routing.health_interval_seconds} 秒` : "待确认"} note={`最近探测：${routing.last_probe_at_display || "暂无"} · ${routing.probe_method || "方法待确认"}`} />
          <MetricCard label="域名分类间隔" value={routing.classification_interval_seconds ? `${routing.classification_interval_seconds} 秒` : "待确认"} note="分类器不可用时使用数据库中的历史分类" />
        </div>
        <p className="cc-route-card__note">选中目标是 AI 路由的候选目标，当前出口依据生效流量路径展示。候选不可达时，当前出口可以是普通数据面。</p>
        <p className="cc-route-card__note">探测检查上游 TCP 或 REALITY 握手，目标 AI 服务的请求结果需结合实际连接确认。管理通道异常显示为探测异常。</p>
        {routing.sync_error ? <p className="cc-tone is-warning">管理同步：{routing.sync_error}</p> : null}
      </Panel>

      <Panel title="AI 转发范围" description="范围与出口选择独立；候选故障时沿用当前回退策略，恢复可达后继续转发。">
        <p><strong>默认策略：{allTraffic ? "全部转发到 AI 节点" : "按域名分流"}</strong> · 未单独设置的端口沿用此策略。</p>
        <p className="cc-route-card__note" id="ai-traffic-scope-help">启用后，进入托管租户入口的 TCP/UDP 代理流量（含普通、未分类域名及 IP 目标）使用所选 AI 出口，保留管理与阻断规则。客户端 DIRECT 流量不经过服务器，此设置无法接管。</p>
        <p className="cc-route-card__note" role="status">{routing.scope_apply_state === "confirmed"
          ? routing.applied_traffic_scope === "mixed" ? "当前应用：按端口转发，已开启的端口全部走 AI，其余按域名分流。" : routing.applied_traffic_scope === "all" ? "当前应用：全部代理流量转发到 AI 节点。" : routing.applied_traffic_scope === "direct" ? "当前应用：数据面直出；保留已选择的转发范围。" : "当前应用：仅已分类 AI 域名转发。"
          : "应用结果待确认，请查看配置应用与最近报告。"}</p>
        {manualMode === "forced_fallback" ? <p className="cc-route-card__note">应急直出优先于转发范围；恢复出口选择后继续使用此范围。</p> : null}
        <button className="a-btn secondary" type="button" role="switch" aria-checked={allTraffic} aria-describedby="ai-traffic-scope-help"
          disabled={!routing.configured || routingBusy || (!allTraffic && !candidates.length)} onClick={switchScope}>
          {routingBusy ? "应用中…" : allTraffic ? "关闭全部转发，恢复域名分流" : "启用全部转发到 AI 节点"}
        </button>
        <p className="cc-route-card__note">按端口独立设置；统一 443 入口按端口对应的账号生效。关闭某个端口的全部转发会恢复该端口的域名分流。</p>
        <DataTable caption="端口 AI 转发设置" rows={portScopes} rowKey={(port) => port.id}
          empty="暂无可设置的端口。"
          columns={[
            { key: "listen_port", label: "端口", render: (port) => <strong>{port.listen_port}</strong> },
            { key: "traffic_scope", label: "设置", render: (port) => `${port.traffic_scope === "all" ? "全部走 AI" : "域名分流"}${port.inherited ? " · 沿用默认" : " · 单独设置"}` },
            { key: "applied", label: "当前应用", render: (port) => !port.active ? "账号未启用或已到期" : port.scope_apply_state !== "confirmed" ? "应用结果待确认" : port.applied_traffic_scope === "direct" ? "故障回退 / 应急直出" : port.applied_traffic_scope === "all" ? "全部走 AI" : "域名分流" },
            { key: "actions", label: "全部转发到 AI 节点", render: (port) => <div className="a-actions">
              <button type="button" className="a-btn secondary" role="switch" aria-checked={port.traffic_scope === "all"}
                aria-label={`端口 ${port.listen_port} 全部转发到 AI 节点`} aria-describedby="ai-traffic-scope-help"
                disabled={!port.active || !routing.configured || routingBusy || (port.traffic_scope !== "all" && !candidates.length)}
                onClick={() => switchPortScope(port, port.traffic_scope === "all" ? "classified" : "all")}>
                {port.traffic_scope === "all" ? "已开启" : "已关闭"}
              </button>
              {!port.inherited ? <button type="button" className="a-btn ghost" disabled={routingBusy || !routing.configured}
                aria-label={`端口 ${port.listen_port} 恢复默认策略`} onClick={() => switchPortScope(port, "inherit")}>恢复默认</button> : null}
            </div> },
          ]} />
      </Panel>

      <Panel
        title="AI 出口控制"
        description="按候选优先级自动选择；固定目标时继续健康探测，不可达则回退普通数据面。"
        actions={
          manualMode !== "auto" ? (
            <button className="a-btn secondary" type="button" disabled={routingBusy} onClick={() => requestSwitch("auto")}>
              {panel.isBusy("switch-ai-auto") ? "恢复中…" : "恢复自动探测"}
            </button>
          ) : null
        }
      >
        <div className="cc-route-cards">
          {candidates.map((candidate) => {
            const status = candidateStatus(candidate);
            const mode = modeForCandidate(candidate);
            const fixed = Boolean(mode && manualMode === mode);
            return (
              <article key={candidate.index} className={`cc-route-card is-${status.tone}`}>
                <div className="cc-route-card__head">
                  <div>
                    <p className="section-kicker">{candidate.index === 0 ? "主节点" : candidate.index === 1 ? "备用节点" : "自动候选"}</p>
                    <strong>{candidateLabel(candidate)}</strong>
                  </div>
                  <StatusPill tone={status.tone} label={status.label} />
                </div>
                <p className="cc-route-card__address">{candidateAddress(candidate)}</p>
                <p className="cc-route-card__note">
                  {candidate.selected ? "报告选中目标" : "候选待命"}
                  {candidate.probe_method ? ` · 探测：${candidate.probe_method}` : ""}
                </p>
                {mode ? (
                  <button
                    className={`a-btn ${fixed ? "ghost" : "secondary"}`}
                    type="button"
                    disabled={fixed || !routing.configured || routingBusy}
                    onClick={() => requestSwitch(mode, candidate)}
                  >
                    {panel.isBusy(`switch-ai-${mode}`) ? "切换中…" : fixed ? "已固定此目标" : candidate.index === 0 ? "固定主 AI" : "切换到备用 AI"}
                  </button>
                ) : <p className="cc-route-card__note">自动候选，按探测结果参与选择</p>}
              </article>
            );
          })}
          {!candidates.length ? <div className="cc-empty">当前没有配置 AI 候选节点。</div> : null}
        </div>

        <details className="cc-advanced">
          <summary>高级应急</summary>
          <div className="cc-advanced__body">
            <p><strong>数据面直出</strong>：移除动态 AI 路由，让代理流量回普通数据面 freedom 直出，保留转发范围偏好。</p>
            <button
              className="a-btn danger"
              type="button"
              disabled={!routing.configured || manualMode === "forced_fallback" || routingBusy}
              onClick={() => requestSwitch("forced_fallback")}
            >
              {panel.isBusy("switch-ai-forced_fallback") ? "处理中…" : manualMode === "forced_fallback" ? "已启用数据面直出" : "强制直出"}
            </button>
          </div>
        </details>
      </Panel>

      <Panel title="当前流量路径" description={routing.route_status_reason || panel.trafficRouting?.scenario || "等待路由状态同步。"}>
        <TrafficTopology panel={panel} />
      </Panel>

      <Panel title="候选节点" description="业务流量探测与管理通道分开展示；待探测不代表可达。">
        <DataTable
          caption="AI 候选节点状态"
          columns={[
            { key: "label", label: "节点", render: (row) => <strong>{candidateLabel(row)}</strong> },
            { key: "candidate_label", label: "地址", render: (row) => candidateAddress(row) },
            { key: "is_reachable", label: "业务健康", render: (row) => <StatusPill tone={candidateStatus(row).tone} label={candidateStatus(row).label} /> },
            { key: "selected", label: "目标选择", render: (row) => row.selected ? "报告选中目标" : "待命" },
            { key: "probe_method", label: "探测方法", render: (row) => row.probe_method || routing.probe_method || "—" },
            { key: "checked_at", label: "探测时间", render: (row) => row.checked_at_display || row.checked_at || "暂无" },
            { key: "failure_reason", label: "探测失败原因", render: (row) => row.failure_reason || (row.is_reachable === true ? "无" : "未报告") },
          ]}
          rows={candidates}
          rowKey={(row) => row.index}
          empty="暂无 AI 候选节点。"
        />
      </Panel>

      <Panel title="历史分类与域名路由" description="历史分类保存在数据库中；AI 分类器不可用时复用已有分类，新域名等待分类。AI 节点不可达时，已分类 AI 域名回退普通数据面。">
        <div className="cc-metric-grid">
          <MetricCard label="历史分类域名" value={cache.total_domains ?? "待确认"} note={`AI ${cache.ai_domains ?? "—"} · 普通 ${cache.non_ai_domains ?? "—"}`} />
          <MetricCard label="分类数据库" value={CACHE_LABELS[cache.status] || "状态待确认"} tone={cache.status === "available" ? "success" : "warning"} />
          <MetricCard label="缓存 AI 规则" value={routing.cached_ai_domains_count ?? "待确认"} note="路由分类依据，不代表配置已应用" />
          <MetricCard label="待分类域名" value={routing.pending_domains_without_classifier ?? "待确认"} note="分类器不可用时未分类的新域名" />
        </div>
        <p className="cc-route-card__note">当前窗口：{routing.window_start_display || "待确认"} — {routing.window_end_display || "待确认"} · 最近域名最多展示 20 条</p>
        <DataTable
          caption="当前窗口域名路由"
          columns={[
            { key: "domain", label: "域名", render: (row) => <strong>{row.domain}</strong> },
            { key: "hits", label: "请求数", render: (row) => row.hits ?? "—" },
            { key: "classification", label: "分类", render: (row) => CLASSIFICATION_LABELS[row.classification] || "待分类" },
            { key: "source", label: "分类来源", render: (row) => SOURCE_LABELS[row.source] || row.source || "未报告" },
            { key: "reason", label: "分类原因", render: (row) => row.reason || "未报告" },
            { key: "traffic_route", label: "报告路由", render: domainRoute },
          ]}
          rows={domains}
          rowKey={(row) => row.domain}
          empty="当前窗口暂无域名报告；历史分类数量见上方数据库统计。"
        />
      </Panel>

      {confirm.dialog}
    </div>
  );
}
