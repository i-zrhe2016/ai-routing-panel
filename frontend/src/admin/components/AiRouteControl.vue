<script>
// The operator-facing AI route switcher. It deliberately keeps the action
// semantics close to the node cards so a manual change is always made with
// the target, health signal, and recovery path in view.
export default {
  name: "AiRouteControl",
  inject: ["panel"],
  props: {
    compact: {
      type: Boolean,
      default: false,
    },
  },
  data() {
    return {
      pendingMode: "",
      pendingCandidate: null,
    };
  },
  computed: {
    aiRouting() {
      return this.panel.aiRoutingStatus || {};
    },
    candidates() {
      return Array.isArray(this.aiRouting.ai_candidates)
        ? this.aiRouting.ai_candidates.slice(0, 2)
        : [];
    },
    manualMode() {
      return this.aiRouting.manual_mode || "auto";
    },
    modeLabel() {
      return {
        auto: "自动探测",
        primary: "人工固定主 AI",
        backup: "人工固定备用 AI",
        forced_fallback: "应急直出",
      }[this.manualMode] || "状态待确认";
    },
    selectedCandidate() {
      if (this.manualMode === "forced_fallback") return null;
      return this.candidates.find((candidate) => candidate.selected) || null;
    },
    routeTarget() {
      if (this.manualMode === "forced_fallback") {
        return {
          label: "普通数据面",
          detail: "AI 流量暂时回到 freedom 直出",
        };
      }
      if (this.selectedCandidate) {
        if (this.selectedCandidate.is_reachable === false) {
          return {
            label: "AI 节点不可达",
            detail: `${this.selectedCandidate.label || "人工目标"} · ${this.addressFor(this.selectedCandidate)}`,
          };
        }
        return {
          label: this.selectedCandidate.label || "AI 节点",
          detail: this.addressFor(this.selectedCandidate),
        };
      }
      return {
        label: "AI 节点待确认",
        detail: "等待下一轮探测结果",
      };
    },
    lastActionLabel() {
      return this.aiRouting.manual_updated_at_display || "暂无人工操作";
    },
    confirmationTitle() {
      if (this.pendingMode === "forced_fallback") return "启用应急直出？";
      if (this.pendingMode === "auto") return "恢复自动探测？";
      return `确认${this.pendingCandidate?.label || "AI 节点"}？`;
    },
    confirmationBody() {
      if (this.pendingMode === "forced_fallback") {
        return "动态 AI 路由会被移除，AI 域名回到普通数据面的 freedom 直出。恢复 AI 节点路由需要手动恢复自动探测。";
      }
      if (this.pendingMode === "auto") {
        return "控制面将恢复按主、备候选的可达性自动选择，不再固定当前人工目标。";
      }
      if (!this.pendingCandidate) return "即将修改 AI 流量的出口策略。";
      const reachability = this.statusLabel(this.pendingCandidate);
      return `${this.addressFor(this.pendingCandidate)} · ${reachability}。此操作会暂停自动探测，直到你手动恢复自动探测。`;
    },
    confirmationTone() {
      return this.pendingMode === "forced_fallback" ? "danger" : "primary";
    },
    titleId() {
      return this.compact ? "ai-route-control-title-detail" : "ai-route-control-title-overview";
    },
  },
  methods: {
    addressFor(candidate) {
      if (!candidate) return "地址待确认";
      return candidate.candidate_label || `${candidate.upstream_host || "地址待确认"}:${candidate.upstream_port || "—"}`;
    },
    statusLabel(candidate) {
      if (!candidate || candidate.is_reachable === null || candidate.is_reachable === undefined) return "待探测";
      return candidate.is_reachable ? "可达" : "不可达";
    },
    statusTone(candidate) {
      if (!candidate || candidate.is_reachable === null || candidate.is_reachable === undefined) return "warn";
      return candidate.is_reachable ? "ok" : "bad";
    },
    roleLabel(candidate) {
      return candidate.index === 0 ? "主 AI 节点" : "备用 AI 节点";
    },
    roleNote(candidate) {
      return candidate.index === 0 ? "默认优先出口" : "主节点故障时的接管出口";
    },
    modeForCandidate(candidate) {
      return candidate.index === 0 ? "primary" : "backup";
    },
    isCurrent(candidate) {
      return this.manualMode !== "forced_fallback" && Boolean(candidate.selected);
    },
    isUnreachableCurrent(candidate) {
      return this.isCurrent(candidate) && candidate.is_reachable === false;
    },
    actionLabel(candidate) {
      if (this.isCurrent(candidate)) return "当前使用";
      return candidate.index === 0 ? "固定主 AI" : "切换到备用 AI";
    },
    openConfirmation(mode, candidate = null) {
      if (this.panel.isBusy(`switch-ai-${mode}`)) return;
      this.pendingMode = mode;
      this.pendingCandidate = candidate;
    },
    closeConfirmation() {
      this.pendingMode = "";
      this.pendingCandidate = null;
    },
    async confirmSwitch() {
      const mode = this.pendingMode;
      if (!mode) return;
      this.closeConfirmation();
      await this.panel.switchAiRoutingMode(mode, { skipConfirm: true });
    },
  },
};
</script>

