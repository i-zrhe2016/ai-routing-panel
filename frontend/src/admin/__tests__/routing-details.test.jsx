import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import RoutingWorkspace from "../workspaces/RoutingWorkspace.jsx";

const { usePanel } = vi.hoisted(() => ({ usePanel: vi.fn() }));
vi.mock("../state/PanelProvider.jsx", () => ({ usePanel }));

function snapshot(overrides = {}) {
  return {
    dataPlaneStatus: { configured: true },
    dnsFailoverStatus: { enabled: false },
    trafficRouting: { path: "normal_fallback", scenario: "AI 不可达，回退普通数据面" },
    aiRoutingStatus: {
      configured: true,
      manual_mode: "auto",
      route_status: "fallback",
      route_status_reason: "AI 候选业务探测失败，使用普通数据面",
      config_apply_status: "direct",
      config_changed: true,
      config_retried: false,
      probe_method: "socks_connect",
      last_probe_at_display: "2026-10-02 12:30:00",
      health_interval_seconds: 30,
      classification_interval_seconds: 3600,
      window_start_display: "2026-10-02 11:30:00",
      window_end_display: "2026-10-02 12:30:00",
      classification_cache: { status: "available", total_domains: 19, ai_domains: 8, non_ai_domains: 11 },
      cached_ai_domains_count: 8,
      pending_domains_without_classifier: 2,
      recent_domains: [
        { domain: "ai.example", hits: 42, classification: "ai", reason: "历史分类命中", source: "cache", traffic_route: { mode: "fallback", outbound_tag: "direct" } },
        { domain: "ordinary.example", hits: 7, classification: "not_ai", reason: "普通站点", source: "openai", traffic_route: { mode: "direct", outbound_tag: "direct" } },
        { domain: "pending.example", hits: 3, classification: "unknown", reason: "分类器不可用", source: "pending", traffic_route: { mode: "direct", outbound_tag: "direct" } },
      ],
      ai_candidates: [
        { index: 0, label: "主 AI", candidate_label: "primary.example:443", is_reachable: false, selected: true, checked_at: "2026-10-02T12:30:00+00:00", failure_reason: "SOCKS connect timeout" },
        { index: 1, label: "备 AI", candidate_label: "backup.example:443", is_reachable: false, selected: false },
        { index: 2, label: "第三 AI", candidate_label: "third.example:443", is_reachable: null, selected: false },
      ],
    },
    isBusy: () => false,
    switchAiRoutingMode: vi.fn(),
    ...overrides,
  };
}

function show(panel = snapshot()) {
  usePanel.mockReturnValue(panel);
  return render(<RoutingWorkspace />);
}

function metric(label) {
  return screen.getByText(label, { selector: ".cc-metric__label" }).closest("article");
}

