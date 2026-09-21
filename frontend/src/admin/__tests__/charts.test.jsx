import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AvailabilityStrip, BarRanking, EventTimeline, FlowPath, GaugeRing, SeriesChart, Sparkline } from "../components/charts/index.jsx";

describe("SeriesChart", () => {
  it("renders an empty state without a series", () => {
    render(<SeriesChart labels={[]} series={[]} />);
    expect(screen.getByText("暂无历史数据")).toBeTruthy();
  });

  it("renders an empty state when every value is zero", () => {
    render(<SeriesChart labels={["2026-09-20", "2026-09-21"]} series={[{ key: "a", label: "入站", values: [0, 0] }]} />);
    expect(screen.getByText("暂无历史数据")).toBeTruthy();
  });

  it("renders a single point without a degenerate path", () => {
    const { container } = render(
      <SeriesChart labels={["2026-09-21"]} series={[{ key: "a", label: "入站", values: [1024] }]} />,
    );
    expect(container.querySelector("circle")).toBeTruthy();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("入站");
  });

  it("summarises the series for assistive technology", () => {
    render(
      <SeriesChart
        labels={["2026-09-20", "2026-09-21"]}
        ariaLabel="全站每日流量"
        series={[{ key: "a", label: "入站", values: [1024, 2048] }]}
      />,
    );
    const label = screen.getByRole("img").getAttribute("aria-label");
    expect(label).toContain("全站每日流量");
    expect(label).toContain("3.00 KB");
  });
});

describe("other chart primitives", () => {
  it("ranks bars by value", () => {
    render(<BarRanking items={[{ key: 1, label: ":31098", value: 2048 }, { key: 2, label: ":31099", value: 1024 }]} />);
    expect(screen.getByRole("list", { name: "排行" })).toBeTruthy();
    expect(screen.getByText(":31098")).toBeTruthy();
  });

  it("reports availability as a text alternative", () => {
    render(
      <AvailabilityStrip
        ariaLabel="端口 31098 探测可用性"
        checks={[{ status: "healthy" }, { status: "unhealthy" }]}
      />,
    );
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("50% 可达");
  });

  it("renders timeline entries and flow nodes", () => {
    render(<EventTimeline events={[{ id: 1, title: "切换 · 成功", detail: "自动切换完成。", created_at_display: "2026-09-20 03:00:00" }]} />);
    expect(screen.getByText("切换 · 成功")).toBeTruthy();
    render(<FlowPath nodes={[{ role: "入口", name: "普通数据面", active: true }]} />);
    expect(screen.getByText("普通数据面")).toBeTruthy();
  });

  it("renders unlimited and percentage gauges", () => {
    const { unmount } = render(<GaugeRing value={512} max={0} label="配额" />);
    expect(screen.getByText("∞")).toBeTruthy();
    unmount();
    render(<GaugeRing value={512} max={1024} label="配额" />);
    expect(screen.getByText("50%")).toBeTruthy();
  });

  it("skips a sparkline without data", () => {
    const { container } = render(<Sparkline values={[]} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
