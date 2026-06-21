<script setup>
// Token-mode subscriber view, mounted at /tenant/<token>. Shows ONE subscription
// (the former tenant panel) read-only, with an inline per-port login card when the
// token has no session yet. Shares the presentation with the account-mode
// SubscriptionDetail via the same shared components (TrafficRing/StatusPill/CopyField).
import { onMounted, reactive, ref } from "vue";
import {
  NButton,
  NCard,
  NConfigProvider,
  NDescriptions,
  NDescriptionsItem,
  NForm,
  NFormItem,
  NInput,
  NSpace,
  NSpin,
} from "naive-ui";

import { createApiClient } from "../shared/apiClient.js";
import { naiveThemeOverrides } from "../shared/tokens.js";
import CopyField from "../shared/ui/CopyField.vue";
import StatusPill from "../shared/ui/StatusPill.vue";
import TrafficRing from "../shared/ui/TrafficRing.vue";

const props = defineProps({
  tenantToken: { type: String, required: true },
  csrfToken: { type: String, default: "" },
});

const themeOverrides = naiveThemeOverrides();
// A 401 here must NOT bounce to the account login — it renders the inline card.
const api = createApiClient({ csrfToken: props.csrfToken, onUnauthorized: () => {} });

const loading = ref(true);
const submitting = ref(false);
const needLogin = ref(false);
const sub = ref(null);
const errorMsg = ref("");
const form = reactive({ username: "", password: "" });

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await api.get(`/api/tenant/${props.tenantToken}/subscription`);
    sub.value = res.data.subscription;
    needLogin.value = false;
  } catch (error) {
    if (error.status === 401) needLogin.value = true;
    else errorMsg.value = error.message;
  } finally {
    loading.value = false;
  }
}

async function doLogin() {
  submitting.value = true;
  errorMsg.value = "";
  try {
    await api.post(`/api/tenant/${props.tenantToken}/login`, { username: form.username, password: form.password });
    await load();
  } catch (error) {
    errorMsg.value = error.message;
  } finally {
    submitting.value = false;
  }
}

onMounted(load);
defineExpose({ load, doLogin });
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <div style="max-width: 880px; margin: 0 auto; padding: 24px">
      <header style="margin-bottom: 16px"><strong style="font-size: 18px">订阅</strong></header>

      <n-spin :show="loading">
        <n-card v-if="needLogin" title="租户登录" data-testid="tenant-login">
          <n-form @submit.prevent="doLogin">
            <n-form-item label="租户用户名">
              <n-input v-model:value="form.username" data-testid="tenant-username" />
            </n-form-item>
            <n-form-item label="租户密码">
              <n-input v-model:value="form.password" type="password" data-testid="tenant-password" />
            </n-form-item>
            <n-button type="primary" :loading="submitting" data-testid="tenant-login-btn" @click="doLogin">登录</n-button>
            <p v-if="errorMsg" style="color: var(--c-danger); margin-top: 8px">{{ errorMsg }}</p>
          </n-form>
        </n-card>

        <div v-else-if="sub">
          <n-card>
            <div style="display: flex; gap: 24px; align-items: center; flex-wrap: wrap">
              <TrafficRing :used="sub.traffic_usage_bytes" :limit="sub.traffic_limit_bytes" />
              <div>
                <n-space align="center">
                  <h2 style="margin: 0">端口 {{ sub.listen_port }}</h2>
                  <StatusPill :status="sub.status" :label="sub.status_label || sub.status" />
                </n-space>
                <p style="color: var(--c-text-muted)">{{ sub.note || sub.plan_name }} · 到期 {{ sub.expires_at_display || "—" }}</p>
              </div>
            </div>
          </n-card>

          <n-card v-if="sub.access" title="订阅链接" style="margin-top: 16px">
            <n-space vertical :size="12">
              <CopyField label="Clash 订阅" :value="sub.access.tenant_subscription_clash_url" />
              <CopyField label="V2Ray 订阅" :value="sub.access.tenant_subscription_v2ray_url" />
              <CopyField label="VLESS 分享链接" :value="sub.access.share_link" />
            </n-space>
          </n-card>

          <n-card v-if="sub.access" title="访问凭据" style="margin-top: 16px">
            <n-descriptions :column="1" label-placement="left">
              <n-descriptions-item label="租户用户名">{{ sub.access.tenant_username }}</n-descriptions-item>
              <n-descriptions-item label="租户密码">{{ sub.access.tenant_password }}</n-descriptions-item>
            </n-descriptions>
          </n-card>
        </div>

        <p v-else-if="errorMsg" style="color: var(--c-danger)">{{ errorMsg }}</p>
      </n-spin>
    </div>
  </n-config-provider>
</template>
