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
  it("organizes the console into three workspaces and exposes contextual tabs", async () => {
    const wrapper = await mountAdmin();
    expect(wrapper.findAll(".workspace-nav__item")).toHaveLength(3);
    expect(wrapper.text()).toContain("运行总览");
    await wrapper.findAll(".workspace-nav__item").at(2).trigger("click");
    expect(wrapper.vm.activeWorkspace).toBe("infra");
    expect(wrapper.find('[role="tablist"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Prometheus 与 Grafana");
  });

  it("switches the resources workspace between ports and commerce", async () => {
    const wrapper = await mountAdmin();
    await wrapper.findAll(".workspace-nav__item").at(1).trigger("click");
    expect(wrapper.vm.activeWorkspace).toBe("resources");
    expect(wrapper.find(".workspace-tabs").text()).toContain("端口与租户");
    await wrapper.get('.workspace-tabs [role="tab"]:nth-child(2)').trigger("click");
    expect(wrapper.vm.resourceTab).toBe("commerce");
    expect(wrapper.find(".commerce-workspace").exists()).toBe(true);
  });

  it("fetches the dashboard on mount and renders a port card", async () => {
    const wrapper = await mountAdmin();
    expect(fetchMock).toHaveBeenCalledWith("/api/dashboard", expect.objectContaining({ method: "GET" }));
    expect(wrapper.text()).toContain("端口 31098");
    expect(wrapper.text()).toContain("客户A");
  });

  it("renders a StatusPill for the port status and selects the first port", async () => {
    const wrapper = await mountAdmin();
    expect(wrapper.findComponent({ name: "StatusPill" }).exists()).toBe(true);
    expect(wrapper.vm.selectedPort && wrapper.vm.selectedPort.id).toBe(1);
  });

  it("renders CopyFields for the selected port subscription links", async () => {
    const wrapper = await mountAdmin();
    const values = wrapper.findAll("input[data-copy-value]").map((node) => node.attributes("data-copy-value"));
    expect(values).toContain("clash://example/abc");
    expect(values).toContain("vless://uuid@1.2.3.4:443");
  });

  it("renders plan and order data", async () => {
    const wrapper = await mountAdmin();
    expect(wrapper.text()).toContain("基础套餐");
    expect(wrapper.text()).toContain("ODR2606210001");
  });

  it("formats traffic via humanBytes in the overview", async () => {
    const wrapper = await mountAdmin();
    // total = 512 + 512 = 1024 -> "1.00 KB"
    expect(wrapper.text()).toContain("1.00 KB");
  });

  it("renders the AI primary/backup control and switches to the backup after confirmation", async () => {
    const wrapper = await mountAdmin();
    const control = wrapper.get('[data-testid="ai-route-control-overview"]');
    expect(control.text()).toContain("主 AI 节点");
    expect(control.text()).toContain("备用 AI 节点");
    expect(control.text()).toContain("nat.qq.pw:27166");
    expect(control.text()).toContain("100.87.76.6:27166");
    await control.get('[data-testid="ai-switch-backup"]').trigger("click");
    expect(control.text()).toContain("确认备用 AI 节点");
    await control.get('[data-testid="ai-confirm-submit"]').trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-routing/switch",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ mode: "backup" }) }),
    );
  });

  it("keeps the topology focused on the current path without duplicating AI actions", async () => {
    const wrapper = await mountAdmin();
    expect(wrapper.text()).toContain("三节点流量切换拓扑");
    expect(wrapper.find('[data-testid="ai-route-control-overview"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="ai-route-control-detail"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="ai-force-direct"]')).toHaveLength(1);
  });

  it("requires confirmation before enabling the advanced data-plane direct fallback", async () => {
    const wrapper = await mountAdmin();
    const control = wrapper.get('[data-testid="ai-route-control-overview"]');
    await control.get('[data-testid="ai-force-direct"]').trigger("click");
    expect(control.text()).toContain("启用应急直出");
    await control.get('[data-testid="ai-confirm-submit"]').trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-routing/switch",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ mode: "forced_fallback" }) }),
    );
  });

  it("keeps an unreachable AI candidate selectable and shows the risk in confirmation", async () => {
    const unreachableDashboard = structuredClone(dashboard);
    unreachableDashboard.meta.ai_routing_status.ai_candidates[1].is_reachable = false;
    fetchMock.mockImplementation((url) => jsonResp({
      ok: true,
      message: "ok",
      dashboard: unreachableDashboard,
    }));

    const wrapper = await mountAdmin();
    const control = wrapper.get('[data-testid="ai-route-control-overview"]');
    const switchButton = control.get('[data-testid="ai-switch-backup"]');
    expect(switchButton.attributes("disabled")).toBeUndefined();
    await switchButton.trigger("click");
    expect(control.text()).toContain("不可达");
  });

  it("restores automatic probing from a manually fixed backup", async () => {
    const manualDashboard = structuredClone(dashboard);
    manualDashboard.meta.ai_routing_status.manual_mode = "backup";
    manualDashboard.meta.ai_routing_status.manual_mode_label = "人工固定备用 AI";
    manualDashboard.meta.ai_routing_status.ai_candidates[0].selected = false;
    manualDashboard.meta.ai_routing_status.ai_candidates[1].selected = true;
    fetchMock.mockImplementation((url) => jsonResp({
      ok: true,
      message: "ok",
      dashboard: manualDashboard,
    }));

    const wrapper = await mountAdmin();
    const control = wrapper.get('[data-testid="ai-route-control-overview"]');
    await control.get('[data-testid="ai-restore-auto"]').trigger("click");
    await control.get('[data-testid="ai-confirm-submit"]').trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-routing/switch",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ mode: "auto" }) }),
    );
  });

  it("does not render a fake backup action when only one AI candidate is configured", async () => {
    const singleCandidateDashboard = structuredClone(dashboard);
    singleCandidateDashboard.meta.ai_routing_status.ai_candidates = [
      singleCandidateDashboard.meta.ai_routing_status.ai_candidates[0],
    ];
    fetchMock.mockImplementation((url) => jsonResp({
      ok: true,
      message: "ok",
      dashboard: singleCandidateDashboard,
    }));

    const wrapper = await mountAdmin();
    const control = wrapper.get('[data-testid="ai-route-control-overview"]');
    expect(control.find('[data-testid="ai-switch-backup"]').exists()).toBe(false);
    expect(control.text()).toContain("主 AI 节点");
  });

  it("fulfills an order via POST /api/orders/<id>/fulfill", async () => {
    const wrapper = await mountAdmin();
    const fulfillBtn = wrapper.findAll("button").find((b) => b.text().includes("审核通过并开通"));
    expect(fulfillBtn).toBeTruthy();
    await fulfillBtn.trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith("/api/orders/5/fulfill", expect.objectContaining({ method: "POST" }));
  });

  it("creates a port via POST /api/ports", async () => {
    const wrapper = await mountAdmin();
    wrapper.vm.createForm.listen_port = "31200";
    // The create button is type=submit; jsdom doesn't translate a click into a
    // form submit, so trigger the form's @submit.prevent directly.
    const createForm = wrapper.findAll("form").find((f) => f.text().includes("创建端口"));
    expect(createForm).toBeTruthy();
    await createForm.trigger("submit");
    await flushPromises();
    const portsCalls = fetchMock.mock.calls.filter((c) => c[0] === "/api/ports" && c[1] && c[1].method === "POST");
    expect(portsCalls).toHaveLength(1);
  });

  it("refreshes and selects the existing port when create returns a listen-port conflict", async () => {
    fetchMock.mockImplementation((url, init) => {
      if (url === "/api/ports" && init?.method === "POST") {
        return jsonResp({ ok: false, message: "监听端口已存在，请更换其他端口。" }, 409);
      }
      return jsonResp({ ok: true, message: "ok", dashboard });
    });
    const wrapper = await mountAdmin();
    wrapper.vm.filters.query = "missing";
    wrapper.vm.filters.status = "disabled";
    wrapper.vm.createForm.listen_port = "31098";
    const createForm = wrapper.findAll("form").find((f) => f.text().includes("创建端口"));

    await createForm.trigger("submit");
    await flushPromises();

    expect(fetchMock.mock.calls.filter((c) => c[0] === "/api/ports")).toHaveLength(1);
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/client-errors")).toBe(false);
    expect(wrapper.vm.selectedPortId).toBe(1);
    expect(wrapper.vm.filters).toMatchObject({ query: "", status: "all" });
    expect(wrapper.vm.flash).toMatchObject({ message: "监听端口已存在，已选中已有端口。", level: "info" });
    expect(wrapper.vm.createForm.listen_port).toBe("");
  });

  it("refreshes the dashboard when deleting a port that is already gone", async () => {
    const emptyDashboard = structuredClone(dashboard);
    emptyDashboard.ports = [];
    emptyDashboard.summary = {
      ...emptyDashboard.summary,
      total_ports: 0,
      active_ports: 0,
      total_connections: 0,
      total_bytes_received: 0,
      total_bytes_sent: 0,
    };
    let dashboardLoads = 0;
    fetchMock.mockImplementation((url, init) => {
      if (url === "/api/ports/1" && init?.method === "DELETE") {
        return jsonResp({ ok: false, message: "端口记录不存在。" }, 400);
      }
      if (url === "/api/dashboard") {
        dashboardLoads += 1;
        return jsonResp({ ok: true, message: "ok", dashboard: dashboardLoads === 1 ? dashboard : emptyDashboard });
      }
      return jsonResp({ ok: true, message: "ok", dashboard });
    });
    const wrapper = await mountAdmin();
    const deleteButton = wrapper.findAll("button").find((b) => b.text().includes("删除端口"));

    await deleteButton.trigger("click");
    await flushPromises();

    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/client-errors")).toBe(false);
    expect(wrapper.vm.ports).toHaveLength(0);
    expect(wrapper.vm.flash).toMatchObject({ message: "端口已不存在，列表已刷新。", level: "info" });
  });
});