<template>
  <section
    class="ai-route-control"
    :class="{ 'ai-route-control--compact': compact }"
    :data-testid="compact ? 'ai-route-control-detail' : 'ai-route-control-overview'"
    :aria-labelledby="titleId"
  >
    <div class="ai-route-control__head">
      <div>
        <p class="eyebrow">AI ROUTE CONTROL</p>
        <h2 :id="titleId">AI 主备节点</h2>
        <p class="ai-route-control__intro">把当前出口、节点健康和人工切换放在同一个操作面上。</p>
      </div>
      <div class="ai-route-control__mode" :class="`is-${manualMode}`">
        <span>当前策略</span>
        <strong>{{ modeLabel }}</strong>
      </div>
    </div>

    <div class="ai-route-control__route" aria-label="当前 AI 流量路径">
      <div class="ai-route-control__route-node">
        <span>入口</span>
        <strong>普通数据面</strong>
        <small>AI 域名动态识别</small>
      </div>
      <div class="ai-route-control__route-line" :class="{ 'is-direct': manualMode === 'forced_fallback' }" aria-hidden="true">
        <span></span>
      </div>
      <div class="ai-route-control__route-node is-target">
        <span>当前出口</span>
        <strong>{{ routeTarget.label }}</strong>
        <small>{{ routeTarget.detail }}</small>
      </div>
    </div>

    <div v-if="!candidates.length" class="ai-route-control__empty">
      <strong>暂无 AI 候选节点</strong>
      <span>请先配置 AI 上游，控制面才会显示主备和人工切换。</span>
    </div>

    <div v-else class="ai-route-control__candidates">
      <article
        v-for="candidate in candidates"
        :key="`ai-control-candidate-${candidate.index}`"
        class="ai-route-card"
        :class="[
          `is-${statusTone(candidate)}`,
          {
            'is-current': isCurrent(candidate),
            'is-unreachable-current': isUnreachableCurrent(candidate),
            'is-manual': manualMode === modeForCandidate(candidate),
          },
        ]"
      >
        <div class="ai-route-card__topline">
          <span class="ai-route-card__index">0{{ candidate.number || candidate.index + 1 }}</span>
          <span class="ai-route-card__role">{{ roleLabel(candidate) }}</span>
          <span class="ai-route-card__status" :class="`is-${statusTone(candidate)}`">
            <i aria-hidden="true"></i>{{ statusLabel(candidate) }}
          </span>
        </div>
        <div class="ai-route-card__body">
          <strong>{{ addressFor(candidate) }}</strong>
          <span>{{ roleNote(candidate) }}</span>
        </div>
        <div class="ai-route-card__footer">
          <span v-if="isUnreachableCurrent(candidate)" class="ai-route-card__current">人工目标不可达</span>
          <span v-else-if="isCurrent(candidate)" class="ai-route-card__current">当前承载 AI 流量</span>
          <span v-else-if="manualMode === modeForCandidate(candidate)" class="ai-route-card__current">人工目标不可达</span>
          <span v-else class="ai-route-card__available">可作为人工目标</span>
          <button
            class="a-btn ai-route-card__action"
            :class="{ ghost: !isCurrent(candidate), secondary: isCurrent(candidate) }"
            type="button"
            :data-testid="candidate.index === 0 ? 'ai-switch-primary' : 'ai-switch-backup'"
            :disabled="isCurrent(candidate) || panel.isBusy(`switch-ai-${modeForCandidate(candidate)}`)"
            @click="openConfirmation(modeForCandidate(candidate), candidate)"
          >
            {{ panel.isBusy(`switch-ai-${modeForCandidate(candidate)}`) ? "切换中..." : actionLabel(candidate) }}
          </button>
        </div>
      </article>
    </div>

    <div class="ai-route-control__footer">
      <div class="ai-route-control__policy">
        <span class="ai-route-control__policy-mark" aria-hidden="true">↗</span>
        <span>人工固定目标不会静默改选另一节点；需要恢复自动探测时请显式操作。</span>
      </div>
      <div class="ai-route-control__actions">
        <button
          v-if="manualMode !== 'auto'"
          class="a-btn secondary"
          type="button"
          data-testid="ai-restore-auto"
          :disabled="panel.isBusy('switch-ai-auto')"
          @click="openConfirmation('auto')"
        >
          {{ panel.isBusy("switch-ai-auto") ? "恢复中..." : "恢复自动探测" }}
        </button>
        <details class="ai-route-control__advanced">
          <summary>高级应急</summary>
          <div class="ai-route-control__advanced-panel">
            <p><strong>数据面直出</strong>：移除动态 AI 路由，让 AI 域名回普通数据面 freedom 直出。</p>
            <button
              class="a-btn danger"
              type="button"
              data-testid="ai-force-direct"
              :disabled="!aiRouting.configured || manualMode === 'forced_fallback' || panel.isBusy('switch-ai-forced_fallback')"
              @click="openConfirmation('forced_fallback')"
            >
              {{ panel.isBusy("switch-ai-forced_fallback") ? "处理中..." : (manualMode === "forced_fallback" ? "已启用数据面直出" : "强制直出") }}
            </button>
          </div>
        </details>
      </div>
    </div>
    <p class="ai-route-control__updated">最近人工操作：{{ lastActionLabel }}</p>

    <div v-if="pendingMode" class="ai-route-modal-backdrop" tabindex="-1" @click.self="closeConfirmation" @keydown.esc="closeConfirmation">
      <div class="ai-route-modal" role="dialog" aria-modal="true" aria-labelledby="ai-route-modal-title">
        <div class="ai-route-modal__mark" :class="`is-${confirmationTone}`" aria-hidden="true">
          {{ pendingMode === "forced_fallback" ? "!" : "↗" }}
        </div>
        <p class="eyebrow">CONFIRM ROUTE CHANGE</p>
        <h3 id="ai-route-modal-title">{{ confirmationTitle }}</h3>
        <p class="ai-route-modal__body">{{ confirmationBody }}</p>
        <div v-if="pendingCandidate" class="ai-route-modal__target">
          <span>目标节点</span>
          <strong>{{ pendingCandidate.label }} · {{ addressFor(pendingCandidate) }}</strong>
          <small :class="`is-${statusTone(pendingCandidate)}`">{{ statusLabel(pendingCandidate) }}</small>
        </div>
        <div class="ai-route-modal__actions">
          <button class="a-btn ghost" type="button" data-testid="ai-confirm-cancel" @click="closeConfirmation">取消</button>
          <button
            class="a-btn"
            :class="confirmationTone === 'danger' ? 'danger' : 'primary'"
            type="button"
            data-testid="ai-confirm-submit"
            :disabled="panel.isBusy(`switch-ai-${pendingMode}`)"
            @click="confirmSwitch"
          >
            {{ panel.isBusy(`switch-ai-${pendingMode}`) ? "处理中..." : "确认切换" }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ai-route-control {
  position: relative;
  display: grid;
  gap: 22px;
  overflow: hidden;
  padding: 28px;
  border: 1px solid #dbe4f0;
  border-radius: 22px;
  background:
    radial-gradient(circle at 92% -20%, rgba(64, 132, 255, 0.16), transparent 34%),
    linear-gradient(135deg, #ffffff 0%, #f7faff 58%, #f0f5fc 100%);
  box-shadow: 0 14px 36px rgba(37, 72, 117, 0.08);
}

.ai-route-control--compact {
  gap: 16px;
  padding: 22px;
  border-radius: 16px;
  box-shadow: none;
}

.ai-route-control__head,
.ai-route-control__footer,
.ai-route-card__topline,
.ai-route-card__footer,
.ai-route-modal__actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.ai-route-control__head h2 {
  margin: 4px 0 6px;
  color: #172a45;
  font-size: clamp(24px, 3vw, 34px);
  letter-spacing: -0.04em;
}

.ai-route-control--compact .ai-route-control__head h2 { font-size: 23px; }

.ai-route-control__intro {
  margin: 0;
  max-width: 48ch;
  color: #65758b;
  font-size: 14px;
  line-height: 1.6;
}

.ai-route-control__mode {
  min-width: 150px;
  padding: 12px 14px;
  border: 1px solid #cfe0f8;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.74);
}

.ai-route-control__mode span,
.ai-route-control__mode strong {
  display: block;
}

.ai-route-control__mode span {
  color: #71839a;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.ai-route-control__mode strong {
  margin-top: 4px;
  color: #1f5db7;
  font-size: 14px;
}

.ai-route-control__mode.is-forced_fallback {
  border-color: #f2c9a2;
  background: #fff8ef;
}

.ai-route-control__mode.is-forced_fallback strong { color: #a95508; }

.ai-route-control__route {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(70px, 0.6fr) minmax(0, 1fr);
  align-items: center;
  gap: 14px;
  padding: 14px;
  border: 1px solid rgba(185, 205, 230, 0.76);
  border-radius: 15px;
  background: rgba(255, 255, 255, 0.64);
}

.ai-route-control__route-node {
  min-width: 0;
  padding: 12px 14px;
  border-left: 3px solid #9cbce7;
  background: rgba(239, 246, 255, 0.66);
}

.ai-route-control__route-node.is-target { border-left-color: #2c76d7; }

.ai-route-control__route-node span,
.ai-route-control__route-node strong,
.ai-route-control__route-node small {
  display: block;
}

.ai-route-control__route-node span {
  color: #70829a;
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.ai-route-control__route-node strong {
  margin: 4px 0;
  overflow-wrap: anywhere;
  color: #203a5d;
  font-size: 16px;
}

.ai-route-control__route-node small {
  overflow-wrap: anywhere;
  color: #71839a;
  font-size: 12px;
}

.ai-route-control__route-line {
  position: relative;
  height: 2px;
  background: #8eb3e6;
}

.ai-route-control__route-line::before,
.ai-route-control__route-line::after {
  position: absolute;
  top: 50%;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #2c76d7;
  content: "";
  transform: translateY(-50%);
}

.ai-route-control__route-line::before { left: 0; }
.ai-route-control__route-line::after { right: 0; }

.ai-route-control__route-line span {
  position: absolute;
  top: -3px;
  left: 30%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1aa36f;
  box-shadow: 0 0 0 4px rgba(26, 163, 111, 0.12);
}

.ai-route-control__route-line.is-direct {
  background: #e7b77d;
}

.ai-route-control__route-line.is-direct::before,
.ai-route-control__route-line.is-direct::after,
.ai-route-control__route-line.is-direct span {
  background: #c76d17;
}

.ai-route-control__route-line.is-direct span { box-shadow: 0 0 0 4px rgba(199, 109, 23, 0.12); }

.ai-route-control__candidates {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.ai-route-card {
  display: grid;
  gap: 18px;
  min-width: 0;
  padding: 18px;
  border: 1px solid #dce5ef;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.88);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.ai-route-card.is-current {
  border-color: #63b998;
  box-shadow: 0 8px 24px rgba(39, 151, 105, 0.12);
}

.ai-route-card.is-manual:not(.is-current) { border-color: #e6a765; }

.ai-route-card:hover { transform: translateY(-1px); }

.ai-route-card__index {
  color: #9aaabd;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 700;
}

.ai-route-card__role {
  flex: 1;
  color: #304866;
  font-size: 13px;
  font-weight: 700;
}

.ai-route-card__status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #71839a;
  font-size: 12px;
}

.ai-route-card__status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9aaabd;
}

.ai-route-card__status.is-ok { color: #13845a; }
.ai-route-card__status.is-ok i { background: #1aa36f; }
.ai-route-card__status.is-bad { color: #bf4b3d; }
.ai-route-card__status.is-bad i { background: #d45a4c; }
.ai-route-card__status.is-warn { color: #a66a17; }
.ai-route-card__status.is-warn i { background: #d89532; }

.ai-route-card__body strong,
.ai-route-card__body span {
  display: block;
  overflow-wrap: anywhere;
}

.ai-route-card__body strong {
  color: #172a45;
  font-size: 19px;
  letter-spacing: -0.02em;
}

.ai-route-card__body span {
  margin-top: 6px;
  color: #73849a;
  font-size: 12px;
}

.ai-route-card__footer {
  align-items: center;
  padding-top: 14px;
  border-top: 1px solid #edf1f5;
}

.ai-route-card__current,
.ai-route-card__available {
  color: #16845b;
  font-size: 12px;
  font-weight: 600;
}

.ai-route-card__available { color: #71839a; font-weight: 500; }
.ai-route-card.is-manual:not(.is-current) .ai-route-card__current { color: #ae5d10; }

.ai-route-card__action {
  flex: 0 0 auto;
  min-height: 34px;
  padding: 0 12px;
  font-size: 12px;
}

.ai-route-control__footer {
  align-items: center;
  padding-top: 4px;
}

.ai-route-control__policy {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 54ch;
  color: #71839a;
  font-size: 12px;
  line-height: 1.5;
}

.ai-route-control__policy-mark {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  color: #286dc3;
  background: #e6f0ff;
  font-size: 14px;
  font-weight: 700;
}

.ai-route-control__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.ai-route-control__advanced { position: relative; }

.ai-route-control__advanced summary {
  min-height: 38px;
  padding: 9px 12px;
  border: 1px solid #dbe4ef;
  border-radius: 10px;
  color: #64768d;
  cursor: pointer;
  font-size: 12px;
  list-style: none;
}

.ai-route-control__advanced summary::-webkit-details-marker { display: none; }
.ai-route-control__advanced summary::before { margin-right: 6px; content: "+"; font-weight: 700; }
.ai-route-control__advanced[open] summary::before { content: "−"; }

.ai-route-control__advanced-panel {
  position: absolute;
  right: 0;
  z-index: 2;
  display: grid;
  gap: 10px;
  width: min(320px, calc(100vw - 48px));
  margin-top: 8px;
  padding: 14px;
  border: 1px solid #f0d2ae;
  border-radius: 12px;
  background: #fffaf4;
  box-shadow: 0 12px 28px rgba(136, 85, 31, 0.13);
}

.ai-route-control__advanced-panel p {
  margin: 0;
  color: #85643e;
  font-size: 12px;
  line-height: 1.55;
}

.ai-route-control__updated {
  margin: -10px 0 0;
  color: #93a0af;
  font-size: 11px;
  text-align: right;
}

.ai-route-control__empty {
  display: grid;
  gap: 5px;
  padding: 24px;
  border: 1px dashed #bfd0e5;
  border-radius: 14px;
  color: #71839a;
  background: rgba(246, 250, 255, 0.7);
  text-align: center;
}

.ai-route-control__empty strong { color: #304866; }

.ai-route-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(23, 42, 69, 0.38);
  backdrop-filter: blur(4px);
}

.ai-route-modal {
  width: min(450px, 100%);
  padding: 26px;
  border: 1px solid #d8e2ee;
  border-radius: 18px;
  background: #ffffff;
  box-shadow: 0 24px 70px rgba(22, 48, 82, 0.24);
}

.ai-route-modal__mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  margin-bottom: 14px;
  border-radius: 11px;
  color: #ffffff;
  background: #2c76d7;
  font-size: 20px;
  font-weight: 800;
}

.ai-route-modal__mark.is-danger { background: #c76d17; }
.ai-route-modal h3 { margin: 5px 0 10px; color: #172a45; font-size: 24px; }
.ai-route-modal__body { margin: 0; color: #65758b; font-size: 14px; line-height: 1.7; }

.ai-route-modal__target {
  display: grid;
  gap: 4px;
  margin: 18px 0;
  padding: 12px 14px;
  border-left: 3px solid #4d8cda;
  background: #f3f7fc;
}

.ai-route-modal__target span,
.ai-route-modal__target small { color: #71839a; font-size: 11px; }
.ai-route-modal__target strong { overflow-wrap: anywhere; color: #304866; font-size: 13px; }
.ai-route-modal__target small.is-ok { color: #13845a; }
.ai-route-modal__target small.is-bad { color: #bf4b3d; }
.ai-route-modal__target small.is-warn { color: #a66a17; }

.ai-route-modal__actions { justify-content: flex-end; margin-top: 22px; }

@media (max-width: 760px) {
  .ai-route-control { padding: 20px; }
  .ai-route-control__head,
  .ai-route-control__footer { display: grid; }
  .ai-route-control__mode { min-width: 0; }
  .ai-route-control__route { grid-template-columns: 1fr; }
  .ai-route-control__route-line { width: 2px; height: 24px; margin: 0 auto; }
  .ai-route-control__route-line::before { top: 0; left: -3px; transform: none; }
  .ai-route-control__route-line::after { top: auto; right: auto; bottom: 0; left: -3px; transform: none; }
  .ai-route-control__route-line span { top: 35%; left: -3px; }
  .ai-route-control__candidates { grid-template-columns: 1fr; }
  .ai-route-control__actions { justify-content: flex-start; }
  .ai-route-control__updated { text-align: left; }
}

@media (prefers-reduced-motion: reduce) {
  .ai-route-card { transition: none; }
  .ai-route-card:hover { transform: none; }
}
</style>
