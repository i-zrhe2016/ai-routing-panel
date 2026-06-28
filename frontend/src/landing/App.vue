<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

import { createApiClient } from "../shared/apiClient.js";
import { initReveal } from "./composables/useReveal.js";

import HeroSection from "./sections/HeroSection.vue";
import FeaturesSection from "./sections/FeaturesSection.vue";
import PrincipleSection from "./sections/PrincipleSection.vue";
import HowItWorksSection from "./sections/HowItWorksSection.vue";
import PricingSection from "./sections/PricingSection.vue";
import FaqSection from "./sections/FaqSection.vue";

const props = defineProps({
  csrfToken: { type: String, default: "" },
});

// Live pricing from the public plans API. Three states feed PricingSection:
// loading -> loaded (with plans) | error (¥49/月 fallback).
const plans = ref([]);
const commerceSettings = ref({});
const pricingState = ref("loading");

const navLinks = [
  { href: "#features", label: "特性" },
  { href: "#how", label: "原理" },
  { href: "#start", label: "接入" },
  { href: "#pricing", label: "价格" },
  { href: "#faq", label: "问答" },
];

const scrolled = ref(false);
const menuOpen = ref(false);
let revealObserver = null;
function onScroll() {
  scrolled.value = (typeof window !== "undefined" ? window.scrollY : 0) > 8;
}
function closeMenu() {
  menuOpen.value = false;
}

onMounted(async () => {
  onScroll();
  revealObserver = initReveal();
  if (typeof window !== "undefined") window.addEventListener("scroll", onScroll, { passive: true });

  const api = createApiClient({ csrfToken: props.csrfToken, onUnauthorized: () => {} });
  try {
    const res = await api.get("/api/customer/plans");
    const data = res.data || res || {};
    plans.value = Array.isArray(data.plans) ? data.plans : [];
    commerceSettings.value = data.commerce_settings || {};
    pricingState.value = "loaded";
  } catch (_error) {
    pricingState.value = "error";
  }
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") window.removeEventListener("scroll", onScroll);
  if (revealObserver) revealObserver.disconnect();
});
</script>

<template>
  <div class="lp">
    <header class="nav" :class="{ 'is-scrolled': scrolled }">
      <div class="wrap nav-inner">
        <a class="brand" href="#top">
          <span class="brand-mark" aria-hidden="true"></span>
          <span class="brand-name">AI 家宽<i>·</i>防降智</span>
        </a>

        <nav class="nav-links" aria-label="主导航">
          <a v-for="l in navLinks" :key="l.href" :href="l.href">{{ l.label }}</a>
        </nav>

        <div class="nav-actions">
          <a class="btn-text nav-login" href="/portal">订阅中心</a>
          <a class="btn btn-primary btn-sm" href="/plans">立即开通</a>
        </div>

        <button
          class="nav-toggle"
          :aria-expanded="menuOpen"
          aria-label="菜单"
          @click="menuOpen = !menuOpen"
        >
          <span :class="{ 'is-open': menuOpen }"></span>
        </button>
      </div>

      <div v-show="menuOpen" class="nav-drawer">
        <a v-for="l in navLinks" :key="l.href" :href="l.href" @click="closeMenu">{{ l.label }}</a>
        <a href="/portal" @click="closeMenu">订阅中心</a>
        <a class="btn btn-primary" href="/plans" @click="closeMenu">立即开通</a>
      </div>
    </header>

    <main id="top">
      <HeroSection />
      <FeaturesSection />
      <PrincipleSection />
      <HowItWorksSection />
      <PricingSection
        :state="pricingState"
        :plans="plans"
        :commerce-settings="commerceSettings"
      />
      <FaqSection />
    </main>

    <footer class="footer">
      <div class="wrap footer-inner">
        <div class="footer-brand">
          <span class="brand-mark" aria-hidden="true"></span>
          <div>
            <p class="footer-name">AI 家宽 · ChatGPT 防降智节点</p>
            <p class="footer-tag">原生家宽 · 自动分流 · 不超售 · 高可用 · 数据安全</p>
          </div>
        </div>
        <div class="footer-cta">
          <a class="btn btn-primary" href="/plans">立即开通</a>
          <a class="btn-text" href="/portal">订阅中心</a>
        </div>
      </div>
      <p class="footer-fine wrap">© AI 家宽 · ChatGPT 防降智节点 · 仅供合法合规用途</p>
    </footer>
  </div>
</template>
