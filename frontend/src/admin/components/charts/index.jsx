// Dependency-free SVG visualization primitives.
//
// The console must not add a charting package: these components render plain
// SVG from props, are pure, and degrade to an explicit empty state instead of
// drawing an invented baseline. Every chart carries a text alternative so the
// value is readable without colour.
import { humanBytes } from "../../../shared/formatters.js";

const CHART_WIDTH = 720;

function hasSeriesData(series) {
  return series.some((item) => (item.values || []).some((value) => Number(value) !== 0));
}

export function SeriesChart({
  labels = [],
  series = [],
  height = 170,
  formatValue = humanBytes,
  ariaLabel = "流量趋势",
  emptyLabel = "暂无历史数据",
  showLegend = true,
}) {
  const usable = series.filter((item) => Array.isArray(item.values) && item.values.length);
  if (!labels.length || !usable.length || !hasSeriesData(usable)) {
    return <div className="cc-chart-empty">{emptyLabel}</div>;
  }

  const maxValue = Math.max(
    1,
    ...usable.flatMap((item) => item.values.map((value) => Number(value) || 0)),
  );
  const padding = { top: 14, right: 14, bottom: 26, left: 14 };
  const innerWidth = CHART_WIDTH - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const lastIndex = labels.length - 1;
  const pointX = (index) =>
    labels.length <= 1 ? padding.left + innerWidth / 2 : padding.left + (innerWidth * index) / lastIndex;
  const pointY = (value) => padding.top + innerHeight - (innerHeight * (Number(value) || 0)) / maxValue;
  const baseline = padding.top + innerHeight;

  const summary = usable
    .map((item) => `${item.label} 合计 ${formatValue(item.values.reduce((sum, value) => sum + (Number(value) || 0), 0))}`)
    .join("；");

  return (
    <figure className="cc-chart">
      <svg
        className="cc-chart__svg"
        viewBox={`0 0 ${CHART_WIDTH} ${height}`}
        role="img"
        aria-label={`${ariaLabel}。${summary}`}
        preserveAspectRatio="none"
      >
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <line
            key={ratio}
            className="cc-chart__grid"
            x1={padding.left}
            x2={CHART_WIDTH - padding.right}
            y1={baseline - innerHeight * ratio}
            y2={baseline - innerHeight * ratio}
          />
        ))}
        <line className="cc-chart__axis" x1={padding.left} x2={CHART_WIDTH - padding.right} y1={baseline} y2={baseline} />
        {usable.map((item) => {
          const points = item.values.map((value, index) => `${pointX(index)},${pointY(value)}`);
          const areaPath = `M ${pointX(0)},${baseline} L ${points.join(" L ")} L ${pointX(lastIndex)},${baseline} Z`;
          return (
            <g key={item.key || item.label}>
              <path className="cc-chart__area" d={areaPath} style={{ fill: item.color }} opacity="0.14" />
              <polyline className="cc-chart__line" points={points.join(" ")} style={{ stroke: item.color }} />
              {labels.length === 1 ? (
                <circle cx={pointX(0)} cy={pointY(item.values[0])} r="3.5" style={{ fill: item.color }} />
              ) : null}
            </g>
          );
        })}
        <text className="cc-chart__label" x={padding.left} y={height - 8}>
          {labels[0]}
        </text>
        <text className="cc-chart__label" x={CHART_WIDTH - padding.right} y={height - 8} textAnchor="end">
          {labels[lastIndex]}
        </text>
      </svg>
      {showLegend ? (
        <figcaption className="cc-chart__legend">
          {usable.map((item) => (
            <span key={`legend-${item.key || item.label}`}>
              <i aria-hidden="true" style={{ backgroundColor: item.color }} />
              {item.label}
            </span>
          ))}
          <span className="cc-chart__legend-max">峰值 {formatValue(maxValue)}</span>
        </figcaption>
      ) : null}
    </figure>
  );
}

