// DNS failover status presentation and manual check/switch actions.
export const DnsMixin = {
  data() {
    return {
      dnsFailoverStatus: {},
    };
  },

  methods: {
    dnsFailoverTone(status) {
      if (!status || !status.enabled) {
        return "warn";
      }
      if (!status.configured) {
        return "bad";
      }
      if (status.current_target === "backup") {
        return "warn";
      }
      if (status.last_probe_status === "unhealthy") {
        return "bad";
      }
      return "ok";
    },

    dnsFailoverSummary(status) {
      if (!status || !status.enabled) {
        return "未启用";
      }
      if (!status.configured) {
        return "配置不完整";
      }
      return status.current_target_label || "未知";
    },

    async runDnsFailoverCheck() {
      await this.runAction("dns-failover-check", async () => {
        const data = await this.requestJson("/api/dns-failover/check", {
          method: "POST",
        });
        this.applyResponse(data);
      });
    },

    async switchDnsTarget(target) {
      const label = target === "primary" ? "主数据面" : (this.dnsFailoverStatus.backup_label || "备用节点");
      if (!window.confirm(`确认把 DNS 切到${label}吗？`)) {
        return;
      }
      await this.runAction(`dns-failover-switch:${target}`, async () => {
        const data = await this.requestJson("/api/dns-failover/switch", {
          method: "POST",
          body: JSON.stringify({ target }),
        });
        this.applyResponse(data);
      });
    },
  },
};
