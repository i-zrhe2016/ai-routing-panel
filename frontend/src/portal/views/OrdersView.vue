<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { NCard, NEmpty, NSpace, NSpin } from "naive-ui";

import StatusPill from "../../shared/ui/StatusPill.vue";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const toast = useToast();
const loading = ref(true);
const orders = ref([]);

onMounted(async () => {
  try {
    const res = await api.get("/api/customer/orders");
    orders.value = res.data.orders;
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <n-spin :show="loading">
    <n-empty v-if="!loading && !orders.length" description="暂无订单" />
    <n-space v-else vertical :size="12">
      <RouterLink
        v-for="o in orders"
        :key="o.order_no"
        :to="`/orders/${o.order_no}`"
        style="text-decoration:none;color:inherit"
      >
        <n-card hoverable>
          <div style="display:flex;justify-content:space-between;align-items:center" data-testid="order-row">
            <div>
              <strong>{{ o.order_no }}</strong>
              <div style="color:var(--c-text-muted);font-size:13px">{{ o.plan_name_snapshot || o.plan_name }}</div>
            </div>
            <StatusPill :tone="o.status_tone" :status="o.status" :label="o.status_label || o.status" />
          </div>
        </n-card>
      </RouterLink>
    </n-space>
  </n-spin>
</template>
