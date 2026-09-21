import { BarRanking, GaugeRing, SeriesChart, Sparkline } from "../components/charts/index.jsx";
import { MetricCard, Panel, StatusPill, Tone } from "../components/ui.jsx";
import { humanBytes } from "../../shared/formatters.js";
import { portTone, trafficToday } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const RECEIVED_COLOR = "var(--c-primary)";
const SENT_COLOR = "var(--c-success)";
const RANGES = [
  { days: 7, label: "7 天" },
  { days: 14, label: "14 天" },
  { days: 30, label: "30 天" },
];

export default function TrafficWorkspace() {
  const panel = usePanel();
  const traffic = panel.insights?.traffic || null;
  const ports = traffic?.ports || [];
  const selected = panel.selectedPort;
  const selectedTraffic = ports.find((item) => item.listen_port === selected?.listen_port) || null;

  const ranking = ports.slice(0, 8).map((item) => ({
    key: item.listen_port,
    label: `:${item.listen_port}`,
    value: item.totals.total_bytes,
    note: `${item.note || "未命名"} · ${item.totals.connections} 连接 · 今日 ${humanBytes(item.today.total_bytes)}`,
    color: "var(--c-primary)",
  }));

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">TRAFFIC</p>
          <h2>流量与端口负载</h2>
          <p>累计计数来自 Xray 统计接口，日粒度历史来自控制面自身的 traffic_daily 表，两者都无需浏览器直接解析 /metrics。</p>
        </div>
        <div className="cc-toolbar__group">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              className={`a-btn ${panel.insightsDays === range.days ? "primary" : "ghost"}`}
              onClick={() => panel.changeInsightsDays(range.days)}
            >
              {range.label}
            </button>
          ))}
        </div>
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="TOTAL TRAFFIC" value={humanBytes(panel.totalTrafficBytes)} note="累计接收 + 发送" accent />
        <MetricCard label="RECEIVED" value={humanBytes(panel.summary.total_bytes_received || 0)} note="累计入站" />
        <MetricCard label="SENT" value={humanBytes(panel.summary.total_bytes_sent || 0)} note="累计出站" />
        <MetricCard label="CONNECTIONS" value={panel.summary.total_connections || 0} note={`${panel.summary.active_ports || 0} 个活跃端口`} />
        <MetricCard
          label={`最近 ${panel.insightsDays} 天`}
          value={traffic ? humanBytes(traffic.totals.total_bytes) : "—"}
          note={traffic ? `${traffic.range_start} → ${traffic.range_end}` : panel.insightsError || "历史未加载"}
          tone="info"
        />
      </section>

      <section className="cc-split">
        <Panel
          kicker="FLEET HISTORY"
          title="全站日流量"
          description={traffic ? `按天统计，共 ${traffic.days} 天。` : "历史数据尚未加载。"}
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
            <div className="cc-chart-empty">{panel.insightsError || "历史数据尚未加载。"}</div>
          )}
        </Panel>

        <Panel kicker="TOP PORTS" title="端口负载排行" description="按所选区间的累计流量排序。">
          <BarRanking items={ranking} ariaLabel="端口流量排行" emptyLabel={panel.insightsError || "暂无端口流量数据。"} />
        </Panel>
      </section>

      <Panel kicker="PORT LOAD" title="端口明细" description="点击端口查看该入口的历史曲线与配额使用。">
        {panel.ports.length ? (
          <div className="cc-table-wrap">
            <table className="cc-table">
              <thead>
                <tr>
                  <th scope="col">端口</th>
                  <th scope="col">租户</th>
                  <th scope="col">状态</th>
                  <th scope="col">连接</th>
                  <th scope="col">今日</th>
                  <th scope="col">累计</th>
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
                      <td>{port.total_connections || 0}</td>
                      <td>{humanBytes(trafficToday(port))}</td>
                      <td>{port.traffic_used_display}</td>
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
            kicker="SELECTED PORT"
            title={`端口 ${selected.listen_port}`}
            description={selected.note || "未填写备注"}
            actions={<StatusPill tone={portTone(selected)} label={selected.status_label || selected.status} />}
          >
            <div className="cc-port-detail">
              <GaugeRing
                value={selected.traffic_usage_bytes}
                max={selected.traffic_limit_bytes || 0}
                label="流量配额"
                caption={`${selected.traffic_used_display}${selected.traffic_limit_bytes ? ` / ${selected.traffic_limit_display}` : ""}`}
              />
              <dl className="cc-facts">
                <div>
                  <dt>今日流量</dt>
                  <dd>{humanBytes(trafficToday(selected))}</dd>
                </div>
                <div>
                  <dt>累计入站</dt>
                  <dd>{humanBytes(selected.total_bytes_received)}</dd>
                </div>
                <div>
                  <dt>累计出站</dt>
                  <dd>{humanBytes(selected.total_bytes_sent)}</dd>
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

          <Panel kicker="PORT HISTORY" title="该端口日流量" description={selectedTraffic ? "所选区间的每日合计。" : "历史数据尚未加载。"}>
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
              <div className="cc-chart-empty">该端口在所选区间内没有历史记录。</div>
            )}
          </Panel>
        </section>
      ) : null}
    </div>
  );
}
