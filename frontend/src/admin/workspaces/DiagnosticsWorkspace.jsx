import { AvailabilityStrip, EventTimeline } from "../components/charts/index.jsx";
import { DataTable, MetricCard, Panel, StatusPill, Tone } from "../components/ui.jsx";
import { buildChecklist, summaryTone } from "../lib/diagnose.js";
import { dnsFailoverSummary, dnsFailoverTone } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

function realityLabel(port) {
  if (!port.reality) return "未检测";
  if (port.reality.ok) return "握手成功";
  return `失败 · ${port.reality.error || "无原因"}`;
}

export default function DiagnosticsWorkspace() {
  const panel = usePanel();
  const diagnosis = panel.diagnosis;
  const probes = panel.insights?.probes || null;
  const events = panel.insights?.failover_events?.events || [];
  const checklist = buildChecklist(panel.panel, panel.insights, diagnosis);

  const timelineEvents = events.map((event) => ({
    id: event.id,
    title: `${event.event_type_label} · ${event.status_label}${event.target_label ? ` · ${event.target_label}` : ""}`,
    detail: event.detail || "无补充说明。",
    created_at_display: event.created_at_display,
    tone: event.event_status === "ok" ? "success" : event.event_status === "error" ? "danger" : "neutral",
  }));

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">DIAGNOSTICS</p>
          <h2>故障后排查</h2>
          <p>先看当前结论，再按顺序核对探测记录、DNS 切换事件和数据面体检结果；所有结论都来自控制面已存储的数据。</p>
        </div>
        <div className="cc-toolbar__group">
          <button
            className="a-btn primary"
            type="button"
            disabled={panel.isBusy("diagnose-data-plane")}
            onClick={() => panel.diagnoseDataPlane()}
          >
            {panel.isBusy("diagnose-data-plane") ? "体检中…" : "运行数据面体检"}
          </button>
          {panel.meta.probe_enabled && panel.meta.probe_dashboard_url ? (
            <a className="a-btn ghost" href={panel.meta.probe_dashboard_url}>打开探针页</a>
          ) : null}
        </div>
      </section>

      <section className="cc-metric-grid">
        <MetricCard
          label="探测可用率"
          value={probes ? `${probes.uptime_ratio}%` : "—"}
          note={probes ? `${probes.total_checks} 次记录 · ${probes.unhealthy_checks} 次失败` : panel.insightsError || "历史未加载"}
          tone={probes && Number(probes.uptime_ratio) >= 99 ? "success" : "warning"}
          accent
        />
        <MetricCard label="最近失败" value={probes?.recent_failures?.length || 0} note="最新一次探测不可达的端口" tone={probes?.recent_failures?.length ? "danger" : "success"} />
        <MetricCard
          label="DNS 切换"
          value={dnsFailoverSummary(panel.dnsFailoverStatus)}
          note={`最近切换：${panel.dnsFailoverStatus?.last_switch_at_display || "暂无"}`}
          tone={dnsFailoverTone(panel.dnsFailoverStatus) === "success" ? "success" : "warning"}
        />
        <MetricCard
          label="连续失败 / 成功"
          value={`${panel.dnsFailoverStatus?.consecutive_failures || 0} / ${panel.dnsFailoverStatus?.consecutive_successes || 0}`}
          note={`阈值：失败 ${panel.dnsFailoverStatus?.failure_threshold ?? "—"} · 恢复 ${panel.dnsFailoverStatus?.recovery_threshold ?? "—"}`}
        />
        <MetricCard
          label="数据面体检"
          value={diagnosis ? `${diagnosis.summary.ports_tcp_ok}/${diagnosis.summary.ports_total} TCP` : "未运行"}
          note={diagnosis ? `Reality ${diagnosis.summary.ports_reality_ok}/${diagnosis.summary.ports_total}` : "点击右上角运行体检"}
          tone={diagnosis ? "info" : "neutral"}
        />
      </section>

      <Panel kicker="CHECK FIRST" title="排查顺序" description="按严重程度排序，先处理会中断流量的项。">
        <ul className="cc-checklist">
          {checklist.map((item) => (
            <li key={item.title} className={`cc-checklist__item is-${item.tone}`}>
              <Tone tone={item.tone}>{item.tone === "danger" ? "阻断" : item.tone === "warning" ? "注意" : "正常"}</Tone>
              <div>
                <strong>{item.title}</strong>
                <small>{item.detail}</small>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        kicker="PROBE HISTORY"
        title="端口探测记录"
        description="每格是一次上游可达性探测，最近的在右侧。"
      >
        {probes?.ports?.length ? (
          <div className="cc-probe-list">
            {probes.ports.map((port) => (
              <article key={port.listen_port} className="cc-probe-row">
                <div className="cc-probe-row__head">
                  <strong>:{port.listen_port}</strong>
                  <span>{port.note || "未命名"}</span>
                  <StatusPill tone={port.status === "healthy" ? "success" : port.status === "unhealthy" ? "danger" : "neutral"} label={port.status_label} />
                  <small>
                    {port.total_checks} 次 · 可用率 {port.uptime_ratio}% · {port.checked_at_display}
                  </small>
                </div>
                <AvailabilityStrip checks={port.checks} ariaLabel={`端口 ${port.listen_port} 探测可用性`} />
              </article>
            ))}
          </div>
        ) : (
          <div className="cc-empty">{panel.insightsError || "暂无探测记录（需要启用 PROBE_ENABLED）。"}</div>
        )}
      </Panel>

      <section className="cc-split">
        <Panel kicker="FAILURES" title="最近失败" description="最新一次不可达的端口与原因。">
          {probes?.recent_failures?.length ? (
            <ul className="cc-failure-list">
              {probes.recent_failures.map((failure) => (
                <li key={`${failure.listen_port}-${failure.checked_at}`}>
                  <div>
                    <strong>:{failure.listen_port}</strong>
                    <span>{failure.note || "未命名"}</span>
                  </div>
                  <p>{failure.failure_reason || "未记录失败原因。"}</p>
                  <time>{failure.checked_at_display}</time>
                </li>
              ))}
            </ul>
          ) : (
            <div className="cc-empty">所选区间内没有失败的探测记录。</div>
          )}
        </Panel>

        <Panel kicker="FAILOVER EVENTS" title="DNS 切换事件" description="探测、切换和回切的完整时间线（最近 40 条）。">
          <EventTimeline events={timelineEvents} emptyLabel={panel.insightsError || "暂无切换事件。"} />
        </Panel>
      </section>

      <Panel
        kicker="DATA PLANE CHECK"
        title="数据面体检"
        description={
          diagnosis
            ? `生成于 ${diagnosis.generated_at} · ${diagnosis.data_plane_mode} 模式`
            : "尚未运行。体检会验证端口 TCP 可达性、Reality 握手以及订阅下发参数与数据面实际配置是否一致。"
        }
      >
        {diagnosis ? (
          <div className="cc-diagnosis">
            <div className="cc-inline-status">
              <span>
                订阅配置：<Tone tone={diagnosis.subscription_profile_available ? "success" : "danger"}>
                  {diagnosis.subscription_profile_available ? "可用" : `不可用 · ${diagnosis.subscription_error || ""}`}
                </Tone>
              </span>
              <span>节点 {diagnosis.node_host || "—"} · SNI {diagnosis.server_name || "—"}</span>
              <span>
                TCP：
                <Tone tone={summaryTone(diagnosis.summary.ports_tcp_ok, diagnosis.summary.ports_total)}>
                  {diagnosis.summary.ports_tcp_ok}/{diagnosis.summary.ports_total}
                </Tone>
              </span>
              <span>
                Reality：
                <Tone tone={summaryTone(diagnosis.summary.ports_reality_ok, diagnosis.summary.ports_total)}>
                  {diagnosis.summary.ports_reality_ok}/{diagnosis.summary.ports_total}
                </Tone>
              </span>
            </div>

            <h4>订阅 ↔ 数据面配置一致性</h4>
            {diagnosis.consistency?.available ? (
              <>
                <p className="cc-diagnosis__source">来源：{diagnosis.consistency.source}</p>
                <DataTable
                  caption="订阅下发参数与数据面实际配置比对"
                  columns={[
                    { key: "field", label: "字段" },
                    { key: "subscription", label: "订阅下发", className: "mono" },
                    { key: "data_plane", label: "数据面实际", className: "mono", render: (row) => (row.data_plane || []).join(", ") || "（空）" },
                    {
                      key: "match",
                      label: "结果",
                      render: (row) => <Tone tone={row.match ? "success" : "danger"}>{row.match ? "一致" : "不一致"}</Tone>,
                    },
                  ]}
                  rows={diagnosis.consistency.fields || []}
                  rowKey={(row) => row.field}
                  empty="没有可比的字段。"
                />
              </>
            ) : (
              <p className="cc-tone is-danger">无法比对：{diagnosis.consistency?.error || "未知原因"}</p>
            )}

            <h4>端口连通性 / Reality 握手</h4>
            <DataTable
              caption="端口连通性与 Reality 握手结果"
              columns={[
                { key: "listen_port", label: "端口", className: "mono" },
                { key: "note", label: "备注", render: (row) => row.note || "—" },
                {
                  key: "tcp_reachable",
                  label: "TCP",
                  render: (row) => (
                    <Tone tone={row.tcp_reachable ? "success" : "danger"}>
                      {row.tcp_reachable ? "通" : `不通 · ${row.tcp_error || ""}`}
                    </Tone>
                  ),
                },
                {
                  key: "reality",
                  label: "Reality",
                  render: (row) => <Tone tone={row.reality?.ok ? "success" : row.reality ? "danger" : "neutral"}>{realityLabel(row)}</Tone>,
                },
                {
                  key: "cert",
                  label: "回落证书",
                  className: "mono",
                  render: (row) => row.reality?.cert_subject_cn || "—",
                },
              ]}
              rows={diagnosis.ports || []}
              rowKey={(row) => row.listen_port}
              empty="没有启用的端口可检测。"
            />
          </div>
        ) : (
          <div className="cc-empty">尚未运行体检。</div>
        )}
      </Panel>
    </div>
  );
}
