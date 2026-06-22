<script>
// Infrastructure: DNS failover status + manual check/switch, data-plane restart,
// AI routing summary, and the tenant-access overview.
export default {
  name: "InfraSection",
  inject: ["panel"],
  computed: {
    dns() {
      return this.panel.dnsFailoverStatus || {};
    },
    peak() {
      return this.dns.peak_window || {};
    },
    peakStatusLabel() {
      if (!this.peak.enabled) return "未启用";
      if (this.peak.config_error) return "配置错误";
      return this.peak.active ? "高峰中 · 优先备用" : "非高峰 · 优先主";
    },
    peakTone() {
      if (!this.peak.enabled) return "warn";
      if (this.peak.config_error) return "bad";
      return this.peak.active ? "ok" : "warn";
    },
    peakDetail() {
      if (!this.peak.enabled) return "未配置高峰窗口切换";
      if (this.peak.config_error) return this.peak.config_error;
      const prefer = this.peak.preferred_target_label || "-";
      const window = `${this.peak.start || "--:--"} - ${this.peak.end || "--:--"}`;
      const tz = this.peak.timezone_label || "服务器本地时区";
      const now = this.peak.current_time ? ` · 现在 ${this.peak.current_time.slice(11, 16)}` : "";
      return `当前优先 ${prefer} · 窗口 ${window} ${tz}${now}`;
    },
    peakNextTime() {
      if (!this.peak.enabled || this.peak.config_error || !this.peak.next_transition_at) return "";
      return this.peak.next_transition_at.slice(11, 16);
    },
    peakCountdown() {
      const total = Number(this.peak.seconds_to_next_transition || 0);
      if (!total) return "";
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      if (hours > 0) return `约 ${hours} 小时 ${minutes} 分后`;
      if (minutes > 0) return `约 ${minutes} 分后`;
      return "即将切换";
    },
    peakNextDetail() {
      if (!this.peakCountdown) return "";
      return `${this.peakCountdown} · 切到 ${this.peak.next_preferred_target_label || "-"}`;
    },
  },
  methods: {
    toneClass(tone) {
      return tone === "ok" ? "ok" : tone === "bad" ? "bad" : "warn";
    },
  },
};
</script>

