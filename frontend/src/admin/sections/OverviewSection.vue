<script>
import StatusPill from "../../shared/ui/StatusPill.vue";
import AiRouteControl from "../components/AiRouteControl.vue";
import FailoverTopology from "../components/FailoverTopology.vue";

// Read-only KPI overview. Injects the root `panel` for all state/helpers.
export default {
  name: "OverviewSection",
  components: { AiRouteControl, StatusPill, FailoverTopology },
  inject: ["panel"],
};
</script>

<template>
  <div class="a-section">
    <ai-route-control />

    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">OVERVIEW</p>
        <h3>把常用运维信息放在第一屏</h3>
        <p>先确认数据面与租户交付是否稳定，再进入端口列表处理编辑、配额、到期与订阅操作。</p>
      </div>
      <div class="a-tiles">
        <div class="a-tile">
          <span>活跃端口</span>
          <strong>{{ panel.summary.active_ports || 0 }}</strong>
          <small>当前可被客户端访问</small>
        </div>
        <div class="a-tile">
          <span>待处理项</span>
          <strong>{{ panel.attentionPortCount }}</strong>
          <small>过期、停用或达到上限的端口</small>
        </div>
        <div class="a-tile">
          <span>租户数量</span>
          <strong>{{ panel.subscription.tenant_count || 0 }}</strong>
          <small>每个端口单独对应一个租户入口</small>
        </div>
        <div class="a-tile">
          <span>累计总流量</span>
          <strong>{{ panel.humanBytes(panel.totalTrafficBytes) }}</strong>
          <small>入站和出站合计</small>
        </div>
        <div class="a-tile">
          <span>待审订单</span>
          <strong>{{ panel.commerceSummary.pending_review_count || 0 }}</strong>
          <small>付款截图待人工审核</small>
        </div>
      </div>
    </div>

    <failover-topology />

    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">STATUS</p>
        <h3>节点状态</h3>
      </div>
      <div class="a-tiles">
        <div class="a-tile">
          <span>数据面状态</span>
          <strong :class="panel.dataPlaneStatus.xray_running ? 'ok' : 'bad'">
            {{ panel.dataPlaneRunningLabel(panel.dataPlaneStatus) }}
          </strong>
          <small>{{ panel.dataPlaneStatus.management_target || "数据面未配置" }}</small>
        </div>
        <div class="a-tile">
          <span>AI 节点状态</span>
          <strong :class="panel.aiNodeStatus ? (panel.aiNodeStatus.reachable ? 'ok' : 'bad') : 'warn'">
            {{ panel.aiNodeStatusLabel() }}
          </strong>
          <small>{{ (panel.aiNodeStatus && panel.aiNodeStatus.management_target) || "AI 节点未纳管" }}</small>
        </div>
        <div class="a-tile">
          <span>AI 路由状态</span>
          <strong :class="(panel.aiRoutingStatus.status_tone === 'ok' ? 'ok' : panel.aiRoutingStatus.status_tone === 'bad' ? 'bad' : 'warn')">
            {{ panel.aiRoutingLabel(panel.aiRoutingStatus) }}
          </strong>
          <small>{{ panel.aiRoutingStatus.sync_error || ("最近报告：" + (panel.aiRoutingStatus.report_generated_at_display || "暂无")) }}</small>
        </div>
        <div class="a-tile">
          <span>DNS 切换</span>
          <strong :class="(panel.dnsFailoverTone(panel.dnsFailoverStatus) === 'ok' ? 'ok' : panel.dnsFailoverTone(panel.dnsFailoverStatus) === 'bad' ? 'bad' : 'warn')">
            {{ panel.dnsFailoverSummary(panel.dnsFailoverStatus) }}
          </strong>
          <small>{{ panel.dnsFailoverStatus.record_content || panel.dnsFailoverStatus.config_error || "等待首次同步" }}</small>
        </div>
        <div class="a-tile">
          <span>流量导向</span>
          <strong class="warn">{{ panel.trafficRouting ? panel.trafficRouting.label : "—" }}</strong>
          <small>{{ panel.trafficRouting ? panel.trafficRouting.scenario : "" }}</small>
        </div>
        <div class="a-tile">
          <span>已停用端口</span>
          <strong>{{ panel.summary.disabled_ports || 0 }}</strong>
        </div>
        <div class="a-tile">
          <span>已过期端口</span>
          <strong>{{ panel.summary.expired_ports || 0 }}</strong>
        </div>
        <div class="a-tile">
          <span>已达上限端口</span>
          <strong>{{ panel.summary.quota_ports || 0 }}</strong>
        </div>
        <div class="a-tile">
          <span>累计连接</span>
          <strong>{{ panel.summary.total_connections || 0 }}</strong>
        </div>
      </div>
    </div>
  </div>
</template>
