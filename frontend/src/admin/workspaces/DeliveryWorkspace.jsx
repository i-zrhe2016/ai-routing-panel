import { useConfirm } from "../components/ConfirmDialog.jsx";
import { CopyField, EmptyState, Panel, StatusPill } from "../components/ui.jsx";
import { humanBytes } from "../../shared/formatters.js";
import { portTone, trafficToday } from "../lib/dashboard.js";
import { usePanel } from "../state/PanelProvider.jsx";

const STATUS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "active", label: "运行中" },
  { value: "disabled", label: "已停用" },
  { value: "expired", label: "已过期" },
  { value: "quota", label: "已达上限" },
];

export default function DeliveryWorkspace() {
  const panel = usePanel();
  const confirm = useConfirm();
  const selected = panel.selectedPort;

  function onCopyError() {
    panel.setFlash("浏览器未允许复制，请手动复制。", "error");
  }

  function rotate(kind, port) {
    const labels = {
      tenant: { title: `重置端口 ${port.listen_port} 的租户面板地址？`, body: "旧地址会立即失效，已登录的租户需要重新获取地址。", run: () => panel.rotateTenantToken(port) },
      credentials: { title: `重置端口 ${port.listen_port} 的租户账号密码？`, body: "旧凭据会立即失效。", run: () => panel.rotateTenantCredentials(port) },
      subscription: { title: `重置端口 ${port.listen_port} 的订阅地址？`, body: "旧订阅地址会立即失效，客户端需要重新导入。", run: () => panel.rotatePortSubscription(port) },
    };
    const item = labels[kind];
    confirm.ask({ title: item.title, body: item.body, tone: "danger", confirmLabel: "确认重置", onConfirm: item.run });
  }

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">DELIVERY</p>
          <h2>端口与租户交付</h2>
          <p>新增监听入口、维护租户配额与到期时间，并生成每个端口独立的登录地址、凭据和订阅链接。</p>
        </div>
        <span className="cc-status-line">
          {panel.filteredPorts.length} / {panel.summary.total_ports || 0} 个端口
        </span>
      </section>

      <Panel kicker="NEW LISTENER" title="新增端口" description="沿用当前节点的 REALITY 参数，只新增一个监听入口。">
        <form
          className="form-grid"
          onSubmit={(event) => {
            event.preventDefault();
            panel.createPort();
          }}
        >
          <label className="field">
            <span>监听端口</span>
            <input
              className="a-input"
              type="number"
              min="1"
              max="65535"
              required
              value={panel.createForm.listen_port}
              onChange={(event) => panel.setCreateForm({ ...panel.createForm, listen_port: event.target.value })}
            />
          </label>
          <label className="field">
            <span>到期时间（{panel.meta.timezone_label || "服务器时区"}）</span>
            <input
              className="a-input"
              type="datetime-local"
              value={panel.createForm.expires_at}
              onChange={(event) => panel.setCreateForm({ ...panel.createForm, expires_at: event.target.value })}
            />
          </label>
          <label className="field">
            <span>流量上限</span>
            <input
              className="a-input"
              type="text"
              placeholder="例如 10G / 500MB"
              value={panel.createForm.traffic_limit}
              onChange={(event) => panel.setCreateForm({ ...panel.createForm, traffic_limit: event.target.value })}
            />
          </label>
          <label className="field field--wide">
            <span>租户备注</span>
            <input
              className="a-input"
              type="text"
              maxLength={200}
              placeholder="例如：客户A / 北京节点"
              value={panel.createForm.note}
              onChange={(event) => panel.setCreateForm({ ...panel.createForm, note: event.target.value })}
            />
          </label>
          <div className="form-actions">
            <button className="a-btn primary" type="submit" disabled={panel.isBusy("create-port")}>
              {panel.isBusy("create-port") ? "创建中…" : "创建端口"}
            </button>
          </div>
        </form>
      </Panel>

      <div className="resource-layout">
        <Panel
          kicker="PORT INVENTORY"
          title="端口管理"
          description="按端口号、备注或状态筛选。"
        >
          <div className="cc-toolbar">
            <label className="search-field">
              <span className="sr-only">搜索端口</span>
              <input
                className="a-input"
                type="search"
                placeholder="搜索端口 / 备注 / 状态"
                value={panel.filters.query}
                onChange={(event) => panel.setFilters({ ...panel.filters, query: event.target.value })}
              />
            </label>
            <div className="status-filters" role="group" aria-label="端口状态筛选">
              {STATUS_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  className={`filter-button${panel.filters.status === option.value ? " active" : ""}`}
                  type="button"
                  onClick={() => panel.setFilters({ ...panel.filters, status: option.value })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {!panel.ports.length ? (
            <EmptyState>当前还没有端口配置，先创建第一个监听端口。</EmptyState>
          ) : !panel.filteredPorts.length ? (
            <EmptyState>当前筛选条件下没有匹配的端口。</EmptyState>
          ) : (
            <div className="port-list">
              {panel.filteredPorts.map((port) => (
                <button
                  key={port.id}
                  className={`port-card${selected?.id === port.id ? " selected" : ""}`}
                  type="button"
                  onClick={() => panel.selectPort(port.id)}
                >
                  <div className="port-card-head">
                    <span className="port-card-number">:{port.listen_port}</span>
                    <StatusPill tone={portTone(port)} label={port.status_label || port.status} />
                  </div>
                  <strong>{port.note || "未填写备注"}</strong>
                  <div className="port-card-stats">
                    <span>今日 {humanBytes(trafficToday(port))}</span>
                    <span>累计 {port.traffic_used_display}</span>
                    <span>{port.total_connections || 0} 连接</span>
                    <span>{port.expires_at_display || "永久"}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        {selected ? (
          <Panel
            id="port-detail-panel"
            kicker="SELECTED PORT"
            title={`端口 ${selected.listen_port}`}
            description={`${selected.note ? `${selected.note} · ` : ""}租户直接接入当前 Xray Reality 入站。`}
            actions={<StatusPill tone={portTone(selected)} label={selected.status_label || selected.status} />}
          >
            <div className="detail-metrics">
              <div><span>累计入站</span><strong>{humanBytes(selected.total_bytes_received)}</strong></div>
              <div><span>累计出站</span><strong>{humanBytes(selected.total_bytes_sent)}</strong></div>
              <div><span>累计连接</span><strong>{selected.total_connections || 0}</strong></div>
              <div><span>到期时间</span><strong>{selected.expires_at_display || "永久"}</strong></div>
            </div>

            <form
              className="form-grid"
              onSubmit={(event) => {
                event.preventDefault();
                panel.updatePort(selected);
              }}
            >
              <label className="field">
                <span>监听端口</span>
                <input
                  className="a-input"
                  type="number"
                  min="1"
                  max="65535"
                  required
                  value={selected.form.listen_port}
                  onChange={(event) => panel.updatePortForm(selected.id, { listen_port: event.target.value })}
                />
              </label>
              <label className="field">
                <span>到期时间（{panel.meta.timezone_label || "服务器时区"}）</span>
                <input
                  className="a-input"
                  type="datetime-local"
                  value={selected.form.expires_at}
                  onChange={(event) => panel.updatePortForm(selected.id, { expires_at: event.target.value })}
                />
              </label>
              <label className="field">
                <span>流量上限</span>
                <input
                  className="a-input"
                  type="text"
                  placeholder="例如 10G / 500MB"
                  value={selected.form.traffic_limit}
                  onChange={(event) => panel.updatePortForm(selected.id, { traffic_limit: event.target.value })}
                />
              </label>
              <label className="field field--wide">
                <span>租户备注</span>
                <input
                  className="a-input"
                  type="text"
                  maxLength={200}
                  value={selected.form.note}
                  onChange={(event) => panel.updatePortForm(selected.id, { note: event.target.value })}
                />
              </label>
              <div className="form-actions">
                <button className="a-btn primary" type="submit" disabled={panel.isBusy(`update:${selected.id}`)}>
                  {panel.isBusy(`update:${selected.id}`) ? "保存中…" : "保存修改"}
                </button>
              </div>
            </form>

            {selected.access ? (
              <div className="access-block">
                <div className="section-heading">
                  <div>
                    <p className="section-kicker">TENANT DELIVERY</p>
                    <h3>租户面板与订阅输出</h3>
                    <p className="section-description">当前端口的登录地址、账号密码和订阅地址独立生成。</p>
                  </div>
                </div>
                <div className="action-row">
                  <button className="a-btn secondary" type="button" disabled={panel.isBusy(`rotate-tenant:${selected.id}`)} onClick={() => rotate("tenant", selected)}>
                    重置面板地址
                  </button>
                  <button className="a-btn secondary" type="button" disabled={panel.isBusy(`rotate-credentials:${selected.id}`)} onClick={() => rotate("credentials", selected)}>
                    重置账号密码
                  </button>
                  <button className="a-btn secondary" type="button" disabled={panel.isBusy(`rotate-subscription:${selected.id}`)} onClick={() => rotate("subscription", selected)}>
                    重置订阅地址
                  </button>
                </div>
                <div className="access-grid">
                  <CopyField label="租户登录地址" value={selected.access.tenant_login_url} onError={onCopyError} />
                  <CopyField label="租户用户名" value={selected.access.tenant_username} onError={onCopyError} />
                  <CopyField label="租户密码" value={selected.access.tenant_password} onError={onCopyError} />
                  <CopyField label="Clash 订阅" value={selected.access.tenant_subscription_clash_url} onError={onCopyError} />
                  <CopyField label="V2Ray 订阅" value={selected.access.tenant_subscription_v2ray_url} onError={onCopyError} />
                  <CopyField label="VLESS 直连分享" value={selected.access.share_link} onError={onCopyError} />
                </div>
              </div>
            ) : null}

            <div className="action-row detail-actions">
              {selected.status === "quota" ? (
                <button
                  className="a-btn secondary"
                  type="button"
                  disabled={panel.isBusy(`reset:${selected.id}`)}
                  onClick={() =>
                    confirm.ask({
                      title: `重置端口 ${selected.listen_port} 的流量？`,
                      body: "累计流量会清零，端口会重新启用。",
                      tone: "danger",
                      confirmLabel: "确认重置",
                      onConfirm: () => panel.resetTraffic(selected),
                    })
                  }
                >
                  重置流量并启用
                </button>
              ) : null}
              {selected.status === "active" || selected.status === "disabled" ? (
                <button
                  className="a-btn secondary"
                  type="button"
                  disabled={panel.isBusy(`toggle:${selected.id}`)}
                  onClick={() => panel.togglePort(selected)}
                >
                  {selected.status === "active" ? "停用端口" : "启用端口"}
                </button>
              ) : null}
              <button
                className="a-btn danger"
                type="button"
                disabled={panel.isBusy(`delete:${selected.id}`)}
                onClick={() =>
                  confirm.ask({
                    title: `删除端口 ${selected.listen_port}？`,
                    body: "端口、租户凭据和该端口的流量记录都会被移除。",
                    tone: "danger",
                    confirmLabel: "确认删除",
                    onConfirm: () => panel.deletePort(selected),
                  })
                }
              >
                删除端口
              </button>
            </div>
          </Panel>
        ) : (
          <Panel className="detail-panel--empty">
            <EmptyState>从左侧列表选择端口，查看连接、到期和租户交付信息。</EmptyState>
          </Panel>
        )}
      </div>

      {confirm.dialog}
    </div>
  );
}
