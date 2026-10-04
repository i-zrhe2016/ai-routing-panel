import { SeriesChart } from "../components/charts/index.jsx";
import TrafficTopology from "../components/TrafficTopology.jsx";
import { MetricCard, Panel, StatusPill, Tone } from "../components/ui.jsx";
import { humanBytes } from "../../shared/formatters.js";
import { buildChecklist } from "../lib/diagnose.js";
import { aiRoutingLabel, toneFromStatus } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const RECEIVED_COLOR = "var(--c-primary)";
const SENT_COLOR = "var(--c-success)";

export default function OverviewWorkspace({ onOpenWorkspace }) {
  const panel = usePanel();
  const checklist = buildChecklist(panel.panel, panel.insights, panel.diagnosis).slice(0, 3);
  const routeTone = toneFromStatus(panel.aiRoutingStatus?.status_tone);
  const traffic = panel.insights?.traffic;
  const historyMessage = panel.insightsLoading
    ? "正在加载流量历史…"
    : panel.insightsError || "暂无流量历史记录。";

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <h1>系统总览</h1>
          <p>掌握流量与运行状态，及时处理连接异常。</p>
        </div>
        <div className="command-hero__status">
          <StatusPill tone={routeTone} label={aiRoutingLabel(panel.aiRoutingStatus)} />
          <span>{panel.trafficRouting?.scenario || "等待路由状态同步"}</span>
        </div>
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="运行端口" value={panel.summary.active_ports || 0} note="当前可被客户端访问" tone="success" accent />
        <MetricCard label="累计总流量" value={humanBytes(panel.totalTrafficBytes)} note="入站 + 出站" />
        <MetricCard label="租户数量" value={panel.subscription.tenant_count || 0} note="每个端口一个租户入口" tone="info" />
        <MetricCard label="需处理端口" value={panel.attentionPortCount} note="过期、停用或达到上限" tone="warning" />
      </section>

      <section className="overview-layout">
        <Panel
          className="overview-traffic"
          title="近期流量"
          description={traffic ? `最近 ${traffic.days} 天的全站日流量。` : "全站入站与出站的每日趋势。"}
          actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("traffic")}>查看流量明细</button>}
        >
          {traffic ? (
            <>
              <div className="overview-traffic-summary">
                <span>区间累计 <strong>{humanBytes(traffic.totals.total_bytes)}</strong></span>
                <span>{traffic.range_start} — {traffic.range_end}</span>
              </div>
              <SeriesChart
                labels={traffic.dates}
                ariaLabel="全站每日流量"
                height={230}
                emptyLabel="所选区间内暂无流量记录。"
                series={[
                  { key: "received", label: "入站", values: traffic.series.bytes_received, color: RECEIVED_COLOR },
                  { key: "sent", label: "出站", values: traffic.series.bytes_sent, color: SENT_COLOR },
                ]}
              />
            </>
          ) : (
            <div className="cc-chart-empty" role={panel.insightsError ? "alert" : "status"}>{historyMessage}</div>
          )}
        </Panel>

        <Panel
          className="overview-attention"
          title="待办与异常"
          description="依据当前快照，优先处理影响连接的事项。"
          actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("topology")}>查看流量路径</button>}
        >
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
      </section>

      <Panel
        className="overview-route"
        title="流量拓扑"
        description={panel.trafficRouting?.label || "等待路由状态同步"}
        actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("topology")}>查看完整拓扑</button>}
      >
        <TrafficTopology panel={panel} compact />
      </Panel>

      <Panel
        className="overview-hosts"
        title="主机一览"
        description="数据面、AI 节点和控制面备用入口的当前状态。"
        actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("hosts")}>查看主机</button>}
      >
        <div className="cc-node-list">
          {panel.nodes.map((node) => (
            <div key={node.key} className="cc-node-row">
              <div>
                <strong>{node.label}</strong>
                <small>{node.management_target || "未配置管理目标"}</small>
              </div>
              <StatusPill
                tone={!node.configured ? "neutral" : node.xray_running === true ? "success" : node.xray_running === false ? "danger" : "warning"}
                label={!node.configured ? "未配置" : node.xray_running === true ? "运行中" : node.xray_running === false ? "未运行" : "待探测"}
              />
            </div>
          ))}
          {!panel.nodes.length ? <div className="cc-empty">控制面未返回主机列表。</div> : null}
        </div>
      </Panel>
    </div>
  );
}
