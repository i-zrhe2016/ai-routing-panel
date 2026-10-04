import { BarRanking, GaugeRing, SeriesChart, Sparkline } from "../components/charts/index.jsx";
import { MetricCard, Panel, StatusPill, Tone } from "../components/ui.jsx";
import { humanBytes } from "../../shared/formatters.js";
import { portTone } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const RECEIVED_COLOR = "var(--c-primary)";
const SENT_COLOR = "var(--c-success)";
const RANGES = [
  { days: 1, label: "今日" },
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
];

export default function TrafficWorkspace() {
  const panel = usePanel();
  const history = panel.insights?.traffic;
  const traffic = history?.days === panel.insightsDays ? history : null;
  const periodLabel = RANGES.find((range) => range.days === panel.insightsDays)?.label || "所选周期";
  const historyMessage = panel.insightsLoading ? "正在加载流量统计…" : panel.insightsError || "流量统计尚未加载。";
  const periodNote = traffic ? `${traffic.range_start} → ${traffic.range_end}` : historyMessage;
  const ports = traffic?.ports || [];
  const selected = panel.selectedPort;
  const selectedTraffic = ports.find((item) => item.listen_port === selected?.listen_port) || null;

  const ranking = ports.slice(0, 8).map((item) => ({
    key: item.listen_port,
    label: `:${item.listen_port}`,
    value: item.totals.total_bytes,
    note: `${item.note || "未命名"} · ${periodLabel} ${item.totals.connections} 连接`,
    color: "var(--c-primary)",
  }));

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <h1>流量与端口负载</h1>
          <p>查看全站与端口的流量趋势、连接负载和配额使用情况。</p>
        </div>
        <div className="cc-toolbar__group">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              className={`a-btn ${panel.insightsDays === range.days ? "primary" : "ghost"}`}
              aria-pressed={panel.insightsDays === range.days}
              onClick={() => panel.changeInsightsDays(range.days)}
            >
              {range.label}
            </button>
          ))}
        </div>
      </section>

      <section className="cc-metric-grid" aria-label={`${periodLabel}流量统计`} aria-busy={panel.insightsLoading}>
        <MetricCard label={`${periodLabel}总流量`} value={traffic ? humanBytes(traffic.totals.total_bytes) : "—"} note={periodNote} accent />
        <MetricCard label={`${periodLabel}入站`} value={traffic ? humanBytes(traffic.totals.bytes_received) : "—"} note={periodNote} />
        <MetricCard label={`${periodLabel}出站`} value={traffic ? humanBytes(traffic.totals.bytes_sent) : "—"} note={periodNote} />
        <MetricCard label={`${periodLabel}连接`} value={traffic ? traffic.totals.connections : "—"} note={periodNote} />
      </section>

      <section className="cc-split">
        <Panel
          title="全站日流量"
          description={traffic ? `${periodLabel}，从首日北京时间 0 点开始统计。` : historyMessage}
        >
          {traffic ? (
            <SeriesChart
              labels={traffic.dates}
              ariaLabel="全站每日流量"
              series={[
                { key: "received", label: "入站", values: traffic.series.bytes_received, color: RECEIVED_COLOR },
                { key: "sent", label: "出站", values: traffic.series.bytes_sent, color: SENT_COLOR },
              ]}
            />
          ) : (
            <div className="cc-chart-empty">{historyMessage}</div>
          )}
        </Panel>

        <Panel title="端口负载排行" description="按所选区间的累计流量排序。">
          <BarRanking items={ranking} ariaLabel="端口流量排行" emptyLabel={traffic ? "暂无端口流量数据。" : historyMessage} />
        </Panel>
      </section>

      <Panel title="端口明细" description={`${periodLabel}的连接与流量；点击端口查看历史曲线与累计配额。`}>
        {panel.ports.length ? (
          <div className="cc-table-wrap">
            <table className="cc-table">
              <thead>
                <tr>
                  <th scope="col">端口</th>
                  <th scope="col">租户</th>
                  <th scope="col">状态</th>
                  <th scope="col">连接</th>
                  <th scope="col">入站</th>
                  <th scope="col">出站</th>
                  <th scope="col">总流量</th>
                  <th scope="col">趋势</th>
                </tr>
              </thead>
              <tbody>
                {panel.ports.map((port) => {
                  const series = ports.find((item) => item.listen_port === port.listen_port);
                  return (
                    <tr
                      key={port.id}
                      className={selected?.id === port.id ? "is-selected" : undefined}
                      onClick={() => panel.selectPort(port.id)}
                    >
                      <td>
                        <button className="cc-link" type="button" onClick={() => panel.selectPort(port.id)}>
                          :{port.listen_port}
                        </button>
                      </td>
                      <td>{port.note || "未命名"}</td>
                      <td>
                        <StatusPill tone={portTone(port)} label={port.status_label || port.status} />
                      </td>
                      <td>{series ? series.totals.connections : "—"}</td>
                      <td>{series ? humanBytes(series.totals.bytes_received) : "—"}</td>
                      <td>{series ? humanBytes(series.totals.bytes_sent) : "—"}</td>
                      <td>{series ? humanBytes(series.totals.total_bytes) : "—"}</td>
                      <td>
                        <Sparkline values={series?.series.total_bytes || []} label={`端口 ${port.listen_port}`} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="cc-empty">当前还没有端口配置。</div>
        )}
      </Panel>

      {selected ? (
        <section className="cc-split">
          <Panel
            title={`端口 ${selected.listen_port}`}
            description={selected.note || "未填写备注"}
            actions={<StatusPill tone={portTone(selected)} label={selected.status_label || selected.status} />}
          >
            <div className="cc-port-detail">
              <GaugeRing
                value={selected.traffic_usage_bytes}
                max={selected.traffic_limit_bytes || 0}
                label="累计流量配额"
                caption={`${selected.traffic_used_display}${selected.traffic_limit_bytes ? ` / ${selected.traffic_limit_display}` : ""}`}
              />
              <dl className="cc-facts">
                <div>
                  <dt>{periodLabel}流量</dt>
                  <dd>{selectedTraffic ? humanBytes(selectedTraffic.totals.total_bytes) : "—"}</dd>
                </div>
                <div>
                  <dt>{periodLabel}入站</dt>
                  <dd>{selectedTraffic ? humanBytes(selectedTraffic.totals.bytes_received) : "—"}</dd>
                </div>
                <div>
                  <dt>{periodLabel}出站</dt>
                  <dd>{selectedTraffic ? humanBytes(selectedTraffic.totals.bytes_sent) : "—"}</dd>
                </div>
                <div>
                  <dt>{periodLabel}连接</dt>
                  <dd>{selectedTraffic ? selectedTraffic.totals.connections : "—"}</dd>
                </div>
                <div>
                  <dt>剩余配额</dt>
                  <dd>{selected.traffic_remaining_display || "无限制"}</dd>
                </div>
                <div>
                  <dt>到期时间</dt>
                  <dd>{selected.expires_at_display || "永久"}</dd>
                </div>
                <div>
                  <dt>最近探测</dt>
                  <dd>
                    <Tone tone={selected.probe_status === "healthy" ? "success" : selected.probe_status === "unhealthy" ? "danger" : "neutral"}>
                      {selected.probe_status_label || "未检测"}
                    </Tone>
                  </dd>
                </div>
              </dl>
            </div>
          </Panel>

          <Panel title="该端口日流量" description={selectedTraffic ? "所选区间的每日合计。" : historyMessage}>
            {selectedTraffic ? (
              <SeriesChart
                labels={traffic.dates}
                ariaLabel={`端口 ${selected.listen_port} 每日流量`}
                series={[
                  { key: "received", label: "入站", values: selectedTraffic.series.bytes_received, color: RECEIVED_COLOR },
                  { key: "sent", label: "出站", values: selectedTraffic.series.bytes_sent, color: SENT_COLOR },
                ]}
              />
            ) : (
              <div className="cc-chart-empty">{traffic ? "该端口在所选区间内没有历史记录。" : historyMessage}</div>
            )}
          </Panel>
        </section>
      ) : null}
    </div>
  );
}
