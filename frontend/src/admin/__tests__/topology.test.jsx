import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { buildTrafficTopology } from "../lib/topology.js";
import TrafficTopology from "../components/TrafficTopology.jsx";

function snapshot(path, candidates = [
  { index: 0, label: "东京 AI", upstream_host: "tokyo.example.test", upstream_port: 443, selected: true, is_reachable: true },
  { index: 1, label: "备用 AI", upstream_host: "backup.example.test", selected: false, is_reachable: false },
]) {
  return {
    trafficRouting: { path, label: `当前 ${path}`, scenario: "服务端路由决策" },
    dataPlaneStatus: { configured: true, reachable: false },
    aiNodeStatus: { reachable: false },
    dnsFailoverStatus: { enabled: true, configured: true, control_plane_backup_xray_enabled: true },
    aiRoutingStatus: { ai_candidates: candidates },
  };
}
const activeEdges = (graph) => graph.edges.filter((edge) => edge.state === "active").map(({ from, to }) => `${from}>${to}`);

describe("traffic topology source of truth", () => {
  it.each([
    ["normal_ai", ["client>normal", "normal>ai-0", "ai-0>exit"]],
    ["normal_fallback", ["client>normal", "normal>normal-direct", "normal-direct>exit"]],
    ["normal_direct", ["client>normal", "normal>normal-direct", "normal-direct>exit"]],
    ["dns_backup_relay_ai", ["client>backup", "backup>ai-0", "ai-0>exit"]],
    ["dns_backup_direct", ["client>backup", "backup>backup-direct", "backup-direct>exit"]],
    ["dns_backup_pending", []],
    ["unknown", []],
  ])("maps %s to only the reported active path", (path, expected) => {
    expect(activeEdges(buildTrafficTopology(snapshot(path)))).toEqual(expected);
  });

  it("explains a waiting report without suggesting configured candidates are missing", () => {
    const panel = snapshot("normal_ai", [{ index: 0, label: "主 AI 节点", selected: false, is_reachable: null }]);
    panel.aiRoutingStatus.status = "waiting_report";
    const graph = buildTrafficTopology(panel);
    expect(graph.label).toBe("等待 AI 路由报告");
    expect(graph.warning).toBe("已配置 1 个 AI 候选，正在等待路由报告；当前出口与链路可达性尚未确认。");
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("unknown");
    expect(activeEdges(graph)).toEqual([]);
  });

  it("distinguishes missing candidates from candidates without a selection", () => {
    expect(buildTrafficTopology(snapshot("normal_ai", [])).warning).toBe("尚未配置 AI 候选，无法展示 AI 出口。");
    expect(buildTrafficTopology(snapshot("normal_ai", [{ index: 0, is_reachable: true, selected: false }])).warning)
      .toBe("已配置 AI 候选，但路由报告尚未确认当前出口。");
  });

  it("uses candidate probes even when the SSH management channel is unavailable", () => {
    const graph = buildTrafficTopology(snapshot("normal_ai"));
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("active");
    expect(graph.nodes.find(({ id }) => id === "ai-1").state).toBe("blocked");
  });

  it.each([false, null, undefined])("does not mark a selected candidate with probe %s healthy", (probe) => {
    const graph = buildTrafficTopology(snapshot("normal_ai", [{ index: 0, selected: true, is_reachable: probe }]));
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe(probe === false ? "blocked" : "unknown");
    expect(activeEdges(graph)).not.toContain("normal>ai-0");
    expect(activeEdges(graph)).not.toContain("ai-0>exit");
  });

  it("does not choose an unselected candidate or invent one from SSH status", () => {
    const noSelection = buildTrafficTopology(snapshot("normal_ai", [{ index: 7, label: "已配置", is_reachable: true }]));
    expect(activeEdges(noSelection)).not.toContain("normal>ai-7");
    expect(buildTrafficTopology(snapshot("normal_ai", [])).nodes.some(({ kind }) => kind === "ai")).toBe(false);
  });

  it("omits unconfigured entries and marks missing/invalid routes unknown", () => {
    const graph = buildTrafficTopology({ trafficRouting: { path: "bogus" } });
    expect(graph.path).toBe("unknown");
    expect(graph.nodes.map(({ id }) => id)).toEqual(["client", "exit"]);
    expect(activeEdges(graph)).toEqual([]);
  });

  it("uses actual DNS record content for entry addresses and accepts provider values", () => {
    const panel = snapshot("normal_direct");
    panel.dnsFailoverStatus.primary_content = "primary.example.test";
    panel.dnsFailoverStatus.backup_content = "backup-entry.example.test";
    const graph = buildTrafficTopology({ panel });
    expect(graph.nodes.find(({ id }) => id === "normal").address).toBe("primary.example.test");
    expect(graph.nodes.find(({ id }) => id === "backup").address).toBe("backup-entry.example.test");
  });

  it("ignores malformed candidate entries and retains every actual candidate", () => {
    const graph = buildTrafficTopology(snapshot("normal_fallback", [null, "bad", [], { index: 5, is_reachable: true }, { index: 8, is_reachable: null }]));
    expect(graph.nodes.filter(({ kind }) => kind === "ai").map(({ id }) => id)).toEqual(["ai-5", "ai-8"]);
    expect(graph.nodes.find(({ id }) => id === "ai-5").state).toBe("standby");
    expect(graph.nodes.find(({ id }) => id === "ai-8").state).toBe("unknown");
  });

  it("does not wire standby AI candidates through a backup configured for direct exit", () => {
    const panel = snapshot("dns_backup_direct");
    panel.dnsFailoverStatus.control_plane_backup_xray_enabled = false;
    expect(buildTrafficTopology(panel).edges.some(({ from, to }) => from === "backup" && to.startsWith("ai-"))).toBe(false);
  });
});

