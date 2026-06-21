<script setup>
// The former per-port "tenant panel", now the customer's subscription detail.
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NCard, NDescriptions, NDescriptionsItem, NSpace, NSpin } from "naive-ui";

import CopyField from "../../shared/ui/CopyField.vue";
import StatusPill from "../../shared/ui/StatusPill.vue";
import TrafficRing from "../../shared/ui/TrafficRing.vue";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const props = defineProps({ id: { type: [String, Number], required: true } });
const toast = useToast();
const router = useRouter();

const loading = ref(true);
const renewing = ref(false);
const sub = ref(null);

async function load() {
  loading.value = true;
  try {
    const res = await api.get(`/api/customer/subscriptions/${props.id}`);
    sub.value = res.data.subscription;
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
}

async function renew() {
  renewing.value = true;
  try {
    const res = await api.post(`/api/customer/subscriptions/${props.id}/renew`);
    toast.success(res.message || "续费订单已创建。");
    if (router) router.push(`/orders/${res.data.order_no}`);
  } catch (error) {
    toast.error(error.message);
  } finally {
    renewing.value = false;
  }
}

onMounted(load);
defineExpose({ renew });
</script>

<template>
  <n-spin :show="loading">
    <div v-if="sub">
      <n-card>
        <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
          <TrafficRing :used="sub.traffic_usage_bytes" :limit="sub.traffic_limit_bytes" />
          <div>
            <n-space align="center">
              <h2 style="margin:0">端口 {{ sub.listen_port }}</h2>
              <StatusPill :status="sub.status" :label="sub.status_label || sub.status" />
            </n-space>
            <p style="color:var(--c-text-muted)">{{ sub.note || sub.plan_name }} · 到期 {{ sub.expires_at_display || "—" }}</p>
            <n-button
              v-if="sub.renewal_allowed"
              type="primary"
              :loading="renewing"
              data-testid="renew-btn"
              @click="renew"
            >
              续费
            </n-button>
          </div>
        </div>
      </n-card>

      <n-card title="订阅链接" style="margin-top:16px">
        <n-space vertical :size="12">
          <CopyField v-if="sub.access" label="Clash 订阅" :value="sub.access.tenant_subscription_clash_url" />
          <CopyField v-if="sub.access" label="V2Ray 订阅" :value="sub.access.tenant_subscription_v2ray_url" />
          <CopyField v-if="sub.access" label="VLESS 分享链接" :value="sub.access.share_link" />
        </n-space>
      </n-card>

      <n-card v-if="sub.access" title="访问凭据" style="margin-top:16px">
        <n-descriptions :column="1" label-placement="left">
          <n-descriptions-item label="租户用户名">{{ sub.access.tenant_username }}</n-descriptions-item>
          <n-descriptions-item label="租户密码">{{ sub.access.tenant_password }}</n-descriptions-item>
        </n-descriptions>
      </n-card>
    </div>
  </n-spin>
</template>
