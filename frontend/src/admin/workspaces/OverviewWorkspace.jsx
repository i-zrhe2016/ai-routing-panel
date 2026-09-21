import { FlowPath, SeriesChart } from "../components/charts/index.jsx";
import { MetricCard, Panel, StatusPill, Tone } from "../components/ui.jsx";
import { humanBytes } from "../../shared/formatters.js";
import { buildChecklist } from "../lib/diagnose.js";
import { aiRoutingLabel, dnsFailoverSummary, dnsFailoverTone, toneFromStatus } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const RECEIVED_COLOR = "var(--c-primary)";
const SENT_COLOR = "var(--c-success)";

export default function OverviewWorkspace({ onOpenWorkspace }) {
  const panel = usePanel();
  const checklist = buildChecklist(panel.panel, panel.insights, panel.diagnosis).slice(0, 3);
  const routeTone = toneFromStatus(panel.aiRoutingStatus?.status_tone);
  const flowNodes = [
    { role: "入口", name: panel.trafficRouting?.entry_node || "普通数据面", active: true },
    { role: "中转", name: (panel.trafficRouting?.transit_nodes || []).join(" · ") || "直出", active: Boolean(panel.trafficRouting?.transit_nodes?.length) },
    { role: "出口", name: panel.trafficRouting?.exit_node || "待确认", active: true },
  ];
  const traffic = panel.insights?.traffic;

  return (
    <div className="workspace-section">
      <section className="command-hero">
        <div>
          <p className="section-kicker">OPERATIONS SNAPSHOT</p>
          <h2>今天的路径，是否值得信任？</h2>
          <p>先确认主机与出口状态，再看流量是否正常，最后才处理配置细节。</p>
        </div>
        <div className="command-hero__status">
          <StatusPill tone={routeTone} label={aiRoutingLabel(panel.aiRoutingStatus)} />
          <span>{panel.trafficRouting?.scenario || "等待路由状态同步"}</span>
        </div>
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="ACTIVE PORTS" value={panel.summary.active_ports || 0} note="当前可被客户端访问" tone="success" accent />
        <MetricCard label="需处理端口" value={panel.attentionPortCount} note="过期、停用或达到上限" tone="warning" />
        <MetricCard label="租户数量" value={panel.subscription.tenant_count || 0} note="每个端口一个租户入口" tone="info" />
        <MetricCard label="累计总流量" value={humanBytes(panel.totalTrafficBytes)} note="入站 + 出站" />
        <MetricCard label="待审订单" value={panel.commerce.summary.pending_review_count || 0} note="付款截图待审核" tone="warning" />
      </section>

      <section className="cc-split">
        <Panel
          kicker="CHECK FIRST"
          title="现在先处理这些"
          description="按当前快照推导，只列出控制面已经报出的异常。"
          actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("diagnostics")}>进入故障排查</button>}
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

        <Panel kicker="TRAFFIC PULSE" title="近期流量" description={traffic ? `最近 ${traffic.days} 天，来自面板自身的历史表。` : "历史数据尚未加载。"}>
          {traffic ? (
            <SeriesChart
              labels={traffic.dates}
              ariaLabel="全站每日流量"
              series={[
                { key: "received", label: "入站", values: traffic.series.bytes_received, color: RECEIVED_COLOR },
                { key: "sent", label: "出站", values: traffic.series.bytes_sent, color: SENT_COLOR },
              ]}
            />
          ) : (
            <div className="cc-chart-empty">{panel.insightsError || "历史数据尚未加载。"}</div>
          )}
        </Panel>
      </section>

      <Panel kicker="ROUTING PATH" title="当前流量路径" description={panel.trafficRouting?.label || "等待路由状态同步"}>
        <FlowPath nodes={flowNodes} />
        <div className="cc-inline-status">
          <span>
            数据面：<StatusPill tone={panel.dataPlaneStatus?.xray_running ? "success" : "danger"} label={panel.dataPlaneRunningLabel()} />
          </span>
          <span>
            AI 节点：<StatusPill tone={panel.aiNodeStatus?.reachable ? "success" : "warning"} label={panel.aiNodeStatus?.reachable ? "可达" : "待确认"} />
          </span>
          <span>
            DNS：<StatusPill tone={dnsFailoverTone(panel.dnsFailoverStatus)} label={dnsFailoverSummary(panel.dnsFailoverStatus)} />
          </span>
        </div>
      </Panel>

      <Panel
        kicker="HOSTS"
        title="主机一览"
        description="数据面、AI 节点和控制面备用入口的当前状态。"
        actions={<button className="a-btn ghost" type="button" onClick={() => onOpenWorkspace("hosts")}>打开主机工作区</button>}
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
