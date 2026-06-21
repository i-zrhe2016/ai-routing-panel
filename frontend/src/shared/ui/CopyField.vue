<script setup>
// Read-only value + copy button, the single component for every subscription
// link / credential across admin and portal. Keeps the old UX: the button
// label flips to "已复制" for ~1.2s. Emits `copied` / `error` so the host can
// show a toast.
import { onBeforeUnmount, ref } from "vue";
import { copyText } from "../clipboard.js";

const props = defineProps({
  value: { type: String, default: "" },
  label: { type: String, default: "" },
});
const emit = defineEmits(["copied", "error"]);

const copied = ref(false);
let timer = null;

async function onCopy() {
  try {
    await copyText(props.value);
    copied.value = true;
    emit("copied", props.value);
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      copied.value = false;
    }, 1200);
  } catch (error) {
    emit("error", error);
  }
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer);
});
</script>

<template>
  <div class="copy-field">
    <span v-if="label" class="copy-field__label">{{ label }}</span>
    <div class="copy-field__row">
      <input class="copy-field__value" :value="value" :data-copy-value="value" readonly />
      <button type="button" class="copy-field__btn" @click="onCopy">
        {{ copied ? "已复制" : "复制" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.copy-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.copy-field__label {
  font-size: 12px;
  color: var(--c-text-muted, #5b6573);
}
.copy-field__row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}
.copy-field__value {
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--c-line, #e6e9ee);
  border-radius: var(--r-md, 12px);
  background: var(--c-surface-muted, #f6f8fb);
  color: var(--c-text, #1a1f26);
  font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
  font-size: 13px;
}
.copy-field__btn {
  flex: none;
  padding: 8px 14px;
  border: 1px solid var(--c-primary, #1a73e8);
  border-radius: var(--r-md, 12px);
  background: var(--c-primary-soft, #e8f0fe);
  color: var(--c-primary-strong, #174ea6);
  font-weight: 600;
  cursor: pointer;
}
.copy-field__btn:hover {
  background: var(--c-primary, #1a73e8);
  color: #fff;
}
</style>
