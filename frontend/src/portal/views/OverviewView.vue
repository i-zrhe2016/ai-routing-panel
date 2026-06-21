<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { NCard, NGrid, NGi, NList, NListItem, NSpin, NEmpty, NStatistic } from "naive-ui";

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
      <n-grid :cols="3" :x-gap="16" responsive="screen" item-responsive>
        <n-gi><n-card><n-statistic label="生效订阅" :value="data.summary.service_count" /></n-card></n-gi>
        <n-gi><n-card><n-statistic label="可续费" :value="data.summary.renewable_count" /></n-card></n-gi>
        <n-gi><n-card><n-statistic label="进行中订单" :value="data.summary.open_order_count" /></n-card></n-gi>
      </n-grid>

      <n-card title="最近订阅" style="margin-top:16px">
        <n-empty v-if="!data.services.length" description="暂无订阅" />
        <n-list v-else>
          <n-list-item v-for="s in data.services" :key="s.id">
            <RouterLink :to="`/subscriptions/${s.id}`">端口 {{ s.listen_port }} · {{ s.note || s.plan_name }}</RouterLink>
          </n-list-item>
        </n-list>
      </n-card>

      <n-card title="最近订单" style="margin-top:16px">
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
