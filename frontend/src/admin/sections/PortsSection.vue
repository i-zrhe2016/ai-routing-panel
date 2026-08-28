<script>
import StatusPill from "../../shared/ui/StatusPill.vue";
import CopyField from "../../shared/ui/CopyField.vue";

export default {
  name: "PortsSection",
  components: { CopyField, StatusPill },
  inject: ["panel"],
  methods: {
    onCopyError() {
      this.panel.setFlash("浏览器未允许复制，请手动复制。", "error");
    },
  },
};
</script>

<template>
  <div class="workspace-section ports-workspace">
    <section class="panel-block create-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">NEW LISTENER</p>
          <h2>新增端口</h2>
          <p class="section-description">生成新的 Xray Reality 监听入口，默认沿用当前节点参数。</p>
        </div>
        <span class="section-index">01</span>
      </div>
      <form class="form-grid" @submit.prevent="panel.createPort">
        <label class="field"><span>监听端口</span><input v-model.number="panel.createForm.listen_port" class="a-input" type="number" min="1" max="65535" required /></label>
        <label class="field"><span>到期时间（{{ panel.meta.timezone_label }}）</span><input v-model="panel.createForm.expires_at" class="a-input" type="datetime-local" /></label>
        <label class="field"><span>流量上限</span><input v-model.trim="panel.createForm.traffic_limit" class="a-input" type="text" placeholder="例如 10G / 500MB" /></label>
        <label class="field field--wide"><span>租户备注</span><input v-model.trim="panel.createForm.note" class="a-input" type="text" maxlength="200" placeholder="例如：客户A / 北京节点" /></label>
        <div class="form-actions"><button class="a-btn primary" type="submit" :disabled="panel.isBusy('create-port')">{{ panel.isBusy("create-port") ? "创建中..." : "创建端口" }}</button></div>
      </form>
    </section>

    <div class="resource-layout">
      <section class="panel-block resource-list-panel">
        <div class="section-heading ports-head">
          <div>
            <p class="section-kicker">PORT INVENTORY</p>
            <h2>端口管理</h2>
            <p class="section-description">当前显示 {{ panel.filteredPorts.length }} / {{ panel.summary.total_ports || 0 }} 个端口。</p>
          </div>
          <div class="ports-toolbar">
            <label class="search-field"><span class="sr-only">搜索端口</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg><input v-model.trim="panel.filters.query" class="a-input" type="search" placeholder="搜索端口 / 备注 / 状态" /></label>
            <div class="status-filters" role="group" aria-label="端口状态筛选">
              <button v-for="option in panel.statusOptions" :key="option.value" class="filter-button" :class="{ active: panel.filters.status === option.value }" type="button" @click="panel.filters.status = option.value">{{ option.label }}</button>
            </div>
          </div>
        </div>

        <div v-if="!panel.ports.length" class="a-empty">当前还没有端口配置，先创建第一个监听端口。</div>
        <div v-else-if="!panel.filteredPorts.length" class="a-empty">当前筛选条件下没有匹配的端口。</div>
        <div v-else class="port-list">
          <button v-for="port in panel.filteredPorts" :key="port.id" class="port-card" :class="{ selected: panel.selectedPort && panel.selectedPort.id === port.id }" type="button" @click="panel.selectPort(port.id)">
            <div class="port-card-head"><span class="port-card-number">:{{ port.listen_port }}</span><status-pill :status="port.status" :label="port.status_label" /></div>
            <strong>{{ port.note || "未填写备注" }}</strong>
            <div class="port-card-stats"><span>累计 {{ port.traffic_used_display }}</span><span>{{ port.total_connections }} 连接</span><span>{{ port.expires_at_display }}</span></div>
          </button>
        </div>
      </section>

      <section v-if="panel.selectedPort" id="port-detail-panel" class="panel-block detail-panel">
        <div class="detail-panel__header">
          <div><p class="section-kicker">SELECTED PORT</p><h2>端口 {{ panel.selectedPort.listen_port }}</h2><p class="section-description">{{ panel.selectedPort.note ? panel.selectedPort.note + " · " : "" }}租户直接接入当前 Xray Reality 入站。</p></div>
          <status-pill :status="panel.selectedPort.status" :label="panel.selectedPort.status_label" />
        </div>
        <div class="detail-metrics">
          <div><span>累计入站</span><strong>{{ panel.humanBytes(panel.selectedPort.total_bytes_received) }}</strong></div>
          <div><span>累计出站</span><strong>{{ panel.humanBytes(panel.selectedPort.total_bytes_sent) }}</strong></div>
          <div><span>累计连接</span><strong>{{ panel.selectedPort.total_connections }}</strong></div>
          <div><span>到期时间</span><strong>{{ panel.selectedPort.expires_at_display }}</strong></div>
        </div>

        <form class="form-grid" @submit.prevent="panel.updatePort(panel.selectedPort)">
          <label class="field"><span>监听端口</span><input v-model.number="panel.selectedPort.form.listen_port" class="a-input" type="number" min="1" max="65535" required /></label>
          <label class="field"><span>到期时间（{{ panel.meta.timezone_label }}）</span><input v-model="panel.selectedPort.form.expires_at" class="a-input" type="datetime-local" /></label>
          <label class="field"><span>流量上限</span><input v-model.trim="panel.selectedPort.form.traffic_limit" class="a-input" type="text" placeholder="例如 10G / 500MB" /></label>
          <label class="field field--wide"><span>租户备注</span><input v-model.trim="panel.selectedPort.form.note" class="a-input" type="text" maxlength="200" /></label>
          <div class="form-actions"><button class="a-btn primary" type="submit" :disabled="panel.isBusy('update:' + panel.selectedPort.id)">{{ panel.isBusy("update:" + panel.selectedPort.id) ? "保存中..." : "保存修改" }}</button></div>
        </form>

        <div v-if="panel.selectedPort.access" class="access-block">
          <div class="section-heading"><div><p class="section-kicker">TENANT DELIVERY</p><h3>租户面板与订阅输出</h3><p class="section-description">当前端口的登录地址、账号密码和订阅地址独立生成。</p></div></div>
          <div class="action-row"><button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-tenant:' + panel.selectedPort.id)" @click="panel.rotateTenantToken(panel.selectedPort)">重置面板地址</button><button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-credentials:' + panel.selectedPort.id)" @click="panel.rotateTenantCredentials(panel.selectedPort)">重置账号密码</button><button class="a-btn secondary" type="button" :disabled="panel.isBusy('rotate-subscription:' + panel.selectedPort.id)" @click="panel.rotatePortSubscription(panel.selectedPort)">重置订阅地址</button></div>
          <div class="access-grid"><copy-field label="租户登录地址" :value="panel.selectedPort.access.tenant_login_url" @error="onCopyError" /><copy-field label="租户用户名" :value="panel.selectedPort.access.tenant_username" @error="onCopyError" /><copy-field label="租户密码" :value="panel.selectedPort.access.tenant_password" @error="onCopyError" /><copy-field label="Clash 订阅" :value="panel.selectedPort.access.tenant_subscription_clash_url" @error="onCopyError" /><copy-field label="V2Ray 订阅" :value="panel.selectedPort.access.tenant_subscription_v2ray_url" @error="onCopyError" /><copy-field label="VLESS 直连分享" :value="panel.selectedPort.access.share_link" @error="onCopyError" /></div>
        </div>

        <div class="action-row detail-actions">
          <button v-if="panel.selectedPort.status === 'quota'" class="a-btn secondary" type="button" :disabled="panel.isBusy('reset:' + panel.selectedPort.id)" @click="panel.resetTraffic(panel.selectedPort)">{{ panel.isBusy("reset:" + panel.selectedPort.id) ? "处理中..." : "重置流量并启用" }}</button>
          <button v-if="panel.canTogglePort(panel.selectedPort)" class="a-btn secondary" type="button" :disabled="panel.isBusy('toggle:' + panel.selectedPort.id)" @click="panel.togglePort(panel.selectedPort)">{{ panel.isBusy("toggle:" + panel.selectedPort.id) ? "处理中..." : panel.toggleLabel(panel.selectedPort) }}</button>
          <button class="a-btn danger" type="button" :disabled="panel.isBusy('delete:' + panel.selectedPort.id)" @click="panel.deletePort(panel.selectedPort)">{{ panel.isBusy("delete:" + panel.selectedPort.id) ? "删除中..." : "删除端口" }}</button>
        </div>
      </section>
      <section v-else class="panel-block detail-panel detail-panel--empty"><span class="empty-illustration" aria-hidden="true">+</span><h2>选择一个端口</h2><p>从左侧列表选择端口，查看连接、到期和租户交付信息。</p></section>
    </div>
  </div>
</template>
