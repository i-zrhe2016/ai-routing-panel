import TrafficTopology from "../components/TrafficTopology.jsx";
import { Panel } from "../components/ui.jsx";
import { usePanel } from "../state/PanelProvider.jsx";

export default function TopologyWorkspace() {
  const panel = usePanel();
  return (
    <div className="workspace-section">
      <section className="cc-page-intro"><div><h1>流量拓扑</h1><p>从入口到出口，查看当前路由、备用路径和每个 AI 候选的探测状态。</p></div></section>
      <Panel title="当前流量路径" description="点击节点查看详情，路由状态随控制台同步更新。"><TrafficTopology panel={panel} /></Panel>
    </div>
  );
}
