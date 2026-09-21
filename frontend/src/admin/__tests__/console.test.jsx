import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "../App.jsx";
import { PanelProvider } from "../state/PanelProvider.jsx";
import { sameOriginLoginUrl } from "../../shared/url.js";
import { createFakeApi, makeDashboard } from "./fixtures.jsx";

function renderConsole(api, props = {}) {
  return render(
    <PanelProvider api={api} pollInterval={0} insightsInterval={0} {...props}>
      <App />
    </PanelProvider>,
  );
}

const WORKSPACE_LABELS = ["总览", "主机", "流量", "故障排查", "AI 路由", "交付", "订单与套餐", "可观测"];

describe("console shell", () => {
  it("renders every workspace and loads the dashboard on mount", async () => {
    const api = createFakeApi();
    renderConsole(api);

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/dashboard")).toBe(true));
    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    for (const label of WORKSPACE_LABELS) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }
  });

  it("loads the read-only insights history next to the dashboard", async () => {
    const api = createFakeApi();
    renderConsole(api);

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/insights?days=14")).toBe(true));
  });

  it("mounts only the active workspace", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);

    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    const nav = screen.getByRole("navigation", { name: "控制台工作区" });
    await user.click(within(nav).getByRole("button", { name: /主机/ }));

    expect(await screen.findByRole("heading", { name: "主机与数据面" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "今天的路径，是否值得信任？" })).toBeNull();
  });

  it("renders an error notice when the dashboard cannot be loaded", async () => {
    const api = createFakeApi({ failure: { url: "/api/dashboard", message: "加载失败。" } });
    renderConsole(api);

    expect(await screen.findByText("加载失败。")).toBeTruthy();
  });

  it("refreshes the dashboard on demand", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });

    const before = api.calls.filter((call) => call.url === "/api/dashboard").length;
    await user.click(screen.getByRole("button", { name: "刷新数据" }));

    await waitFor(() => {
      expect(api.calls.filter((call) => call.url === "/api/dashboard").length).toBeGreaterThan(before);
    });
  });

  it("surfaces the pending review count on the commerce workspace", async () => {
    const api = createFakeApi();
    renderConsole(api);

    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    const commerceButton = within(nav).getByRole("button", { name: /订单与套餐/ });
    expect(commerceButton.textContent).toContain("1");
  });
});

describe("sameOriginLoginUrl", () => {
  const location = { origin: "https://panel.example.com" };

  it("keeps same-origin redirects", () => {
    expect(sameOriginLoginUrl("/login?next=%2F", location)).toBe("https://panel.example.com/login?next=%2F");
  });

  it("rejects cross-origin and unparseable redirects", () => {
    expect(sameOriginLoginUrl("https://attacker.example/phish", location)).toBe("/login");
    expect(sameOriginLoginUrl("http://[invalid", location)).toBe("/login");
  });
});

describe("api client", () => {
  it("sends the CSRF token on mutations", async () => {
    const user = userEvent.setup();
    window.__BOOT__ = { csrf_token: "csrf-boot" };
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true, dashboard: makeDashboard() }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    renderConsole(undefined);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    await user.click(within(nav).getByRole("button", { name: /故障排查/ }));
    await user.click(await screen.findByRole("button", { name: "运行数据面体检" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === "/api/data-plane/diagnose");
      expect(call).toBeTruthy();
      expect(call[1].headers["X-CSRF-Token"]).toBe("csrf-boot");
    });

    vi.unstubAllGlobals();
    delete window.__BOOT__;
  });
});
