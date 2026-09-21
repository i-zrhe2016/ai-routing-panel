import { useConfirm } from "../components/ConfirmDialog.jsx";
import { GrafanaPanels, GrafanaToolbar, HOST_PANELS, useGrafana } from "../components/GrafanaPanels.jsx";
import { FlowPath } from "../components/charts/index.jsx";
import { MetricCard, Panel, StatusPill } from "../components/ui.jsx";
import { buildHostInventory, hostStateLabel, hostTone } from "../lib/hosts.js";
import { usePanel } from "../state/PanelProvider.jsx";

export default function HostsWorkspace() {
  const panel = usePanel();
  const confirm = useConfirm();
  const grafana = useGrafana(panel.meta);
  const hosts = buildHostInventory(panel.panel, panel.insights);
  const reachable = hosts.filter((host) => host.reachable).length;
  const running = hosts.filter((host) => host.running === true).length;

  const flowNodes = [
    { role: "入口", name: panel.trafficRouting?.entry_node || "普通数据面", active: true },
    {
      role: "中转",
      name: (panel.trafficRouting?.transit_nodes || []).join(" · ") || "直出",
      active: Boolean(panel.trafficRouting?.transit_nodes?.length),
    },
    { role: "出口", name: panel.trafficRouting?.exit_node || "待确认", active: true },
  ];

  function restart(host) {
    const isDataPlane = host.role === "data_plane";
    confirm.ask({
      title: `重启${isDataPlane ? "数据面" : "AI 节点"}？`,
      body: `${host.label}（${host.target || "未配置管理目标"}）会短暂中断连接，随后由控制面重新下发配置。`,
      tone: "danger",
      confirmLabel: "确认重启",
      onConfirm: () =>
        isDataPlane ? panel.restartDataPlane() : panel.restartAiNode(host.nodeId),
    });
  }

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">HOSTS</p>
          <h2>主机与数据面</h2>
          <p>每台纳管主机的角色、管理通道、运行时状态和可用操作都在这里；重启和体检是主机级的唯一写操作。</p>
        </div>
        <span className="cc-status-line">{hosts.length} 台纳管主机</span>
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="纳管主机" value={hosts.length} note="控制面返回的主机清单" accent />
        <MetricCard label="可达" value={`${reachable} / ${hosts.length}`} note="管理通道可达" tone={reachable === hosts.length ? "success" : "warning"} />
        <MetricCard label="Xray 运行" value={`${running} / ${hosts.length}`} note="容器内进程状态" tone={running === hosts.length ? "success" : "warning"} />
        <MetricCard
          label="当前出口"
          value={panel.trafficRouting?.exit_node || "待确认"}
          note={panel.trafficRouting?.label || "等待路由状态"}
          tone="info"
        />
      </section>

      <Panel kicker="PATH" title="当前流量路径" description={panel.trafficRouting?.scenario || "等待路由状态同步。"}>
        <FlowPath nodes={flowNodes} />
      </Panel>

      <div className="cc-host-grid">
        {hosts.map((host) => (
          <article key={host.key} className={`cc-host-card is-${hostTone(host)}`}>
            <div className="cc-host-card__head">
              <div>
                <p className="section-kicker">{host.roleLabel}</p>
                <h3>{host.label}</h3>
              </div>
              <StatusPill tone={hostTone(host)} label={hostStateLabel(host)} />
            </div>
            <dl className="cc-host-card__facts">
              <div>
                <dt>管理目标</dt>
                <dd>{host.target || "未配置"}</dd>
              </div>
              {host.apiServer ? (
                <div>
                  <dt>Xray API</dt>
                  <dd>{host.apiServer}</dd>
                </div>
              ) : null}
              {host.configPath ? (
                <div>
                  <dt>配置路径</dt>
                  <dd>{host.configPath}</dd>
                </div>
              ) : null}
              {host.accessLogPath ? (
                <div>
                  <dt>访问日志</dt>
                  <dd>{host.accessLogPath}</dd>
                </div>
              ) : null}
              <div>
                <dt>支持操作</dt>
                <dd>
                  {[host.supportsRestart ? "重启" : null, host.supportsSync ? "下发配置" : null]
                    .filter(Boolean)
                    .join(" · ") || "只读"}
                </dd>
              </div>
            </dl>
            {host.lastError ? <p className="cc-host-card__error">{host.lastError}</p> : null}
            <div className="cc-host-card__actions">
              {host.supportsRestart ? (
                <button
                  className="a-btn secondary"
                  type="button"
                  disabled={panel.isBusy(host.role === "data_plane" ? "restart-data-plane" : `restart-ai-node:${host.nodeId}`)}
                  onClick={() => restart(host)}
                >
                  {panel.isBusy(host.role === "data_plane" ? "restart-data-plane" : `restart-ai-node:${host.nodeId}`)
                    ? "重启中…"
                    : "重启"}
                </button>
              ) : null}
              {host.role === "data_plane" ? (
                <button
                  className="a-btn ghost"
                  type="button"
                  disabled={panel.isBusy("diagnose-data-plane")}
                  onClick={() => panel.diagnoseDataPlane()}
                >
                  {panel.isBusy("diagnose-data-plane") ? "体检中…" : "数据面体检"}
                </button>
              ) : null}
            </div>
          </article>
        ))}
        {!hosts.length ? <div className="cc-empty">控制面未返回任何纳管主机。</div> : null}
      </div>

      <Panel
        kicker="HOST RESOURCES"
        title="主机资源"
        description="CPU、内存、磁盘和负载来自 Prometheus，由 Grafana 出图。"
        actions={null}
      >
        <GrafanaToolbar grafana={grafana} />
        <GrafanaPanels grafana={grafana} panels={HOST_PANELS} />
      </Panel>

      {confirm.dialog}
    </div>
  );
}
