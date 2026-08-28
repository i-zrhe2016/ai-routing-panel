<script setup>
defineProps({
  label: { type: String, default: "" },
  value: { type: [String, Number], default: "—" },
  note: { type: String, default: "" },
  tone: { type: String, default: "neutral" },
  accent: { type: Boolean, default: false },
});
</script>

<template>
  <article class="kpi-card" :class="[`is-${tone}`, { 'is-accent': accent }]">
    <div class="kpi-card__topline">
      <span>{{ label }}</span>
      <i aria-hidden="true"></i>
    </div>
    <strong>{{ value }}</strong>
    <small>{{ note }}</small>
  </article>
</template>
