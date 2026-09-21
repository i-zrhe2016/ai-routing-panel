<script>
import StatusPill from "../../shared/ui/StatusPill.vue";

export default {
  name: "TrafficSection",
  components: { StatusPill },
  inject: ["panel"],
  computed: {
    receivedBytes() {
      return Number(this.panel.summary.total_bytes_received || 0);
    },
    sentBytes() {
      return Number(this.panel.summary.total_bytes_sent || 0);
    },
    connectionCount() {
      return Number(this.panel.summary.total_connections || 0);
    },
    topPorts() {
      return [...(this.panel.ports || [])]
        .sort((a, b) => this.totalForPort(b) - this.totalForPort(a))
        .slice(0, 8);
    },
  },
  methods: {
    totalForPort(port) {
      return Number(port.total_bytes_received || 0) + Number(port.total_bytes_sent || 0);
    },
    toneForPort(port) {
      return port.status === "active" ? "success" : port.status === "disabled" ? "danger" : "warning";
    },
  },
};
</script>

<template>
  <div class="workspace-section traffic-workspace">
    <section class="cc-page-intro">
      <div>
        <p class="section-kicker">TRAFFIC</p>
        <h2>流量与连接</h2>
        <p>先用控制面已有累计计数观察入口负载；历史趋势继续由 Prometheus / Grafana 承担，避免浏览器直接解析 /metrics。</p>
      </div>
      <span class="cc-status-line">Dashboard snapshot</span>
    </section>

    <section class="cc-metric-grid">
      <article class="cc-metric">
        <span class="cc-metric__label">TOTAL TRAFFIC</span>
        <strong>{{ panel.humanBytes(panel.totalTrafficBytes) }}</strong>
        <small>累计接收 + 发送</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">RECEIVED</span>
        <strong>{{ panel.humanBytes(receivedBytes) }}</strong>
        <small>累计入站流量</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">SENT</span>
        <strong>{{ panel.humanBytes(sentBytes) }}</strong>
        <small>累计出站流量</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">CONNECTIONS</span>
        <strong>{{ connectionCount }}</strong>
        <small>{{ panel.summary.active_ports || 0 }} 个活跃端口</small>
      </article>
    </section>

    <section class="cc-split">
      <article class="cc-card">
        <div class="cc-card__head">
          <div>
            <p class="section-kicker">TOP PORTS</p>
            <h3>端口流量</h3>
            <p>按累计流量排序，快速找到主要负载入口。</p>
          </div>
        </div>
        <div v-if="topPorts.length" class="cc-table-wrap">
          <table class="cc-table">
            <thead><tr><th>端口</th><th>租户</th><th>状态</th><th>连接</th><th>累计流量</th></tr></thead>
            <tbody>
              <tr v-for="port in topPorts" :key="port.id">
                <td><strong>{{ port.listen_port }}</strong></td>
                <td>{{ port.note || "未命名" }}</td>
                <td><status-pill :tone="toneForPort(port)" :label="port.status_label || port.status" /></td>
                <td>{{ port.total_connections || 0 }}</td>
                <td>{{ panel.humanBytes(totalForPort(port)) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="cc-empty">暂无端口流量数据。</div>
      </article>

      <article class="cc-card">
        <div class="cc-card__head">
          <div>
            <p class="section-kicker">NETWORK STATE</p>
            <h3>当前节点</h3>
            <p>当前 snapshot 的 reachability 与 runtime 状态。</p>
          </div>
        </div>
        <div class="cc-node-list">
          <div class="cc-node-row">
            <div>
              <strong>Normal Data Plane</strong>
              <small>{{ panel.dataPlaneStatus.management_target || "未配置管理目标" }}</small>
            </div>
            <status-pill :tone="panel.dataPlaneStatus.xray_running ? 'success' : 'danger'" :label="panel.dataPlaneRunningLabel(panel.dataPlaneStatus)" />
          </div>
          <div class="cc-node-row">
            <div>
              <strong>AI Data Plane</strong>
              <small>{{ panel.aiNodeStatus.management_target || "AI 节点未纳管" }}</small>
            </div>
            <status-pill :tone="panel.aiNodeStatus.reachable ? 'success' : 'warning'" :label="panel.aiNodeStatusLabel()" />
          </div>
          <div class="cc-node-row">
            <div>
              <strong>Routing Path</strong>
              <small>{{ panel.trafficRouting.path || "unknown" }}</small>
            </div>
            <span>{{ panel.trafficRouting.label || "等待同步" }}</span>
          </div>
        </div>
      </article>
    </section>
  </div>
</template>
