<script setup>
import SignalCanvas from "./SignalCanvas.vue";
</script>

<template>
  <section class="hero">
    <div class="wrap hero-grid">
      <p class="eyebrow reveal" data-reveal-delay="0">AI ROUTING NETWORK</p>

      <h1 class="display hero-title reveal" data-reveal-delay="80">
        AI 流量，<br />走更稳定的路径
      </h1>

      <div class="hero-signal reveal" data-reveal-delay="160">
        <SignalCanvas />
        <div class="signal-legend">
          <span class="lead"><i class="dot dot-signal"></i>AI 优选路径</span>
          <span class="ghost"><i class="dot dot-faint"></i>普通网络路径</span>
        </div>
      </div>

      <p class="hero-sub reveal" data-reveal-delay="200">
        自动识别 AI 目的域名，将相关流量送往独立 AI 网络；通过候选节点健康探测和故障切换，
        在网络层提供更稳定、可观测、可恢复的连接路径。
      </p>

      <div class="hero-cta reveal" data-reveal-delay="260">
        <a class="btn btn-primary" href="/plans">查看套餐</a>
        <a class="btn-text" href="#how">了解路由原理 <span aria-hidden="true">→</span></a>
      </div>

      <dl class="readout reveal" data-reveal-delay="320">
        <div class="readout-item">
          <dt>Routing</dt>
          <dd><span>AUTO</span></dd>
        </div>
        <div class="readout-item">
          <dt>Failover</dt>
          <dd><span>READY</span></dd>
        </div>
        <div class="readout-item">
          <dt>Visibility</dt>
          <dd><span>LIVE</span></dd>
        </div>
      </dl>
    </div>

    <div class="trust wrap reveal">
      <span>AI Services</span>
      <b>ChatGPT</b><b>Claude</b><b>Gemini</b>
      <span class="trust-sep">·</span>
      <span>自动识别</span><span class="trust-sep">·</span>
      <span>健康探测</span><span class="trust-sep">·</span>
      <span>故障切换</span><span class="trust-sep">·</span>
      <span>实时可观测</span>
    </div>
  </section>
</template>
