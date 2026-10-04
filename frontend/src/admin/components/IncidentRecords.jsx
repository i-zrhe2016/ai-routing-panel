import { useId, useRef, useState } from "react";

import { usePanel } from "../state/PanelProvider.jsx";
import { useDialogFocus } from "./useDialogFocus.js";
import { Panel, StatusPill } from "./ui.jsx";

const statuses = { queued: "排队中", running: "分析中", completed: "分析完成", failed: "分析失败" };
const sources = { upstream: "普通上游", ai_upstream: "AI 上游", dns_failover: "DNS 主入口", diagnostics: "按需体检" };

export default function IncidentRecords() {
  const panel = usePanel();
  const [report, setReport] = useState(null);
  const reportRequest = useRef(0);
  const dialogRef = useRef(null);
  const titleId = useId();
  const close = () => { reportRequest.current += 1; setReport(null); };
  useDialogFocus(dialogRef, Boolean(report), close);
  const open = async (incident) => {
    const request = ++reportRequest.current;
    setReport({ incident, loading: true, text: "", error: "" });
    try {
      const text = await panel.loadIncidentReport(incident.id);
      if (request === reportRequest.current) setReport({ incident, loading: false, text, error: "" });
    } catch (error) {
      if (request === reportRequest.current) setReport({ incident, loading: false, text: "", error: error?.message || "故障文档加载失败。" });
    }
  };
  return (
    <Panel title="Codex 故障记录" description="独立探测失败后自动排队分析；恢复记录保留，分析不会修改生产节点。">
      <div className="cc-toolbar__group">
        <button type="button" className="a-btn ghost" onClick={panel.loadIncidents} disabled={panel.incidentsLoading}>刷新故障记录</button>
      </div>
      {panel.incidentsLoading ? <p role="status">故障记录加载中…</p> : null}
      {panel.incidentsError ? <p role="alert">{panel.incidentsError}</p> : null}
      {!panel.incidentsLoading && !panel.incidentsError && !panel.incidents?.length ? <div className="cc-empty">暂无自动故障记录。</div> : null}
      <ul className="cc-incident-list">
        {(panel.incidents || []).map((incident) => (
          <li key={incident.id}>
            <div className="cc-toolbar__group">
              <strong>{incident.target}</strong>
              <StatusPill label={statuses[incident.status] || incident.status} tone={incident.status === "failed" ? "danger" : incident.status === "completed" ? "success" : "info"} />
              <StatusPill label={incident.recovered_at ? "探测已恢复" : "故障持续中"} tone={incident.recovered_at ? "success" : "warning"} />
            </div>
            <p>{sources[incident.source] || incident.source} · {incident.kind === "executor_error" ? "探测执行器错误" : "节点请求失败"} · 探测来源 {incident.probe_origin || "未记录"}</p>
            <p>首次 <time>{incident.first_seen_at}</time> · 最近 <time>{incident.last_seen_at}</time> · {incident.occurrences} 次失败</p>
            {incident.recovered_at ? <p>恢复于 <time>{incident.recovered_at}</time></p> : null}
            {incident.diagnosis_error ? <p className="cc-tone is-danger">{incident.diagnosis_error}</p> : null}
            <button type="button" className="a-btn ghost" disabled={!incident.report_available} onClick={() => open(incident)} aria-label={`打开 ${incident.target} 故障文档`}>打开故障文档</button>
          </li>
        ))}
      </ul>
      {report ? (
        <div className="cc-modal-backdrop" onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
          <div className="cc-modal cc-incident-report" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={dialogRef}>
            <h2 id={titleId}>{report.incident.target} 故障文档</h2>
            <button type="button" className="a-btn ghost" onClick={close} data-dialog-autofocus>关闭文档</button>
            {report.loading ? <p role="status">故障文档加载中…</p> : null}
            {report.error ? <p role="alert">{report.error}</p> : null}
            {report.text ? <pre>{report.text}</pre> : null}
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
