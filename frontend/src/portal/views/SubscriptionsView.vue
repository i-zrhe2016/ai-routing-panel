<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { NCard, NEmpty, NSpace, NSpin } from "naive-ui";

import StatusPill from "../../shared/ui/StatusPill.vue";
import { humanBytes } from "../../shared/formatters.js";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const toast = useToast();
const loading = ref(true);
const subscriptions = ref([]);

onMounted(async () => {
  try {
    const res = await api.get("/api/customer/subscriptions");
    subscriptions.value = res.data.subscriptions;
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <n-spin :show="loading">
    <n-empty v-if="!loading && !subscriptions.length" description="还没有订阅，请联系管理员开通。" />
    <n-space v-else vertical :size="12">
      <RouterLink
        v-for="s in subscriptions"
        :key="s.id"
        :to="`/subscriptions/${s.id}`"
        style="text-decoration:none;color:inherit"
      >
        <n-card hoverable>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <strong>端口 {{ s.listen_port }}</strong>
              <div style="color:var(--c-text-muted);font-size:13px">{{ s.note || s.plan_name }}</div>
            </div>
            <n-space align="center">
              <span style="font-size:13px;color:var(--c-text-muted)">
                {{ humanBytes(s.traffic_usage_bytes) }} / {{ s.traffic_limit_bytes ? humanBytes(s.traffic_limit_bytes) : "不限" }}
              </span>
              <StatusPill :status="s.status" :label="s.status_label || s.status" />
            </n-space>
          </div>
        </n-card>
      </RouterLink>
    </n-space>
  </n-spin>
</template>
