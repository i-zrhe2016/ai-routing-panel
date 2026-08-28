<script>
import StatusPill from "../../shared/ui/StatusPill.vue";
import KpiCard from "../components/KpiCard.vue";
import AiRouteControl from "../components/AiRouteControl.vue";
import FailoverTopology from "../components/FailoverTopology.vue";

export default {
  name: "OverviewSection",
  components: { AiRouteControl, FailoverTopology, KpiCard, StatusPill },
  inject: ["panel"],
  computed: {
    routeTone() {
      const tone = this.panel.aiRoutingStatus?.status_tone;
      return tone === "ok" ? "success" : tone === "bad" ? "danger" : "warning";
    },
    dnsTone() {
      const tone = this.panel.dnsFailoverTone(this.panel.dnsFailoverStatus);
      return tone === "ok" ? "success" : tone === "bad" ? "danger" : "warning";
    },
  },
};
</script>

<template>
  <div class="workspace-section overview-workspace">
    <section class="command-hero">
      <div>
        <p class="section-kicker">OPERATIONS SNAPSHOT</p>
        <h2>今天的路径，是否值得信任？</h2>
        <p>把入口、AI 出口和故障切换放在同一个视野里，先处理影响流量的异常，再处理配置细节。</p>
      </div>
      <div class="command-hero__status">
        <status-pill :tone="routeTone" :label="panel.aiRoutingLabel(panel.aiRoutingStatus)" />
        <span>{{ panel.trafficRouting?.scenario || "等待路由状态同步" }}</span>
      </div>
    </section>

    <section class="panel-block">
      <div class="section-heading">
        <div>
          <p class="section-kicker">AT A GLANCE</p>
          <h3>关键运营指标</h3>
        </div>
        <span class="section-caption">仅展示当前控制面快照</span>
      </div>
      <div class="kpi-grid">
        <kpi-card label="活跃端口" :value="panel.summary.active_ports || 0" note="当前可被客户端访问" tone="success" accent />
        <kpi-card label="待处理端口" :value="panel.attentionPortCount" note="过期、停用或达到上限" tone="warning" />
        <kpi-card label="租户数量" :value="panel.subscription.tenant_count || 0" note="每个端口对应一个租户入口" tone="info" />
        <kpi-card label="累计总流量" :value="panel.humanBytes(panel.totalTrafficBytes)" note="入站和出站合计" tone="neutral" />
        <kpi-card label="待审订单" :value="panel.commerceSummary.pending_review_count || 0" note="付款截图待人工审核" tone="warning" />
      </div>
    </section>

    <section class="panel-block route-control-block">
      <div class="section-heading">
        <div>
          <p class="section-kicker">AI ROUTING</p>
          <h3>AI 出口控制</h3>
        </div>
        <span class="section-caption">自动探测优先，人工操作必须显式恢复</span>
      </div>
      <ai-route-control />
    </section>

    <section class="panel-block">
      <failover-topology />
    </section>

    <section class="panel-block">
      <div class="section-heading">
        <div>
          <p class="section-kicker">SYSTEM STATUS</p>
          <h3>节点与路由状态</h3>
        </div>
        <span class="section-caption">颜色只做辅助，文字是状态事实</span>
      </div>
      <div class="status-grid">
        <article class="status-row">
          <span class="status-row__marker is-success" aria-hidden="true"></span>
          <div><strong>数据面</strong><small>{{ panel.dataPlaneStatus.management_target || "数据面未配置" }}</small></div>
          <status-pill :tone="panel.dataPlaneStatus.xray_running ? 'success' : 'danger'" :label="panel.dataPlaneRunningLabel(panel.dataPlaneStatus)" />
        </article>
        <article class="status-row">
          <span class="status-row__marker" :class="panel.aiNodeStatus?.reachable ? 'is-success' : 'is-warning'" aria-hidden="true"></span>
          <div><strong>AI 节点</strong><small>{{ panel.aiNodeStatus?.management_target || "AI 节点未纳管" }}</small></div>
          <status-pill :tone="panel.aiNodeStatus?.reachable ? 'success' : 'warning'" :label="panel.aiNodeStatusLabel()" />
        </article>
        <article class="status-row">
          <span class="status-row__marker" :class="`is-${routeTone}`" aria-hidden="true"></span>
          <div><strong>AI 路由</strong><small>{{ panel.aiRoutingStatus.sync_error || ("最近报告：" + (panel.aiRoutingStatus.report_generated_at_display || "暂无")) }}</small></div>
          <status-pill :tone="routeTone" :label="panel.aiRoutingLabel(panel.aiRoutingStatus)" />
        </article>
        <article class="status-row">
          <span class="status-row__marker" :class="`is-${dnsTone}`" aria-hidden="true"></span>
          <div><strong>DNS 切换</strong><small>{{ panel.dnsFailoverStatus.record_content || panel.dnsFailoverStatus.config_error || "等待首次同步" }}</small></div>
          <status-pill :tone="dnsTone" :label="panel.dnsFailoverSummary(panel.dnsFailoverStatus)" />
        </article>
      </div>
    </section>
  </div>
</template>
