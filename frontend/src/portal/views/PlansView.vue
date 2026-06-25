<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NCard, NEmpty, NGi, NGrid, NSpin } from "naive-ui";

import { humanBytes } from "../../shared/formatters.js";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const toast = useToast();
const router = useRouter();
const loading = ref(true);
const buying = ref("");
const plans = ref([]);

function yuan(fen) {
  return (Number(fen || 0) / 100).toFixed(2);
}

onMounted(async () => {
  try {
    const res = await api.get("/api/customer/plans");
    plans.value = res.data.plans;
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
});

async function buy(plan) {
  buying.value = plan.slug;
  try {
    const res = await api.post("/api/customer/orders", { plan_slug: plan.slug });
    toast.success(res.message || "订单已创建。");
    if (router) router.push(`/orders/${res.data.order_no}`);
  } catch (error) {
    toast.error(error.message);
  } finally {
    buying.value = "";
  }
}
</script>

<template>
  <n-spin :show="loading">
    <n-empty v-if="!loading && !plans.length" description="暂无可购买的套餐" />
    <n-grid v-else cols="1 s:2 l:3" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
      <n-gi v-for="plan in plans" :key="plan.slug">
        <n-card :title="plan.name">
          <p style="color:var(--c-text-muted)">{{ plan.description }}</p>
          <p><strong style="font-size:22px">¥{{ yuan(plan.price_fen) }}</strong></p>
          <p style="font-size:13px;color:var(--c-text-muted)">
            {{ plan.duration_days }} 天 · {{ plan.traffic_limit_bytes ? humanBytes(plan.traffic_limit_bytes) : "不限流量" }}
          </p>
          <n-button type="primary" block :loading="buying === plan.slug" @click="buy(plan)">购买</n-button>
        </n-card>
      </n-gi>
    </n-grid>
  </n-spin>
</template>
