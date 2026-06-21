// Data-plane / AI-routing status presentation and the data-plane restart action.
export const AiDomainsMixin = {
  data() {
    return {
      dataPlaneStatus: {},
      aiRoutingStatus: {},
    };
  },

  methods: {
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
  },
};
