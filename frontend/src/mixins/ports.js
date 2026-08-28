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

    findPortByListenPort(listenPort) {
      const target = String(listenPort ?? "").trim();
      if (!target) return null;
      return this.ports.find((port) => String(port.listen_port) === target) || null;
    },

    revealPort(port) {
      if (!port) return;
      this.filters.query = "";
      this.filters.status = "all";
      this.selectedPortId = port.id;
    },

    async refreshPortsFromDashboard() {
      const data = await this.requestJson("/api/dashboard");
      this.applyDashboard(data.dashboard || {});
    },

    async createPort() {
      await this.runAction("create-port", async () => {
        const createdListenPort = String(this.createForm.listen_port ?? "").trim();
        let data;
        try {
          data = await this.requestJson("/api/ports", {
            method: "POST",
            body: JSON.stringify(this.createForm),
          });
        } catch (error) {
          if (error.status === 409 && createdListenPort) {
            await this.refreshPortsFromDashboard();
            const existingPort = this.findPortByListenPort(createdListenPort);
            if (existingPort) {
              this.revealPort(existingPort);
              this.setFlash("监听端口已存在，已选中已有端口。", "info");
              this.resetCreateForm();
              return;
            }
          }
          throw error;
        }
        this.applyResponse(data);
        const createdPort = this.findPortByListenPort(createdListenPort);
        if (createdPort) {
          this.revealPort(createdPort);
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
        let data;
        try {
          data = await this.requestJson(`/api/ports/${port.id}`, {
            method: "DELETE",
          });
        } catch (error) {
          if (error.status === 400 && error.message === "端口记录不存在。") {
            await this.refreshPortsFromDashboard();
            this.setFlash("端口已不存在，列表已刷新。", "info");
            return;
          }
          throw error;
        }
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
