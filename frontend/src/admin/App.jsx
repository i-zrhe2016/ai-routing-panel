import { useEffect, useState } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary.jsx";
import WorkspaceNav from "./components/WorkspaceNav.jsx";
import { Loader, Notice, StatusPill } from "./components/ui.jsx";
import { usePanel } from "./state/PanelProvider.jsx";
import CommerceWorkspace from "./workspaces/CommerceWorkspace.jsx";
import DeliveryWorkspace from "./workspaces/DeliveryWorkspace.jsx";
import DiagnosticsWorkspace from "./workspaces/DiagnosticsWorkspace.jsx";
import HostsWorkspace from "./workspaces/HostsWorkspace.jsx";
import ObservabilityWorkspace from "./workspaces/ObservabilityWorkspace.jsx";
import OverviewWorkspace from "./workspaces/OverviewWorkspace.jsx";
import RoutingWorkspace from "./workspaces/RoutingWorkspace.jsx";
import TrafficWorkspace from "./workspaces/TrafficWorkspace.jsx";

// The console is organized around the incident questions: what the hosts are
// doing, what the traffic looks like, and what to check after a failure.
const WORKSPACES = [
  { key: "overview", label: "总览", description: "健康、路径与待处理", blurb: "先确认系统健康、当前路径和待处理，再进入具体操作。" },
  { key: "hosts", label: "主机", description: "数据面与纳管节点", blurb: "每台纳管主机的角色、管理通道、运行时状态与可用操作。" },
  { key: "traffic", label: "流量", description: "日流量与端口负载", blurb: "累计流量、日粒度历史、端口负载排行与单端口明细。" },
  { key: "diagnostics", label: "故障排查", description: "探测、切换与体检", blurb: "按顺序核对探测记录、DNS 切换事件和数据面体检结果。" },
  { key: "routing", label: "AI 路由", description: "出口、探测与切换", blurb: "解释当前 AI 出口、候选健康、人工策略和故障切换。" },
  { key: "delivery", label: "交付", description: "端口与租户凭据", blurb: "管理监听入口、租户配额与每个端口的独立订阅凭据。" },
  { key: "commerce", label: "订单与套餐", description: "售卖、审核与开通", blurb: "管理套餐、订单审核与自动开通。" },
  { key: "observe", label: "可观测", description: "Prometheus 与 Grafana", blurb: "从指标和 Grafana 深入排查资源与流量异常。" },
];

const WORKSPACE_COMPONENTS = {
  overview: OverviewWorkspace,
  hosts: HostsWorkspace,
  traffic: TrafficWorkspace,
  diagnostics: DiagnosticsWorkspace,
  routing: RoutingWorkspace,
  delivery: DeliveryWorkspace,
  commerce: CommerceWorkspace,
  observe: ObservabilityWorkspace,
};

export default function App() {
  const panel = usePanel();
  const [activeWorkspace, setActiveWorkspace] = useState("overview");
  const [isMobile, setIsMobile] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    function updateIsMobile() {
      const mobile = typeof window !== "undefined" && window.innerWidth <= 840;
      setIsMobile(mobile);
      if (!mobile) setMobileNavOpen(false);
    }
    updateIsMobile();
    window.addEventListener("resize", updateIsMobile);
    return () => window.removeEventListener("resize", updateIsMobile);
  }, []);

  const meta = WORKSPACES.find((item) => item.key === activeWorkspace) || WORKSPACES[0];
  const ActiveWorkspace = WORKSPACE_COMPONENTS[activeWorkspace];
  const badges = {
    diagnostics: panel.insights?.probes?.recent_failures?.length || 0,
    commerce: panel.commerce.summary.pending_review_count || 0,
  };
  const lastRefreshLabel =
    panel.meta?.dashboard_updated_at_display || panel.meta?.updated_at_display || "自动刷新 15 秒";

  function selectWorkspace(key) {
    setActiveWorkspace(key);
    setMobileNavOpen(false);
  }

  return (
    <div className="admin-shell">
      <aside className={`admin-sidebar${mobileNavOpen ? " is-open" : ""}`} aria-label="控制台导航">
        <div className="sidebar-scroll">
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true">XR</div>
            <div>
              <p className="brand-kicker">ROUTING PANEL</p>
              <strong>Control Center</strong>
            </div>
          </div>
          <p className="brand-description">主机、流量与故障排查的一体化控制面。</p>
          <WorkspaceNav items={WORKSPACES} activeKey={activeWorkspace} onSelect={selectWorkspace} badges={badges} />
          <div className="sidebar-status-stack">
            <div className="sidebar-status-card">
              <div className="sidebar-status-card__head">
                <span>DATA PLANE</span>
                <i className={panel.dataPlaneStatus?.xray_running ? "is-ok" : "is-bad"} />
              </div>
              <strong>{panel.dataPlaneRunningLabel()}</strong>
              <small>{panel.dataPlaneStatus?.management_target || "当前未配置数据面"}</small>
            </div>
            <div className="sidebar-status-card">
              <div className="sidebar-status-card__head">
                <span>AI NODE</span>
                <i className={panel.aiNodeStatus?.reachable ? "is-ok" : "is-warn"} />
              </div>
              <strong>{panel.aiNodeStatus?.reachable ? "可达" : "待确认"}</strong>
              <small>{panel.aiNodeStatus?.management_target || "AI 节点未纳管"}</small>
            </div>
          </div>
        </div>
        <div className="sidebar-footer">
          <span>CONTROL PLANE</span>
          <strong>{panel.meta?.panel_address || "—"}</strong>
          <small>{panel.meta?.timezone_label || "服务器本地时区"}</small>
        </div>
      </aside>

      {isMobile && mobileNavOpen ? (
        <button className="mobile-scrim" type="button" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />
      ) : null}

      <div className="admin-main">
        <header className="admin-topbar">
          <div className="topbar-title">
            {isMobile ? (
              <button
                className="icon-button mobile-menu-button"
                type="button"
                aria-label="打开控制台导航"
                onClick={() => setMobileNavOpen((open) => !open)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
              </button>
            ) : null}
            <div>
              <p className="breadcrumb">
                CONTROL CENTER <span>/</span> {meta.label}
              </p>
              <h1>{meta.label}</h1>
              <p>{meta.blurb}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <div className="live-indicator">
              <span className={`live-dot${panel.loading ? " is-busy" : ""}`} />
              <span>{panel.loading ? "同步中" : lastRefreshLabel}</span>
            </div>
            <StatusPill
              tone={panel.dataPlaneStatus?.xray_running ? "success" : "danger"}
              label={panel.dataPlaneRunningLabel()}
            />
            <button
              className="icon-button"
              type="button"
              aria-label={panel.loading ? "正在刷新" : "刷新数据"}
              disabled={panel.loading}
              onClick={() => panel.refreshDashboard()}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M20 11a8.1 8.1 0 0 0-14.9-3M4 5v4h4M4 13a8.1 8.1 0 0 0 14.9 3M20 19v-4h-4" />
              </svg>
            </button>
          </div>
        </header>

        <main className="admin-content">
          <Notice message={panel.flash.message} level={panel.flash.level} onClose={panel.clearFlash} />
          <Loader show={panel.loading} />
          <ErrorBoundary>
            <ActiveWorkspace onOpenWorkspace={selectWorkspace} />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
