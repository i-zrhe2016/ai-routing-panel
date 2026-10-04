import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { PanelProvider } from "../state/PanelProvider.jsx";
import TrafficWorkspace from "../workspaces/TrafficWorkspace.jsx";
import { createFakeApi, makeDashboard, makeInsights } from "./fixtures.jsx";

function periodInsights(days, empty = false) {
  const dates = Array.from({ length: days }, (_, index) => {
    const date = new Date(Date.UTC(2026, 9, 2 - days + index + 1));
    return date.toISOString().slice(0, 10);
  });
  const ports = [
    { listen_port: 31098, note: "客户A", received: 60, sent: 30, connections: 4 },
    { listen_port: 31099, note: "客户B", received: 20, sent: 10, connections: 2 },
  ].map((port) => {
    const factor = empty ? 0 : days;
    const totals = {
      bytes_received: port.received * factor,
      bytes_sent: port.sent * factor,
      connections: port.connections * factor,
      total_bytes: (port.received + port.sent) * factor,
    };
    return {
      ...port,
      totals,
      today: { bytes_received: port.received, bytes_sent: port.sent, connections: port.connections, total_bytes: port.received + port.sent },
      series: Object.fromEntries(Object.entries(totals).map(([key, value]) => [key, dates.map((_, index) => index === days - 1 ? value : 0)])),
    };
  });
  const totals = Object.fromEntries(Object.keys(ports[0].totals).map((key) => [key, ports.reduce((sum, port) => sum + port.totals[key], 0)]));
  const series = Object.fromEntries(Object.keys(totals).map((key) => [key, dates.map((_, index) => ports.reduce((sum, port) => sum + port.series[key][index], 0))]));
  return makeInsights({ traffic: { days, dates, range_start: dates[0], range_end: dates.at(-1), ports, totals, series } });
}

function makePeriodApi(history = async (days) => periodInsights(days)) {
  const api = createFakeApi();
  api.get = async (url) => {
    api.calls.push({ method: "GET", url });
    if (url.startsWith("/api/insights")) {
      const days = Number(new URL(url, "https://panel.example.com").searchParams.get("days"));
      return { ok: true, insights: await history(days) };
    }
    return { ok: true, dashboard: makeDashboard() };
  };
  return api;
}

function renderTraffic(api) {
  return render(
    <PanelProvider api={api} pollInterval={0} insightsInterval={0}>
      <TrafficWorkspace />
    </PanelProvider>,
  );
}

function metrics(label) {
  return screen.getByRole("region", { name: `${label}流量统计` });
}

function values(region) {
  return within(region).getAllByRole("article").map((card) => card.querySelector("strong").textContent);
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe("calendar traffic periods", () => {
  it("starts today and keeps fleet, port table and selected details on the selected period", async () => {
    const user = userEvent.setup();
    const api = makePeriodApi();
    renderTraffic(api);

    await waitFor(() => expect(values(metrics("今日"))).toEqual(["120 B", "80 B", "40 B", "6"]));
    expect(api.calls.find((call) => call.url.startsWith("/api/insights")).url).toBe("/api/insights?days=1");
    expect(screen.getByRole("button", { name: "今日" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("今日，从首日北京时间 0 点开始统计。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "14 天" })).toBeNull();
    await user.click(screen.getByRole("button", { name: ":31098" }));
    expect(await screen.findByRole("img", { name: /^累计流量配额/ })).toBeTruthy();

    for (const [label, fleet, port] of [
      ["今日", ["120 B", "80 B", "40 B", "6"], ["4", "60 B", "30 B", "90 B"]],
      ["近 7 天", ["840 B", "560 B", "280 B", "42"], ["28", "420 B", "210 B", "630 B"]],
      ["近 30 天", ["3.52 KB", "2.34 KB", "1.17 KB", "180"], ["120", "1.76 KB", "900 B", "2.64 KB"]],
    ]) {
      await user.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(values(metrics(label))).toEqual(fleet));
      const row = screen.getByRole("button", { name: ":31098" }).closest("tr");
      expect(within(row).getAllByRole("cell").slice(3, 7).map((cell) => cell.textContent)).toEqual(port);
      const detail = screen.getByRole("heading", { name: "端口 31098" }).closest("section");
      for (const [suffix, expected] of [["流量", port[3]], ["入站", port[1]], ["出站", port[2]], ["连接", port[0]]]) {
        expect(within(detail).getByText(`${label}${suffix}`).nextElementSibling.textContent).toBe(expected);
      }
    }
    expect(api.calls.some((call) => call.url === "/api/insights?days=7")).toBe(true);
    expect(api.calls.some((call) => call.url === "/api/insights?days=30")).toBe(true);
  });

  it("shows pending values until loaded and ignores a late response for a previous period", async () => {
    const user = userEvent.setup();
    const initial = deferred();
    const week = deferred();
    const month = deferred();
    renderTraffic(makePeriodApi((days) => ({ 1: initial, 7: week, 30: month })[days].promise));

    expect(values(metrics("今日"))).toEqual(["—", "—", "—", "—"]);
    expect(screen.getAllByText("正在加载流量统计…").length).toBeGreaterThan(0);
    await act(async () => initial.resolve(periodInsights(1)));
    await waitFor(() => expect(values(metrics("今日"))[0]).toBe("120 B"));

    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    expect(values(metrics("近 7 天"))).toEqual(["—", "—", "—", "—"]);
    const row = screen.getByRole("button", { name: ":31098" }).closest("tr");
    expect(within(row).getAllByRole("cell").slice(3, 7).map((cell) => cell.textContent)).toEqual(["—", "—", "—", "—"]);
    await user.click(screen.getByRole("button", { name: "近 30 天" }));
    await act(async () => month.resolve(periodInsights(30)));
    await waitFor(() => expect(values(metrics("近 30 天"))[0]).toBe("3.52 KB"));
    await act(async () => week.resolve(periodInsights(7)));
    expect(values(metrics("近 30 天"))[0]).toBe("3.52 KB");
    expect(screen.getByRole("button", { name: "近 30 天" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("explains a failed period request and keeps its totals unknown", async () => {
    const user = userEvent.setup();
    renderTraffic(makePeriodApi(async (days) => {
      if (days === 7) throw new Error("近 7 天统计加载失败。");
      return periodInsights(days);
    }));
    await waitFor(() => expect(values(metrics("今日"))[0]).toBe("120 B"));
    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(screen.getAllByText("近 7 天统计加载失败。").length).toBeGreaterThan(0));
    expect(values(metrics("近 7 天"))).toEqual(["—", "—", "—", "—"]);
  });

  it("shows real zero totals once an empty period has loaded", async () => {
    renderTraffic(makePeriodApi(async (days) => periodInsights(days, true)));
    await waitFor(() => expect(values(metrics("今日"))).toEqual(["0 B", "0 B", "0 B", "0"]));
  });
});
