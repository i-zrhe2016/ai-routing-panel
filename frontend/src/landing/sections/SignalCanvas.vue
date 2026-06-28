<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { prefersReducedMotion } from "../composables/useReveal.js";

// The page's signature: a crisp, full-amplitude "fidelity" waveform (your model,
// undegraded) drawn over a low, jittery "degraded" ghost line (a throttled route).
// Ambient drift only — quiet by design. Pauses offscreen; static if the visitor
// prefers reduced motion.
const host = ref(null);
const canvas = ref(null);
let ctx, raf = 0, t = 0, running = false, io, ro;
let w = 0, h = 0, dpr = 1;
let signalColor = "#6BA5FF", faintColor = "#5C616B";

function resize() {
  const el = canvas.value;
  if (!el) return;
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  w = el.clientWidth;
  h = el.clientHeight;
  el.width = Math.round(w * dpr);
  el.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// Smooth fidelity wave: two summed sines, full amplitude, no noise.
function fidelityY(x, time) {
  const mid = h / 2;
  const amp = h * 0.30;
  const k = (x / w) * Math.PI * 2;
  return mid + Math.sin(k * 1.6 + time) * amp * 0.7 + Math.sin(k * 0.7 - time * 0.6) * amp * 0.3;
}

// Degraded ghost: low amplitude + per-point jitter, clamped/steppy — visibly worse.
function degradedY(x, time) {
  const mid = h / 2;
  const amp = h * 0.12;
  const k = (x / w) * Math.PI * 2;
  const stepped = Math.round(Math.sin(k * 1.6 + time * 0.8) * 4) / 4;
  const jitter = Math.sin(x * 12.9898 + time * 7.0) * 0.5; // cheap pseudo-noise
  return mid + stepped * amp + jitter * (h * 0.04);
}

function drawWave(yfn, time, { color, width, alpha, dash }) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  if (dash) ctx.setLineDash(dash);
  ctx.beginPath();
  const step = 3;
  for (let x = 0; x <= w; x += step) {
    const y = yfn(x, time);
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.restore();
}

function frame() {
  if (!running) return;
  ctx.clearRect(0, 0, w, h);
  drawWave(degradedY, t, { color: faintColor, width: 1, alpha: 0.5, dash: [2, 5] });
  // soft glow under the fidelity line
  ctx.save();
  ctx.shadowColor = signalColor;
  ctx.shadowBlur = 14;
  drawWave(fidelityY, t, { color: signalColor, width: 1.6, alpha: 0.95 });
  ctx.restore();
  t += 0.018;
  raf = requestAnimationFrame(frame);
}

function drawStatic() {
  ctx.clearRect(0, 0, w, h);
  drawWave(degradedY, 0, { color: faintColor, width: 1, alpha: 0.5, dash: [2, 5] });
  drawWave(fidelityY, 0, { color: signalColor, width: 1.6, alpha: 0.95 });
}

function start() {
  if (running) return;
  running = true;
  raf = requestAnimationFrame(frame);
}
function stop() {
  running = false;
  if (raf) cancelAnimationFrame(raf);
}

onMounted(() => {
  try {
    ctx = canvas.value && canvas.value.getContext ? canvas.value.getContext("2d") : null;
  } catch (_e) {
    ctx = null;
  }
  if (!ctx) return; // no 2d context (e.g. headless/SSR) — skip the canvas entirely
  const styles = getComputedStyle(host.value);
  signalColor = styles.getPropertyValue("--signal").trim() || signalColor;
  faintColor = styles.getPropertyValue("--faint").trim() || faintColor;
  resize();

  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => {
      resize();
      if (!running) drawStatic();
    });
    ro.observe(canvas.value);
  } else {
    window.addEventListener("resize", resize);
  }

  if (prefersReducedMotion()) {
    drawStatic();
    return;
  }
  // Only animate while in view.
  if (typeof IntersectionObserver !== "undefined") {
    io = new IntersectionObserver(
      (entries) => (entries[0].isIntersecting ? start() : stop()),
      { threshold: 0.05 },
    );
    io.observe(host.value);
  } else {
    start();
  }
});

onBeforeUnmount(() => {
  stop();
  if (io) io.disconnect();
  if (ro) ro.disconnect();
  window.removeEventListener("resize", resize);
});
</script>

<template>
  <div ref="host" class="signal" aria-hidden="true">
    <canvas ref="canvas" class="signal-canvas"></canvas>
  </div>
</template>
