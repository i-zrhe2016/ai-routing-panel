import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { buildTrafficTopology } from "../lib/topology.js";
import TrafficTopology from "../components/TrafficTopology.jsx";

function snapshot(path, candidates = [
  { index: 0, label: "主 AI", upstream_host: "primary.example.com", upstream_port: 443, selected: true, is_reachable: true },
  { index: 1, label: "备用 AI", upstream_host: "backup.example.com", selected: false, is_reachable: false },
]) {
  return {
    trafficRouting: {
      path, label: `当前 ${path}`, scenario: "服务端路由决策",
      ordinary_direct_state: ["normal_ai", "normal_ai_pending", "normal_fallback", "normal_direct", "dns_backup_direct"].includes(path) ? "active" : "standby",
      ai_branch_state: ["normal_ai", "dns_backup_relay_ai"].includes(path) ? "active" : path === "normal_ai_pending" ? "unknown" : "standby",
      traffic_scope: path.startsWith("dns_backup") ? "all_traffic" : "split_domains",
    },
    dataPlaneStatus: { configured: true, reachable: false },
    aiNodeStatus: { reachable: false },
    dnsFailoverStatus: { enabled: true, configured: true, current_target: path.startsWith("dns_backup") && path !== "dns_backup_pending" ? "backup" : "primary", control_plane_backup_xray_enabled: true,
      backup_xray_mode: path === "dns_backup_direct" ? "direct" : "relay",
      backup_relay_target: { upstream_host: "primary.example.com", upstream_port: 443 },
    },
    aiRoutingStatus: { status: "applied", config_apply_status: "direct", report_generated_at: "2026-10-02T13:00:00+00:00", report_generated_at_display: "2026-10-02 13:00:00", ai_candidates: candidates },
  };
}
const activeEdges = (graph) => graph.edges.filter((edge) => edge.state === "active").map(({ from, to }) => `${from}>${to}`);

