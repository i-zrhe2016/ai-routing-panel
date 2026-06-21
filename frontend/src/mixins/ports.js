import { createEmptyPortForm } from "../utils.js";

// Port/tenant state and actions: filtering, selection, CRUD, and token/credential
// rotation. `filteredPorts`/`selectedPort` and the selection watcher live here.
export const PortsMixin = {
  data() {
    return {
      ports: [],
      createForm: createEmptyPortForm(),
      filters: {
        query: "",
        status: "all",
      },
      statusOptions: [
        { value: "all", label: "全部" },
        { value: "active", label: "运行中" },
        { value: "disabled", label: "已停用" },
        { value: "expired", label: "已过期" },
        { value: "quota", label: "已达上限" },
      ],
      selectedPortId: null,
    };
  },

  computed: {
    filteredPorts() {
      const query = String(this.filters.query || "").trim().toLowerCase();
      return this.ports.filter((port) => {
        if (this.filters.status !== "all" && port.status !== this.filters.status) {
          return false;
        }
        if (!query) {
          return true;
        }
        const haystacks = [
          String(port.listen_port || ""),
          String(port.note || ""),
          String(port.upstream_host || ""),
          String(port.status_label || ""),
        ];
        return haystacks.some((item) => item.toLowerCase().includes(query));
      });
    },

    selectedPort() {
      return this.filteredPorts.find((port) => port.id === this.selectedPortId) || null;
    },
  },

  watch: {
    filteredPorts: {
      immediate: true,
      handler(nextPorts) {
        this.syncSelection(nextPorts);
      },
    },
  },

  methods: {
    preparePort(port) {
      return {
        ...port,
        form: {
          listen_port: String(port.listen_port ?? ""),
          expires_at: port.expires_at_input || "",
          traffic_limit: port.traffic_limit_input || "",
          note: port.note || "",
        },
      };
    },

    trafficToday(port) {
      return Number(port.today_bytes_received || 0) + Number(port.today_bytes_sent || 0);
    },

    syncSelection(visiblePorts = this.filteredPorts) {
      if (visiblePorts.some((port) => port.id === this.selectedPortId)) {
        return;
      }
      this.selectedPortId = visiblePorts.length ? visiblePorts[0].id : null;
    },

    selectPort(portId) {
      this.selectedPortId = portId;
      if (window.matchMedia("(max-width: 1240px)").matches) {
        window.requestAnimationFrame(() => {
          const detailPanel = document.getElementById("port-detail-panel");
          if (detailPanel) {
            detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        });
      }
    },

    resetCreateForm() {
      this.createForm = createEmptyPortForm();
    },

    async createPort() {
      await this.runAction("create-port", async () => {
        const createdListenPort = String(this.createForm.listen_port || "");
        const data = await this.requestJson("/api/ports", {
          method: "POST",
          body: JSON.stringify(this.createForm),
        });
        this.applyResponse(data);
        if (createdListenPort) {
          const createdPort = this.ports.find((port) => String(port.listen_port) === createdListenPort);
          if (createdPort) {
            this.selectedPortId = createdPort.id;
          }
        }
        this.resetCreateForm();
      });
    },

    async updatePort(port) {
      await this.runAction(`update:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}`, {
          method: "PUT",
          body: JSON.stringify(port.form),
        });
        this.applyResponse(data);
      });
    },

    canTogglePort(port) {
      return port.status === "active" || port.status === "disabled";
    },

    toggleLabel(port) {
      return port.status === "active" ? "停用端口" : "启用端口";
    },

    async togglePort(port) {
      await this.runAction(`toggle:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}/toggle`, {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },

    async deletePort(port) {
      if (!window.confirm(`确认删除端口 ${port.listen_port} 吗？`)) {
        return;
      }
      await this.runAction(`delete:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}`, {
          method: "DELETE",
        });
        this.applyResponse(data);
      });
    },

    async resetTraffic(port) {
      if (!window.confirm(`确认重置端口 ${port.listen_port} 的流量并重新启用吗？`)) {
        return;
      }
      await this.runAction(`reset:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}/reset-traffic`, {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },

    async rotateTenantToken(port) {
      if (!window.confirm(`确认重置端口 ${port.listen_port} 的租户面板地址吗？旧地址会立即失效。`)) {
        return;
      }
      await this.runAction(`rotate-tenant:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}/rotate-tenant-token`, {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },

    async rotateTenantCredentials(port) {
      if (!window.confirm(`确认重置端口 ${port.listen_port} 的租户登录账号和密码吗？旧凭据会立即失效。`)) {
        return;
      }
      await this.runAction(`rotate-credentials:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}/rotate-tenant-credentials`, {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },

    async rotatePortSubscription(port) {
      if (!window.confirm(`确认重置端口 ${port.listen_port} 的订阅地址吗？旧地址会立即失效。`)) {
        return;
      }
      await this.runAction(`rotate-subscription:${port.id}`, async () => {
        const data = await this.requestJson(`/api/ports/${port.id}/rotate-subscription-token`, {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },
  },
};
