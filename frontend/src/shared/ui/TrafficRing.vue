<script setup>
// Conic-gradient usage ring for a subscription's traffic quota — preserves the
// distinctive visual from the old probe/tenant pages. A zero/absent limit means
// unlimited and renders "∞".
import { computed } from "vue";
import { humanBytes, usageFraction } from "../formatters.js";

const props = defineProps({
  used: { type: [Number, String], default: 0 },
  limit: { type: [Number, String], default: 0 },
});

const unlimited = computed(() => Number(props.limit || 0) <= 0);
const fraction = computed(() => usageFraction(props.used, props.limit));
const percent = computed(() => Math.round(fraction.value * 100));
const angle = computed(() => `${fraction.value * 360}deg`);
const tone = computed(() => {
  if (unlimited.value) return "var(--c-primary, #1a73e8)";
  if (fraction.value >= 1) return "var(--c-danger, #d93025)";
  if (fraction.value >= 0.85) return "var(--c-warning, #b06000)";
  return "var(--c-success, #188038)";
});
</script>

<template>
  <div class="traffic-ring" :style="{ '--angle': angle, '--tone': tone }" data-testid="traffic-ring">
    <div class="traffic-ring__core">
      <strong>{{ unlimited ? "∞" : percent + "%" }}</strong>
      <small>
        {{ humanBytes(used) }}<template v-if="!unlimited"> / {{ humanBytes(limit) }}</template>
      </small>
    </div>
  </div>
</template>

<style scoped>
.traffic-ring {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: conic-gradient(var(--tone) 0deg var(--angle), var(--c-gray-200, #e6eaf0) var(--angle) 360deg);
}
.traffic-ring__core {
  width: 102px;
  height: 102px;
  border-radius: 50%;
  background: var(--c-surface, #fff);
  display: grid;
  place-items: center;
  text-align: center;
  gap: 2px;
}
.traffic-ring__core strong {
  font-size: 22px;
  color: var(--c-text, #1a1f26);
}
.traffic-ring__core small {
  font-size: 11px;
  color: var(--c-text-muted, #5b6573);
}
</style>