describe("traffic topology source of truth", () => {
  it.each([
    ["normal_ai", ["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit", "normal>ai-0", "ai-0>ai-exit"]],
    ["normal_ai_pending", ["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit"]],
    ["normal_fallback", ["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit"]],
    ["normal_direct", ["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit"]],
    ["dns_backup_relay_ai", ["client>backup", "backup>backup-relay", "backup-relay>backup-exit"]],
    ["dns_backup_direct", ["client>backup", "backup>backup-direct", "backup-direct>backup-exit"]],
    ["dns_backup_unknown", ["client>backup"]],
    ["dns_backup_pending", []],
    ["unknown", []],
  ])("maps %s to its configured traffic classes", (path, expected) => {
    expect(activeEdges(buildTrafficTopology(snapshot(path)))).toEqual(expected);
  });

  it("keeps ordinary and AI destinations distinct without allocating fleet counters to edges", () => {
    const panel = snapshot("normal_ai");
    panel.summary = { total_bytes_sent: 999999 };
    const graph = buildTrafficTopology(panel);
    expect(graph.edges.find(({ from, to }) => from === "normal" && to === "normal-direct").trafficClass).toBe("普通 / 未分类域名");
    expect(graph.edges.find(({ from, to }) => from === "normal" && to === "ai-0").trafficClass).toBe("AI 域名");
    expect(graph.nodes.filter(({ kind }) => kind === "exit").map(({ id }) => id)).toEqual(["ordinary-exit", "ai-exit", "backup-exit"]);
    expect(graph.edges.every((edge) => !("bytes" in edge) && !("rate" in edge))).toBe(true);
  });

  it("requires applied evidence even when legacy backend path says AI", () => {
    for (const apply of ["delegated", "unmanaged", "unknown"]) {
      const panel = snapshot("normal_ai");
      panel.aiRoutingStatus.config_apply_status = apply;
      const graph = buildTrafficTopology(panel);
      expect(graph.path).toBe("normal_ai_pending");
      expect(activeEdges(graph)).not.toContain("normal>ai-0");
      expect(activeEdges(graph)).toContain("normal>normal-direct");
    }
  });

  it("uses the backup's actual relay destination independently of normal selection", () => {
    const panel = snapshot("dns_backup_relay_ai");
    panel.dnsFailoverStatus.backup_relay_target = { upstream_host: "relay.example.com", upstream_port: 8443 };
    panel.trafficRouting.ai_branch_state = "unknown";
    const graph = buildTrafficTopology(panel);
    expect(graph.nodes.find(({ id }) => id === "backup-relay").address).toBe("relay.example.com:8443");
    expect(graph.nodes.find(({ id }) => id === "backup-relay").state).toBe("unknown");
    expect(graph.edges.filter(({ from }) => from === "backup").map(({ to }) => to)).toEqual(["backup-relay"]);
    expect(activeEdges(graph)).not.toContain("backup>ai-0");
    expect(graph.edges.find(({ from, to }) => from === "backup" && to === "backup-relay").trafficClass).toBe("全部域名");
  });

  it("does not invent backup direct when the configured relay fails", () => {
    const panel = snapshot("dns_backup_relay_ai", [{ index: 0, upstream_host: "primary.example.com", upstream_port: 443, selected: true, is_reachable: false }]);
    panel.trafficRouting.ai_branch_state = "blocked";
    const graph = buildTrafficTopology(panel);
    expect(graph.path).toBe("dns_backup_relay_ai");
    expect(graph.nodes.find(({ id }) => id === "backup-relay").state).toBe("blocked");
    expect(graph.edges.some(({ from, to }) => from === "backup" && to === "backup-direct")).toBe(false);
    expect(activeEdges(graph)).toEqual(["client>backup"]);
  });

  it("keeps the applied AI path blocked instead of pretending fallback when probe fails", () => {
    const panel = snapshot("normal_ai", [{ index: 0, selected: true, is_reachable: false }]);
    panel.trafficRouting.ai_branch_state = "blocked";
    const graph = buildTrafficTopology(panel);
    expect(graph.path).toBe("normal_ai");
    expect(activeEdges(graph)).toContain("normal>normal-direct");
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("blocked");
  });

  it("explains a waiting report without suggesting configured candidates are missing", () => {
    const panel = snapshot("normal_ai", [{ index: 0, label: "主 AI 节点", selected: false, is_reachable: null }]);
    panel.aiRoutingStatus.status = "waiting_report";
    const graph = buildTrafficTopology(panel);
    expect(graph.label).toBe("普通直出 · 等待 AI 路由报告");
    expect(graph.warning).toBe("已配置 1 个 AI 候选，正在等待路由报告或配置应用确认；普通直出保留，AI 分流尚未确认。");
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("unknown");
    expect(activeEdges(graph)).toEqual(["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit"]);
  });

  it("distinguishes missing candidates from candidates without a selection", () => {
    expect(buildTrafficTopology(snapshot("normal_ai", [])).warning).toBe("尚未配置 AI 候选，无法展示 AI 出口。");
    expect(buildTrafficTopology(snapshot("normal_ai", [{ index: 0, is_reachable: true, selected: false }])).warning)
      .toBe("已配置 1 个 AI 候选，正在等待路由报告或配置应用确认；普通直出保留，AI 分流尚未确认。");
  });

  it("keeps graph references and activation valid across every path and probe combination", () => {
    for (const path of ["normal_ai", "normal_ai_pending", "normal_fallback", "normal_direct", "dns_backup_relay_ai", "dns_backup_direct", "dns_backup_unknown", "dns_backup_pending", "unknown"]) {
      for (const probe of [true, false, null]) {
        const panel = snapshot(path, [{ index: 3, upstream_host: "primary.example.com", upstream_port: 443, selected: true, is_reachable: probe }]);
        panel.trafficRouting.ai_branch_state = probe === true ? "active" : probe === false ? "blocked" : "unknown";
        const graph = buildTrafficTopology(panel);
        const ids = graph.nodes.map(({ id }) => id);
        expect(new Set(ids).size).toBe(ids.length);
        for (const edge of graph.edges) {
          expect(ids).toContain(edge.from);
          expect(ids).toContain(edge.to);
          if (edge.state === "active" && edge.to.startsWith("ai-")) {
            expect(path).toBe("normal_ai");
            expect(probe).toBe(true);
          }
        }
        if (path === "dns_backup_relay_ai") expect(graph.edges.some(({ from, to }) => from === "backup" && to === "backup-direct")).toBe(false);
      }
    }
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
    expect(activeEdges(graph)).not.toContain("ai-0>ai-exit");
  });

  it("does not choose an unselected candidate or invent one from SSH status", () => {
    const noSelection = buildTrafficTopology(snapshot("normal_ai", [{ index: 7, label: "已配置", is_reachable: true }]));
    expect(activeEdges(noSelection)).not.toContain("normal>ai-7");
    expect(buildTrafficTopology(snapshot("normal_ai", [])).nodes.some(({ kind }) => kind === "ai")).toBe(false);
  });

  it("treats probe management failures as unknown AI reachability", () => {
    const panel = snapshot("normal_ai", [{ index: 0, selected: true, is_reachable: false, probe_management_error: true }]);
    const graph = buildTrafficTopology(panel);
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("unknown");
    expect(activeEdges(graph)).not.toContain("normal>ai-0");
    expect(activeEdges(graph)).toContain("normal>normal-direct");
  });

  it("matches the actual backup relay target instead of the normal AI selection", () => {
    const panel = snapshot("dns_backup_relay_ai");
    panel.dnsFailoverStatus.backup_relay_target = { upstream_host: "backup.example.com", upstream_port: 8443 };
    panel.aiRoutingStatus.ai_candidates[1].upstream_port = 8443;
    panel.aiRoutingStatus.ai_candidates[1].is_reachable = true;
    const graph = buildTrafficTopology(panel);
    expect(activeEdges(graph)).toEqual(["client>backup", "backup>backup-relay", "backup-relay>backup-exit"]);
    expect(graph.nodes.find(({ id }) => id === "ai-0").state).toBe("standby");
  });

  it("never marks an ambiguous candidate selection as the active AI branch", () => {
    const panel = snapshot("normal_ai");
    panel.aiRoutingStatus.ai_candidates[1].selected = true;
    panel.aiRoutingStatus.ai_candidates[1].is_reachable = true;
    const graph = buildTrafficTopology(panel);
    expect(activeEdges(graph)).toEqual(["client>normal", "normal>normal-direct", "normal-direct>ordinary-exit"]);
  });

  it("omits unconfigured entries and marks missing/invalid routes unknown", () => {
    const graph = buildTrafficTopology({ trafficRouting: { path: "bogus" } });
    expect(graph.path).toBe("unknown");
    expect(graph.nodes.map(({ id }) => id)).toEqual(["client"]);
    expect(activeEdges(graph)).toEqual([]);
  });

  it("uses actual DNS record content for entry addresses and accepts provider values", () => {
    const panel = snapshot("normal_direct");
    panel.dnsFailoverStatus.primary_content = "primary.example.com";
    panel.dnsFailoverStatus.backup_content = "backup-entry.example.com";
    const graph = buildTrafficTopology({ panel });
    expect(graph.nodes.find(({ id }) => id === "normal").address).toBe("primary.example.com");
    expect(graph.nodes.find(({ id }) => id === "backup").address).toBe("backup-entry.example.com");
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
    const node = screen.getByRole("button", { name: /主 AI.*配置路径/ });
    node.focus();
    await user.keyboard("{Enter}");
    expect(node.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("primary.example.com:443")).toBeTruthy();
    expect(screen.getByText(/候选探测可达/)).toBeTruthy();
    expect(screen.getByText(/不代表实时吞吐量/)).toBeTruthy();
    expect(screen.getByText("2026-10-02 13:00:00")).toBeTruthy();
    expect(screen.getByRole("region", { name: "拓扑连线与节点" }).getAttribute("tabindex")).toBe("0");
    expect(screen.getByRole("list", { name: "按域名类型划分的出口" }).textContent).toContain("普通 / 未分类域名");
  });

  it("updates the selected node details when a live snapshot changes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TrafficTopology panel={snapshot("normal_ai")} />);
    await user.click(screen.getByRole("button", { name: /主 AI.*配置路径/ }));
    const changed = snapshot("normal_fallback");
    changed.aiRoutingStatus.ai_candidates[0].is_reachable = false;
    rerender(<TrafficTopology panel={changed} />);
    expect(screen.getByRole("button", { name: /主 AI.*不可达/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/候选探测不可达/)).toBeTruthy();
    expect(screen.getByText("当前 normal_fallback")).toBeTruthy();
    expect(screen.getByRole("button", { name: /数据面直出.*配置路径/ })).toBeTruthy();
    changed.aiRoutingStatus.ai_candidates[0].is_reachable = true;
    changed.trafficRouting.path = "normal_ai";
    changed.trafficRouting.ai_branch_state = "active";
    rerender(<TrafficTopology panel={changed} />);
    expect(screen.getByRole("button", { name: /主 AI.*配置路径/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /数据面直出.*配置路径/ })).toBeTruthy();
  });

  it("changes measured ingress animation with traffic without inventing branch throughput", () => {
    const panel = snapshot("normal_ai");
    const { container, rerender } = render(<TrafficTopology panel={{ ...panel, trafficActivity: { bytes: 1024, bytesPerSecond: 512 } }} />);
    expect(screen.getByText(/全站流量速率：512 B\/s/)).toBeTruthy();
    expect(container.querySelectorAll(".is-flowing")).toHaveLength(1);
    expect(container.querySelector(".is-flowing").getAttribute("data-from")).toBe("client");
    rerender(<TrafficTopology panel={{ ...panel, trafficActivity: { bytes: 0, bytesPerSecond: 0 } }} />);
    expect(container.querySelectorAll(".is-flowing")).toHaveLength(0);
    expect(screen.getByText(/全站流量速率：0 B\/s/)).toBeTruthy();
  });

  it("falls back to client details when the selected candidate disappears", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TrafficTopology panel={snapshot("normal_ai")} />);
    await user.click(screen.getByRole("button", { name: /主 AI.*配置路径/ }));
    rerender(<TrafficTopology panel={snapshot("unknown", [])} />);
    expect(screen.getByRole("button", { name: /客户端/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("primary.example.com:443")).toBeNull();
  });

  it("explains missing candidate telemetry without fabricating an active AI node", () => {
    render(<TrafficTopology panel={snapshot("normal_ai", [])} compact />);
    expect(screen.getByText(/尚未配置 AI 候选/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /AI 节点/ })).toBeNull();
  });
});


it("all-AI applied scope deactivates ordinary direct and includes IP/unknown traffic", () => {
  const panel = snapshot("normal_ai");
  panel.aiRoutingStatus.applied_traffic_scope = "all";
  const graph = buildTrafficTopology(panel);
  expect(activeEdges(graph)).toEqual(["client>normal", "normal>ai-0", "ai-0>ai-exit"]);
  expect(graph.edges.find((edge) => edge.to === "ai-0").trafficClass).toBe("全部代理 TCP/UDP");
  expect(graph.nodes.find((node) => node.id === "normal-direct").state).toBe("standby");
  expect(graph.nodes.find((node) => node.id === "ai-exit").label).toBe("全部代理目标");
});
