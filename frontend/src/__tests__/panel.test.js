import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { createEmptyPortForm, createEmptyPlanForm } from "../utils.js";
import { createPanelApp } from "../createPanelApp.js";

// The real component has no `template` (it uses the in-DOM template from
// index.html); for unit tests we attach a trivial template so @vue/test-utils
// can mount it and we can drive computed/methods through wrapper.vm.
function mountPanel(initialState = {}) {
  return mount({ ...createPanelApp(initialState), template: "<div></div>" });
}

const SAMPLE_STATE = {
  meta: { csrf_token: "tok", data_plane_status: { configured: true, xray_running: true } },
  summary: {
    expired_ports: 1,
    quota_ports: 2,
    disabled_ports: 3,
    total_bytes_received: 1000,
    total_bytes_sent: 24,
  },
  ports: [
    { id: 1, listen_port: 30001, status: "active", note: "alpha" },
    { id: 2, listen_port: 30002, status: "disabled", note: "beta" },
  ],
  commerce: {
    plans: [{ id: 7, slug: "basic", name: "Basic", enabled: true }],
    orders: [{ id: 9, order_no: "ODR1", review_note: "hi" }],
    settings: { foo: "bar" },
  },
};

describe("utils", () => {
  it("createEmptyPortForm has empty defaults", () => {
    const form = createEmptyPortForm();
    expect(form.listen_port).toBe("");
    expect(form.note).toBe("");
  });

  it("createEmptyPlanForm defaults enabled and sort_order", () => {
    const form = createEmptyPlanForm();
    expect(form.enabled).toBe(true);
    expect(form.sort_order).toBe("0");
  });
});

describe("createPanelApp", () => {
  it("applies the initial dashboard on mount (prepares ports/plans/orders)", () => {
    const wrapper = mountPanel(SAMPLE_STATE);
    expect(wrapper.vm.ports).toHaveLength(2);
    expect(wrapper.vm.ports[0].form.listen_port).toBe("30001");
    expect(wrapper.vm.plans[0].form.slug).toBe("basic");
    expect(wrapper.vm.orders[0].form.review_note).toBe("hi");
    expect(wrapper.vm.dnsFailoverStatus).toEqual({});
  });

  it("attentionPortCount and totalTrafficBytes compute across mixins", () => {
    const wrapper = mountPanel(SAMPLE_STATE);
    expect(wrapper.vm.attentionPortCount).toBe(6);
    expect(wrapper.vm.totalTrafficBytes).toBe(1024);
  });

  it("filteredPorts filters by status and query (PortsMixin)", () => {
    const wrapper = mountPanel(SAMPLE_STATE);
    wrapper.vm.filters.status = "disabled";
    expect(wrapper.vm.filteredPorts.map((p) => p.id)).toEqual([2]);
    wrapper.vm.filters.status = "all";
    wrapper.vm.filters.query = "alpha";
    expect(wrapper.vm.filteredPorts.map((p) => p.id)).toEqual([1]);
  });

  it("humanBytes formats sizes (CoreMixin)", () => {
    const wrapper = mountPanel();
    expect(wrapper.vm.humanBytes(512)).toBe("512 B");
    expect(wrapper.vm.humanBytes(1024)).toBe("1.00 KB");
  });

  it("setFlash/clearFlash mutate shared flash state", () => {
    const wrapper = mountPanel();
    wrapper.vm.setFlash("hello", "error");
    expect(wrapper.vm.flash).toEqual({ message: "hello", level: "error" });
    wrapper.vm.clearFlash();
    expect(wrapper.vm.flash).toEqual({ message: "", level: "info" });
  });

  it("status presenters resolve across DNS/AI mixins", () => {
    const wrapper = mountPanel(SAMPLE_STATE);
    expect(wrapper.vm.dataPlaneRunningLabel({ configured: true, xray_running: true })).toBe("运行中");
    expect(wrapper.vm.dnsFailoverSummary({ enabled: false })).toBe("未启用");
  });
});
