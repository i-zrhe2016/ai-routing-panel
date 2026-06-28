<script setup>
// Spec-sheet rows rather than cards: a mono short-code, a thin line mark, a
// serif title, and a plain-spoken line. The four pillars of the product.
const features = [
  {
    code: "RESIDENTIAL",
    title: "原生家宽",
    desc: "真实家庭宽带出口，不被识别为机房 IP——从根源上避开 ChatGPT 的风控降级，保住原生模型智力。",
    icon: `<path d="M3 11l9-7 9 7" /><path d="M5 10v9h14v-9" /><path d="M10 19v-5h4v5" />`,
  },
  {
    code: "ROUTING",
    title: "自动分流",
    desc: "内置 AI 域名识别，自动把 ChatGPT、Claude、Gemini 的流量分流到优选直连线路，其余流量本地直连——既快又稳，不触发降智。",
    icon: `<path d="M3 7h6l4 5h8M19 12l-3-3M19 12l-3 3" /><path d="M3 17h6" />`,
  },
  {
    code: "DEDICATED",
    title: "独享不超售",
    desc: "独立端口与带宽配额，流量公开透明、绝不超卖。高峰期同样跑满，不与他人抢占资源。",
    icon: `<rect x="4" y="5" width="16" height="14" rx="2" /><path d="M9 9h6M9 13h6M9 17h3" />`,
  },
  {
    code: "FAILOVER",
    title: "多路高可用",
    desc: "多上游冗余加 DNS 故障自动切换，节点异常时秒级切到备用线路，连通性实时探测，长期稳定在线。",
    icon: `<path d="M12 4v6M12 14v6" /><circle cx="12" cy="12" r="2.2" /><path d="M5 8a8 8 0 0 0 0 8M19 8a8 8 0 0 1 0 8" />`,
  },
  {
    code: "PRIVACY",
    title: "端到端加密",
    desc: "全程端到端加密，最小化日志、隐私优先。专属订阅与凭证仅你可见，对话内容不留存、不分析。",
    icon: `<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" /><path d="M9.5 12l1.8 1.8L15 10" />`,
  },
];
</script>

<template>
  <section id="features" class="section">
    <div class="wrap">
      <header class="section-head reveal">
        <p class="eyebrow">WHY · 不降智的理由</p>
        <h2 class="display section-title">五重保障</h2>
      </header>

      <ul class="spec">
        <li
          v-for="(f, i) in features"
          :key="f.code"
          class="spec-row reveal"
          :data-reveal-delay="i * 70"
        >
          <span class="spec-code">{{ f.code }}</span>
          <span class="spec-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"
                 stroke-linecap="round" stroke-linejoin="round" v-html="f.icon"></svg>
          </span>
          <div class="spec-body">
            <h3 class="display spec-title">{{ f.title }}</h3>
            <p class="spec-desc">{{ f.desc }}</p>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>
