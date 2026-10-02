import TrafficTopology from "../components/TrafficTopology.jsx";
import { Panel } from "../components/ui.jsx";
import { usePanel } from "../state/PanelProvider.jsx";

export default function TopologyWorkspace() {
  const panel = usePanel();
  return (
    <div className="workspace-section">
      <section className="cc-page-intro"><div><h1>流量拓扑</h1><p>查看普通与 AI 域名的实际分流规则、备用入口出口，以及配置应用和探测状态。</p></div></section>
      <Panel title="域名分流与出口" description="普通直出与 AI 分流可同时存在；备用入口按自身配置转发全部域名。"><TrafficTopology panel={panel} /></Panel>
    </div>
  );
}
