<script>
import {
  NConfigProvider,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NSpin,
} from "naive-ui";

import { naiveThemeOverrides } from "../shared/tokens.js";
import StatusPill from "../shared/ui/StatusPill.vue";
import { AiDomainsMixin } from "../mixins/domains.js";
import { CommerceMixin } from "../mixins/commerce.js";
import { CoreMixin } from "../mixins/core.js";
import { DnsMixin } from "../mixins/dns.js";
import { PortsMixin } from "../mixins/ports.js";
import CommerceSection from "./sections/CommerceSection.vue";
import InfraSection from "./sections/InfraSection.vue";
import OverviewSection from "./sections/OverviewSection.vue";
import PortsSection from "./sections/PortsSection.vue";

// Root admin component. Reuses the per-domain Options-API mixins verbatim (Vue
// merges them, so this.x resolves across all), provides itself as `panel` to the
// section children, and renders a Naive UI layout shell. All /api/* calls and
// the data/computed/methods are unchanged from the legacy in-DOM-template app.
export default {
  name: "AdminApp",
  components: {
    NConfigProvider,
    NLayout,
    NLayoutContent,
    NLayoutHeader,
    NLayoutSider,
    NMenu,
    NSpin,
    StatusPill,
    OverviewSection,
    PortsSection,
    CommerceSection,
    InfraSection,
  },
  mixins: [CoreMixin, PortsMixin, CommerceMixin, DnsMixin, AiDomainsMixin],
  provide() {
    return { panel: this };
  },
  data() {
    return {
      themeOverrides: naiveThemeOverrides(),
      activeSection: "overview",
      loading: true,
      authEnabled: Boolean(typeof window !== "undefined" && window.__BOOT__ && window.__BOOT__.auth_enabled),
      menuOptions: [
        { label: "总览", key: "overview" },
        { label: "端口管理", key: "ports" },
        { label: "套餐与订单", key: "commerce" },
        { label: "数据面与 DNS", key: "infra" },
      ],
    };
  },
  computed: {
    dataPlaneTone() {
      return this.dataPlaneStatus && this.dataPlaneStatus.xray_running ? "success" : "danger";
    },
  },
  async mounted() {
    const boot = (typeof window !== "undefined" && window.__BOOT__) || {};
    this.meta = { csrf_token: boot.csrf_token || "" };
    try {
      const data = await this.requestJson("/api/dashboard");
      this.applyDashboard(data.dashboard || {});
    } catch (error) {
      this.setFlash(error.message || "加载失败。", "error");
    } finally {
      this.loading = false;
    }
  },
  methods: {
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
    <n-layout has-sider class="admin-root">
      <n-layout-sider bordered :width="248" content-class="admin-sider">
        <div class="brand">
          <p class="eyebrow">XRAY ROUTING PANEL</p>
          <h2>控制台</h2>
          <p class="brand-sub">端口、租户与数据面统一后台</p>
        </div>
        <n-menu v-model:value="activeSection" :options="menuOptions" />
        <div class="sider-cards">
          <div class="sider-card">
            <span>数据面状态</span>
            <strong>{{ dataPlaneRunningLabel(dataPlaneStatus) }}</strong>
            <small>{{ dataPlaneStatus.management_target || "当前未配置数据面" }}</small>
          </div>
          <div class="sider-card">
            <span>面板地址</span>
            <strong>{{ meta.panel_address || "—" }}</strong>
            <small>{{ (meta.timezone_label || "") + " · Xray Direct" }}</small>
          </div>
        </div>
      </n-layout-sider>

      <n-layout>
        <n-layout-header bordered class="topbar">
          <div class="topbar-copy">
            <p class="eyebrow">ADMIN WORKSPACE</p>
            <h1>控制台总览</h1>
          </div>
          <div class="topbar-actions">
            <status-pill :tone="dataPlaneTone" :label="dataPlaneRunningLabel(dataPlaneStatus)" />
            <span class="topbar-badge">端口 · {{ summary.total_ports || 0 }}</span>
            <button v-if="authEnabled" class="a-btn ghost" type="button" @click="logout">退出登录</button>
          </div>
        </n-layout-header>

        <n-layout-content class="content">
          <div v-if="flash.message" class="banner" :class="flash.level || 'info'">
            <span>{{ flash.message }}</span>
            <button class="a-btn link" type="button" @click="clearFlash">关闭</button>
          </div>

          <n-spin :show="loading">
            <overview-section v-show="activeSection === 'overview'" />
            <ports-section v-show="activeSection === 'ports'" />
            <commerce-section v-show="activeSection === 'commerce'" />
            <infra-section v-show="activeSection === 'infra'" />
          </n-spin>
        </n-layout-content>
      </n-layout>
    </n-layout>
  </n-config-provider>
</template>

<style>
/* App-wide (unscoped) helpers shared by the section components. */
body {
  margin: 0;
  font-family: var(--font-sans);
  color: var(--c-text);
  background: var(--c-bg);
}
.admin-root {
  min-height: 100vh;
}
.admin-sider {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  padding: var(--space-6) var(--space-4);
}
.brand h2 {
  margin: 4px 0;
  font-size: 20px;
}
.brand-sub {
  margin: 0;
  font-size: 12px;
  color: var(--c-text-muted);
}
.eyebrow {
  margin: 0;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--c-primary);
}
.sider-cards {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: auto;
}
.sider-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3);
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
}
.sider-card span {
  font-size: 12px;
  color: var(--c-text-muted);
}
.sider-card strong {
  font-size: 15px;
}
.sider-card small {
  font-size: 11px;
  color: var(--c-text-soft);
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-6);
  background: var(--c-surface);
}
.topbar-copy h1 {
  margin: 2px 0 0;
  font-size: 22px;
}
.topbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.topbar-badge {
  font-size: 12px;
  color: var(--c-text-muted);
  padding: 4px 10px;
  border: 1px solid var(--c-line);
  border-radius: 999px;
}
.content {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--r-md);
  border: 1px solid var(--c-line);
  background: var(--c-surface);
}
.banner.success {
  border-color: var(--c-success);
  background: var(--c-success-soft);
}
.banner.error {
  border-color: var(--c-danger);
  background: var(--c-danger-soft);
}
.banner.info {
  border-color: var(--c-primary);
  background: var(--c-primary-soft);
}

