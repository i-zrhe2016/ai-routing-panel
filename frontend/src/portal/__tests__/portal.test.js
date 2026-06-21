import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const postForm = vi.fn();

// The views import { api } from "../store.js"; mock it so no network is hit and
// no window.__BOOT__ is needed.
vi.mock("../store.js", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), postForm: (...a) => postForm(...a) },
  portal: { me: { email: "user@example.com" }, csrfToken: "t" },
  refreshMe: vi.fn(),
  logout: vi.fn(),
}));

import OrdersView from "../views/OrdersView.vue";
import SubscriptionDetailView from "../views/SubscriptionDetailView.vue";

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  postForm.mockReset();
});

const SUBSCRIPTION = {
  id: 7,
  listen_port: 35000,
  status: "quota",
  status_label: "流量用尽",
  renewal_allowed: true,
  traffic_usage_bytes: 1024,
  traffic_limit_bytes: 2048,
  note: "测试订阅",
  expires_at_display: "2026-07-01",
  access: {
    tenant_subscription_clash_url: "https://x/clash",
    tenant_subscription_v2ray_url: "https://x/v2ray",
    share_link: "vless://abc",
    tenant_username: "tu",
    tenant_password: "tp",
  },
};

describe("SubscriptionDetailView (former tenant panel)", () => {
  it("renders the three subscription CopyFields, the traffic ring, and a gated renew button", async () => {
    get.mockResolvedValue({ data: { subscription: SUBSCRIPTION } });
    const wrapper = mount(SubscriptionDetailView, { props: { id: 7 } });
    await flushPromises();

    expect(get).toHaveBeenCalledWith("/api/customer/subscriptions/7");
    expect(wrapper.findAll(".copy-field").length).toBe(3);
    expect(wrapper.find('[data-testid="traffic-ring"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("续费"); // renew button shown when renewal_allowed
    // The link lives in the CopyField's input value (not textContent).
    expect(wrapper.find(".copy-field__value").attributes("data-copy-value")).toBe("https://x/clash");
  });

  it("hides the renew button when renewal is not allowed", async () => {
    get.mockResolvedValue({ data: { subscription: { ...SUBSCRIPTION, renewal_allowed: false } } });
    const wrapper = mount(SubscriptionDetailView, { props: { id: 7 } });
    await flushPromises();
    expect(wrapper.text()).not.toContain("续费");
  });

  it("posts to the renew endpoint when renew is invoked", async () => {
    get.mockResolvedValue({ data: { subscription: SUBSCRIPTION } });
    post.mockResolvedValue({ message: "续费订单已创建。", data: { order_no: "ODR1" } });
    const wrapper = mount(SubscriptionDetailView, { props: { id: 7 } });
    await flushPromises();
    await wrapper.vm.renew();
    expect(post).toHaveBeenCalledWith("/api/customer/subscriptions/7/renew");
  });
});

describe("OrdersView", () => {
  it("renders order rows with a status pill", async () => {
    get.mockResolvedValue({
      data: {
        orders: [
          { order_no: "ODR9", status: "pending_payment", status_tone: "warning", status_label: "待付款", plan_name_snapshot: "基础套餐" },
        ],
      },
    });
    const wrapper = mount(OrdersView, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="order-row"]').exists()).toBe(true);
    expect(wrapper.find(".status-pill").text()).toContain("待付款");
    expect(wrapper.text()).toContain("ODR9");
  });
});