describe("accessible traffic topology", () => {
  it("lets keyboard users select a node and read its actual address and probe", async () => {
    const user = userEvent.setup();
    render(<TrafficTopology panel={snapshot("normal_ai")} />);
    expect(screen.getByRole("region", { name: "流量拓扑图" })).toBeTruthy();
    const node = screen.getByRole("button", { name: /东京 AI.*当前路径/ });
    node.focus();
    await user.keyboard("{Enter}");
    expect(node.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("tokyo.example.test:443")).toBeTruthy();
    expect(screen.getByText(/候选探测可达/)).toBeTruthy();
    expect(screen.getByText(/当前路由不代表链路吞吐量/)).toBeTruthy();
  });

  it("updates the selected node details when a live snapshot changes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TrafficTopology panel={snapshot("normal_ai")} />);
    await user.click(screen.getByRole("button", { name: /东京 AI.*当前路径/ }));
    const changed = snapshot("normal_fallback");
    changed.aiRoutingStatus.ai_candidates[0].is_reachable = false;
    rerender(<TrafficTopology panel={changed} />);
    expect(screen.getByRole("button", { name: /东京 AI.*不可达/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/候选探测不可达/)).toBeTruthy();
    expect(screen.getByText("当前 normal_fallback")).toBeTruthy();
  });

  it("falls back to client details when the selected candidate disappears", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TrafficTopology panel={snapshot("normal_ai")} />);
    await user.click(screen.getByRole("button", { name: /东京 AI.*当前路径/ }));
    rerender(<TrafficTopology panel={snapshot("unknown", [])} />);
    expect(screen.getByRole("button", { name: /客户端/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("tokyo.example.test:443")).toBeNull();
  });

  it("explains missing candidate telemetry without fabricating an active AI node", () => {
    render(<TrafficTopology panel={snapshot("normal_ai", [])} compact />);
    expect(screen.getByText(/尚未配置 AI 候选/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /AI 节点/ })).toBeNull();
  });
});
