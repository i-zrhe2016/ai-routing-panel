<script setup>
import { onMounted, ref } from "vue";
import { NCard, NEmpty, NGi, NGrid, NSpin } from "naive-ui";

import { humanBytes } from "../../shared/formatters.js";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const toast = useToast();
const loading = ref(true);
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

</script>

<template>
  <n-spin :show="loading">
    <p v-if="!loading" style="color:var(--c-text-muted);margin:0 0 16px">
      套餐信息仅供查看，如需开通请联系管理员。
    </p>
    <n-empty v-if="!loading && !plans.length" description="暂无可用套餐" />
    <n-grid v-else cols="1 s:2 l:3" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
      <n-gi v-for="plan in plans" :key="plan.slug">
        <n-card :title="plan.name">
          <p style="color:var(--c-text-muted)">{{ plan.description }}</p>
          <p><strong style="font-size:22px">¥{{ yuan(plan.price_fen) }}</strong></p>
          <p style="font-size:13px;color:var(--c-text-muted)">
            {{ plan.duration_days }} 天 · {{ plan.traffic_limit_bytes ? humanBytes(plan.traffic_limit_bytes) : "不限流量" }}
          </p>
        </n-card>
      </n-gi>
    </n-grid>
  </n-spin>
</template>
