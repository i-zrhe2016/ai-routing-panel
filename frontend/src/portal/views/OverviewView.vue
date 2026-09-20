<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { NCard, NEmpty, NList, NListItem, NSpin } from "naive-ui";

import { api } from "../store.js";
import { useToast } from "../notify.js";

const toast = useToast();
const loading = ref(true);
const data = ref(null);

onMounted(async () => {
  try {
    const res = await api.get("/api/customer/overview");
    data.value = res.data;
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <n-spin :show="loading">
    <div v-if="data">
      <header class="customer-page-head">
        <h1>Your service</h1>
        <p>查看当前订阅、续费状态和最近订单，不暴露底层 Xray / data plane 实现细节。</p>
      </header>

      <section class="customer-summary-grid">
        <article class="cc-metric">
          <span class="cc-metric__label">ACTIVE SERVICES</span>
          <strong>{{ data.summary.service_count }}</strong>
          <small>当前生效订阅</small>
        </article>
        <article class="cc-metric">
          <span class="cc-metric__label">RENEWABLE</span>
          <strong>{{ data.summary.renewable_count }}</strong>
          <small>当前可续费服务</small>
        </article>
        <article class="cc-metric">
          <span class="cc-metric__label">OPEN ORDERS</span>
          <strong>{{ data.summary.open_order_count }}</strong>
          <small>进行中的订单</small>
        </article>
      </section>

      <n-card title="My services" class="customer-list-card">
        <n-empty v-if="!data.services.length" description="暂无订阅" />
        <n-list v-else>
          <n-list-item v-for="s in data.services" :key="s.id">
            <RouterLink :to="`/subscriptions/${s.id}`">端口 {{ s.listen_port }} · {{ s.note || s.plan_name }}</RouterLink>
          </n-list-item>
        </n-list>
      </n-card>

      <n-card title="Recent orders" class="customer-list-card">
        <n-empty v-if="!data.orders.length" description="暂无订单" />
        <n-list v-else>
          <n-list-item v-for="o in data.orders" :key="o.order_no">
            <RouterLink :to="`/orders/${o.order_no}`">{{ o.order_no }} · {{ o.status_label }}</RouterLink>
          </n-list-item>
        </n-list>
      </n-card>
    </div>
  </n-spin>
</template>
