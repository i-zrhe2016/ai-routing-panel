<script setup>
// The "原理" section: a live routing topology that shows a request's journey —
// encrypted tunnel in, AI domains split to a preferred direct route (full-
// strength), everything else local-direct, a standby node on DNS failover.
// The wires carry an animated "current" (flowing dashes); the AI/fidelity path
// glows. Built as HTML nodes (crisp, accessible) over one stretched SVG wire
// layer. Motion is CSS-only and pauses under prefers-reduced-motion; the four
// points below carry the same meaning for screen readers (the SVG is hidden).
const points = [
  { code: "01", title: "加密直达", desc: "端到端 VLESS + REALITY 隧道，握手伪装、无明文暴露。" },
  { code: "02", title: "智能分流", desc: "节点内置 AI 域名识别，自动把 ChatGPT / Claude / Gemini 走优选直连。" },
  { code: "03", title: "原生出口", desc: "真实家宽 IP 出口，被视作普通住宅用户，不触发风控降智。" },
  { code: "04", title: "故障切换", desc: "多上游冗余 + DNS 秒级切换，节点异常自动接管。" },
];
</script>

<template>
  <section id="how" class="section section-divide">
    <div class="wrap">
      <header class="section-head reveal">
        <p class="eyebrow">PRINCIPLE · 工作原理</p>
        <h2 class="display section-title">一次请求的旅程</h2>
        <p class="section-lead">
          从你的设备到 AI，全程加密、智能分流、原生出口——每一步都为「不降智」服务。
        </p>
      </header>

      <!-- Dynamic structure: nodes + animated wire current -->
      <div class="principle-map reveal" data-reveal-delay="120">
        <svg class="principle-wires" viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
          <path class="wire wire-tunnel" d="M95 210 L430 210" />
          <path class="wire wire-fidelity" d="M430 210 C600 210 650 120 815 120" />
          <path class="wire wire-normal" d="M430 210 C600 210 650 300 815 300" />
          <path class="wire wire-ghost" d="M430 210 L430 372" />
        </svg>

        <span class="wire-label wire-label-tunnel" aria-hidden="true">加密隧道</span>

        <div class="principle-node node-client" style="left: 9.5%; top: 50%">
          <span class="node-code">CLIENT</span>
          <span class="node-name">你的设备</span>
        </div>

        <div class="principle-node principle-hub node-hub" style="left: 43%; top: 50%">
          <span class="node-code">NODE</span>
          <span class="node-name">原生家宽节点</span>
          <span class="node-sub">AI 自动分流</span>
        </div>

        <div class="principle-node principle-leg node-ai" style="left: 81.5%; top: 28.57%">
          <span class="node-code node-code-signal">AI · 满血保真</span>
          <span class="node-name">AI 优选直连</span>
          <span class="node-sub">ChatGPT · Claude · Gemini</span>
        </div>

        <div class="principle-node principle-leg node-local" style="left: 81.5%; top: 71.43%">
          <span class="node-code">OTHER</span>
          <span class="node-name">其它流量</span>
          <span class="node-sub">本地直连</span>
        </div>

        <div class="principle-node principle-standby node-standby" style="left: 43%; top: 88.57%">
          <span class="node-code">STANDBY</span>
          <span class="node-name">备用节点</span>
          <span class="node-sub">DNS 故障切换待命</span>
        </div>
      </div>

      <ol class="principle-points">
        <li v-for="(p, i) in points" :key="p.code" class="principle-point reveal" :data-reveal-delay="i * 70">
          <span class="point-code">{{ p.code }}</span>
          <h3 class="point-title">{{ p.title }}</h3>
          <p class="point-desc">{{ p.desc }}</p>
        </li>
      </ol>
    </div>
  </section>
</template>
