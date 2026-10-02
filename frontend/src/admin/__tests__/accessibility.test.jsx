import { useState } from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App.jsx";
import { ConfirmDialog } from "../components/ConfirmDialog.jsx";
import { PanelProvider } from "../state/PanelProvider.jsx";
import { createFakeApi } from "./fixtures.jsx";

const originalWidth = window.innerWidth;
afterEach(() => {
  cleanup();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth });
  document.body.style.overflow = "";
});

function ConfirmationHarness({ busy = false, onConfirm = vi.fn(), onCancel = vi.fn() }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>重置测试凭据</button>
      <ConfirmDialog
        request={open ? { title: "确认重置凭据", body: "旧凭据会立即失效。", tone: "danger", confirmLabel: "确认重置" } : null}
        busy={busy}
        onConfirm={onConfirm}
        onCancel={() => { onCancel(); setOpen(false); }}
      />
    </>
  );
}

function renderMobileConsole() {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
  const api = createFakeApi();
  render(<PanelProvider api={api} pollInterval={0} insightsInterval={0}><App /></PanelProvider>);
  return api;
}

describe("confirmation keyboard and dismissal", () => {
  it("focuses cancel, contains Tab in both directions, and restores focus after Escape", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmationHarness onConfirm={onConfirm} />);
    const trigger = screen.getByRole("button", { name: "重置测试凭据" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "确认重置凭据" });
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认重置" });
    expect(document.activeElement).toBe(cancel);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(confirm);
    await user.tab();
    expect(document.activeElement).toBe(cancel);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("dismisses only backdrop clicks, without running the destructive action", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmationHarness onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: "重置测试凭据" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByText("旧凭据会立即失效。"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(dialog.parentElement);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("keeps an in-flight confirmation open on Escape, backdrop click, and Tab", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<ConfirmationHarness busy onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: "重置测试凭据" }));
    const dialog = screen.getByRole("dialog");
    await user.keyboard("{Escape}");
    await user.click(dialog.parentElement);
    await user.tab();
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe("mobile navigation", () => {
  it("removes closed navigation from keyboard access and restores menu focus on Escape", async () => {
    const user = userEvent.setup();
    renderMobileConsole();
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "控制台工作区" })).toBeNull());
    const trigger = screen.getByRole("button", { name: "打开控制台导航" });
    await user.click(trigger);
    const drawer = screen.getByRole("dialog", { name: "控制台导航" });
    const close = within(drawer).getByRole("button", { name: "关闭导航" });
    expect(document.activeElement).toBe(close);
    await user.tab({ shift: true });
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(close);
    await user.tab();
    expect(document.activeElement).toBe(close);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "控制台工作区" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes the drawer after selecting a workspace and unlocks page scrolling", async () => {
    const user = userEvent.setup();
    renderMobileConsole();
    await user.click(await screen.findByRole("button", { name: "打开控制台导航" }));
    const drawer = screen.getByRole("dialog", { name: "控制台导航" });
    expect(document.body.style.overflow).toBe("hidden");
    await user.click(within(drawer).getByRole("button", { name: /主机/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByRole("heading", { name: "主机与数据面", level: 1 })).toBeTruthy();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});

describe("delivery and table access", () => {
  it("names every credential field and allows keyboard focus on a wide routing table", async () => {
    const user = userEvent.setup();
    render(<PanelProvider api={createFakeApi()} pollInterval={0} insightsInterval={0}><App /></PanelProvider>);
    const nav = await screen.findByRole("navigation", { name: "控制台工作区" });
    await user.click(within(nav).getByRole("button", { name: /交付/ }));
    expect(screen.getByRole("textbox", { name: "租户用户名" }).readOnly).toBe(true);
    expect(screen.getByRole("textbox", { name: "租户密码" }).readOnly).toBe(true);
    await user.click(within(nav).getByRole("button", { name: /AI 路由/ }));
    const region = screen.getByRole("region", { name: "AI 候选节点状态" });
    expect(region.tabIndex).toBe(0);
    region.focus();
    expect(document.activeElement).toBe(region);
  });
});
