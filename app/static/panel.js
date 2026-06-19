function createEmptyPortForm() {
  return {
    listen_port: "",
    expires_at: "",
    traffic_limit: "",
    note: "",
  };
}

function fallbackCopyText(value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  return copied;
}

function createPanelApp(initialState) {
  return {
    data() {
      return {
        meta: {},
        summary: {},
        subscription: {},
        ports: [],
        dataPlaneStatus: {},
        aiRoutingStatus: {},
        flash: { message: "", level: "info" },
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
        busyActions: {},
        selectedPortId: null,
        copiedKey: "",
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
      applyDashboard(dashboard) {
        this.meta = dashboard.meta || {};
        this.summary = dashboard.summary || {};
        this.subscription = dashboard.subscription || {};
        this.dataPlaneStatus = dashboard.meta?.data_plane_status || {};
        this.aiRoutingStatus = dashboard.meta?.ai_routing_status || {};
        this.flash = dashboard.flash || { message: "", level: "info" };
        this.ports = (dashboard.ports || []).map((port) => this.preparePort(port));
      },

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

      clearFlash() {
        this.flash = { message: "", level: "info" };
      },

      setFlash(message, level = "info") {
        this.flash = { message, level };
      },

      humanBytes(value) {
        let size = Number(value || 0);
        const units = ["B", "KB", "MB", "GB", "TB", "PB"];
        for (let index = 0; index < units.length; index += 1) {
          const unit = units[index];
          if (size < 1024 || unit === units[units.length - 1]) {
            if (unit === "B") {
              return `${Math.trunc(size)} ${unit}`;
            }
            return `${size.toFixed(2)} ${unit}`;
          }
          size /= 1024;
        }
        return "0 B";
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
        if (window.matchMedia("(max-width: 1180px)").matches) {
          window.requestAnimationFrame(() => {
            const detailPanel = document.getElementById("port-detail-panel");
            if (detailPanel) {
              detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
            }
          });
        }
      },

      isBusy(key) {
        return Boolean(this.busyActions[key]);
      },

      async runAction(key, callback) {
        if (this.isBusy(key)) {
          return;
        }
        this.busyActions[key] = true;
        try {
          await callback();
        } catch (error) {
          this.setFlash(error.message || "操作失败。", "error");
        } finally {
          delete this.busyActions[key];
        }
      },

      async requestJson(url, options = {}) {
        const headers = {
          Accept: "application/json",
          ...(options.headers || {}),
        };
        if (options.body !== undefined) {
          headers["Content-Type"] = "application/json";
        }
        const response = await fetch(url, {
          method: options.method || "GET",
          headers,
          body: options.body,
          credentials: "same-origin",
        });
        const rawText = await response.text();
        let data = {};
        if (rawText) {
          try {
            data = JSON.parse(rawText);
          } catch (_error) {
            if (response.status === 401) {
              window.location.assign("/login");
              throw new Error("登录已失效，请重新登录。");
            }
            throw new Error(`服务返回了无法解析的响应（${response.status}）。`);
          }
        }
        if (response.status === 401) {
          window.location.assign(data.login_url || "/login");
          throw new Error(data.message || "登录已失效，请重新登录。");
        }
        if (!response.ok || data.ok === false) {
          throw new Error(data.message || `请求失败（${response.status}）。`);
        }
        return data;
      },

      applyResponse(data) {
        if (data.dashboard) {
          this.applyDashboard(data.dashboard);
        }
        if (data.message && !data.dashboard) {
          this.setFlash(data.message, data.level || "success");
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

      dataPlaneRunningLabel(status) {
        if (!status || !status.configured) {
          return "未配置";
        }
        if (status.xray_running === true) {
          return "运行中";
        }
        if (status.xray_running === false) {
          return "未运行";
        }
        return "未知";
      },

      aiRoutingLabel(status) {
        if (!status || !status.configured) {
          return "未启用";
        }
        return status.status_label || "未知";
      },

      async restartDataPlane() {
        if (!this.dataPlaneStatus || !this.dataPlaneStatus.configured) {
          return;
        }
        if (!window.confirm("确认重启数据面吗？")) {
          return;
        }
        await this.runAction("restart-data-plane", async () => {
          const data = await this.requestJson("/api/data-plane/restart", {
            method: "POST",
          });
          this.applyResponse(data);
        });
      },

      copyLabel(key) {
        return this.copiedKey === key ? "已复制" : "复制";
      },

      async copy(value, key) {
        try {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(value);
          } else if (!fallbackCopyText(value)) {
            throw new Error("复制失败。");
          }
          this.copiedKey = key;
          window.setTimeout(() => {
            if (this.copiedKey === key) {
              this.copiedKey = "";
            }
          }, 1200);
        } catch (_error) {
          this.setFlash("浏览器未允许复制，请手动复制。", "error");
        }
      },
    },

    mounted() {
      this.applyDashboard(initialState || {});
    },
  };
}

function mountPanelApp() {
  const root = document.getElementById("app");
  if (!root || !window.Vue) {
    return;
  }
  window.Vue.createApp(createPanelApp(window.__PANEL_STATE__)).mount(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountPanelApp, { once: true });
} else {
  mountPanelApp();
}
