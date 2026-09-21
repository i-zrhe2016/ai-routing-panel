import { render, screen, waitFor, within } from "@testing-library/react";
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
  await user.click(within(nav).getByRole("button", { name: new RegExp(name) }));
}

describe("hosts workspace", () => {
  it("lists every reported host with its management target", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
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
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
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
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "主机");

    await user.click(await screen.findByRole("button", { name: "数据面体检" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/data-plane/diagnose")).toBe(true));
    expect(await screen.findByText("体检完成。")).toBeTruthy();
  });

  it("confirms before restarting the data plane", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
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
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "流量");

    expect(await screen.findByRole("heading", { name: "流量与端口负载" })).toBeTruthy();
    expect(screen.getAllByText("900 B").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("img", { name: /每日流量/ }).length).toBeGreaterThan(0);
  });

  it("refetches history when the range changes", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "流量");

    await user.click(await screen.findByRole("button", { name: "7 天" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/insights?days=7")).toBe(true));
  });

  it("explains when history is unavailable instead of showing numbers", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({ failure: { url: "/api/insights?days=14", message: "历史数据加载失败。" } });
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "流量");

    expect(await screen.findByRole("heading", { name: "流量与端口负载" })).toBeTruthy();
    expect(screen.getAllByText("历史数据加载失败。").length).toBeGreaterThan(0);
  });
});

describe("diagnostics workspace", () => {
  it("lists probe failures, failover events and the diagnosis result", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "故障排查");

    expect(await screen.findByRole("heading", { name: "故障后排查" })).toBeTruthy();
    expect(screen.getAllByText("connection refused").length).toBeGreaterThan(0);
    expect(screen.getByText("切换 · 成功 · 控制面备用 Xray")).toBeTruthy();
    expect(screen.getByText("1 个端口最近探测失败")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "运行数据面体检" }));
    expect(await screen.findByText("Reality shortId")).toBeTruthy();
    expect(screen.getByText("不一致")).toBeTruthy();
  });

  it("renders the empty state when no probe history exists", async () => {
    const user = userEvent.setup();
    const insights = makeInsights({
      probes: { ports: [], recent_failures: [], total_checks: 0, unhealthy_checks: 0, uptime_ratio: "0.0" },
      failover_events: { events: [], total_events: 0 },
    });
    renderConsole(createFakeApi({ insights }));
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "故障排查");

    expect(await screen.findByText(/暂无探测记录/)).toBeTruthy();
    expect(screen.getByText("暂无切换事件。")).toBeTruthy();
  });
});

describe("delivery workspace", () => {
  it("creates a port with the form payload", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "交付");

    const form = await screen.findByRole("heading", { name: "新增端口" });
    const panel = form.closest("section");
    await user.type(within(panel).getByLabelText("监听端口"), "31100");
    await user.click(within(panel).getByRole("button", { name: "创建端口" }));

    await waitFor(() => {
      const call = api.calls.find((item) => item.url === "/api/ports" && item.method === "POST");
      expect(call).toBeTruthy();
      expect(call.json.listen_port).toBe("31100");
    });
  });

  it("filters the inventory and shows tenant delivery for the selected port", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "交付");

    expect(await screen.findByDisplayValue("vless://uuid@192.0.2.10:443")).toBeTruthy();
    await user.type(screen.getByPlaceholderText("搜索端口 / 备注 / 状态"), "客户B");
    expect(screen.getByText("客户B")).toBeTruthy();
    expect(screen.queryByText("客户A")).toBeNull();
  });

  it("rotates tenant credentials only after confirmation", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "交付");

    await user.click(await screen.findByRole("button", { name: "重置账号密码" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认重置" }));

    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/ports/1/rotate-tenant-credentials")).toBe(true));
  });
});

describe("commerce workspace", () => {
  it("blocks a rejection without a reason and posts a fulfilment note", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "订单与套餐");

    await user.click(await screen.findByRole("tab", { name: "订单审核" }));
    await user.click(screen.getByRole("button", { name: "驳回订单" }));
    expect(await screen.findByText("驳回订单前请填写原因。")).toBeTruthy();
    expect(api.calls.some((call) => call.url === "/api/orders/3/reject")).toBe(false);

    await user.type(screen.getByLabelText("审核备注 / 驳回原因"), "金额不匹配");
    await user.click(screen.getByRole("button", { name: "审核通过并开通" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "通过并开通" }));

    await waitFor(() => {
      const call = api.calls.find((item) => item.url === "/api/orders/3/fulfill");
      expect(call?.json.review_note).toBe("金额不匹配");
    });
  });

  it("saves commerce settings", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "订单与套餐");

    await user.click(await screen.findByRole("tab", { name: "商业设置" }));
    await user.click(screen.getByRole("button", { name: "保存商业设置" }));
    await waitFor(() => expect(api.calls.some((call) => call.url === "/api/commerce-settings" && call.method === "PUT")).toBe(true));
  });
});

describe("routing workspace", () => {
  it("switches the AI route mode after confirmation", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    renderConsole(api);
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
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
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "AI 路由");

    expect(await screen.findByRole("button", { name: "强制直出" })).toBeTruthy();
  });
});

describe("observability workspace", () => {
  it("embeds the Grafana panels when the base URL is configured", async () => {
    const user = userEvent.setup();
    renderConsole(createFakeApi());
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "可观测");

    const frames = await screen.findAllByTitle(/使用率|流量|速率/);
    expect(frames.length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Grafana 观测面板/ })).toBeTruthy();
  });

  it("shows the configuration hint without a Grafana URL", async () => {
    const user = userEvent.setup();
    const dashboard = makeDashboard();
    dashboard.meta = { ...dashboard.meta, grafana_url: "" };
    renderConsole(createFakeApi({ dashboard }));
    await screen.findByRole("heading", { name: "今天的路径，是否值得信任？" });
    await openWorkspace(user, "可观测");

    expect(await screen.findByText("监控未配置")).toBeTruthy();
  });
});
