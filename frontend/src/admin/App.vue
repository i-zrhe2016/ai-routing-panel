<script>
import { NConfigProvider } from "naive-ui/es/config-provider";
import { NSpin } from "naive-ui/es/spin";

import { AiDomainsMixin } from "../mixins/domains.js";
import { CommerceMixin } from "../mixins/commerce.js";
import { CoreMixin } from "../mixins/core.js";
import { DnsMixin } from "../mixins/dns.js";
import { PortsMixin } from "../mixins/ports.js";
import StatusPill from "../shared/ui/StatusPill.vue";
import { naiveThemeOverrides } from "../shared/tokens.js";
import WorkspaceNav from "./components/WorkspaceNav.vue";
import AiRoutingSection from "./sections/AiRoutingSection.vue";
import CommerceSection from "./sections/CommerceSection.vue";
import InfraSection from "./sections/InfraSection.vue";
import MonitoringSection from "./sections/MonitoringSection.vue";
import OverviewSection from "./sections/OverviewSection.vue";
import PortsSection from "./sections/PortsSection.vue";
import TrafficSection from "./sections/TrafficSection.vue";

export default {
  name: "AdminApp",
  components: {
    AiRoutingSection,
    CommerceSection,
    InfraSection,
    MonitoringSection,
    NConfigProvider,
    NSpin,
    OverviewSection,
    PortsSection,
    StatusPill,
    TrafficSection,
    WorkspaceNav,
  },
  mixins: [CoreMixin, PortsMixin, CommerceMixin, DnsMixin, AiDomainsMixin],
  provide() {
    return { panel: this };
  },
  data() {
    return {
      themeOverrides: naiveThemeOverrides(),
      activeWorkspace: "overview",
      isMobile: false,
      mobileNavOpen: false,
      loading: true,
      dashboardPollTimer: null,
      dashboardRefreshBusy: false,
      authEnabled: Boolean(typeof window !== "undefined" && window.__BOOT__ && window.__BOOT__.auth_enabled),
      workspaceOptions: [
        { key: "overview", label: "Overview", description: "系统健康与待处理" },
        { key: "routing", label: "AI Routing", description: "出口、探测与切换" },
        { key: "traffic", label: "Traffic", description: "流量、端口与连接" },
        { key: "resources", label: "Resources", description: "端口与租户交付" },
        { key: "commerce", label: "Orders & Plans", description: "订单、套餐与审核" },
        { key: "infra", label: "Infrastructure", description: "节点、DNS 与运行时" },
        { key: "observe", label: "Observability", description: "Prometheus 与 Grafana" },
      ],
    };
  },
  computed: {
    dataPlaneTone() {
      return this.dataPlaneStatus && this.dataPlaneStatus.xray_running ? "success" : "danger";
    },
    activeWorkspaceMeta() {
      return this.workspaceOptions.find((item) => item.key === this.activeWorkspace) || this.workspaceOptions[0];
    },
    workspaceTitle() {
      return this.activeWorkspaceMeta.label;
    },
    workspaceDescription() {
      return {
        overview: "先确认系统健康、当前路径和待处理，再进入具体操作。",
        routing: "解释当前 AI 出口、候选健康、人工策略和故障切换。",
        traffic: "观察累计流量、连接和主要端口负载。",
        resources: "管理监听入口、租户交付和订阅凭据。",
        commerce: "管理套餐、订单审核与服务开通。",
        infra: "检查数据面、AI 节点、DNS 与运行时状态。",
        observe: "从指标和 Grafana 深入排查资源与流量异常。",
      }[this.activeWorkspace] || "";
    },
    lastRefreshLabel() {
      return this.meta?.dashboard_updated_at_display || this.meta?.updated_at_display || "自动刷新 15 秒";
    },
  },
  async mounted() {
    this.updateIsMobile();
    if (typeof window !== "undefined") {
      window.addEventListener("resize", this.updateIsMobile);
    }
    const boot = (typeof window !== "undefined" && window.__BOOT__) || {};
    this.meta = { csrf_token: boot.csrf_token || "" };
    try {
      const data = await this.requestJson("/api/dashboard");
      this.applyDashboard(data.dashboard || {});
      if (import.meta.env.MODE !== "test") {
        this.dashboardPollTimer = window.setInterval(this.refreshDashboard, 15000);
      }
    } catch (error) {
      this.setFlash(error.message || "加载失败。", "error");
    } finally {
      this.loading = false;
    }
  },
  beforeUnmount() {
    if (this.dashboardPollTimer) {
      window.clearInterval(this.dashboardPollTimer);
      this.dashboardPollTimer = null;
    }
    if (this.topologyTransitionTimer) {
      window.clearTimeout(this.topologyTransitionTimer);
      this.topologyTransitionTimer = null;
    }
    if (typeof window !== "undefined") {
      window.removeEventListener("resize", this.updateIsMobile);
    }
  },
  methods: {
    updateIsMobile() {
      const mobile = typeof window !== "undefined" && window.innerWidth <= 840;
      this.isMobile = mobile;
      if (!mobile) this.mobileNavOpen = false;
    },
    selectWorkspace(key) {
      this.activeWorkspace = key;
      this.mobileNavOpen = false;
    },
    toggleMobileNav() {
      this.mobileNavOpen = !this.mobileNavOpen;
    },
    async refreshNow() {
      await this.refreshDashboard();
    },
    async refreshDashboard() {
      if (this.dashboardRefreshBusy || this.loading) return;
      this.dashboardRefreshBusy = true;
      try {
        const data = await this.requestJson("/api/dashboard");
        this.applyDashboard(data.dashboard || {});
      } catch (_error) {
        // Keep the last known state visible when a background refresh fails.
      } finally {
        this.dashboardRefreshBusy = false;
      }
    },
    logout() {
      const form = document.createElement("form");
      form.method = "post";
      form.action = "/logout";
      document.body.appendChild(form);
      form.submit();
    },
  },
};
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <div class="admin-shell">
      <aside class="admin-sidebar" :class="{ 'is-open': mobileNavOpen }" aria-label="控制台导航">
        <div class="sidebar-scroll">
          <div class="brand-lockup">
            <div class="brand-mark" aria-hidden="true">XR</div>
            <div>
              <p class="brand-kicker">ROUTING PANEL</p>
              <strong>Control Center</strong>
            </div>
          </div>
          <p class="brand-description">AI 路由、网络流量、租户与运营的一体化控制面。</p>

          <workspace-nav :items="workspaceOptions" :active-key="activeWorkspace" @select="selectWorkspace" />

          <div class="sidebar-status-stack" aria-label="基础设施状态">
            <div class="sidebar-status-card">
              <div class="sidebar-status-card__head"><span>DATA PLANE</span><i :class="dataPlaneStatus.xray_running ? 'is-ok' : 'is-bad'"></i></div>
              <strong>{{ dataPlaneRunningLabel(dataPlaneStatus) }}</strong>
              <small>{{ dataPlaneStatus.management_target || "当前未配置数据面" }}</small>
            </div>
            <div class="sidebar-status-card">
              <div class="sidebar-status-card__head"><span>AI NODE</span><i :class="aiNodeStatus && aiNodeStatus.reachable ? 'is-ok' : 'is-warn'"></i></div>
              <strong>{{ aiNodeStatusLabel() }}</strong>
              <small>{{ (aiNodeStatus && aiNodeStatus.management_target) || "AI 节点未纳管" }}</small>
            </div>
          </div>
        </div>
        <div class="sidebar-footer">
          <span>CONTROL PLANE</span>
          <strong>{{ meta.panel_address || "—" }}</strong>
          <small>{{ meta.timezone_label || "服务器本地时区" }}</small>
        </div>
      </aside>

      <button v-if="isMobile && mobileNavOpen" class="mobile-scrim" type="button" aria-label="关闭导航" @click="mobileNavOpen = false"></button>

      <div class="admin-main">
        <header class="admin-topbar">
          <div class="topbar-leading">
            <button v-if="isMobile" class="icon-button mobile-menu-button" type="button" aria-label="打开控制台导航" @click="toggleMobileNav">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
            </button>
            <div>
              <p class="breadcrumb">CONTROL CENTER <span>/</span> {{ workspaceTitle.toUpperCase() }}</p>
              <h1>{{ workspaceTitle }}</h1>
              <p>{{ workspaceDescription }}</p>
            </div>
          </div>
          <div class="topbar-actions">
            <div class="refresh-meta">
              <span class="live-dot" :class="{ 'is-busy': dashboardRefreshBusy }"></span>
              <span>{{ dashboardRefreshBusy ? "同步中" : lastRefreshLabel }}</span>
            </div>
            <status-pill :tone="dataPlaneTone" :label="dataPlaneRunningLabel(dataPlaneStatus)" />
            <button class="icon-button" type="button" :aria-label="dashboardRefreshBusy ? '正在刷新' : '刷新数据'" :disabled="dashboardRefreshBusy" @click="refreshNow">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8.1 8.1 0 0 0-14.9-3M4 5v4h4M4 13a8.1 8.1 0 0 0 14.9 3M20 19v-4h-4" /></svg>
            </button>
            <button v-if="authEnabled" class="topbar-logout" type="button" @click="logout">退出登录</button>
          </div>
        </header>

        <main class="admin-content">
          <div v-if="flash.message" class="admin-notice" :class="`is-${flash.level || 'info'}`" role="status" aria-live="polite">
            <span class="admin-notice__mark" aria-hidden="true">{{ flash.level === "error" ? "!" : "i" }}</span>
            <span>{{ flash.message }}</span>
            <button class="notice-close" type="button" aria-label="关闭提示" @click="clearFlash">关闭</button>
          </div>

          <n-spin :show="loading">
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'overview' }" :aria-hidden="activeWorkspace !== 'overview'">
              <overview-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'routing' }" :aria-hidden="activeWorkspace !== 'routing'">
              <ai-routing-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'traffic' }" :aria-hidden="activeWorkspace !== 'traffic'">
              <traffic-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'resources' }" :aria-hidden="activeWorkspace !== 'resources'">
              <ports-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'commerce' }" :aria-hidden="activeWorkspace !== 'commerce'">
              <commerce-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'infra' }" :aria-hidden="activeWorkspace !== 'infra'">
              <infra-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'observe' }" :aria-hidden="activeWorkspace !== 'observe'">
              <monitoring-section />
            </section>
          </n-spin>
        </main>
      </div>
    </div>
  </n-config-provider>
</template>