<template>
  <div class="a-section">
    <!-- DNS failover -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">DNS FAILOVER</p>
        <h3>DNS 故障切换</h3>
        <p>仅数据面公网 TCP 探测参与自动切换，AI 节点状态不参与任何 DNS 切换决策。</p>
      </div>
      <div class="a-tiles">
        <div class="a-tile">
          <span>功能状态</span>
          <strong :class="toneClass(panel.dnsFailoverTone(dns))">{{ panel.dnsFailoverSummary(dns) }}</strong>
          <small>{{ dns.enabled ? (dns.config_error || dns.fast_propagation_note) : "未启用自动切换" }}</small>
        </div>
        <div class="a-tile">
          <span>当前 DNS 指向</span>
          <strong>{{ dns.current_target_label || "未知" }}</strong>
          <small>{{ dns.record_name ? ((dns.record_type || "") + " " + dns.record_name) : "未配置记录" }}</small>
        </div>
        <div class="a-tile">
          <span>记录值</span>
          <strong>{{ dns.record_content || "暂无" }}</strong>
          <small>TTL {{ dns.record_ttl || "-" }} 秒 · {{ dns.record_proxied ? "Cloudflare 代理" : "仅 DNS" }}</small>
        </div>
        <div class="a-tile">
          <span>最近探测</span>
          <strong :class="toneClass(panel.dnsFailoverTone(dns))">{{ dns.last_probe_status_label || "未检测" }}</strong>
          <small>{{ dns.last_probe_checked_at_display || "暂无" }}</small>
        </div>
        <div class="a-tile" :class="{ 'peak-active': peak.active }">
          <span>高峰专用节点</span>
          <strong :class="toneClass(peakTone)">{{ peakStatusLabel }}</strong>
          <small>{{ peakDetail }}</small>
        </div>
        <div v-if="peak.enabled && !peak.config_error" class="a-tile">
          <span>下次自动切换</span>
          <strong>{{ peakNextTime || "—" }}</strong>
          <small>{{ peakNextDetail || "暂无" }}</small>
        </div>
        <div class="a-tile">
          <span>探测目标</span>
          <strong>{{ (dns.probe_host || "-") + ":" + (dns.probe_port || "-") }}</strong>
        </div>
        <div class="a-tile">
          <span>连续失败 / 成功</span>
          <strong>{{ (dns.consecutive_failures || 0) + " / " + (dns.consecutive_successes || 0) }}</strong>
        </div>
      </div>
      <div class="a-actions">
        <button class="a-btn secondary" type="button" :disabled="panel.isBusy('dns-failover-check') || !dns.enabled || !dns.configured" @click="panel.runDnsFailoverCheck">
          {{ panel.isBusy("dns-failover-check") ? "检测中..." : "立即检测" }}
        </button>
        <button class="a-btn secondary" type="button" :disabled="panel.isBusy('dns-failover-switch:primary') || !dns.enabled || !dns.configured" @click="panel.switchDnsTarget('primary')">
          {{ panel.isBusy("dns-failover-switch:primary") ? "切换中..." : "切到主" }}
        </button>
        <button class="a-btn secondary" type="button" :disabled="panel.isBusy('dns-failover-switch:backup') || !dns.enabled || !dns.configured" @click="panel.switchDnsTarget('backup')">
          {{ panel.isBusy("dns-failover-switch:backup") ? "切换中..." : "切到备" }}
        </button>
      </div>
    </div>

    <!-- Data plane + AI -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">DATA PLANE</p>
        <h3>数据面与 AI 路由</h3>
        <p>控制面只管理一个数据面，AI 路由作为该数据面的附属能力统一呈现。</p>
      </div>
      <div class="a-tiles">
        <div class="a-tile">
          <span>{{ panel.dataPlaneStatus.label || "数据面" }}</span>
          <strong :class="panel.dataPlaneStatus.xray_running ? 'ok' : 'bad'">{{ panel.dataPlaneRunningLabel(panel.dataPlaneStatus) }}</strong>
          <small>{{ panel.dataPlaneStatus.management_target || "数据面未配置" }}</small>
        </div>
        <div class="a-tile">
          <span>AI 路由</span>
          <strong :class="toneClass(panel.aiRoutingStatus.status_tone)">{{ panel.aiRoutingLabel(panel.aiRoutingStatus) }}</strong>
          <small>{{ panel.aiRoutingStatus.sync_error || ("最近报告：" + (panel.aiRoutingStatus.report_generated_at_display || "暂无")) }}</small>
        </div>
      </div>
      <div class="a-actions">
        <button
          v-if="panel.dataPlaneStatus.configured && panel.dataPlaneStatus.supports_restart"
          class="a-btn secondary"
          type="button"
          :disabled="panel.isBusy('restart-data-plane')"
          @click="panel.restartDataPlane"
        >
          {{ panel.isBusy("restart-data-plane") ? "重启中..." : "重启数据面" }}
        </button>
        <a v-if="panel.meta.ai_domain_dashboard_url" class="a-btn ghost" :href="panel.meta.ai_domain_dashboard_url">打开 AI 域名页</a>
        <a v-if="panel.meta.probe_enabled && panel.meta.probe_dashboard_url" class="a-btn ghost" :href="panel.meta.probe_dashboard_url">打开探针页</a>
      </div>
    </div>

    <!-- Tenant access overview -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">TENANT ACCESS</p>
        <h3>租户面板与订阅</h3>
        <p>每个有效端口独立生成登录地址、随机凭据和订阅地址；过期端口会自动清理。</p>
      </div>
      <div v-if="panel.subscription.available" class="a-tiles">
        <div class="a-tile"><span>租户数量</span><strong>{{ panel.subscription.tenant_count }}</strong></div>
        <div class="a-tile"><span>客户端目标</span><strong>{{ panel.subscription.server }}</strong></div>
        <div class="a-tile"><span>登录路径示例</span><strong>{{ panel.subscription.tenant_panel_path_example }}</strong></div>
        <div class="a-tile"><span>订阅路径示例</span><strong>{{ panel.subscription.tenant_subscription_path_example }}</strong></div>
      </div>
      <div v-else class="a-empty">
        订阅功能未就绪：{{ panel.subscription.error }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.a-tile.peak-active {
  box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.5);
}
</style>
