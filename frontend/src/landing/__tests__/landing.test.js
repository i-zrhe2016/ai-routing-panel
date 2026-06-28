import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App.vue";

// Smoke test: the landing SPA mounts without runtime errors in jsdom and renders
// its key surfaces. jsdom lacks IntersectionObserver/canvas, so the components'
// guards should degrade gracefully (reveal shows immediately, canvas is skipped).
describe("landing App", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() => Promise.reject(new Error("offline")));
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders hero, sections, and the ¥49 pricing fallback when plans fail to load", async () => {
    const wrapper = mount(App, { props: { csrfToken: "test" } });
    // let the onMounted plans fetch reject and flush microtasks
    await Promise.resolve();
    await Promise.resolve();
    await wrapper.vm.$nextTick();

    const text = wrapper.text();
    expect(text).toContain("满血智力");
    expect(text).toContain("原生家宽");
    expect(text).toContain("五重保障");
    expect(text).toContain("工作原理");
    expect(text).toContain("智能分流");
    expect(text).toContain("四步接入");
    expect(text).toContain("常见问题");
    // pricing fallback (fetch rejected -> error state)
    expect(text).toContain("¥49");
    // signature canvas element present
    expect(wrapper.find(".signal-canvas").exists()).toBe(true);

    wrapper.unmount();
  });

  it("toggles a FAQ item open and closed", async () => {
    const wrapper = mount(App, { props: { csrfToken: "test" } });
    await wrapper.vm.$nextTick();
    const buttons = wrapper.findAll(".faq-q");
    expect(buttons.length).toBeGreaterThan(0);
    // first item starts open (open=0); clicking it closes it
    expect(wrapper.find(".faq-a.is-open").exists()).toBe(true);
    await buttons[0].trigger("click");
    expect(wrapper.find(".faq-a.is-open").exists()).toBe(false);
    wrapper.unmount();
  });
});
