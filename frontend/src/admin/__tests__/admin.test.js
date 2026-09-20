import { flushPromises, mount } from "@vue/test-utils";
import naive from "naive-ui";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App.vue";
import { sameOriginLoginUrl } from "../../mixins/core.js";

// A dashboard payload shaped like build_dashboard_state(), enough to exercise
// every section. preparePort/preparePlan/prepareOrder add the .form fields.
function makeDashboard() {
  return {
    meta: {
      csrf_token: "csrf-x",
      panel_address: "panel.example.com",
      timezone_label: "UTC",
      probe_enabled: false,
      ai_domain_dashboard_url: "/ai-domain-dashboard",
      data_plane_status: { xray_running: true, configured: true, label: "数据面", management_target: "docker" },
      ai_node_status: { configured: true, reachable: true, label: "AI 节点", management_target: "root@ai-node" },
      ai_routing_status: {
        configured: true,
        status_tone: "ok",
        status_label: "正常",
        manual_mode: "auto",
        manual_mode_label: "自动探测",
        ai_candidates: [
          { index: 0, label: "主 AI 节点", candidate_label: "nat.qq.pw:27166", upstream_host: "nat.qq.pw", upstream_port: 27166, is_reachable: true, selected: true },
          { index: 1, label: "备用 AI 节点", candidate_label: "100.87.76.6:27166", upstream_host: "100.87.76.6", upstream_port: 27166, is_reachable: true, selected: false },
        ],
      },
      dns_failover_status: { enabled: true, configured: true, current_target: "primary", backup_label: "控制面备用 Xray" },
      traffic_routing: {
        path: "normal_ai",
        label: "数据面→AI 节点直出",
        scenario: "正常：AI 流量经 AI 节点直出",
        entry_node: "普通数据面",
        transit_nodes: ["AI 节点"],
        exit_node: "AI 节点 freedom 直出",
      },
      backup_xray_mode: "relay",
    },
    summary: {
      total_ports: 1,
      active_ports: 1,
      disabled_ports: 0,
      expired_ports: 0,
      quota_ports: 0,
      total_connections: 12,
      total_bytes_received: 512,
      total_bytes_sent: 512,
    },
    subscription: { available: true, tenant_count: 1, server: "1.2.3.4:443" },
    ports: [
      {
        id: 1,
        listen_port: 31098,
        status: "active",
        status_label: "运行中",
        note: "客户A",
        traffic_used_display: "1.00 KB",
        total_connections: 12,
        total_bytes_received: 512,
        total_bytes_sent: 512,
        today_bytes_received: 0,
        today_bytes_sent: 0,
        expires_at_display: "永久",
        expires_at_input: "",
        traffic_limit_input: "",
        access: {
          tenant_login_url: "https://panel.example.com/login?next=/tenant/abc",
          tenant_username: "tenant_user",
          tenant_password: "tenant_pass",
          tenant_subscription_clash_url: "clash://example/abc",
          tenant_subscription_v2ray_url: "v2ray://example/abc",
          share_link: "vless://uuid@1.2.3.4:443",
        },
      },
    ],
    commerce: {
      summary: { pending_review_count: 1, enabled_plan_count: 1, customer_count: 3, service_count: 1 },
      settings: { order_expiry_hours: 24, auto_port_start: 31000, auto_port_end: 39999, payment_proof_max_display: "5 MB" },
      plans: [
        {
          id: 7,
          slug: "basic-30d-100g",
          name: "基础套餐",
          price_display: "¥9.90",
          duration_days: 30,
          traffic_limit_display: "100 GB",
          enabled: true,
          status_label: "已上架",
          sort_order: 1,
          price_fen: 990,
          description: "30 天 100G",
        },
      ],
      orders: [
        {
          id: 5,
          order_no: "ODR2606210001",
          customer_email: "user@example.com",
          plan_name_snapshot: "基础套餐",
          price_display: "¥9.90",
          status: "payment_submitted",
          status_label: "待审核",
          status_tone: "info",
          kind: "new_purchase",
          created_at_display: "2026-06-21 10:00",
          payer_note: "已付款",
          listen_port: null,
          proof_available: true,
          latest_submission_id: 9,
          review_note: "",
        },
      ],
    },
  };
}

function jsonResp(body, status = 200) {
  return Promise.resolve({
    status,
    ok: status >= 200 && status < 300,
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let fetchMock;
const dashboard = makeDashboard();

beforeEach(() => {
  vi.stubGlobal("__BOOT__", { csrf_token: "csrf-x", auth_enabled: true });
  vi.stubGlobal("confirm", () => true);
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {} }));
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  fetchMock = vi.fn((url, init) => jsonResp({ ok: true, message: "ok", dashboard, ...(url === "/api/dashboard" ? { dashboard } : {}) }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function mountAdmin() {
  const wrapper = mount(App, { global: { plugins: [naive] }, attachTo: document.body });
  await flushPromises();
  await wrapper.vm.$nextTick();
  return wrapper;
}

describe("sameOriginLoginUrl", () => {
  const location = { origin: "https://panel.example.com" };

  it("accepts local login destinations", () => {
    expect(sameOriginLoginUrl("/login?next=%2F", location)).toBe(
      "https://panel.example.com/login?next=%2F",
    );
  });

  it("rejects cross-origin and malformed destinations", () => {
    expect(sameOriginLoginUrl("https://attacker.example/phish", location)).toBe("/login");
    expect(sameOriginLoginUrl("http://[invalid", location)).toBe("/login");
  });
});

describe("AdminApp", () => {
  it("organizes the console into seven first-class control-center workspaces", async () => {
    const wrapper = await mountAdmin();
    expect(wrapper.findAll(".workspace-nav__item")).toHaveLength(7);
    expect(wrapper.text()).toContain("Overview");
    expect(wrapper.text()).toContain("AI Routing");
    expect(wrapper.text()).toContain("Traffic");
    await wrapper.findAll(".workspace-nav__item").at(1).trigger("click");
    expect(wrapper.vm.activeWorkspace).toBe("routing");
    expect(wrapper.find(".routing-workspace").exists()).toBe(true);
  });  it("exposes resources and commerce as direct workspaces", async () => {
    const wrapper = await mountAdmin();
    await wrapper.findAll(".workspace-nav__item").at(3).trigger("click");
    expect(wrapper.vm.activeWorkspace).toBe("resources");
    expect(wrapper.find(".ports-workspace").exists()).toBe(true);
    await wrapper.findAll(".workspace-nav__item").at(4).trigger("click");
    expect(wrapper.vm.activeWorkspace).toBe("commerce");
    expect(wrapper.find(".commerce-workspace").exists()).toBe(true);
  });
