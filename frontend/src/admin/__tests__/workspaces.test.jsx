import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "../App.jsx";
import { PanelProvider } from "../state/PanelProvider.jsx";
import { createFakeApi, makeDashboard, makeInsights } from "./fixtures.jsx";

function renderConsole(api) {
  return render(
    <PanelProvider api={api} pollInterval={0} insightsInterval={0}>
      <App />
    </PanelProvider>,
  );
}

async function openWorkspace(user, name) {
  const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
  await user.click(within(nav).getByRole("button", { name: new RegExp(`^${name}(?:\\s|$)`) }));
}

describe("hosts workspace", () => {
  it("lists every reported host with its management target", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "主机");

    expect(await screen.findByRole("heading", { name: "主机与数据面" })).toBeTruthy();
    expect(screen.getAllByText("docker:xray").length).toBeGreaterThan(0);
    expect(screen.getAllByText("root@ai-node").length).toBeGreaterThan(0);
    expect(screen.getByText("控制面本机")).toBeTruthy();
    expect(screen.getByText("127.0.0.1:10085")).toBeTruthy();
  });

  it("offers restart only where the host reports restart support", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "主机");

    const cards = await screen.findAllByRole("article");
    const restartButtons = screen.getAllByRole("button", { name: "重启" });
    expect(restartButtons).toHaveLength(2);
    const backup = cards.find((card) => card.textContent.includes("控制面备用"));
    expect(within(backup).queryByRole("button", { name: "重启" })).toBeNull();
  });

  it("runs the data-plane diagnosis and renders the returned tables", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "主机");

    await user.click(await screen.findByRole("button", { name: "数据面体检" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/data-plane/diagnose")).toBe(true));
    expect(await screen.findByText("体检完成。")).toBeTruthy();
  });

  it("confirms before restarting the data plane", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "主机");

    await user.click((await screen.findAllByRole("button", { name: "重启" }))[0]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("确认重启");
    expect(api.calls.some((call) => call.url === "/api/data-plane/restart")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "确认重启" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/data-plane/restart")).toBe(true));
  });
});

describe("traffic workspace", () => {
  it("renders the fleet totals and per-port history from the insights payload", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "流量");

    expect(await screen.findByRole("heading", { name: "流量与端口负载" })).toBeTruthy();
    expect(screen.getAllByText("900 B").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("img", { name: /每日流量/ }).length).toBeGreaterThan(0);
  });

  it("refetches history when the range changes", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "流量");

    await user.click(await screen.findByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/insights?days=7")).toBe(true));
  });

  it("explains when history is unavailable instead of showing numbers", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({ failure: { url: "/api/insights?days=1", message: "历史数据加载失败。" } });
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "流量");

    expect(await screen.findByRole("heading", { name: "流量与端口负载" })).toBeTruthy();
    expect(screen.getAllByText("历史数据加载失败。").length).toBeGreaterThan(0);
  });
});

