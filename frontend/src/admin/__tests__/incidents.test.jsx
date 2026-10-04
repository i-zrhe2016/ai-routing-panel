import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App.jsx";
import IncidentRecords from "../components/IncidentRecords.jsx";
import { PanelProvider } from "../state/PanelProvider.jsx";
import { createFakeApi } from "./fixtures.jsx";

const incident = { id: "abc", source: "ai_upstream", target: "node:443", kind: "executor_error", probe_origin: "dedicated-probe", status: "completed", report_available: true, occurrences: 3, first_seen_at: "2026-10-03T01:00:00Z", last_seen_at: "2026-10-03T01:10:00Z", recovered_at: "2026-10-03T01:11:00Z" };
function mount(get) {
  const api = createFakeApi();
  const original = api.get;
  api.get = (url) => url.startsWith("/api/probe-incidents") ? get(url) : original(url);
  render(<PanelProvider api={api} pollInterval={0} insightsInterval={0}><IncidentRecords /></PanelProvider>);
}

describe("automatic fault records", () => {
  it("opens fault records from retained Hosts navigation without restoring diagnostics", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    const original = api.get;
    api.get = (url) => url === "/api/probe-incidents" ? Promise.resolve({ incidents: [incident] }) : original(url);
    render(<PanelProvider api={api} pollInterval={0} insightsInterval={0}><App /></PanelProvider>);
    await user.click(await screen.findByRole("button", { name: /^主机/ }));
    expect(await screen.findByRole("heading", { name: "Codex 故障记录" })).toBeTruthy();
    expect(await screen.findByText("分析完成")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "故障排查" })).toBeNull();
  });

  it("shows origin, source, lifecycle and escaped report with keyboard focus", async () => {
    const user = userEvent.setup();
    mount(async (url) => url.endsWith("/report") ? { report: '<script>alert("x")</script>\n## Facts' } : { incidents: [incident] });
    expect(await screen.findByText("分析完成")).toBeTruthy();
    expect(screen.getByText("探测已恢复")).toBeTruthy();
    expect(screen.getByText(/AI 上游 · 探测执行器错误 · 探测来源 dedicated-probe/)).toBeTruthy();
    expect(screen.getByText(/3 次失败/)).toBeTruthy();
    const open = screen.getByRole("button", { name: "打开 node:443 故障文档" });
    await user.click(open);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/<script>alert/)).toBeTruthy();
    expect(dialog.querySelector("script")).toBeNull();
    expect(document.activeElement.textContent).toBe("关闭文档");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(open);
  });

  it("shows pending records without allowing report download", async () => {
    mount(async () => ({ incidents: [{ ...incident, status: "queued", recovered_at: null, report_available: false }] }));
    expect(await screen.findByText("排队中")).toBeTruthy();
    expect(screen.getByText("故障持续中")).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开 node:443 故障文档" }).disabled).toBe(true);
  });

  it("handles initial loading and empty state", async () => {
    let resolve;
    mount(() => new Promise((done) => { resolve = done; }));
    expect(screen.getByRole("status").textContent).toContain("加载中");
    resolve({ incidents: [] });
    expect(await screen.findByText("暂无自动故障记录。")).toBeTruthy();
  });

  it("shows request and report errors honestly", async () => {
    const user = userEvent.setup();
    let failed = true;
    mount(async (url) => { if (failed || url.endsWith("/report")) throw new Error("网络不可用"); return { incidents: [incident] }; });
    expect(await screen.findByRole("alert")).toBeTruthy();
    failed = false;
    await user.click(screen.getByRole("button", { name: "刷新故障记录" }));
    await user.click(await screen.findByRole("button", { name: "打开 node:443 故障文档" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeTruthy();
  });
});
