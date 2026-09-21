import { useState } from "react";

import { useConfirm } from "../components/ConfirmDialog.jsx";
import { EmptyState, MetricCard, Panel, StatusPill } from "../components/ui.jsx";
import { usePanel } from "../state/PanelProvider.jsx";

const TABS = [
  { key: "plans", label: "套餐管理" },
  { key: "orders", label: "订单审核" },
  { key: "settings", label: "商业设置" },
];

export default function CommerceWorkspace() {
  const panel = usePanel();
  const confirm = useConfirm();
  const [tab, setTab] = useState("plans");
  const summary = panel.commerce.summary;
  const settings = panel.commerce.settings;

  return (
    <div className="workspace-section">
      <section className="cc-page-intro">
        <div>
          <p className="section-kicker">SERVICE COMMERCE</p>
          <h2>套餐与订单</h2>
          <p>售卖规则、付款审核和自动端口配置在同一个工作区；审核通过会立即开通对应端口。</p>
        </div>
        <span className="cc-status-line">{summary.pending_review_count || 0} 个待审订单</span>
      </section>

      <section className="cc-metric-grid">
        <MetricCard label="上架套餐" value={summary.enabled_plan_count || 0} tone="success" />
        <MetricCard label="客户数量" value={summary.customer_count || 0} tone="info" />
        <MetricCard label="服务实例" value={summary.service_count || 0} />
        <MetricCard label="待审订单" value={summary.pending_review_count || 0} tone="warning" accent />
      </section>

      <div className="local-tabs" role="tablist" aria-label="商业管理视图">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={`local-tab${tab === item.key ? " is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "plans" ? (
        <>
          <Panel kicker="NEW PLAN" title="新增套餐" description="管理公开售卖的时长 + 总流量套餐。">
            <form
              className="form-grid"
              onSubmit={(event) => {
                event.preventDefault();
                panel.createPlan();
              }}
            >
              <label className="field">
                <span>套餐 slug</span>
                <input className="a-input" type="text" placeholder="basic-30d-100g" value={panel.planCreateForm.slug} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, slug: event.target.value })} />
              </label>
              <label className="field">
                <span>套餐名称</span>
                <input className="a-input" type="text" maxLength={80} required value={panel.planCreateForm.name} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, name: event.target.value })} />
              </label>
              <label className="field">
                <span>价格（分）</span>
                <input className="a-input" type="number" min="1" required value={panel.planCreateForm.price_fen} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, price_fen: event.target.value })} />
              </label>
              <label className="field">
                <span>时长（天）</span>
                <input className="a-input" type="number" min="1" required value={panel.planCreateForm.duration_days} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, duration_days: event.target.value })} />
              </label>
              <label className="field">
                <span>流量</span>
                <input className="a-input" type="text" placeholder="例如 100G" required value={panel.planCreateForm.traffic_limit} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, traffic_limit: event.target.value })} />
              </label>
              <label className="field">
                <span>排序</span>
                <input className="a-input" type="number" step="1" value={panel.planCreateForm.sort_order} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, sort_order: event.target.value })} />
              </label>
              <label className="field field--wide">
                <span>套餐说明</span>
                <input className="a-input" type="text" maxLength={1000} value={panel.planCreateForm.description} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, description: event.target.value })} />
              </label>
              <label className="check-field">
                <input type="checkbox" checked={panel.planCreateForm.enabled} onChange={(event) => panel.setPlanCreateForm({ ...panel.planCreateForm, enabled: event.target.checked })} />
                <span>创建后立即上架</span>
              </label>
              <div className="form-actions">
                <button className="a-btn primary" type="submit" disabled={panel.isBusy("create-plan")}>
                  {panel.isBusy("create-plan") ? "创建中…" : "创建套餐"}
                </button>
              </div>
            </form>
          </Panel>

          <Panel kicker="PLANS" title="套餐列表" description={`共 ${panel.commerce.plans.length} 个套餐。`}>
            {panel.commerce.plans.length ? (
              <div className="plan-list">
                {panel.commerce.plans.map((plan) => (
                  <article key={plan.id} className="record-card">
                    <div className="record-card__head">
                      <div>
                        <strong>{plan.name}</strong>
                        <p>{plan.slug} · {plan.price_display} · {plan.duration_days} 天 · {plan.traffic_limit_display}</p>
                      </div>
                      <StatusPill tone={plan.enabled ? "success" : "danger"} label={plan.status_label} />
                    </div>
                    <form
                      className="form-grid compact-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        panel.updatePlan(plan);
                      }}
                    >
                      <label className="field"><span>slug</span><input className="a-input" type="text" value={plan.form.slug} onChange={(event) => panel.updatePlanForm(plan.id, { slug: event.target.value })} /></label>
                      <label className="field"><span>名称</span><input className="a-input" type="text" maxLength={80} value={plan.form.name} onChange={(event) => panel.updatePlanForm(plan.id, { name: event.target.value })} /></label>
                      <label className="field"><span>价格（分）</span><input className="a-input" type="number" min="1" value={plan.form.price_fen} onChange={(event) => panel.updatePlanForm(plan.id, { price_fen: event.target.value })} /></label>
                      <label className="field"><span>时长（天）</span><input className="a-input" type="number" min="1" value={plan.form.duration_days} onChange={(event) => panel.updatePlanForm(plan.id, { duration_days: event.target.value })} /></label>
                      <label className="field"><span>流量</span><input className="a-input" type="text" value={plan.form.traffic_limit} onChange={(event) => panel.updatePlanForm(plan.id, { traffic_limit: event.target.value })} /></label>
                      <label className="field"><span>排序</span><input className="a-input" type="number" step="1" value={plan.form.sort_order} onChange={(event) => panel.updatePlanForm(plan.id, { sort_order: event.target.value })} /></label>
                      <label className="field field--wide"><span>说明</span><input className="a-input" type="text" maxLength={1000} value={plan.form.description} onChange={(event) => panel.updatePlanForm(plan.id, { description: event.target.value })} /></label>
                      <label className="check-field">
                        <input type="checkbox" checked={plan.form.enabled} onChange={(event) => panel.updatePlanForm(plan.id, { enabled: event.target.checked })} />
                        <span>上架售卖</span>
                      </label>
                      <div className="form-actions">
                        <button className="a-btn primary" type="submit" disabled={panel.isBusy(`update-plan:${plan.id}`)}>
                          {panel.isBusy(`update-plan:${plan.id}`) ? "保存中…" : "保存"}
                        </button>
                      </div>
                    </form>
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState>当前还没有套餐。</EmptyState>
            )}
          </Panel>
        </>
      ) : null}

      {tab === "orders" ? (
        <Panel kicker="ORDERS" title="订单审核" description="人工核对付款截图后，驳回、取消或直接开通。">
          {panel.commerce.orders.length ? (
            <div className="order-list">
              {panel.commerce.orders.map((order) => (
                <article key={order.id} className="record-card order-card">
                  <div className="record-card__head">
                    <div>
                      <strong className="mono">{order.order_no}</strong>
                      <p>{order.customer_email} · {order.plan_name_snapshot} · {order.price_display}</p>
                    </div>
                    <StatusPill tone={order.status_tone} status={order.status} label={order.status_label} />
                  </div>
                  <div className="record-meta">
                    <span>类型：{order.kind === "renewal" ? "续费" : "新购"}</span>
                    <span>创建：{order.created_at_display}</span>
                    <span>付款备注：{order.payer_note || "暂无"}</span>
                    <span>服务端口：{order.listen_port || "待分配"}</span>
                  </div>
                  {order.proof_available ? (
                    <a className="proof-link" href={`/payment-proofs/${order.latest_submission_id}`} target="_blank" rel="noreferrer">
                      查看付款截图 ↗
                    </a>
                  ) : null}
                  <label className="field field--wide">
                    <span>审核备注 / 驳回原因</span>
                    <input
                      className="a-input"
                      type="text"
                      maxLength={300}
                      placeholder="例如付款信息已核对 / 金额不匹配"
                      value={order.form.review_note}
                      onChange={(event) => panel.updateOrderNote(order.id, event.target.value)}
                    />
                  </label>
                  <div className="action-row">
                    <button
                      className="a-btn primary"
                      type="button"
                      disabled={panel.isBusy(`fulfill-order:${order.id}`) || order.status !== "payment_submitted"}
                      onClick={() =>
                        confirm.ask({
                          title: `审核通过订单 ${order.order_no}？`,
                          body: "订单会立即开通并分配端口。",
                          confirmLabel: "通过并开通",
                          onConfirm: () => panel.fulfillOrder(order),
                        })
                      }
                    >
                      {panel.isBusy(`fulfill-order:${order.id}`) ? "开通中…" : "审核通过并开通"}
                    </button>
                    <button
                      className="a-btn secondary"
                      type="button"
                      disabled={panel.isBusy(`reject-order:${order.id}`) || order.status !== "payment_submitted"}
                      onClick={() => panel.rejectOrder(order)}
                    >
                      {panel.isBusy(`reject-order:${order.id}`) ? "处理中…" : "驳回订单"}
                    </button>
                    <button
                      className="a-btn danger"
                      type="button"
                      disabled={panel.isBusy(`cancel-order:${order.id}`) || ["fulfilled", "cancelled", "expired"].includes(order.status)}
                      onClick={() =>
                        confirm.ask({
                          title: `取消订单 ${order.order_no}？`,
                          body: "取消后客户需要重新下单。",
                          tone: "danger",
                          confirmLabel: "确认取消",
                          onConfirm: () => panel.cancelOrder(order),
                        })
                      }
                    >
                      {panel.isBusy(`cancel-order:${order.id}`) ? "处理中…" : "取消订单"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState>当前没有需要处理的商业化订单。</EmptyState>
          )}
        </Panel>
      ) : null}

      {tab === "settings" ? (
        <Panel kicker="SETTINGS" title="商业设置" description="配置公开付款说明、二维码地址和订单有效期。">
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              panel.updateCommerceSettings(panel.commerce.settings);
            }}
          >
            <label className="field">
              <span>订单有效期（小时）</span>
              <input className="a-input" type="number" min="1" required value={settings.order_expiry_hours || ""} onChange={(event) => panel.editCommerceSettings({ order_expiry_hours: event.target.value })} />
            </label>
            <label className="field field--wide">
              <span>支付宝二维码地址</span>
              <input className="a-input" type="url" placeholder="https://..." value={settings.payment_qr_code_url || ""} onChange={(event) => panel.editCommerceSettings({ payment_qr_code_url: event.target.value })} />
            </label>
            <label className="field field--wide">
              <span>付款说明</span>
              <input className="a-input" type="text" maxLength={1000} value={settings.payment_instructions || ""} onChange={(event) => panel.editCommerceSettings({ payment_instructions: event.target.value })} />
            </label>
            <div className="readonly-field">
              <span>自动端口范围</span>
              <strong>{(settings.auto_port_start || "-") + " - " + (settings.auto_port_end || "-")}</strong>
            </div>
            <div className="readonly-field">
              <span>截图大小上限</span>
              <strong>{settings.payment_proof_max_display || "-"}</strong>
            </div>
            <div className="form-actions">
              <button className="a-btn primary" type="submit" disabled={panel.isBusy("update-commerce-settings")}>
                {panel.isBusy("update-commerce-settings") ? "保存中…" : "保存商业设置"}
              </button>
            </div>
          </form>
        </Panel>
      ) : null}

      {confirm.dialog}
    </div>
  );
}
