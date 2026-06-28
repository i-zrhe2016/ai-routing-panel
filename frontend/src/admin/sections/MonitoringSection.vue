<script>
// Monitoring: embeds Grafana panels (d-solo iframes) from the monitoring stack
// so admins see host resources + per-port traffic time-series inside the panel,
// without a separate Grafana login. Data is sourced from Prometheus via Grafana;
// the panel's own SQLite-backed views (overview/ports/commerce) are unaffected.
//
// The Grafana base URL comes from the backend (meta.grafana_url, set from the
// GRAFANA_PUBLIC_URL env var). When unset, a configuration hint is shown instead.
export default {
  name: "MonitoringSection",
  inject: ["panel"],
  data() {
    return {
      range: "6h",
      ranges: [
        { key: "1h", label: "1 小时" },
        { key: "6h", label: "6 小时" },
        { key: "24h", label: "24 小时" },
      ],
      // Panel ids must match monitoring/grafana/dashboards/xray-observability.json.
      panels: [
        { id: 1, title: "CPU 使用率", wide: false },
        { id: 2, title: "内存使用率", wide: false },
        { id: 3, title: "网络流量 (eth)", wide: false },
        { id: 4, title: "根分区磁盘使用率", wide: false },
        { id: 5, title: "负载 (1 分钟)", wide: false },
        { id: 6, title: "Swap 使用率", wide: false },
        { id: 7, title: "每端口流量速率", wide: true },
        { id: 8, title: "每端口连接速率", wide: false },
      ],
    };
  },
  computed: {
    grafanaUrl() {
      return (this.panel.meta && this.panel.meta.grafana_url) || "";
    },
    dashboardUid() {
      return (this.panel.meta && this.panel.meta.grafana_observability_uid) || "xray-observability";
    },
    configured() {
      return Boolean(this.grafanaUrl);
    },
    dashboardLink() {
      if (!this.configured) return "";
      return `${this.grafanaUrl}/d/${this.dashboardUid}/${this.dashboardUid}?from=now-${this.range}&to=now`;
    },
  },
  methods: {
    panelSrc(id) {
      const base = this.grafanaUrl;
      const uid = this.dashboardUid;
      const params = new URLSearchParams({
        orgId: "1",
        panelId: String(id),
        theme: "light",
        from: `now-${this.range}`,
        to: "now",
        refresh: "30s",
      });
      return `${base}/d-solo/${uid}/${uid}?${params.toString()}`;
    },
    setRange(key) {
      this.range = key;
    },
  },
};
</script>

<template>
  <div class="a-section">
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">MONITORING</p>
        <h3>监控（Prometheus · Grafana）</h3>
        <p>主机系统资源与每端口流量来自 Prometheus，由 Grafana 出图内嵌于此。配置/订单等数据仍由面板自身（SQLite）提供。</p>
      </div>

      <template v-if="configured">
        <div class="m-toolbar">
          <div class="m-ranges">
            <button
              v-for="r in ranges"
              :key="r.key"
              type="button"
              class="a-btn"
              :class="r.key === range ? 'primary' : 'ghost'"
              @click="setRange(r.key)"
            >
              {{ r.label }}
            </button>
          </div>
          <a class="a-btn ghost" :href="dashboardLink" target="_blank" rel="noopener">在 Grafana 打开 ↗</a>
        </div>

        <div class="m-grid">
          <div
            v-for="p in panels"
            :key="p.id"
            class="m-panel"
            :class="{ wide: p.wide }"
          >
            <iframe
              :key="`${p.id}-${range}`"
              :src="panelSrc(p.id)"
              :title="p.title"
              frameborder="0"
              loading="lazy"
            ></iframe>
          </div>
        </div>
      </template>

      <div v-else class="m-empty">
        <strong>监控未配置</strong>
        <p>
          设置环境变量 <code>GRAFANA_PUBLIC_URL</code>（浏览器可达的 Grafana 地址，如
          <code>http://your-host:3000</code>），并启动 <code>monitoring/</code> 监控栈后，此处将内嵌主机资源与流量图表。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.m-ranges {
  display: flex;
  gap: var(--space-2);
}
.m-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}
.m-panel {
  background: var(--c-bg);
  border: 1px solid var(--c-border, rgba(0, 0, 0, 0.08));
  border-radius: 12px;
  overflow: hidden;
}
.m-panel.wide {
  grid-column: 1 / -1;
}
.m-panel iframe {
  display: block;
  width: 100%;
  height: 260px;
  border: 0;
}
.m-empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--c-text-muted);
}
.m-empty code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 6px;
  border-radius: 6px;
}
@media (max-width: 768px) {
  .m-grid {
    grid-template-columns: 1fr;
  }
  .m-panel.wide {
    grid-column: auto;
  }
}
</style>
