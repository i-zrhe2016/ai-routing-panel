<script>
export default {
  name: "FailoverTopology",
  inject: ["panel"],
  computed: {
    dns() {
      return this.panel.dnsFailoverStatus || {};
    },
    ai() {
      return this.panel.aiNodeStatus || {};
    },
    aiRouting() {
      return this.panel.aiRoutingStatus || {};
    },
    aiCandidates() {
      return Array.isArray(this.aiRouting.ai_candidates) ? this.aiRouting.ai_candidates : [];
    },
    selectedAiCandidate() {
      return this.aiCandidates.find((candidate) => candidate && candidate.selected === true) || null;
    },
    selectedAiHasProbe() {
      return Boolean(
        this.selectedAiCandidate
        && (this.selectedAiCandidate.is_reachable === true || this.selectedAiCandidate.is_reachable === false),
      );
    },
    selectedAiReachable() {
      return this.selectedAiHasProbe
        ? this.selectedAiCandidate.is_reachable === true
        : Boolean(this.ai.reachable);
    },
    selectedAiLabel() {
      return this.selectedAiCandidate?.label || this.selectedAiCandidate?.candidate_label || "AI 节点";
    },
    route() {
      return this.panel.trafficRouting || {};
    },
    backup() {
      return this.panel.meta?.backup_xray_mode || "disabled";
    },
    dataPlane() {
      return this.panel.dataPlaneStatus || {};
    },
    backupActive() {
      return this.dns.current_target === "backup";
    },
    aiActive() {
      return ["normal_ai", "dns_backup_relay_ai"].includes(this.route.path) && this.selectedAiReachable;
    },
    aiStatus() {
      if (this.selectedAiHasProbe) return this.selectedAiReachable ? "运行中" : "不可达";
      return this.ai.reachable ? "运行中" : (this.ai.configured ? "不可达" : "未纳管");
    },
    aiTone() {
      if (this.aiActive) return "ok";
      if (this.aiRouting.manual_mode === "forced_fallback") return "warn";
      if (this.selectedAiHasProbe && !this.selectedAiReachable) return "bad";
      return this.ai.configured ? "bad" : "warn";
    },
    aiNote() {
      if (this.aiRouting.manual_mode === "forced_fallback") return "人工回退到数据面直出";
      const mode = this.aiRouting.manual_mode_label || "自动探测";
      return `${this.selectedAiLabel} · ${mode}`;
    },
    controlPlaneStatus() {
      return this.backupActive ? "备用 Xray 接管中" : "管理面在线";
    },
    controlPlaneTone() {
      return this.backupActive ? "info" : "ok";
    },
    routeSummary() {
      return this.route.label || "状态未知";
    },
    routeDetail() {
      return this.route.scenario || "节点状态待确认";
    },
    nodes() {
      return [
        {
          key: "control_plane",
          label: "控制面",
          subtitle: "管理与编排",
          status: this.controlPlaneStatus,
          tone: this.controlPlaneTone,
          active: true,
          note: this.backupActive ? `控制面备用 Xray · ${this.backupLabel}` : "不在正常代理路径中",
          action: "none",
        },
        {
          key: "data_plane",
          label: "普通数据面",
          subtitle: "主流量入口",
          status: this.panel.dataPlaneRunningLabel(this.dataPlane),
          tone: this.dataPlane.xray_running ? "ok" : "bad",
          active: !this.backupActive && this.route.path !== "unknown",
          note: this.dataPlane.management_target || "未配置",
          action: "dns",
        },
        {
          key: "ai_node",
          label: "AI 节点",
          subtitle: "AI 流量出口",
          status: this.aiStatus,
          tone: this.aiTone,
          active: this.aiActive,
          note: this.aiNote,
          action: "none",
        },
      ];
    },
    backupLabel() {
      return this.dns.backup_label || "控制面备用 Xray";
    },
    pathNodes() {
      const names = [this.route.entry_node, ...(this.route.transit_nodes || []), this.route.exit_node];
      return names.filter(Boolean).join(" → ") || "路径待确认";
    },
  },
  methods: {
    nodeClass(node) {
      return [
        `topology-node-${node.key}`,
        node.active ? "is-active" : "is-muted",
        `tone-${node.tone}`,
      ];
    },
    async switchDns(target) {
      await this.panel.switchDnsTarget(target);
    },
  },
};
</script>

