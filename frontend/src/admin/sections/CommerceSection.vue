<script>
import StatusPill from "../../shared/ui/StatusPill.vue";

export default {
  name: "CommerceSection",
  components: { StatusPill },
  inject: ["panel"],
  data() {
    return { tab: "plans" };
  },
  methods: {
    selectTab(tab) {
      this.tab = tab;
    },
  },
};
</script>

<template>
  <div class="workspace-section commerce-workspace">
    <section class="panel-block commerce-intro">
      <div class="section-heading"><div><p class="section-kicker">SERVICE COMMERCE</p><h2>套餐与订单</h2><p class="section-description">把售卖规则、付款审核和自动端口配置放在同一个工作区。</p></div><span class="section-index">02</span></div>
      <div class="commerce-summary"><div><span>上架套餐</span><strong>{{ panel.commerceSummary.enabled_plan_count || 0 }}</strong></div><div><span>客户数量</span><strong>{{ panel.commerceSummary.customer_count || 0 }}</strong></div><div><span>服务实例</span><strong>{{ panel.commerceSummary.service_count || 0 }}</strong></div><div><span>待审订单</span><strong class="is-warning">{{ panel.commerceSummary.pending_review_count || 0 }}</strong></div></div>
      <div class="local-tabs" role="tablist" aria-label="商业管理视图">
        <button v-for="item in [{ key: 'plans', label: '套餐管理' }, { key: 'orders', label: '订单审核' }, { key: 'settings', label: '商业设置' }]" :key="item.key" class="local-tab" :class="{ 'is-active': tab === item.key }" type="button" role="tab" :aria-selected="tab === item.key" @click="selectTab(item.key)">{{ item.label }}</button>
      </div>
    </section>

    <section v-show="tab === 'plans'" class="panel-block">
      <div class="section-heading"><div><p class="section-kicker">PLANS</p><h3>套餐管理</h3><p class="section-description">管理公开售卖的时长 + 总流量套餐。</p></div></div>
      <form class="form-grid" @submit.prevent="panel.createPlan">
        <label class="field"><span>套餐 slug</span><input v-model.trim="panel.planCreateForm.slug" class="a-input" type="text" placeholder="basic-30d-100g" /></label>
        <label class="field"><span>套餐名称</span><input v-model.trim="panel.planCreateForm.name" class="a-input" type="text" maxlength="80" required /></label>
        <label class="field"><span>价格（分）</span><input v-model.number="panel.planCreateForm.price_fen" class="a-input" type="number" min="1" required /></label>
        <label class="field"><span>时长（天）</span><input v-model.number="panel.planCreateForm.duration_days" class="a-input" type="number" min="1" required /></label>
        <label class="field"><span>流量</span><input v-model.trim="panel.planCreateForm.traffic_limit" class="a-input" type="text" placeholder="例如 100G" required /></label>
        <label class="field"><span>排序</span><input v-model.number="panel.planCreateForm.sort_order" class="a-input" type="number" step="1" /></label>
        <label class="field field--wide"><span>套餐说明</span><input v-model.trim="panel.planCreateForm.description" class="a-input" type="text" maxlength="1000" /></label>
        <label class="check-field"><input v-model="panel.planCreateForm.enabled" type="checkbox" /><span>创建后立即上架</span></label>
        <div class="form-actions"><button class="a-btn primary" type="submit" :disabled="panel.isBusy('create-plan')">{{ panel.isBusy("create-plan") ? "创建中..." : "创建套餐" }}</button></div>
      </form>
      <div v-if="panel.plans.length" class="plan-list">
        <article v-for="plan in panel.plans" :key="plan.id" class="record-card">
          <div class="record-card__head"><div><strong>{{ plan.name }}</strong><p>{{ plan.slug }} · {{ plan.price_display }} · {{ plan.duration_days }} 天 · {{ plan.traffic_limit_display }}</p></div><status-pill :tone="plan.enabled ? 'success' : 'danger'" :label="plan.status_label" /></div>
          <form class="form-grid compact-form" @submit.prevent="panel.updatePlan(plan)">
            <label class="field"><span>slug</span><input v-model.trim="plan.form.slug" class="a-input" type="text" /></label><label class="field"><span>名称</span><input v-model.trim="plan.form.name" class="a-input" type="text" maxlength="80" /></label><label class="field"><span>价格（分）</span><input v-model.number="plan.form.price_fen" class="a-input" type="number" min="1" /></label><label class="field"><span>时长（天）</span><input v-model.number="plan.form.duration_days" class="a-input" type="number" min="1" /></label><label class="field"><span>流量</span><input v-model.trim="plan.form.traffic_limit" class="a-input" type="text" /></label><label class="field"><span>排序</span><input v-model.number="plan.form.sort_order" class="a-input" type="number" step="1" /></label><label class="field field--wide"><span>说明</span><input v-model.trim="plan.form.description" class="a-input" type="text" maxlength="1000" /></label><label class="check-field"><input v-model="plan.form.enabled" type="checkbox" /><span>上架此套餐</span></label><div class="form-actions"><button class="a-btn primary" type="submit" :disabled="panel.isBusy('update-plan:' + plan.id)">{{ panel.isBusy("update-plan:" + plan.id) ? "保存中..." : "保存套餐" }}</button></div>
          </form>
        </article>
      </div>
    </section>

    <section v-show="tab === 'orders'" class="panel-block">
      <div class="section-heading"><div><p class="section-kicker">ORDERS</p><h3>订单审核</h3><p class="section-description">人工核对付款截图后，驳回、取消或直接开通。</p></div></div>
      <div v-if="!panel.orders.length" class="a-empty">当前没有需要处理的商业化订单。</div>
      <div v-else class="order-list">
        <article v-for="order in panel.orders" :key="order.id" class="record-card order-card">
          <div class="record-card__head"><div><strong class="mono">{{ order.order_no }}</strong><p>{{ order.customer_email }} · {{ order.plan_name_snapshot }} · {{ order.price_display }}</p></div><status-pill :tone="order.status_tone" :status="order.status" :label="order.status_label" /></div>
          <div class="record-meta"><span>类型：{{ order.kind === "renewal" ? "续费" : "新购" }}</span><span>创建：{{ order.created_at_display }}</span><span>付款备注：{{ order.payer_note || "暂无" }}</span><span>服务端口：{{ order.listen_port || "待分配" }}</span></div>
          <a v-if="order.proof_available" class="proof-link" :href="'/payment-proofs/' + order.latest_submission_id" target="_blank" rel="noreferrer">查看付款截图 ↗</a>
          <label class="field field--wide"><span>审核备注 / 驳回原因</span><input v-model.trim="order.form.review_note" class="a-input" type="text" maxlength="300" placeholder="例如付款信息已核对 / 金额不匹配" /></label>
          <div class="action-row"><button class="a-btn primary" type="button" :disabled="panel.isBusy('fulfill-order:' + order.id) || order.status !== 'payment_submitted'" @click="panel.fulfillOrder(order)">{{ panel.isBusy("fulfill-order:" + order.id) ? "开通中..." : "审核通过并开通" }}</button><button class="a-btn secondary" type="button" :disabled="panel.isBusy('reject-order:' + order.id) || order.status !== 'payment_submitted'" @click="panel.rejectOrder(order)">{{ panel.isBusy("reject-order:" + order.id) ? "处理中..." : "驳回订单" }}</button><button class="a-btn danger" type="button" :disabled="panel.isBusy('cancel-order:' + order.id) || ['fulfilled', 'cancelled', 'expired'].includes(order.status)" @click="panel.cancelOrder(order)">{{ panel.isBusy("cancel-order:" + order.id) ? "处理中..." : "取消订单" }}</button></div>
        </article>
      </div>
    </section>

    <section v-show="tab === 'settings'" class="panel-block">
      <div class="section-heading"><div><p class="section-kicker">SETTINGS</p><h3>商业设置</h3><p class="section-description">配置公开付款说明、二维码地址和订单有效期。</p></div></div>
      <form class="form-grid" @submit.prevent="panel.updateCommerceSettings">
        <label class="field"><span>订单有效期（小时）</span><input v-model.number="panel.commerceSettings.order_expiry_hours" class="a-input" type="number" min="1" required /></label>
        <label class="field field--wide"><span>支付宝二维码地址</span><input v-model.trim="panel.commerceSettings.payment_qr_code_url" class="a-input" type="url" placeholder="https://..." /></label>
        <label class="field field--wide"><span>付款说明</span><input v-model.trim="panel.commerceSettings.payment_instructions" class="a-input" type="text" maxlength="1000" /></label>
        <div class="readonly-field"><span>自动端口范围</span><strong>{{ (panel.commerceSettings.auto_port_start || "-") + " - " + (panel.commerceSettings.auto_port_end || "-") }}</strong></div><div class="readonly-field"><span>截图大小上限</span><strong>{{ panel.commerceSettings.payment_proof_max_display || "-" }}</strong></div>
        <div class="form-actions"><button class="a-btn primary" type="submit" :disabled="panel.isBusy('update-commerce-settings')">{{ panel.isBusy("update-commerce-settings") ? "保存中..." : "保存商业设置" }}</button></div>
      </form>
    </section>
  </div>
</template>
