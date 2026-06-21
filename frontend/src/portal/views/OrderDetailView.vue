<script setup>
import { computed, onMounted, ref } from "vue";
import { NAlert, NButton, NCard, NDescriptions, NDescriptionsItem, NImage, NSpace, NSpin, NUpload } from "naive-ui";

import StatusPill from "../../shared/ui/StatusPill.vue";
import { api } from "../store.js";
import { useToast } from "../notify.js";

const props = defineProps({ orderNo: { type: String, required: true } });
const toast = useToast();

const loading = ref(true);
const submitting = ref(false);
const order = ref(null);
const settings = ref({});
const fileList = ref([]);
const payerNote = ref("");

const canSubmitProof = computed(
  () => order.value && ["pending_payment", "payment_rejected"].includes(order.value.status),
);

async function load() {
  loading.value = true;
  try {
    const res = await api.get(`/api/customer/orders/${props.orderNo}`);
    order.value = res.data.order;
    settings.value = res.data.commerce_settings || {};
  } catch (error) {
    toast.error(error.message);
  } finally {
    loading.value = false;
  }
}

async function submitProof() {
  const file = fileList.value[0]?.file;
  if (!file) {
    toast.error("请先选择支付截图。");
    return;
  }
  submitting.value = true;
  try {
    const form = new FormData();
    form.append("proof_image", file);
    form.append("payer_note", payerNote.value);
    const res = await api.postForm(`/api/customer/orders/${props.orderNo}/payment-proof`, form);
    toast.success(res.message || "支付凭证已提交。");
    fileList.value = [];
    await load();
  } catch (error) {
    toast.error(error.message);
  } finally {
    submitting.value = false;
  }
}

onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <div v-if="order">
      <n-card>
        <n-space align="center">
          <h2 style="margin:0">{{ order.order_no }}</h2>
          <StatusPill :tone="order.status_tone" :status="order.status" :label="order.status_label || order.status" />
        </n-space>
        <n-descriptions :column="1" label-placement="left" style="margin-top:12px">
          <n-descriptions-item label="套餐">{{ order.plan_name_snapshot || order.plan_name }}</n-descriptions-item>
          <n-descriptions-item label="到期">{{ order.expires_at_display || "—" }}</n-descriptions-item>
        </n-descriptions>
        <n-alert v-if="order.rejection_reason" type="error" style="margin-top:12px">
          驳回原因：{{ order.rejection_reason }}
        </n-alert>
      </n-card>

      <n-card v-if="canSubmitProof" title="支付与上传凭证" style="margin-top:16px">
        <p v-if="settings.payment_instructions" style="white-space:pre-wrap">{{ settings.payment_instructions }}</p>
        <n-image v-if="settings.payment_qr_code_url" :src="settings.payment_qr_code_url" width="180" />
        <n-space vertical :size="12" style="margin-top:12px">
          <n-upload v-model:file-list="fileList" :max="1" :default-upload="false" accept="image/*">
            <n-button>选择支付截图</n-button>
          </n-upload>
          <n-button type="primary" :loading="submitting" @click="submitProof">提交凭证</n-button>
        </n-space>
      </n-card>
    </div>
  </n-spin>
</template>
