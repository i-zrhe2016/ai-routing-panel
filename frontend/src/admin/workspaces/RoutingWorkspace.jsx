import { useConfirm } from "../components/ConfirmDialog.jsx";
import { FlowPath } from "../components/charts/index.jsx";
import { DataTable, MetricCard, Panel, StatusPill } from "../components/ui.jsx";
import { aiRoutingLabel, toneFromStatus } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const MODE_LABELS = {
  auto: "自动探测",
  primary: "人工固定主 AI",
  backup: "人工固定备用 AI",
  forced_fallback: "应急直出",
};

function candidateAddress(candidate) {
  if (!candidate) return "地址待确认";
  return candidate.candidate_label || `${candidate.upstream_host || "地址待确认"}:${candidate.upstream_port || "—"}`;
}

function candidateStatus(candidate) {
  if (candidate?.is_reachable === null || candidate?.is_reachable === undefined) return { label: "待探测", tone: "warning" };
  return candidate.is_reachable ? { label: "可达", tone: "success" } : { label: "不可达", tone: "danger" };
}

function modeForCandidate(candidate) {
  return candidate.index === 0 ? "primary" : "backup";
}

export default function RoutingWorkspace() {
  const panel = usePanel();
  const confirm = useConfirm();
  const routing = panel.aiRoutingStatus || {};
  const candidates = (Array.isArray(routing.ai_candidates) ? routing.ai_candidates : []).slice(0, 2);
  const manualMode = routing.manual_mode || "auto";
  const selectedCandidate = manualMode === "forced_fallback"
    ? null
    : candidates.find((candidate) => candidate.selected) || null;
  const healthyCount = candidates.filter((candidate) => candidate.is_reachable === true).length;
  const routeTone = toneFromStatus(routing.status_tone);

  const routeTarget = manualMode === "forced_fallback"
    ? { label: "普通数据面", detail: "AI 流量暂时回到 freedom 直出" }
    : selectedCandidate
      ? {
        label: selectedCandidate.is_reachable === false ? "AI 节点不可达" : selectedCandidate.label || "AI 节点",
        detail: candidateAddress(selectedCandidate),
      }
      : { label: "AI 节点待确认", detail: "等待下一轮探测结果" };

  const flowNodes = [
    { role: "入口", name: panel.trafficRouting?.entry_node || "普通数据面", active: true },
    { role: "中转", name: (panel.trafficRouting?.transit_nodes || []).join(" · ") || "直出", active: Boolean(panel.trafficRouting?.transit_nodes?.length) },
    { role: "出口", name: panel.trafficRouting?.exit_node || "待确认", active: true },
  ];

  function requestSwitch(mode, candidate) {
    const title = mode === "forced_fallback"
      ? "启用应急直出？"
      : mode === "auto"
        ? "恢复自动探测？"
        : `确认切换到${candidate?.label || "该 AI 节点"}？`;
    const body = mode === "forced_fallback"
      ? "动态 AI 路由会被移除，AI 域名回到普通数据面的 freedom 直出。恢复 AI 节点路由需要手动恢复自动探测。"
      : mode === "auto"
        ? "控制面将恢复按主、备候选的可达性自动选择，不再固定当前人工目标。"
        : `${candidateAddress(candidate)} · ${candidateStatus(candidate).label}。此操作会暂停自动探测，直到手动恢复。`;
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
          <p className="section-kicker">AI ROUTING</p>
          <h2>路由决策与故障切换</h2>
          <p>把当前出口、候选节点、人工策略和探测结果放在同一个操作面，优先解释“现在为什么走这条路”。</p>
        </div>
        <StatusPill tone={routeTone} label={aiRoutingLabel(routing)} />
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="ACTIVE ROUTE" value={routeTarget.label} note={routeTarget.detail} accent />
        <MetricCard label="CANDIDATES" value={`${healthyCount} / ${candidates.length}`} note="当前探测可达" tone={healthyCount === candidates.length && candidates.length ? "success" : "warning"} />
        <MetricCard label="MANUAL MODE" value={MODE_LABELS[manualMode] || manualMode} note={`最近人工操作：${routing.manual_updated_at_display || "暂无"}`} tone={manualMode === "auto" ? "success" : "warning"} />
        <MetricCard label="LAST REPORT" value={routing.report_generated_at_display || "—"} note={routing.sync_error || "控制面最近一次路由报告"} />
      </section>

      <Panel
        kicker="ROUTE CONTROL"
        title="AI 出口控制"
        description="自动探测优先；人工固定目标不会静默改选另一个节点。"
        actions={
          manualMode !== "auto" ? (
            <button
              className="a-btn secondary"
              type="button"
              disabled={panel.isBusy("switch-ai-auto")}
              onClick={() => requestSwitch("auto")}
            >
              {panel.isBusy("switch-ai-auto") ? "恢复中…" : "恢复自动探测"}
            </button>
          ) : null
        }
      >
        <div className="cc-route-cards">
          {candidates.map((candidate) => {
            const status = candidateStatus(candidate);
            const mode = modeForCandidate(candidate);
            const current = manualMode !== "forced_fallback" && Boolean(candidate.selected);
            return (
              <article key={candidate.index} className={`cc-route-card is-${status.tone}`}>
                <div className="cc-route-card__head">
                  <div>
                    <p className="section-kicker">{candidate.index === 0 ? "PRIMARY" : "BACKUP"}</p>
                    <strong>{candidate.label || (candidate.index === 0 ? "主 AI 节点" : "备用 AI 节点")}</strong>
                  </div>
                  <StatusPill tone={status.tone} label={status.label} />
                </div>
                <p className="cc-route-card__address">{candidateAddress(candidate)}</p>
                <p className="cc-route-card__note">
                  {candidate.index === 0 ? "默认优先出口" : "主节点故障时的接管出口"}
                  {candidate.probe_method ? ` · 探测：${candidate.probe_method}` : ""}
                </p>
                <button
                  className={`a-btn ${current ? "ghost" : "secondary"}`}
                  type="button"
                  disabled={current || !routing.configured || panel.isBusy(`switch-ai-${mode}`)}
                  onClick={() => requestSwitch(mode, candidate)}
                >
                  {panel.isBusy(`switch-ai-${mode}`)
                    ? "切换中…"
                    : current
                      ? (candidate.is_reachable === false ? "人工目标不可达" : "当前使用")
                      : (candidate.index === 0 ? "固定主 AI" : "切换到备用 AI")}
                </button>
              </article>
            );
          })}
          {!candidates.length ? <div className="cc-empty">当前没有配置 AI 候选节点。</div> : null}
        </div>

        <details className="cc-advanced">
          <summary>高级应急</summary>
          <div className="cc-advanced__body">
            <p><strong>数据面直出</strong>：移除动态 AI 路由，让 AI 域名回普通数据面 freedom 直出。</p>
            <button
              className="a-btn danger"
              type="button"
              disabled={!routing.configured || manualMode === "forced_fallback" || panel.isBusy("switch-ai-forced_fallback")}
              onClick={() => requestSwitch("forced_fallback")}
            >
              {panel.isBusy("switch-ai-forced_fallback")
                ? "处理中…"
                : manualMode === "forced_fallback"
                  ? "已启用数据面直出"
                  : "强制直出"}
            </button>
          </div>
        </details>
      </Panel>

      <Panel kicker="PATH" title="当前流量路径" description={routing.route_status_reason || panel.trafficRouting?.scenario || "等待路由状态同步。"}>
        <FlowPath nodes={flowNodes} />
      </Panel>

      <Panel kicker="UPSTREAM HEALTH" title="候选节点" description="只展示控制面实际返回的候选状态。">
        <DataTable
          caption="AI 候选节点状态"
          columns={[
            { key: "label", label: "节点", render: (row) => <strong>{row.label || (row.index === 0 ? "主 AI 节点" : "备用 AI 节点")}</strong> },
            { key: "candidate_label", label: "地址", render: (row) => candidateAddress(row) },
            { key: "is_reachable", label: "健康", render: (row) => <StatusPill tone={candidateStatus(row).tone} label={candidateStatus(row).label} /> },
            { key: "selected", label: "选中", render: (row) => (row.selected ? "当前出口" : "待命") },
            { key: "probe_method", label: "探测", render: (row) => row.probe_method || routing.probe_method || "—" },
          ]}
          rows={candidates}
          rowKey={(row) => row.index}
          empty="暂无 AI 候选节点。"
        />
      </Panel>

      {confirm.dialog}
    </div>
  );
}
