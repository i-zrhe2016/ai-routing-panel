<script>
import StatusPill from "../../shared/ui/StatusPill.vue";

// Commerce: plans CRUD, order review (fulfill/reject/cancel), commerce settings.
export default {
  name: "CommerceSection",
  components: { StatusPill },
  inject: ["panel"],
};
</script>

<template>
  <div class="a-section">
    <!-- Plans -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">PLANS</p>
        <h3>套餐管理</h3>
        <p>管理公开售卖的时长 + 总流量套餐。</p>
      </div>
      <div class="a-tiles">
        <div class="a-tile"><span>上架套餐</span><strong>{{ panel.commerceSummary.enabled_plan_count || 0 }}</strong></div>
        <div class="a-tile"><span>客户数量</span><strong>{{ panel.commerceSummary.customer_count || 0 }}</strong></div>
        <div class="a-tile"><span>服务实例</span><strong>{{ panel.commerceSummary.service_count || 0 }}</strong></div>
      </div>

      <form class="a-grid" @submit.prevent="panel.createPlan">
        <label class="a-field"><span>套餐 slug</span><input v-model="panel.planCreateForm.slug" class="a-input" type="text" placeholder="basic-30d-100g" /></label>
        <label class="a-field"><span>套餐名称</span><input v-model="panel.planCreateForm.name" class="a-input" type="text" maxlength="80" required /></label>
        <label class="a-field"><span>价格（分）</span><input v-model="panel.planCreateForm.price_fen" class="a-input" type="number" min="1" required /></label>
        <label class="a-field"><span>时长（天）</span><input v-model="panel.planCreateForm.duration_days" class="a-input" type="number" min="1" required /></label>
        <label class="a-field"><span>流量</span><input v-model="panel.planCreateForm.traffic_limit" class="a-input" type="text" placeholder="例如 100G" required /></label>
        <label class="a-field"><span>排序</span><input v-model="panel.planCreateForm.sort_order" class="a-input" type="number" step="1" /></label>
        <label class="a-field full"><span>套餐说明</span><input v-model="panel.planCreateForm.description" class="a-input" type="text" maxlength="1000" /></label>
        <label class="a-field a-check"><input v-model="panel.planCreateForm.enabled" type="checkbox" /><span>创建后立即上架</span></label>
        <div class="a-actions">
          <button class="a-btn primary" type="submit" :disabled="panel.isBusy('create-plan')">
            {{ panel.isBusy("create-plan") ? "创建中..." : "创建套餐" }}
          </button>
        </div>
      </form>

      <div v-if="panel.plans.length" class="plan-list">
        <div v-for="plan in panel.plans" :key="plan.id" class="plan-item">
          <div class="plan-item-head">
            <div>
              <strong>{{ plan.name }}</strong>
              <p>{{ plan.slug + " · " + plan.price_display + " · " + plan.duration_days + " 天 · " + plan.traffic_limit_display }}</p>
            </div>
            <status-pill :tone="plan.enabled ? 'success' : 'danger'" :label="plan.status_label" />
          </div>
          <form class="a-grid" @submit.prevent="panel.updatePlan(plan)">
            <label class="a-field"><span>slug</span><input v-model="plan.form.slug" class="a-input" type="text" /></label>
            <label class="a-field"><span>名称</span><input v-model="plan.form.name" class="a-input" type="text" maxlength="80" /></label>
            <label class="a-field"><span>价格（分）</span><input v-model="plan.form.price_fen" class="a-input" type="number" min="1" /></label>
            <label class="a-field"><span>时长（天）</span><input v-model="plan.form.duration_days" class="a-input" type="number" min="1" /></label>
            <label class="a-field"><span>流量</span><input v-model="plan.form.traffic_limit" class="a-input" type="text" /></label>
            <label class="a-field"><span>排序</span><input v-model="plan.form.sort_order" class="a-input" type="number" step="1" /></label>
            <label class="a-field full"><span>说明</span><input v-model="plan.form.description" class="a-input" type="text" maxlength="1000" /></label>
            <label class="a-field a-check"><input v-model="plan.form.enabled" type="checkbox" /><span>上架此套餐</span></label>
            <div class="a-actions">
              <button class="a-btn primary" type="submit" :disabled="panel.isBusy('update-plan:' + plan.id)">
                {{ panel.isBusy("update-plan:" + plan.id) ? "保存中..." : "保存套餐" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Orders -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">ORDERS</p>
        <h3>订单审核</h3>
        <p>人工核对付款截图后，驳回、取消或直接开通。</p>
      </div>
      <div v-if="!panel.orders.length" class="a-empty">当前没有需要处理的商业化订单。</div>
      <div v-else class="order-list">
        <div v-for="order in panel.orders" :key="order.id" class="order-item">
          <div class="order-item-head">
            <div>
              <strong>{{ order.order_no }}</strong>
              <p>{{ order.customer_email + " · " + order.plan_name_snapshot + " · " + order.price_display }}</p>
            </div>
            <status-pill :tone="order.status_tone" :status="order.status" :label="order.status_label" />
          </div>
          <div class="order-meta">
            <span>类型：{{ order.kind === "renewal" ? "续费" : "新购" }}</span>
            <span>创建：{{ order.created_at_display }}</span>
            <span>付款备注：{{ order.payer_note || "暂无" }}</span>
            <span>服务端口：{{ order.listen_port || "待分配" }}</span>
          </div>
          <a v-if="order.proof_available" class="proof-link" :href="'/payment-proofs/' + order.latest_submission_id" target="_blank" rel="noreferrer">查看截图</a>
          <label class="a-field full">
            <span>审核备注 / 驳回原因</span>
            <input v-model="order.form.review_note" class="a-input" type="text" maxlength="300" placeholder="例如付款信息已核对 / 金额不匹配" />
          </label>
          <div class="a-actions">
            <button class="a-btn primary" type="button" :disabled="panel.isBusy('fulfill-order:' + order.id) || order.status !== 'payment_submitted'" @click="panel.fulfillOrder(order)">
              {{ panel.isBusy("fulfill-order:" + order.id) ? "开通中..." : "审核通过并开通" }}
            </button>
            <button class="a-btn secondary" type="button" :disabled="panel.isBusy('reject-order:' + order.id) || order.status !== 'payment_submitted'" @click="panel.rejectOrder(order)">
              {{ panel.isBusy("reject-order:" + order.id) ? "处理中..." : "驳回订单" }}
            </button>
            <button class="a-btn danger" type="button" :disabled="panel.isBusy('cancel-order:' + order.id) || ['fulfilled', 'cancelled', 'expired'].includes(order.status)" @click="panel.cancelOrder(order)">
              {{ panel.isBusy("cancel-order:" + order.id) ? "处理中..." : "取消订单" }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Commerce settings -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">SETTINGS</p>
        <h3>商业设置</h3>
        <p>配置公开付款说明、二维码地址和订单有效期。</p>
      </div>
      <form class="a-grid" @submit.prevent="panel.updateCommerceSettings">
        <label class="a-field"><span>订单有效期（小时）</span><input v-model="panel.commerceSettings.order_expiry_hours" class="a-input" type="number" min="1" required /></label>
        <label class="a-field full"><span>支付宝二维码地址</span><input v-model="panel.commerceSettings.payment_qr_code_url" class="a-input" type="url" placeholder="https://..." /></label>
        <label class="a-field full"><span>付款说明</span><input v-model="panel.commerceSettings.payment_instructions" class="a-input" type="text" maxlength="1000" /></label>
        <div class="a-field"><span>自动端口范围</span><strong>{{ (panel.commerceSettings.auto_port_start || "-") + " - " + (panel.commerceSettings.auto_port_end || "-") }}</strong></div>
        <div class="a-field"><span>截图大小上限</span><strong>{{ panel.commerceSettings.payment_proof_max_display || "-" }}</strong></div>
        <div class="a-actions">
          <button class="a-btn primary" type="submit" :disabled="panel.isBusy('update-commerce-settings')">
            {{ panel.isBusy("update-commerce-settings") ? "保存中..." : "保存商业设置" }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.plan-list,
.order-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.plan-item,
.order-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
}
.plan-item-head,
.order-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.plan-item-head p,
.order-item-head p {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--c-text-muted);
}
.order-meta {
  display: flex;
  gap: var(--space-4);
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--c-text-muted);
}
.proof-link {
  font-size: 13px;
  color: var(--c-primary);
}
</style>
