import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import TokenApp from "../TokenApp.vue";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function fetchOnce(status, bodyObj) {
  return vi.fn(() =>
    Promise.resolve({
      status,
      ok: status >= 200 && status < 300,
      text: () => Promise.resolve(JSON.stringify(bodyObj)),
    }),
  );
}

const SUB = {
  listen_port: 31001,
  status: "active",
  status_label: "正常",
  traffic_usage_bytes: 512,
  traffic_limit_bytes: 1024,
  expires_at_display: "2026-07-01",
  note: "Tenant A",
  renewal_allowed: false,
  access: {
    tenant_subscription_clash_url: "https://node/clash",
    tenant_subscription_v2ray_url: "https://node/v2ray",
    share_link: "vless://abc",
    tenant_username: "tu",
    tenant_password: "tp",
  },
};

describe("TokenApp (tenant token mode)", () => {
  it("renders the subscription read-only (links + ring, no renew) when authed", async () => {
    vi.stubGlobal("fetch", fetchOnce(200, { ok: true, data: { subscription: SUB } }));
    const wrapper = mount(TokenApp, { props: { tenantToken: "tok", csrfToken: "c" } });
    await flushPromises();
    const html = wrapper.html();
    expect(html).toContain("端口 31001");
    expect(html).toContain("https://node/clash");
    expect(html).toContain("vless://abc");
    expect(wrapper.find('[data-testid="traffic-ring"]').exists()).toBe(true);
    // token mode is read-only: no renew action
    expect(wrapper.find('[data-testid="renew-btn"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tenant-login"]').exists()).toBe(false);
  });

  it("renders the inline login card on 401", async () => {
    vi.stubGlobal("fetch", fetchOnce(401, { ok: false, code: "auth_required" }));
    const wrapper = mount(TokenApp, { props: { tenantToken: "tok", csrfToken: "c" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="tenant-login"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tenant-username"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tenant-login-btn"]').exists()).toBe(true);
  });
});