export function BarRanking({ items = [], formatValue = humanBytes, ariaLabel = "排行", emptyLabel = "暂无数据" }) {
  if (!items.length) return <div className="cc-chart-empty">{emptyLabel}</div>;
  const max = Math.max(1, ...items.map((item) => Number(item.value) || 0));
  return (
    <ul className="cc-ranking" aria-label={ariaLabel}>
      {items.map((item) => {
        const width = Math.max(2, Math.round(((Number(item.value) || 0) / max) * 100));
        return (
          <li key={item.key || item.label} className="cc-ranking__row">
            <div className="cc-ranking__meta">
              <strong>{item.label}</strong>
              <span>{formatValue(item.value)}</span>
            </div>
            <div className="cc-ranking__track">
              <span className="cc-ranking__bar" style={{ width: `${width}%`, backgroundColor: item.color }} />
            </div>
            {item.note ? <small>{item.note}</small> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function Sparkline({ values = [], color = "var(--c-primary)", label = "趋势" }) {
  const numbers = values.map((value) => Number(value) || 0);
  if (!numbers.length) return null;
  const max = Math.max(1, ...numbers);
  const step = numbers.length <= 1 ? 0 : 100 / (numbers.length - 1);
  const points = numbers.map((value, index) => `${index * step},${28 - (28 * value) / max}`);
  return (
    <svg className="cc-sparkline" viewBox="0 0 100 30" role="img" aria-label={`${label} 迷你趋势`} preserveAspectRatio="none">
      <polyline points={points.join(" ")} style={{ stroke: color }} />
    </svg>
  );
}

export function AvailabilityStrip({ checks = [], ariaLabel = "可用性", emptyLabel = "暂无探测记录" }) {
  if (!checks.length) return <div className="cc-chart-empty">{emptyLabel}</div>;
  const healthy = checks.filter((check) => check.status === "healthy").length;
  const ratio = Math.round((healthy / checks.length) * 100);
  return (
    <div className="cc-strip" role="img" aria-label={`${ariaLabel}：最近 ${checks.length} 次探测，${ratio}% 可达`}>
      {checks.map((check, index) => (
        <span
          key={`${check.checked_at || index}`}
          className={`cc-strip__cell is-${check.status}`}
          title={`${check.checked_at_display || ""} ${check.status === "healthy" ? "可达" : "不可达"}${check.failure_reason ? ` · ${check.failure_reason}` : ""}`}
        />
      ))}
    </div>
  );
}

export function EventTimeline({ events = [], emptyLabel = "暂无事件", formatTime = (item) => item.created_at_display }) {
  if (!events.length) return <div className="cc-chart-empty">{emptyLabel}</div>;
  return (
    <ol className="cc-timeline">
      {events.map((event) => (
        <li key={event.id ?? `${event.created_at}-${event.detail}`} className={`cc-timeline__item is-${event.tone || "neutral"}`}>
          <span className="cc-timeline__marker" aria-hidden="true" />
          <div className="cc-timeline__body">
            <div className="cc-timeline__head">
              <strong>{event.title}</strong>
              <time>{formatTime(event)}</time>
            </div>
            {event.detail ? <p>{event.detail}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function FlowPath({ nodes = [], ariaLabel = "当前流量路径" }) {
  if (!nodes.length) return <div className="cc-chart-empty">路径状态未知</div>;
  return (
    <ol className="cc-flow" aria-label={ariaLabel}>
      {nodes.map((node, index) => (
        <li key={`${node.role}-${index}`} className={`cc-flow__node${node.active ? " is-active" : ""}`}>
          <span className="cc-flow__role">{node.role}</span>
          <strong>{node.name}</strong>
          <small>{node.active ? "当前经过" : "未经过"}</small>
        </li>
      ))}
    </ol>
  );
}

export function GaugeRing({ value = 0, max = 0, label, caption, emptyLabel = "无上限" }) {
  const limit = Number(max) || 0;
  const used = Number(value) || 0;
  const fraction = limit > 0 ? Math.max(0, Math.min(1, used / limit)) : 0;
  const tone = limit <= 0
    ? "var(--c-primary)"
    : fraction >= 1
      ? "var(--c-danger)"
      : fraction >= 0.85
        ? "var(--c-warning)"
        : "var(--c-success)";
  const percent = Math.round(fraction * 100);
  return (
    <div
      className="cc-ring"
      style={{ "--angle": `${fraction * 360}deg`, "--tone": tone }}
      role="img"
      aria-label={`${label || "流量"}：${limit > 0 ? `${percent}%` : emptyLabel}`}
    >
      <div className="cc-ring__core">
        <strong>{limit > 0 ? `${percent}%` : "∞"}</strong>
        <small>{caption || humanBytes(used)}</small>
      </div>
    </div>
  );
}
