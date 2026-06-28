<script setup>
import { computed } from "vue";

const props = defineProps({
  state: { type: String, default: "loading" }, // loading | loaded | error
  plans: { type: Array, default: () => [] },
  commerceSettings: { type: Object, default: () => ({}) },
});

const hasPlans = computed(() => props.state === "loaded" && props.plans.length > 0);
const featured = computed(() => (hasPlans.value ? props.plans[0] : null));
const others = computed(() => (hasPlans.value ? props.plans.slice(1) : []));

function checkoutUrl(slug) {
  return slug ? `/checkout/${slug}` : "/plans";
}
</script>

<template>
  <section id="pricing" class="section section-divide">
    <div class="wrap">
      <header class="section-head reveal">
        <p class="eyebrow">PRICING · 不超售</p>
        <h2 class="display section-title">透明定价</h2>
      </header>

      <!-- Loaded: feature the first plan as the panel -->
      <div v-if="hasPlans" class="price-stage reveal">
        <article class="price-panel">
          <span class="price-tag">推荐</span>
          <p class="price-name">{{ featured.name }}</p>
          <p class="price-amount display">{{ featured.price_display }}</p>
          <p class="price-meta">{{ featured.duration_days }} 天 · {{ featured.traffic_limit_display }}</p>
          <p class="price-desc">
            {{ featured.description || "专属端口、AI 自动分流、Clash / V2Ray 订阅与 VLESS 分享链接。" }}
          </p>
          <a class="btn btn-primary price-cta" :href="checkoutUrl(featured.slug)">立即开通</a>
        </article>

        <ul v-if="others.length" class="price-more">
          <li v-for="p in others" :key="p.slug">
            <span class="price-more-name">{{ p.name }}</span>
            <span class="price-more-meta">{{ p.duration_days }} 天 · {{ p.traffic_limit_display }}</span>
            <span class="price-more-amount">{{ p.price_display }}</span>
            <a class="btn-text" :href="checkoutUrl(p.slug)">选择 <span aria-hidden="true">→</span></a>
          </li>
        </ul>
      </div>

      <!-- Loading -->
      <div v-else-if="state === 'loading'" class="price-stage reveal">
        <article class="price-panel is-skeleton">
          <span class="skel skel-name"></span>
          <span class="skel skel-amount"></span>
          <span class="skel skel-line"></span>
          <span class="skel skel-cta"></span>
        </article>
      </div>

      <!-- Empty / error fallback: ¥49/月 -->
      <div v-else class="price-stage reveal">
        <article class="price-panel">
          <span class="price-tag">月付</span>
          <p class="price-name">标准月付</p>
          <p class="price-amount display">¥49<span class="price-unit">/月</span></p>
          <p class="price-meta">独享端口 · AI 自动分流 · 不超售</p>
          <p class="price-desc">含专属订阅、Clash / V2Ray 订阅与 VLESS 分享链接，多路高可用、数据加密。</p>
          <a class="btn btn-primary price-cta" href="/plans">查看套餐并开通</a>
        </article>
      </div>
    </div>
  </section>
</template>
