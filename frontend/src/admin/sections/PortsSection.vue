<script>
import StatusPill from "../../shared/ui/StatusPill.vue";
import CopyField from "../../shared/ui/CopyField.vue";

// Port/tenant management. All state and actions come from the injected panel
// (PortsMixin + CoreMixin). Subscription/credential links use the shared
// CopyField; statuses use StatusPill.
export default {
  name: "PortsSection",
  components: { StatusPill, CopyField },
  inject: ["panel"],
  methods: {
    onCopyError() {
      this.panel.setFlash("浏览器未允许复制，请手动复制。", "error");
    },
  },
};
</script>

<template>
  <div class="a-section">
    <!-- Create port -->
    <div class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">CREATE</p>
        <h3>新增端口</h3>
        <p>快速生成新的 Xray Reality 监听入口，默认沿用当前节点参数。</p>
      </div>
      <form class="a-grid" @submit.prevent="panel.createPort">
        <label class="a-field">
          <span>监听端口</span>
          <input v-model="panel.createForm.listen_port" class="a-input" type="number" min="1" max="65535" required />
        </label>
        <label class="a-field">
          <span>到期时间（{{ panel.meta.timezone_label }}）</span>
          <input v-model="panel.createForm.expires_at" class="a-input" type="datetime-local" />
        </label>
        <label class="a-field">
          <span>流量上限</span>
          <input v-model="panel.createForm.traffic_limit" class="a-input" type="text" placeholder="例如 10G / 500MB" />
        </label>
        <label class="a-field full">
          <span>租户备注</span>
          <input v-model="panel.createForm.note" class="a-input" type="text" maxlength="200" placeholder="例如：客户A / 北京节点" />
        </label>
        <div class="a-actions">
          <button class="a-btn primary" type="submit" :disabled="panel.isBusy('create-port')">
            {{ panel.isBusy("create-port") ? "创建中..." : "创建端口" }}
          </button>
        </div>
      </form>
    </div>

    <!-- Ports list -->
    <div class="a-card">
      <div class="a-card-head ports-head">
        <div>
          <p class="eyebrow">PORTS</p>
          <h3>端口管理</h3>
          <p>当前显示 {{ panel.filteredPorts.length }} / {{ panel.summary.total_ports || 0 }} 个端口。</p>
        </div>
        <div class="ports-toolbar">
          <input v-model.trim="panel.filters.query" class="a-input" type="search" placeholder="搜索端口 / 备注 / 状态" />
          <div class="status-filters">
            <button
              v-for="option in panel.statusOptions"
              :key="option.value"
              class="a-btn ghost filter"
              :class="{ active: panel.filters.status === option.value }"
              type="button"
              @click="panel.filters.status = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="!panel.ports.length" class="a-empty">当前还没有端口配置，先创建第一个监听端口。</div>
      <div v-else-if="!panel.filteredPorts.length" class="a-empty">当前筛选条件下没有匹配的端口。</div>
      <div v-else class="port-list">
        <button
          v-for="port in panel.filteredPorts"
          :key="port.id"
          class="port-card"
          :class="{ selected: panel.selectedPort && panel.selectedPort.id === port.id }"
          type="button"
          @click="panel.selectPort(port.id)"
        >
          <div class="port-card-head">
            <strong>端口 {{ port.listen_port }}</strong>
            <status-pill :status="port.status" :label="port.status_label" />
          </div>
          <p class="port-card-note">{{ port.note || "未填写备注" }}</p>
          <div class="port-card-stats">
            <span>累计 {{ port.traffic_used_display }}</span>
            <span>连接 {{ port.total_connections }}</span>
            <span>到期 {{ port.expires_at_display }}</span>
          </div>
        </button>
      </div>
    </div>

    <!-- Selected port detail -->
    <div v-if="panel.selectedPort" id="port-detail-panel" class="a-card">
      <div class="a-card-head">
        <p class="eyebrow">DETAIL</p>
        <h3>端口 {{ panel.selectedPort.listen_port }}</h3>
        <p>{{ panel.selectedPort.note ? panel.selectedPort.note + " · " : "" }}租户直接接入当前 Xray Reality 入站。</p>
      </div>

      <div class="a-tiles">
        <div class="a-tile"><span>累计入站</span><strong>{{ panel.humanBytes(panel.selectedPort.total_bytes_received) }}</strong></div>
        <div class="a-tile"><span>累计出站</span><strong>{{ panel.humanBytes(panel.selectedPort.total_bytes_sent) }}</strong></div>
        <div class="a-tile"><span>累计连接</span><strong>{{ panel.selectedPort.total_connections }}</strong></div>
        <div class="a-tile"><span>到期时间</span><strong>{{ panel.selectedPort.expires_at_display }}</strong></div>
      </div>

      <form class="a-grid" @submit.prevent="panel.updatePort(panel.selectedPort)">
        <label class="a-field">
          <span>监听端口</span>
          <input v-model="panel.selectedPort.form.listen_port" class="a-input" type="number" min="1" max="65535" required />
        </label>
        <label class="a-field">
          <span>到期时间（{{ panel.meta.timezone_label }}）</span>
          <input v-model="panel.selectedPort.form.expires_at" class="a-input" type="datetime-local" />
        </label>
        <label class="a-field">
          <span>流量上限</span>
          <input v-model="panel.selectedPort.form.traffic_limit" class="a-input" type="text" placeholder="例如 10G / 500MB" />
        </label>
        <label class="a-field full">
          <span>租户备注</span>
          <input v-model="panel.selectedPort.form.note" class="a-input" type="text" maxlength="200" />
        </label>
        <div class="a-actions">
          <button class="a-btn primary" type="submit" :disabled="panel.isBusy('update:' + panel.selectedPort.id)">
            {{ panel.isBusy("update:" + panel.selectedPort.id) ? "保存中..." : "保存修改" }}
          </button>
        </div>
      </form>

      <div v-if="panel.selectedPort.access" class="access-block">
        <div class="a-card-head">
          <h3>租户面板与订阅输出</h3>
          <p>当前端口的登录地址、账号密码和订阅地址独立生成。</p>
        </div>
        <div class="a-actions">
          <button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-tenant:' + panel.selectedPort.id)" @click="panel.rotateTenantToken(panel.selectedPort)">重置面板地址</button>
          <button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-credentials:' + panel.selectedPort.id)" @click="panel.rotateTenantCredentials(panel.selectedPort)">重置账号密码</button>
          <button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-subscription:' + panel.selectedPort.id)" @click="panel.rotatePortSubscription(panel.selectedPort)">重置订阅地址</button>
        </div>
        <div class="access-grid">
          <copy-field label="租户登录地址" :value="panel.selectedPort.access.tenant_login_url" @error="onCopyError" />
          <copy-field label="租户用户名" :value="panel.selectedPort.access.tenant_username" @error="onCopyError" />
          <copy-field label="租户密码" :value="panel.selectedPort.access.tenant_password" @error="onCopyError" />
          <copy-field label="Clash 订阅" :value="panel.selectedPort.access.tenant_subscription_clash_url" @error="onCopyError" />
          <copy-field label="V2Ray 订阅" :value="panel.selectedPort.access.tenant_subscription_v2ray_url" @error="onCopyError" />
          <copy-field label="VLESS 直连分享" :value="panel.selectedPort.access.share_link" @error="onCopyError" />
        </div>
      </div>

      <div class="a-actions">
        <button
          v-if="panel.selectedPort.status === 'quota'"
          class="a-btn secondary"
          type="button"
          :disabled="panel.isBusy('reset:' + panel.selectedPort.id)"
          @click="panel.resetTraffic(panel.selectedPort)"
        >
          {{ panel.isBusy("reset:" + panel.selectedPort.id) ? "处理中..." : "重置流量并启用" }}
        </button>
        <button
          v-if="panel.canTogglePort(panel.selectedPort)"
          class="a-btn secondary"
          type="button"
          :disabled="panel.isBusy('toggle:' + panel.selectedPort.id)"
          @click="panel.togglePort(panel.selectedPort)"
        >
          {{ panel.isBusy("toggle:" + panel.selectedPort.id) ? "处理中..." : panel.toggleLabel(panel.selectedPort) }}
        </button>
        <button class="a-btn danger" type="button" :disabled="panel.isBusy('delete:' + panel.selectedPort.id)" @click="panel.deletePort(panel.selectedPort)">
          {{ panel.isBusy("delete:" + panel.selectedPort.id) ? "删除中..." : "删除端口" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ports-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.ports-toolbar {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 260px;
}
.status-filters {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.a-btn.filter {
  height: 30px;
  padding: 0 12px;
  font-size: 13px;
}
.a-btn.filter.active {
  background: var(--c-primary-soft);
  border-color: var(--c-primary);
  color: var(--c-primary-strong);
}
.port-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-3);
}
.port-card {
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--space-4);
  border: 1px solid var(--c-line);
  border-radius: var(--r-md);
  background: var(--c-surface);
  cursor: pointer;
}
.port-card.selected {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 1px var(--c-primary);
}
.port-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.port-card-note {
  margin: 0;
  font-size: 13px;
  color: var(--c-text-muted);
}
.port-card-stats {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--c-text-soft);
}
.access-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--c-line);
}
.access-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}
</style>
