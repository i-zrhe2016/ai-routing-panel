<script setup>
// Status badge driven by the backend's status_tone (or a raw status string,
// mapped via statusTone). Preserves the green=ok / orange=warn / red=bad
// vocabulary from the old .badge/.status-pill classes.
import { computed } from "vue";
import { TONES, statusTone } from "../tokens.js";

const props = defineProps({
  label: { type: String, default: "" },
  tone: { type: String, default: "" },
  status: { type: String, default: "" },
});

const effectiveTone = computed(() => props.tone || statusTone(props.status));
const palette = computed(() => TONES[effectiveTone.value] || TONES.neutral);
</script>

<template>
  <span class="status-pill" :style="{ color: palette.color, backgroundColor: palette.soft }">
    <span class="status-pill__dot" :style="{ backgroundColor: palette.color }" />
    {{ label || status }}
  </span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
}
.status-pill__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
</style>
