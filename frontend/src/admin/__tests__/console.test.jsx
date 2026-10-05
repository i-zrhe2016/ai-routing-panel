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

const WORKSPACE_LABELS = ["总览", "主机", "流量", "AI 路由", "交付", "流量拓扑"];

describe("console shell", () => {
  it("renders every workspace and loads the dashboard on mount", async () => {
    const api = createFakeApi();
    renderConsole(api);

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/dashboard")).toBe(true));
    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    expect(within(nav).getAllByRole("button")).toHaveLength(6);
    for (const label of WORKSPACE_LABELS) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }
  });

  it("groups all workspaces and gives every active page one primary heading", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    expect(within(nav).getByText("运行监控")).toBeTruthy();
    expect(within(nav).getByText("配置与业务")).toBeTruthy();
    for (const label of WORKSPACE_LABELS) {
      await user.click(within(nav).getByRole("button", { name: new RegExp(`^${label}(?:\\s|$)`) }));
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    }
  });

  it("removes diagnostics, commerce and embedded monitoring even when legacy metadata is supplied", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    const { container } = renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    const nav = screen.getByRole("navigation", { name: "控制台工作区" });
    expect(within(nav).queryByRole("button", { name: /订单与套餐|可观测|故障排查/ })).toBeNull();
    expect(screen.queryByText("待审订单")).toBeNull();
    expect(screen.queryByRole("button", { name: "故障排查" })).toBeNull();
    expect(screen.queryByRole("button", { name: "查看订单" })).toBeNull();
    for (const label of WORKSPACE_LABELS) {
      await user.click(within(nav).getByRole("button", { name: new RegExp(`^${label}(?:\\s|$)`) }));
      expect(container.querySelector("iframe")).toBeNull();
      expect(screen.queryByText(/Grafana|Prometheus|新增套餐|商业设置|订单审核|故障后排查/)).toBeNull();
    }
    expect(api.calls.some((call) => /\/api\/(plans|orders|commerce-settings)/.test(call.url))).toBe(false);
  });

  it("blocks duplicate manual refreshes while a request is pending", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    const refresh = await screen.findByRole("button", { name: "刷新数据" });
    let finish;
    api.get = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    await user.click(refresh);
    const busy = screen.getByRole("button", { name: "正在刷新" });
    expect(busy.disabled).toBe(true);
    await user.click(busy);
    expect(api.get).toHaveBeenCalledTimes(1);
    finish({ ok: true, dashboard: makeDashboard() });
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新数据" }).disabled).toBe(false));
  });

  it("loads the read-only insights history next to the dashboard", async () => {
    const api = createFakeApi();
    renderConsole(api);

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/insights?days=1")).toBe(true));
  });

  it("mounts only the active workspace", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);

    await screen.findByRole("heading", { name: "系统总览" });
    const nav = screen.getByRole("navigation", { name: "控制台工作区" });
    await user.click(within(nav).getByRole("button", { name: /主机/ }));

    expect(await screen.findByRole("heading", { name: "主机与数据面" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "系统总览" })).toBeNull();
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
    await screen.findByRole("heading", { name: "系统总览" });

    const before = api.calls.filter((call) => call.url === "/api/dashboard").length;
    await user.click(screen.getByRole("button", { name: "刷新数据" }));

    await waitFor(() => {
      expect(api.calls.filter((call) => call.url === "/api/dashboard").length).toBeGreaterThan(before);
    });
  });

  it("opens the retained topology from the overview attention panel", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await user.click(await screen.findByRole("button", { name: "查看流量路径" }));
    expect(await screen.findByRole("heading", { name: "流量拓扑", level: 1 })).toBeTruthy();
  });

  it("opens the full traffic topology from the overview", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await user.click(await screen.findByRole("button", { name: "查看完整拓扑" }));
    expect(await screen.findByRole("heading", { name: "流量拓扑", level: 1 })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "系统总览" })).toBeNull();
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
    await user.click(within(nav).getByRole("button", { name: /主机/ }));
    await user.click(await screen.findByRole("button", { name: "数据面体检" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === "/api/data-plane/diagnose");
      expect(call).toBeTruthy();
      expect(call[1].headers["X-CSRF-Token"]).toBe("csrf-boot");
    });

    vi.unstubAllGlobals();
    delete window.__BOOT__;
  });
});


it("failed scope API keeps the existing switch value and exposes the error", async () => {
  const user = userEvent.setup();
  const api = createFakeApi();
  api.post = vi.fn(async () => { throw new Error("路由重载失败，请重试。"); });
  renderConsole(api);
  const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
  await user.click(within(nav).getByRole("button", { name: /^AI 路由/ }));
  await user.click(screen.getByRole("switch", { name: "启用全部转发到 AI 节点" }));
  expect(await screen.findByText("路由重载失败，请重试。")).toBeTruthy();
  const control = screen.getByRole("switch", { name: "启用全部转发到 AI 节点" });
  expect(control.getAttribute("aria-checked")).toBe("false");
  expect(control.disabled).toBe(false);
  expect(api.post).toHaveBeenCalledWith("/api/ai-routing/scope", { traffic_scope: "all" });
});
