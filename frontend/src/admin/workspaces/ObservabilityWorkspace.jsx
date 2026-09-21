import { GrafanaPanels, GrafanaToolbar, TRAFFIC_PANELS, useGrafana } from "../components/GrafanaPanels.jsx";
import { Panel } from "../components/ui.jsx";
import { usePanel } from "../state/PanelProvider.jsx";

export default function ObservabilityWorkspace() {
  const panel = usePanel();
  const grafana = useGrafana(panel.meta);

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">OBSERVABILITY</p>
          <h2>指标与 Grafana</h2>
          <p>主机资源与每端口速率来自 Prometheus，由 Grafana 出图并内嵌于此；配置和订单数据仍由面板自身的 SQLite 提供。</p>
        </div>
        <span className="cc-status-line">{grafana.configured ? "Grafana 已配置" : "Grafana 未配置"}</span>
      </section>

      <Panel
        kicker="GRAFANA"
        title="监控面板"
        description={grafana.configured ? "面板按需加载，不缓存历史数据。" : "设置 GRAFANA_PUBLIC_URL 后此处会内嵌图表。"}
      >
        <GrafanaToolbar grafana={grafana} label="范围" />
        <GrafanaPanels grafana={grafana} panels={TRAFFIC_PANELS} />
      </Panel>

      <Panel kicker="DEEP LINKS" title="相关页面" description="控制面自带的其他排障入口。">
        <div className="action-row">
          {panel.meta.ai_domain_dashboard_url ? (
            <a className="a-btn ghost" href={panel.meta.ai_domain_dashboard_url}>AI 域名页</a>
          ) : null}
          {panel.meta.probe_enabled && panel.meta.probe_dashboard_url ? (
            <a className="a-btn ghost" href={panel.meta.probe_dashboard_url}>探针页</a>
          ) : null}
          {grafana.dashboardLink ? (
            <a className="a-btn ghost" href={grafana.dashboardLink} target="_blank" rel="noopener noreferrer">Grafana 观测面板 ↗</a>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
