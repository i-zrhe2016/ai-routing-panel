import { useEffect, useRef, useState } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary.jsx";
import WorkspaceNav from "./components/WorkspaceNav.jsx";
import { Loader, Notice, StatusPill } from "./components/ui.jsx";
import { useDialogFocus } from "./components/useDialogFocus.js";
import { usePanel } from "./state/PanelProvider.jsx";
import DeliveryWorkspace from "./workspaces/DeliveryWorkspace.jsx";
import DiagnosticsWorkspace from "./workspaces/DiagnosticsWorkspace.jsx";
import HostsWorkspace from "./workspaces/HostsWorkspace.jsx";
import OverviewWorkspace from "./workspaces/OverviewWorkspace.jsx";
import RoutingWorkspace from "./workspaces/RoutingWorkspace.jsx";
import TrafficWorkspace from "./workspaces/TrafficWorkspace.jsx";
import TopologyWorkspace from "./workspaces/TopologyWorkspace.jsx";

const WORKSPACES = [
  { key: "overview", label: "总览", description: "系统健康与待处理", group: "运行监控" },
  { key: "hosts", label: "主机", description: "数据面与纳管节点", group: "运行监控" },
  { key: "traffic", label: "流量", description: "使用趋势与端口负载", group: "运行监控" },
  { key: "diagnostics", label: "故障排查", description: "探测、切换与体检", group: "运行监控" },
  { key: "topology", label: "流量拓扑", description: "当前路径与候选出口", group: "运行监控" },
  { key: "routing", label: "AI 路由", description: "出口策略与候选节点", group: "配置与业务" },
  { key: "delivery", label: "交付", description: "端口与订阅链接", group: "配置与业务" },
];

const WORKSPACE_COMPONENTS = {
  overview: OverviewWorkspace,
  hosts: HostsWorkspace,
  traffic: TrafficWorkspace,
  diagnostics: DiagnosticsWorkspace,
  routing: RoutingWorkspace,
  delivery: DeliveryWorkspace,
  topology: TopologyWorkspace,
};

function mobileViewport() {
  return typeof window !== "undefined" && window.innerWidth <= 840;
}

export default function App() {
  const panel = usePanel();
  const [activeWorkspace, setActiveWorkspace] = useState("overview");
  const [isMobile, setIsMobile] = useState(mobileViewport);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const navigationRef = useRef(null);
  const drawerOpen = isMobile && mobileNavOpen;
  useDialogFocus(navigationRef, drawerOpen, () => setMobileNavOpen(false));

  useEffect(() => {
    function updateViewport() {
      const mobile = mobileViewport();
      setIsMobile(mobile);
      if (!mobile) setMobileNavOpen(false);
    }
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  const meta = WORKSPACES.find((item) => item.key === activeWorkspace) || WORKSPACES[0];
  const ActiveWorkspace = WORKSPACE_COMPONENTS[activeWorkspace];
  const badges = {
    diagnostics: panel.insights?.probes?.recent_failures?.length || 0,
  };
  const lastRefreshLabel = panel.meta?.dashboard_updated_at_display || panel.meta?.updated_at_display || "自动刷新 15 秒";
  const syncing = panel.loading || refreshing;
  const dataPlaneTone = panel.dataPlaneStatus?.xray_running === true ? "success" :
    panel.dataPlaneStatus?.xray_running === false ? "danger" : "neutral";
  const aiTone = panel.aiNodeStatus?.reachable === true ? "success" :
    panel.aiNodeStatus?.reachable === false ? "warning" : "neutral";

  function selectWorkspace(key) {
    setActiveWorkspace(key);
    setMobileNavOpen(false);
  }

  async function refresh() {
    if (syncing) return;
    setRefreshing(true);
    try {
      await panel.refreshDashboard();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="admin-shell">
      <a className="admin-skip-link" href="#admin-content">跳至主要内容</a>
      <aside
        id="admin-navigation"
        className={`admin-sidebar${drawerOpen ? " is-open" : ""}`}
        aria-label="控制台导航"
        role={drawerOpen ? "dialog" : undefined}
        aria-modal={drawerOpen ? true : undefined}
        tabIndex={drawerOpen ? -1 : undefined}
        hidden={isMobile && !drawerOpen}
        ref={navigationRef}
      >
        <div className="sidebar-scroll">
          {isMobile ? (
            <button className="icon-button sidebar-close" type="button" aria-label="关闭导航" data-dialog-autofocus onClick={() => setMobileNavOpen(false)}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
            </button>
          ) : null}
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true">XR</div>
            <div>
              <strong>Routing Panel</strong>
              <p className="brand-kicker">控制中心</p>
            </div>
          </div>
          <WorkspaceNav items={WORKSPACES} activeKey={activeWorkspace} onSelect={selectWorkspace} badges={badges} />
          <div className="sidebar-status-stack">
            <p className="sidebar-section-label">节点状态</p>
            <div className="sidebar-status-card">
              <div className="sidebar-status-card__head">
                <span>数据面</span>
                <StatusPill tone={dataPlaneTone} label={panel.dataPlaneRunningLabel()} />
              </div>
              <small>{panel.dataPlaneStatus?.management_target || "未配置管理目标"}</small>
            </div>
            <div className="sidebar-status-card">
              <div className="sidebar-status-card__head">
                <span>AI 节点</span>
                <StatusPill tone={aiTone} label={panel.aiNodeStatus?.reachable === true ? "可达" : panel.aiNodeStatus?.reachable === false ? "不可达" : "待确认"} />
              </div>
              <small>{panel.aiNodeStatus?.management_target || "未配置管理目标"}</small>
            </div>
          </div>
        </div>
        <div className="sidebar-footer">
          <span>控制面</span>
          <strong>{panel.meta?.panel_address || "—"}</strong>
          <small>{panel.meta?.timezone_label || "北京时间（UTC+08:00）"}</small>
        </div>
      </aside>

      {drawerOpen ? (
        <button className="mobile-scrim" type="button" aria-label="关闭导航遮罩" tabIndex={-1} onClick={() => setMobileNavOpen(false)} />
      ) : null}

      <div className="admin-main" inert={drawerOpen ? true : undefined} aria-hidden={drawerOpen ? true : undefined}>
        <header className="admin-topbar">
          <div className="topbar-title">
            {isMobile ? (
              <button
                className="icon-button mobile-menu-button"
                type="button"
                aria-label="打开控制台导航"
                aria-expanded={drawerOpen}
                aria-controls="admin-navigation"
                onClick={() => setMobileNavOpen(true)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
              </button>
            ) : null}
            <p className="breadcrumb">控制中心 <span>/</span> <strong>{meta.label}</strong></p>
          </div>
          <div className="topbar-actions">
            <div className="live-indicator" role="status">
              <span className={`live-dot${syncing ? " is-busy" : ""}`} aria-hidden="true" />
              <span>{syncing ? "同步中" : lastRefreshLabel}</span>
            </div>
            <button className="icon-button" type="button" aria-label={syncing ? "正在刷新" : "刷新数据"} disabled={syncing} onClick={refresh}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8.1 8.1 0 0 0-14.9-3M4 5v4h4M4 13a8.1 8.1 0 0 0 14.9 3M20 19v-4h-4" /></svg>
            </button>
          </div>
        </header>
        <main id="admin-content" className="admin-content" tabIndex={-1}>
          <Notice message={panel.flash.message} level={panel.flash.level} onClose={panel.clearFlash} />
          <Loader show={panel.loading} />
          <ErrorBoundary>
            <ActiveWorkspace key={activeWorkspace} onOpenWorkspace={selectWorkspace} />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
