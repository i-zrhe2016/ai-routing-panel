import { createEmptyPlanForm } from "../utils.js";

// Commerce state and actions: plans, orders (fulfill/reject/cancel) and the
// commerce settings form.
export const CommerceMixin = {
  data() {
    return {
      commerceSummary: {},
      commerceSettings: {},
      plans: [],
      orders: [],
      planCreateForm: createEmptyPlanForm(),
    };
  },

  methods: {
    preparePlan(plan) {
      return {
        ...plan,
        form: {
          slug: String(plan.slug || ""),
          name: String(plan.name || ""),
          description: String(plan.description || ""),
          price_fen: String(plan.price_fen ?? ""),
          duration_days: String(plan.duration_days ?? ""),
          traffic_limit: String(plan.traffic_limit_display || ""),
          enabled: Boolean(plan.enabled),
          sort_order: String(plan.sort_order ?? 0),
        },
      };
    },

    prepareOrder(order) {
      return {
        ...order,
        form: {
          review_note: String(order.review_note || order.rejection_reason || ""),
        },
      };
    },

    resetPlanCreateForm() {
      this.planCreateForm = createEmptyPlanForm();
    },

    async createPlan() {
      await this.runAction("create-plan", async () => {
        const data = await this.requestJson("/api/plans", {
          method: "POST",
          body: JSON.stringify(this.planCreateForm),
        });
        this.applyResponse(data);
        this.resetPlanCreateForm();
      });
    },

    async updatePlan(plan) {
      await this.runAction(`update-plan:${plan.id}`, async () => {
        const data = await this.requestJson(`/api/plans/${plan.id}`, {
          method: "PUT",
          body: JSON.stringify(plan.form),
        });
        this.applyResponse(data);
      });
    },

    async updateCommerceSettings() {
      await this.runAction("update-commerce-settings", async () => {
        const data = await this.requestJson("/api/commerce-settings", {
          method: "PUT",
          body: JSON.stringify(this.commerceSettings),
        });
        this.applyResponse(data);
      });
    },

    async fulfillOrder(order) {
      if (!window.confirm(`确认审核通过订单 ${order.order_no} 并立即开通吗？`)) {
        return;
      }
      await this.runAction(`fulfill-order:${order.id}`, async () => {
        const data = await this.requestJson(`/api/orders/${order.id}/fulfill`, {
          method: "POST",
          body: JSON.stringify({ review_note: order.form.review_note }),
        });
        this.applyResponse(data);
      });
    },

    async rejectOrder(order) {
      if (!order.form.review_note.trim()) {
        this.setFlash("驳回订单前请填写原因。", "error");
        return;
      }
      await this.runAction(`reject-order:${order.id}`, async () => {
        const data = await this.requestJson(`/api/orders/${order.id}/reject`, {
          method: "POST",
          body: JSON.stringify({ review_note: order.form.review_note }),
        });
        this.applyResponse(data);
      });
    },

    async cancelOrder(order) {
      if (!window.confirm(`确认取消订单 ${order.order_no} 吗？`)) {
        return;
      }
      await this.runAction(`cancel-order:${order.id}`, async () => {
        const data = await this.requestJson(`/api/orders/${order.id}/cancel`, {
          method: "POST",
          body: JSON.stringify({ review_note: order.form.review_note }),
        });
        this.applyResponse(data);
      });
    },
  },
};
