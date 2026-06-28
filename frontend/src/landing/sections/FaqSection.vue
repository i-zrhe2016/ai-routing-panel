<script setup>
import { ref } from "vue";

const faqs = [
  {
    q: "什么是「防降智」？",
    a: "线路被风控、共享 IP 或质量太差时，会触发 AI 服务商的降级策略，让模型回答变笨、变慢甚至拒答。我们把 AI 流量自动分流到优选直连线路，保住原生模型的满血智力。",
  },
  {
    q: "为什么家宽 IP 能防降智？",
    a: "ChatGPT 会对机房 / 数据中心 IP 加强风控，触发降级甚至封禁。原生家宽是真实家庭宽带出口，在服务商眼里就是普通住宅用户，从源头避免被降智。",
  },
  {
    q: "你们会超售吗？",
    a: "不会。每个套餐分配独立端口与带宽配额，流量公开透明、绝不超卖。高峰期也能跑满，不与他人抢资源。",
  },
  {
    q: "节点掉线或不稳定怎么办？",
    a: "多上游冗余加 DNS 故障自动切换，节点异常会秒级切到备用线路，连通性实时探测，长期稳定在线。",
  },
  {
    q: "支持哪些客户端？",
    a: "提供 Clash、V2Ray 订阅以及 VLESS 分享链接，主流客户端都能一键导入使用。",
  },
  {
    q: "我的数据安全吗？",
    a: "全程端到端加密，最小化日志、隐私优先。专属订阅与凭证仅你可见，对话内容不留存、不分析。",
  },
  {
    q: "如何开始？",
    a: "选择套餐下单，审核通过后系统自动开通专属端口与订阅地址，导入客户端即可使用。",
  },
];

const open = ref(0);
function toggle(i) {
  open.value = open.value === i ? -1 : i;
}
</script>

<template>
  <section id="faq" class="section section-divide">
    <div class="wrap wrap-narrow">
      <header class="section-head reveal">
        <p class="eyebrow">FAQ</p>
        <h2 class="display section-title">常见问题</h2>
      </header>

      <ul class="faq">
        <li v-for="(item, i) in faqs" :key="i" class="faq-item reveal" :data-reveal-delay="i * 40">
          <button
            class="faq-q"
            :aria-expanded="open === i"
            @click="toggle(i)"
          >
            <span>{{ item.q }}</span>
            <i class="faq-mark" :class="{ 'is-open': open === i }" aria-hidden="true"></i>
          </button>
          <div class="faq-a" :class="{ 'is-open': open === i }">
            <p>{{ item.a }}</p>
          </div>
        </li>
      </ul>

      <div class="faq-cta reveal">
        <a class="btn btn-primary" href="/plans">立即开通</a>
      </div>
    </div>
  </section>
</template>