/* Section / card / form helpers */
.a-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.a-card {
  background: var(--c-surface);
  border: 1px solid var(--c-line);
  border-radius: var(--r-lg);
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.a-card-head h3 {
  margin: 2px 0;
  font-size: 18px;
}
.a-card-head p {
  margin: 0;
  font-size: 13px;
  color: var(--c-text-muted);
}
.a-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-3);
}
.a-tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-4);
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
}
.a-tile span {
  font-size: 12px;
  color: var(--c-text-muted);
}
.a-tile strong {
  font-size: 22px;
}
.a-tile small {
  font-size: 11px;
  color: var(--c-text-soft);
}
.a-tile strong.ok {
  color: var(--c-success);
}
.a-tile strong.bad {
  color: var(--c-danger);
}
.a-tile strong.warn {
  color: var(--c-warning);
}
.a-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
}
.a-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.a-field.full {
  grid-column: 1 / -1;
}
.a-field span {
  font-size: 12px;
  color: var(--c-text-muted);
}
.a-input {
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface);
  color: var(--c-text);
  font-size: 14px;
}
.a-input:focus {
  outline: none;
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px rgba(26, 115, 232, 0.12);
}
.a-check {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}
.a-actions {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.a-btn {
  height: 38px;
  padding: 0 16px;
  border-radius: var(--r-md);
  border: 1px solid transparent;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.a-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.a-btn.primary {
  background: var(--c-primary);
  color: #fff;
}
.a-btn.secondary {
  background: var(--c-primary-soft);
  color: var(--c-primary-strong);
  border-color: var(--c-primary-soft);
}
.a-btn.danger {
  background: var(--c-danger-soft);
  color: var(--c-danger);
  border-color: var(--c-danger-soft);
}
.a-btn.ghost {
  background: transparent;
  border-color: var(--c-line);
  color: var(--c-text-muted);
}
.a-btn.link {
  background: transparent;
  border: none;
  color: var(--c-primary);
  padding: 0 4px;
}
.a-empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--c-text-muted);
  border: 1px dashed var(--c-line);
  border-radius: var(--r-md);
}
</style>
