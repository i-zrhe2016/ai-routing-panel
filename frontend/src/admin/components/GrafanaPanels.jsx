// Grafana embeds. Host resources and per-port rates come from the monitoring
// stack; the console only frames them, it never re-samples Prometheus itself.
import { useState } from "react";

const HOST_PANELS = [
  { id: 1, title: "CPU 使用率" },
  { id: 2, title: "内存使用率" },
  { id: 4, title: "根分区磁盘使用率" },
  { id: 5, title: "负载 (1 分钟)" },
];

const TRAFFIC_PANELS = [
  { id: 3, title: "网络流量 (eth)", wide: true },
  { id: 7, title: "每端口流量速率", wide: true },
  { id: 8, title: "每端口连接速率" },
  { id: 6, title: "Swap 使用率" },
];

export function useGrafana(meta) {
  const baseUrl = meta?.grafana_url || "";
  const uid = meta?.grafana_observability_uid || "xray-observability";
  const [range, setRange] = useState("6h");
  const configured = Boolean(baseUrl);
  return {
    configured,
    range,
    setRange,
    dashboardLink: configured ? `${baseUrl}/d/${uid}/${uid}?from=now-${range}&to=now` : "",
    panelSrc: (panelId) => {
      const params = new URLSearchParams({
        orgId: "1",
        panelId: String(panelId),
        theme: "light",
        from: `now-${range}`,
        to: "now",
        refresh: "30s",
      });
      return `${baseUrl}/d-solo/${uid}/${uid}?${params.toString()}`;
    },
  };
}

export function GrafanaToolbar({ grafana, label = "主机资源来自 Prometheus · Grafana" }) {
  const ranges = [
    { key: "1h", label: "1 小时" },
    { key: "6h", label: "6 小时" },
    { key: "24h", label: "24 小时" },
  ];
  return (
    <div className="cc-toolbar">
      <span className="cc-toolbar__label">{label}</span>
      <div className="cc-toolbar__group">
        {ranges.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`a-btn ${item.key === grafana.range ? "primary" : "ghost"}`}
            onClick={() => grafana.setRange(item.key)}
          >
            {item.label}
          </button>
        ))}
        {grafana.dashboardLink ? (
          <a className="a-btn ghost" href={grafana.dashboardLink} target="_blank" rel="noopener noreferrer">
            在 Grafana 打开 ↗
          </a>
        ) : null}
      </div>
    </div>
  );
}

export function GrafanaPanels({ grafana, panels = HOST_PANELS }) {
  if (!grafana.configured) {
    return (
      <div className="cc-empty">
        <strong>监控未配置</strong>
        <p>
          设置环境变量 <code>GRAFANA_PUBLIC_URL</code>（浏览器可达的 Grafana 地址）并启动 <code>monitoring/</code>
          监控栈后，此处会内嵌主机资源与端口速率图表。
        </p>
      </div>
    );
  }
  return (
    <div className="cc-grafana-grid">
      {panels.map((panel) => (
        <div key={panel.id} className={`cc-grafana-panel${panel.wide ? " is-wide" : ""}`}>
          <iframe
            key={`${panel.id}-${grafana.range}`}
            src={grafana.panelSrc(panel.id)}
            title={panel.title}
            loading="lazy"
            frameBorder="0"
          />
        </div>
      ))}
    </div>
  );
}

export { HOST_PANELS, TRAFFIC_PANELS };
