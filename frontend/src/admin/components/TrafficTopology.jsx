import { useId, useState } from "react";
import { buildTrafficTopology, TOPOLOGY_STATES } from "../lib/topology.js";
import "./traffic-topology.css";
import { humanBytes } from "../../shared/formatters.js";

const COLUMN = { client: 0, entry: 1, ai: 2, direct: 2, exit: 3 };
function layout(nodes) {
  const columns = [0, 1, 2, 3].map((column) => nodes.filter((node) => COLUMN[node.kind] === column));
  const height = Math.max(280, columns[2].length * 88 + 72);
  const minHeight = Math.max(280, (columns[2].length + 1) * 78);
  return { height, minHeight, nodes: nodes.map((node) => {
    const column = COLUMN[node.kind];
    const group = columns[column];
    const index = group.indexOf(node);
    return { ...node, x: 105 + column * 250, y: height * (index + 1) / (group.length + 1) };
  }) };
}

export default function TrafficTopology({ panel, compact = false }) {
  const graph = buildTrafficTopology(panel);
  const [selectedId, select] = useState("client");
  const { nodes, height, minHeight } = layout(graph.nodes);
  const selected = nodes.find(({ id }) => id === selectedId) || nodes[0];
  const detailsId = useId();
  const summaryId = useId();
  const arrowId = useId().replace(/:/g, "");
  const activity = panel.trafficActivity;
  return (
    <section className={`traffic-topology${compact ? " traffic-topology--compact" : ""}`} aria-label="流量拓扑图" aria-describedby={summaryId}>
      <div className="traffic-topology__heading">
        <div><span className="traffic-topology__eyebrow">TRAFFIC FLOW</span><p className="traffic-topology__route" aria-live="polite">{graph.label}</p></div>
        <span className="traffic-topology__snapshot">自动同步</span>
      </div>
      <p id={summaryId} className="traffic-topology__summary">{graph.scenario}</p>
      <ul className="traffic-topology__legend" aria-label="链路状态图例">
        {Object.entries(TOPOLOGY_STATES).map(([state, label]) => <li key={state}><i className={`traffic-topology__key is-${state}`} aria-hidden="true" />{label}</li>)}
      </ul>
      {graph.warning ? <p className="traffic-topology__notice" role="status">{graph.warning}</p> : null}
      <div className="traffic-topology__canvas" style={{ aspectRatio: `960 / ${height}`, minHeight }}>
        <svg viewBox={`0 0 960 ${height}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <defs><marker id={arrowId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" /></marker></defs>
          {graph.edges.map((edge) => {
            const from = nodes.find(({ id }) => id === edge.from);
            const to = nodes.find(({ id }) => id === edge.to);
            const x1 = from.x + 78, x2 = to.x - 78, mid = (x1 + x2) / 2;
            const moving = edge.from === "client" && edge.state === "active" && activity?.bytes > 0;
            return <path key={edge.id} className={`traffic-topology__edge is-${edge.state}${moving ? " is-flowing" : ""}`} data-from={edge.from} data-to={edge.to} d={`M ${x1} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${x2} ${to.y}`} vectorEffect="non-scaling-stroke" markerEnd={`url(#${arrowId})`}><title>{edge.trafficClass}</title></path>;
          })}
        </svg>
        {nodes.map((node) => <button
          key={node.id} type="button"
          className={`traffic-topology__node is-${node.state}`}
          style={{ left: `${node.x / 960 * 100}%`, top: `${node.y / height * 100}%` }}
          aria-label={`${node.label} · ${node.role} · ${TOPOLOGY_STATES[node.state]}`}
          aria-pressed={selected.id === node.id} aria-controls={detailsId}
          onClick={() => select(node.id)}
        >
          <span className="traffic-topology__node-role">{node.role}</span>
          <strong title={node.label}>{node.label}</strong>
          <span className="traffic-topology__node-state"><i aria-hidden="true" />{TOPOLOGY_STATES[node.state]}</span>
        </button>)}
      </div>
      <div className="traffic-topology__detail" id={detailsId} aria-label="所选节点详情" role="region" aria-live="polite">
        <div className="traffic-topology__detail-heading"><strong>{selected.label}</strong><span>{selected.role} · {TOPOLOGY_STATES[selected.state]}</span></div>
        {selected.address ? <p className="traffic-topology__address">{selected.address}</p> : null}
        {selected.probeLabel ? <p>{selected.probeLabel} · {selected.selected ? "报告已选中" : "未选中"}</p> : null}
        <p>{selected.description}</p>
      </div>
      <p className="traffic-topology__footnote" aria-live="polite">全站流量速率：{activity ? `${humanBytes(activity.bytesPerSecond)}/s` : "等待下一次流量采样"} · 普通直出与 AI 分流可同时生效。当前路由不代表链路吞吐量，分支状态依据已应用路由与候选探测。</p>
    </section>
  );
}