describe("delivery workspace", () => {
  it("creates a port with the form payload", async () => {
    const user = userEvent.setup();
    const dashboard = makeDashboard();
    dashboard.meta.timezone_label = "";
    const api = createFakeApi({ dashboard });
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    expect(screen.getByText("北京时间（UTC+08:00）")).toBeTruthy();
    await openWorkspace(user, "交付");

    const form = await screen.findByRole("heading", { name: "新增端口" });
    const panel = form.closest("section");
    await user.type(within(panel).getByLabelText("监听端口（可选）"), "31100");
    fireEvent.change(within(panel).getByLabelText("到期时间（北京时间（UTC+08:00））"), {
      target: { value: "2026-10-02T00:00" },
    });
    await user.click(within(panel).getByRole("button", { name: "创建端口" }));

    await waitFor(() => {
      const call = api.calls.find((item) => item.url === "/api/ports" && item.method === "POST");
      expect(call).toBeTruthy();
      expect(call.json.listen_port).toBe("31100");
      expect(call.json.expires_at).toBe("2026-10-02T00:00");
    });
  });

  it("creates a tenant without entering a port and selects the server-assigned tenant", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    const dashboard = makeDashboard();
    const created = { ...dashboard.ports[0], id: 3, listen_port: 31000, note: "自动租户" };
    api.post = async (url, json) => {
      api.calls.push({ method: "POST", url, json });
      return {
        ok: true,
        message: "端口已创建并写入 Xray。",
        created_port_id: created.id,
        dashboard: { ...dashboard, ports: [created, ...dashboard.ports] },
      };
    };
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "交付");
    const panel = screen.getByRole("heading", { name: "新增端口" }).closest("section");
    const portInput = within(panel).getByLabelText("监听端口（可选）");
    expect(portInput.required).toBe(false);
    expect(portInput.placeholder).toBe("留空自动分配");
    await user.type(screen.getByPlaceholderText("搜索端口 / 备注 / 状态"), "不存在的租户");
    await user.type(within(panel).getByLabelText("租户备注"), "自动租户");
    await user.click(within(panel).getByRole("button", { name: "创建端口" }));

    expect(await screen.findByRole("heading", { name: "端口 31000" })).toBeTruthy();
    expect(screen.getByPlaceholderText("搜索端口 / 备注 / 状态").value).toBe("");
    expect(api.calls.find((call) => call.url === "/api/ports").json).toMatchObject({ listen_port: "", note: "自动租户" });
    expect(within(panel).getByLabelText("租户备注").value).toBe("");
    expect(portInput.value).toBe("");
  });

  it("keeps the tenant form when the automatic port range is exhausted", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({ failure: { url: "/api/ports", message: "自动分配端口范围已耗尽。" } });
    api.post = async () => { throw new Error("自动分配端口范围已耗尽。"); };
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "交付");
    const panel = screen.getByRole("heading", { name: "新增端口" }).closest("section");
    await user.type(within(panel).getByLabelText("租户备注"), "待创建租户");
    await user.click(within(panel).getByRole("button", { name: "创建端口" }));
    expect(await screen.findByText("自动分配端口范围已耗尽。")).toBeTruthy();
    expect(within(panel).getByLabelText("租户备注").value).toBe("待创建租户");
    expect(within(panel).getByRole("button", { name: "创建端口" }).disabled).toBe(false);
  });

  it("filters the inventory and shows tenant delivery for the selected port", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "交付");

    expect(await screen.findByDisplayValue("vless://uuid@192.0.2.10:443")).toBeTruthy();
    for (const label of ["租户登录地址", "租户用户名", "租户密码"]) {
      expect(screen.queryByRole("textbox", { name: label })).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "重置面板地址" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重置账号密码" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Clash 订阅" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "V2Ray 订阅" })).toBeTruthy();
    await user.type(screen.getByPlaceholderText("搜索端口 / 备注 / 状态"), "客户B");
    expect(screen.getByText("客户B")).toBeTruthy();
    expect(screen.queryByText("客户A")).toBeNull();
  });

  it("rotates the subscription address only after confirmation", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "交付");

    await user.click(await screen.findByRole("button", { name: "重置订阅地址" }));
    const dialog = await screen.findByRole("dialog");
    expect(api.calls.some((call) => call.url === "/api/ports/1/rotate-subscription-token")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "确认重置" }));

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/ports/1/rotate-subscription-token")).toBe(true));
  });
});

describe("routing workspace", () => {
  it("switches the AI route mode after confirmation", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "AI 路由");

    expect(await screen.findByRole("heading", { name: "路由决策与故障切换" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "切换到备用 AI" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("203.0.113.10:27166");
    await user.click(within(dialog).getByRole("button", { name: "确认切换" }));

    await waitFor(() => {
      const call = api.calls.find((item) => item.url === "/api/ai-routing/switch");
      expect(call?.json.mode).toBe("backup");
    });
  });

  it("keeps the forced-direct action behind the advanced section", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "系统总览" });
    await openWorkspace(user, "AI 路由");

    expect(await screen.findByRole("button", { name: "强制直出" })).toBeTruthy();
  });
});
