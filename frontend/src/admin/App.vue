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
import CommerceSection from "./sections/CommerceSection.vue";
import InfraSection from "./sections/InfraSection.vue";
import MonitoringSection from "./sections/MonitoringSection.vue";
import OverviewSection from "./sections/OverviewSection.vue";
import PortsSection from "./sections/PortsSection.vue";

export default {
  name: "AdminApp",
  components: {
    CommerceSection,
    InfraSection,
    MonitoringSection,
    NConfigProvider,
    NSpin,
    OverviewSection,
    PortsSection,
    StatusPill,
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
      resourceTab: "ports",
      infraTab: "infrastructure",
      isMobile: false,
      mobileNavOpen: false,
      loading: true,
      dashboardPollTimer: null,
      dashboardRefreshBusy: false,
      authEnabled: Boolean(typeof window !== "undefined" && window.__BOOT__ && window.__BOOT__.auth_enabled),
      workspaceOptions: [
        { key: "overview", label: "运行总览", description: "路由健康与待处理" },
        { key: "resources", label: "资源与租户", description: "端口、套餐与订单" },
        { key: "infra", label: "基础设施", description: "数据面、DNS 与监控" },
      ],
      resourceTabs: [
        { key: "ports", label: "端口与租户", description: "监听入口和订阅交付" },
        { key: "commerce", label: "套餐与订单", description: "售卖、审核与设置" },
      ],
      infraTabs: [
        { key: "infrastructure", label: "数据面与 DNS", description: "路由、探测与故障切换" },
        { key: "monitoring", label: "监控", description: "Prometheus 与 Grafana" },
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
        overview: "先确认当前路径和异常，再进入具体操作。",
        resources: "管理监听入口、租户交付和商业化服务。",
        infra: "检查数据面、DNS、AI 路由与观测信号。",
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
    selectResourceTab(key) {
      this.resourceTab = key;
    },
    selectInfraTab(key) {
      this.infraTab = key;
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
              <p class="brand-kicker">XRAY ROUTING</p>
              <strong>控制台</strong>
            </div>
          </div>
          <p class="brand-description">网络流量、AI 路由和租户交付的统一操作面。</p>

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
          <span>面板地址</span>
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
              <p class="breadcrumb">CONTROL PLANE <span>/</span> {{ activeWorkspaceMeta.label.toUpperCase() }}</p>
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

          <div v-if="activeWorkspace === 'resources'" class="workspace-tabs" role="tablist" aria-label="资源与租户视图">
            <button v-for="tab in resourceTabs" :key="tab.key" class="workspace-tab" :class="{ 'is-active': resourceTab === tab.key }" type="button" role="tab" :aria-selected="resourceTab === tab.key" @click="selectResourceTab(tab.key)">
              <strong>{{ tab.label }}</strong><small>{{ tab.description }}</small>
            </button>
          </div>
          <div v-if="activeWorkspace === 'infra'" class="workspace-tabs" role="tablist" aria-label="基础设施视图">
            <button v-for="tab in infraTabs" :key="tab.key" class="workspace-tab" :class="{ 'is-active': infraTab === tab.key }" type="button" role="tab" :aria-selected="infraTab === tab.key" @click="selectInfraTab(tab.key)">
              <strong>{{ tab.label }}</strong><small>{{ tab.description }}</small>
            </button>
          </div>

          <n-spin :show="loading">
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'overview' }" :aria-hidden="activeWorkspace !== 'overview'">
              <overview-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'resources' || resourceTab !== 'ports' }" :aria-hidden="activeWorkspace !== 'resources' || resourceTab !== 'ports'">
              <ports-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'resources' || resourceTab !== 'commerce' }" :aria-hidden="activeWorkspace !== 'resources' || resourceTab !== 'commerce'">
              <commerce-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'infra' || infraTab !== 'infrastructure' }" :aria-hidden="activeWorkspace !== 'infra' || infraTab !== 'infrastructure'">
              <infra-section />
            </section>
            <section class="workspace-view" :class="{ 'is-hidden': activeWorkspace !== 'infra' || infraTab !== 'monitoring' }" :aria-hidden="activeWorkspace !== 'infra' || infraTab !== 'monitoring'">
              <monitoring-section />
            </section>
          </n-spin>
        </main>
      </div>
    </div>
  </n-config-provider>
</template>
