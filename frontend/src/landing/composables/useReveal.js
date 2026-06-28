// Lightweight scroll-reveal + count-up helpers for the landing page.
// No dependencies; both respect prefers-reduced-motion by skipping animation
// and showing the final state immediately.

import { onBeforeUnmount, onMounted, ref } from "vue";

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// Activate every element marked with the `reveal` class: it starts hidden and
// gets `is-in` once it scrolls into view (once). Stagger via data-reveal-delay
// (ms). Child components mount before the parent's onMounted, so calling this
// from the root App on mount catches all of them. Returns the observer so the
// caller can disconnect on teardown.
export function initReveal(root) {
  const scope = root || (typeof document !== "undefined" ? document : null);
  if (!scope) return null;
  const els = scope.querySelectorAll(".reveal:not(.is-in)");
  if (prefersReducedMotion() || typeof IntersectionObserver === "undefined") {
    els.forEach((el) => el.classList.add("is-in"));
    return null;
  }
  const io = new IntersectionObserver(
    (entries, obs) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const delay = Number(entry.target.dataset.revealDelay || 0);
        if (delay) entry.target.style.transitionDelay = `${delay}ms`;
        entry.target.classList.add("is-in");
        obs.unobserve(entry.target);
      }
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
  );
  els.forEach((el) => io.observe(el));
  return io;
}

// Count a number up from 0 to `target` once the host element is visible.
// Returns a ref<string> you bind in the template; `format` shapes the output.
export function useCountUp(target, { duration = 1100, decimals = 0, format } = {}) {
  const display = ref(format ? format(0) : "0");
  const elRef = ref(null);
  let raf = 0;

  const shape = (v) =>
    format ? format(v) : v.toFixed(decimals);

  onMounted(() => {
    const el = elRef.value;
    if (!el) return;
    if (prefersReducedMotion() || typeof IntersectionObserver === "undefined") {
      display.value = shape(target);
      return;
    }
    const io = new IntersectionObserver(
      (entries, obs) => {
        if (!entries[0].isIntersecting) return;
        obs.disconnect();
        let start = 0;
        const tick = (ts) => {
          if (!start) start = ts;
          const p = Math.min(1, (ts - start) / duration);
          // easeOutCubic
          const eased = 1 - Math.pow(1 - p, 3);
          display.value = shape(target * eased);
          if (p < 1) raf = requestAnimationFrame(tick);
          else display.value = shape(target);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.5 },
    );
    io.observe(el);
    el._countObserver = io;
  });

  onBeforeUnmount(() => {
    if (raf) cancelAnimationFrame(raf);
    if (elRef.value?._countObserver) elRef.value._countObserver.disconnect();
  });

  return { display, elRef };
}

export { prefersReducedMotion };