describe("detailed AI routing", () => {
  it("reports the effective fallback outlet separately from its failed selected target and shows every candidate", () => {
    show();
    expect(within(metric("当前出口")).getByText("普通数据面")).toBeTruthy();
    expect(within(metric("选中目标")).getByText("主 AI")).toBeTruthy();
    const table = screen.getByRole("table", { name: "AI 候选节点状态" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("第三 AI")).toBeTruthy();
    expect(within(table).getByText("待探测")).toBeTruthy();
    expect(within(table).getByText("SOCKS connect timeout")).toBeTruthy();
    expect(within(table).getByText("2026-10-02T12:30:00+00:00")).toBeTruthy();
    expect(screen.getByText("自动候选，按探测结果参与选择")).toBeTruthy();
  });

  it("shows probe cadence, application evidence, cache totals and current domain decisions", () => {
    show();
    expect(screen.getByText("30 秒")).toBeTruthy();
    expect(screen.getByText("3600 秒")).toBeTruthy();
    expect(screen.getByText("已直接应用")).toBeTruthy();
    expect(screen.getByText("已生成配置变更 · 未触发配置重试")).toBeTruthy();
    expect(within(metric("历史分类域名")).getByText("19")).toBeTruthy();
    expect(screen.getByText("AI 8 · 普通 11")).toBeTruthy();
    expect(within(metric("待分类域名")).getByText("2")).toBeTruthy();
    const table = screen.getByRole("table", { name: "当前窗口域名路由" });
    expect(within(table).getByText("ai.example")).toBeTruthy();
    expect(within(table).getByText("历史分类命中")).toBeTruthy();
    expect(within(table).getByText("历史缓存")).toBeTruthy();
    expect(within(table).getByText("普通域名")).toBeTruthy();
    expect(within(table).getByText("OpenAI 分类器")).toBeTruthy();
    expect(within(table).getByText("普通数据面回退 · direct")).toBeTruthy();
  });

  it.each([
    ["unknown", "应用状态待确认"],
    ["delegated", "已委托同步，结果待确认"],
  ])("does not claim applied configuration or an effective AI outlet without evidence (%s)", (applyStatus, label) => {
    const panel = snapshot();
    panel.aiRoutingStatus.config_apply_status = applyStatus;
    panel.aiRoutingStatus.status = "waiting_report";
    panel.aiRoutingStatus.sync_error = "管理通道连接失败";
    panel.trafficRouting.path = "normal_ai";
    show(panel);
    expect(within(metric("当前出口")).getByText("出口待确认")).toBeTruthy();
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText("已直接应用")).toBeNull();
    const table = screen.getByRole("table", { name: "AI 候选节点状态" });
    expect(within(table).getByText("SOCKS connect timeout")).toBeTruthy();
    expect(within(table).queryByText("管理通道连接失败")).toBeNull();
    expect(screen.getByText("管理同步：管理通道连接失败")).toBeTruthy();
  });

  it("preserves confirmation and primary/backup mode controls while keeping extra candidates automatic", async () => {
    const user = userEvent.setup();
    const panel = snapshot();
    show(panel);
    expect(screen.getByRole("button", { name: "固定主 AI" }).disabled).toBe(false);
    await user.click(screen.getByRole("button", { name: "切换到备用 AI" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("健康探测仍会继续");
    expect(dialog.textContent).toContain("不可达时回退普通数据面");
    expect(panel.switchAiRoutingMode).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "确认切换" }));
    expect(panel.switchAiRoutingMode).toHaveBeenCalledWith("backup");
    const third = screen.getByText("第三 AI", { selector: ".cc-route-card strong" }).closest("article");
    expect(within(third).queryByRole("button")).toBeNull();
  });

  it("keeps missing reports and classification data explicitly unconfirmed", () => {
    show(snapshot({ trafficRouting: { path: "unknown" }, aiRoutingStatus: {} }));
    expect(within(metric("当前出口")).getByText("出口待确认")).toBeTruthy();
    expect(within(metric("配置应用")).getByText("应用状态待确认")).toBeTruthy();
    expect(within(metric("历史分类域名")).getByText("待确认")).toBeTruthy();
    expect(within(metric("待分类域名")).getByText("待确认")).toBeTruthy();
    expect(screen.getByText("当前窗口暂无域名报告；历史分类数量见上方数据库统计。")).toBeTruthy();
  });

  it("does not render candidate authentication values", () => {
    const panel = snapshot();
    Object.assign(panel.aiRoutingStatus.ai_candidates[0], { username: "dummy-private-user", password: "dummy-private-password", auth: "dummy-private-token" });
    const view = show(panel);
    for (const secret of ["dummy-private-user", "dummy-private-password", "dummy-private-token"]) {
      expect(view.container.textContent).not.toContain(secret);
    }
  });

  it("updates the effective outlet when probes and traffic path recover", () => {
    const panel = snapshot();
    const view = show(panel);
    expect(within(metric("当前出口")).getByText("普通数据面")).toBeTruthy();
    const recovered = { ...panel, trafficRouting: { path: "normal_ai" }, aiRoutingStatus: { ...panel.aiRoutingStatus, ai_candidates: [{ ...panel.aiRoutingStatus.ai_candidates[0], is_reachable: true }] } };
    usePanel.mockReturnValue(recovered);
    view.rerender(<RoutingWorkspace />);
    expect(within(metric("当前出口")).getByText("主 AI")).toBeTruthy();
  });
});
