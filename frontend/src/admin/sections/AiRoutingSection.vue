<script>
import StatusPill from "../../shared/ui/StatusPill.vue";
import AiRouteControl from "../components/AiRouteControl.vue";
import FailoverTopology from "../components/FailoverTopology.vue";

export default {
  name: "AiRoutingSection",
  components: { AiRouteControl, FailoverTopology, StatusPill },
  inject: ["panel"],
  computed: {
    routing() {
      return this.panel.aiRoutingStatus || {};
    },
    candidates() {
      return Array.isArray(this.routing.ai_candidates) ? this.routing.ai_candidates : [];
    },
    selectedCandidate() {
      return this.candidates.find((candidate) => candidate.selected) || null;
    },
    selectedLabel() {
      if (this.routing.manual_mode === "forced_fallback") return "数据面直出";
      return this.selectedCandidate?.label || "等待探测";
    },
    modeLabel() {
      return this.routing.manual_mode_label || {
        auto: "自动探测",
        primary: "人工固定主 AI",
        backup: "人工固定备用 AI",
        forced_fallback: "应急直出",
      }[this.routing.manual_mode] || "自动探测";
    },
    healthyCandidateCount() {
      return this.candidates.filter((candidate) => candidate.is_reachable === true).length;
    },
    routeTone() {
      const tone = this.routing.status_tone;
      return tone === "ok" ? "success" : tone === "bad" ? "danger" : "warning";
    },
  },
};
</script>

<template>
  <div class="workspace-section routing-workspace">
    <section class="cc-page-intro">
      <div>
        <p class="section-kicker">AI ROUTING</p>
        <h2>路由决策与故障切换</h2>
        <p>把当前出口、候选节点、人工策略与数据面拓扑放在一个操作面，优先解释“现在为什么走这条路”。</p>
      </div>
      <status-pill :tone="routeTone" :label="panel.aiRoutingLabel(routing)" />
    </section>

    <section class="cc-metric-grid">
      <article class="cc-metric">
        <span class="cc-metric__label">ACTIVE ROUTE</span>
        <strong>{{ selectedLabel }}</strong>
        <small>{{ modeLabel }}</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">CANDIDATES</span>
        <strong>{{ healthyCandidateCount }} / {{ candidates.length }}</strong>
        <small>当前探测可达</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">ROUTE STATUS</span>
        <strong>{{ panel.aiRoutingLabel(routing) }}</strong>
        <small>{{ routing.route_status || routing.status || "等待状态同步" }}</small>
      </article>
      <article class="cc-metric">
        <span class="cc-metric__label">LAST REPORT</span>
        <strong>{{ routing.report_generated_at_display || "—" }}</strong>
        <small>{{ routing.sync_error || "控制面最近一次路由报告" }}</small>
      </article>
    </section>

    <section class="panel-block route-control-block">
      <ai-route-control />
    </section>

    <section class="panel-block">
      <failover-topology />
    </section>

    <section class="cc-card">
      <div class="cc-card__head">
        <div>
          <p class="section-kicker">UPSTREAM HEALTH</p>
          <h3>候选节点</h3>
          <p>只展示控制面实际返回的 candidate 状态，不推断应用层模型或 token 指标。</p>
        </div>
      </div>
      <div v-if="candidates.length" class="cc-table-wrap">
        <table class="cc-table">
          <thead>
            <tr><th>节点</th><th>地址</th><th>健康</th><th>选中</th><th>探测</th></tr>
          </thead>
          <tbody>
            <tr v-for="candidate in candidates" :key="candidate.index">
              <td><strong>{{ candidate.label || (candidate.index === 0 ? "主 AI 节点" : "备用 AI 节点") }}</strong></td>
              <td>{{ candidate.candidate_label || ((candidate.upstream_host || "—") + ":" + (candidate.upstream_port || "—")) }}</td>
              <td>{{ candidate.is_reachable === true ? "可达" : candidate.is_reachable === false ? "不可达" : "待探测" }}</td>
              <td>{{ candidate.selected ? "当前出口" : "待命" }}</td>
              <td>{{ candidate.probe_method || routing.probe_method || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="cc-empty">暂无 AI 候选节点。</div>
    </section>
  </div>
</template>
