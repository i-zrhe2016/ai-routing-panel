<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  activeKey: { type: String, default: "" },
});

const emit = defineEmits(["select"]);

function iconPath(key) {
  return {
    overview: "M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm10 0h6v-9h-6v9Zm0-16v5h6V4h-6Z",
    routing: "M4 6h6l2 3h8M4 18h6l2-3h8M12 9v6",
    traffic: "M4 17V9m5 8V5m5 12v-6m5 6V3",
    resources: "M4 6h16M4 12h16M4 18h16",
    commerce: "M5 7h14l-1 12H6L5 7Zm3 0V5a4 4 0 0 1 8 0v2",
    infra: "M12 3v18M3 12h18M5.64 5.64l12.72 12.72M18.36 5.64 5.64 18.36",
    observe: "M4 15l4-4 3 3 5-7 4 4M4 20h16",
  }[key] || "M5 12h14";
}
</script>

<template>
  <nav class="workspace-nav" aria-label="控制台工作区">
    <p class="nav-label">CONTROL CENTER</p>
    <button
      v-for="item in items"
      :key="item.key"
      class="workspace-nav__item"
      :class="{ 'is-active': activeKey === item.key }"
      type="button"
      :aria-current="activeKey === item.key ? 'page' : undefined"
      @click="emit('select', item.key)"
    >
      <span class="workspace-nav__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path :d="iconPath(item.key)" />
        </svg>
      </span>
      <span class="workspace-nav__copy">
        <strong>{{ item.label }}</strong>
        <small>{{ item.description }}</small>
      </span>
      <span v-if="item.badge" class="workspace-nav__badge">{{ item.badge }}</span>
    </button>
  </nav>
</template>