<template>
  <section class="topology-panel" :class="{ 'is-transitioning': panel.topologyTransitioning }" aria-label="三节点故障切换拓扑">
    <div class="topology-head">
      <div>
        <p class="eyebrow">FAILOVER TOPOLOGY</p>
        <h3>三节点流量切换拓扑</h3>
        <p>控制面负责决策，数据面承载主入口；路径变化时只播放一次真实切换动画。</p>
      </div>
      <div class="topology-current">
        <span>当前路径</span>
        <strong>{{ routeSummary }}</strong>
        <small>{{ routeDetail }}</small>
      </div>
    </div>

    <div class="topology-route" :key="panel.topologyTransitionKey">
      <div v-for="(node, index) in nodes" :key="node.key" class="topology-step">
        <article class="topology-node" :class="nodeClass(node)">
          <div class="topology-node-mark" aria-hidden="true">
            <span></span>
          </div>
          <div class="topology-node-copy">
            <span class="topology-node-label">{{ node.label }}</span>
            <strong>{{ node.status }}</strong>
            <small>{{ node.subtitle }} · {{ node.note }}</small>
          </div>
          <div v-if="node.action === 'dns'" class="topology-actions">
            <button
              class="a-btn compact"
              type="button"
              :disabled="!dns.enabled || !dns.configured || panel.isBusy('dns-failover-switch:backup') || backupActive"
              @click="switchDns('backup')"
            >
              {{ panel.isBusy("dns-failover-switch:backup") ? "切换中" : "切到备用" }}
            </button>
            <button
              class="a-btn compact ghost"
              type="button"
              :disabled="!dns.enabled || !dns.configured || panel.isBusy('dns-failover-switch:primary') || !backupActive"
              @click="switchDns('primary')"
            >
              {{ panel.isBusy("dns-failover-switch:primary") ? "切换中" : "回到主面" }}
            </button>
          </div>
        </article>
        <div v-if="index < nodes.length - 1" class="topology-link" :class="{ 'is-active': node.active && nodes[index + 1].active }" aria-hidden="true">
          <span></span>
        </div>
      </div>
    </div>

    <div class="topology-footer">
      <span class="topology-path">{{ pathNodes }}</span>
      <span class="topology-legend"><i class="legend-dot ok"></i>运行中 <i class="legend-dot info"></i>接管中 <i class="legend-dot bad"></i>故障</span>
    </div>
  </section>
</template>

<style scoped>
.topology-panel {
  padding: 24px;
  border: 1px solid var(--c-line);
  border-radius: var(--r-lg);
  background:
    radial-gradient(circle at 100% 0, rgba(26, 115, 232, 0.1), transparent 35%),
    var(--c-surface);
  overflow: hidden;
}

.topology-head,
.topology-footer {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.topology-head h3 {
  margin: 4px 0 6px;
}

.topology-head p:not(.eyebrow) {
  margin: 0;
  color: var(--c-text-muted);
  font-size: 13px;
}

.topology-current {
  min-width: 190px;
  padding: 12px 14px;
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: rgba(255, 255, 255, 0.7);
}

.topology-current span,
.topology-current small {
  display: block;
  color: var(--c-text-muted);
  font-size: 12px;
}

.topology-current strong {
  display: block;
  margin: 3px 0;
  font-size: 15px;
}

.topology-route {
  display: grid;
  grid-template-columns: 1fr 72px 1fr 72px 1fr;
  align-items: center;
  margin: 24px 0 18px;
}

.topology-step { display: contents; }

.topology-node {
  position: relative;
  min-height: 154px;
  padding: 18px;
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.topology-node.is-active {
  border-color: rgba(26, 115, 232, 0.45);
  box-shadow: 0 8px 24px rgba(26, 115, 232, 0.12);
}

.topology-node.is-muted { opacity: 0.58; }

.topology-node-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-bottom: 12px;
  border-radius: 50%;
  background: var(--c-gray-200);
}

.topology-node-mark span {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--c-gray-500);
}

.tone-ok .topology-node-mark span { background: var(--c-success); }
.tone-info .topology-node-mark span { background: var(--c-primary); }
.tone-bad .topology-node-mark span { background: var(--c-danger); }

.topology-node-label {
  display: block;
  color: var(--c-text-muted);
  font-size: 12px;
}

.topology-node-copy strong {
  display: block;
  margin: 2px 0 5px;
  font-size: 16px;
}

.topology-node-copy small {
  display: block;
  min-height: 34px;
  color: var(--c-text-muted);
  font-size: 12px;
  line-height: 1.45;
}

.topology-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.topology-actions .a-btn.compact {
  height: 30px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  font-size: 12px;
}

.topology-link {
  position: relative;
  height: 2px;
  background: var(--c-line-strong);
}

.topology-link.is-active { background: rgba(26, 115, 232, 0.35); }

.topology-link span {
  position: absolute;
  top: -3px;
  left: -8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-primary);
  opacity: 0;
}

.is-transitioning .topology-link.is-active span {
  animation: topology-flow 1.2s ease-out both;
}

.topology-footer {
  align-items: center;
  padding-top: 14px;
  border-top: 1px solid var(--c-line);
}

.topology-path {
  color: var(--c-text-muted);
  font-size: 12px;
}

.topology-legend {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--c-text-muted);
  font-size: 12px;
  white-space: nowrap;
}

.legend-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 8px;
  border-radius: 50%;
}

.legend-dot.ok { background: var(--c-success); }
.legend-dot.info { background: var(--c-primary); }
.legend-dot.bad { background: var(--c-danger); }

@keyframes topology-flow {
  0% { left: -8px; opacity: 0; }
  20% { opacity: 1; }
  100% { left: calc(100% + 8px); opacity: 0; }
}

@media (max-width: 900px) {
  .topology-route { grid-template-columns: 1fr; gap: 10px; }
  .topology-link { width: 2px; height: 24px; margin: 0 auto; }
  .topology-link span { top: -8px; left: -3px; }
  @keyframes topology-flow {
    0% { top: -8px; opacity: 0; }
    20% { opacity: 1; }
    100% { top: calc(100% + 8px); opacity: 0; }
  }
}

@media (max-width: 640px) {
  .topology-panel { padding: 18px; }
  .topology-head,
  .topology-footer { display: block; }
  .topology-current { min-width: 0; margin-top: 14px; }
  .topology-legend { margin-top: 10px; }
}

@media (prefers-reduced-motion: reduce) {
  .topology-node { transition: none; }
  .is-transitioning .topology-link.is-active span { animation: none; }
}
</style>
