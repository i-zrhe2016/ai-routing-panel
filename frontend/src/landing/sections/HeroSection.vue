<script setup>
import SignalCanvas from "./SignalCanvas.vue";
import { useCountUp } from "../composables/useReveal.js";

// Live-feeling instrument readouts. They animate once on first view.
const { display: fidelityText, elRef: fidelityEl } = useCountUp(100, { format: (v) => `${Math.round(v)}` });
const { display: uptimeText, elRef: uptimeEl } = useCountUp(99.98, { decimals: 2 });
const { display: latencyText, elRef: latencyEl } = useCountUp(38, { format: (v) => `${Math.round(v)}` });
</script>

<template>
  <section class="hero">
    <div class="wrap hero-grid">
      <p class="eyebrow reveal" data-reveal-delay="0">AI 家宽 · ChatGPT 防降智</p>

      <h1 class="display hero-title reveal" data-reveal-delay="80">
        满血智力，<br />全程不降级
      </h1>

      <div class="hero-signal reveal" data-reveal-delay="160">
        <SignalCanvas />
        <div class="signal-legend">
          <span class="lead"><i class="dot dot-signal"></i>保真信号</span>
          <span class="ghost"><i class="dot dot-faint"></i>降智线路</span>
        </div>
      </div>

      <p class="hero-sub reveal" data-reveal-delay="200">
        原生家宽 IP 出口，不被识别为机房、不触发 ChatGPT 风控降智；AI 流量自动分流到优选直连线路，
        独享不超售、多路高可用、端到端加密——让每一次对话都满血、稳定、私密。
      </p>

      <div class="hero-cta reveal" data-reveal-delay="260">
        <a class="btn btn-primary" href="/plans">查看套餐</a>
        <a class="btn-text" href="#how">了解原理 <span aria-hidden="true">→</span></a>
      </div>

      <dl class="readout reveal" data-reveal-delay="320">
        <div class="readout-item">
          <dt>保真度</dt>
          <dd><span ref="fidelityEl">{{ fidelityText }}</span><u>%</u></dd>
        </div>
        <div class="readout-item">
          <dt>在线率</dt>
          <dd><span ref="uptimeEl">{{ uptimeText }}</span><u>%</u></dd>
        </div>
        <div class="readout-item">
          <dt>直连延迟</dt>
          <dd><span ref="latencyEl">{{ latencyText }}</span><u>ms</u></dd>
        </div>
      </dl>
    </div>

    <div class="trust wrap reveal">
      <span>支持</span>
      <b>ChatGPT</b><b>Claude</b><b>Gemini</b>
      <span class="trust-sep">·</span>
      <b>原生家宽 IP</b><span class="trust-sep">·</span>
      <span>独享不超售</span><span class="trust-sep">·</span>
      <span>多路高可用</span>
    </div>
  </section>
</template>
