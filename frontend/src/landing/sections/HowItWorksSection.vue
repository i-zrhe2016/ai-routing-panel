<script setup>
// A real sequence, so the numbering carries meaning. A hairline runs through the
// step nodes — the signal line, continued.
const steps = [
  { no: "01", title: "下单开通", desc: "选择套餐下单，审核通过后系统自动生成你的专属端口与订阅地址。" },
  { no: "02", title: "导入客户端", desc: "复制订阅链接，导入 Clash、V2Ray 等客户端，一键完成配置。" },
  { no: "03", title: "自动分流", desc: "节点自动识别 AI 流量走优选线路，其余本地直连，无需手动切换。" },
  { no: "04", title: "稳定保活", desc: "多上游与 DNS 故障切换持续保活，对话不掉线、模型不降智。" },
];
</script>

<template>
  <section id="start" class="section section-divide">
    <div class="wrap">
      <header class="section-head reveal">
        <p class="eyebrow">ONBOARDING · 全程自动</p>
        <h2 class="display section-title">四步接入</h2>
      </header>

      <ol class="flow">
        <li
          v-for="(s, i) in steps"
          :key="s.no"
          class="flow-step reveal"
          :data-reveal-delay="i * 80"
        >
          <span class="flow-node">{{ s.no }}</span>
          <h3 class="display flow-title">{{ s.title }}</h3>
          <p class="flow-desc">{{ s.desc }}</p>
        </li>
      </ol>
    </div>
  </section>
</template>
