// Turns the current snapshot into an ordered "check this first" list. It only
// reads values the control plane returned; it never guesses a cause.
export function buildChecklist(panel, insights, diagnosis) {
  const items = [];
  const push = (tone, title, detail) => items.push({ tone, title, detail });

  if (!panel.dataPlaneStatus?.configured) {
    push("warning", "数据面未配置", "先配置数据面的管理目标，再排查流量问题。");
  } else if (panel.dataPlaneStatus?.xray_running === false) {
    push("danger", "数据面未运行", "端口不会接受连接；先重启数据面或检查容器状态。");
  }

  const aiRouting = panel.aiRoutingStatus || {};
  if (aiRouting.configured && aiRouting.status_tone === "bad") {
    push("danger", "AI 路由异常", aiRouting.sync_error || aiRouting.status_label || "查看 AI 路由报告。");
  }
  if (aiRouting.manual_mode === "forced_fallback") {
    push("warning", "当前为人工强制直出", "AI 域名走普通数据面，确认这是有意为之。");
  }
  if (panel.aiNodeStatus?.configured && panel.aiNodeStatus?.reachable === false) {
    push("warning", "AI 节点不可达", panel.aiNodeStatus?.last_error || "检查 AI 节点 SSH 通道与容器状态。");
  }

  const dns = panel.dnsFailoverStatus || {};
  if (dns.enabled && !dns.configured) {
    push("danger", "DNS 切换配置不完整", dns.config_error || "自动切换不会生效。");
  }
  if (dns.current_target === "backup") {
    push("warning", "DNS 当前指向备用", `最近切换：${dns.last_switch_at_display || "暂无"} · ${dns.last_switch_reason || "无原因记录"}`);
  }

  const failures = insights?.probes?.recent_failures || [];
  if (failures.length) {
    const first = failures[0];
    push(
      "danger",
      `${failures.length} 个端口最近探测失败`,
      `端口 ${first.listen_port}：${first.failure_reason || "无失败原因"}（${first.checked_at_display}）`,
    );
  }

  const summary = panel.summary || {};
  if (Number(summary.expired_ports || 0) > 0 || Number(summary.quota_ports || 0) > 0) {
    push(
      "warning",
      "存在过期或已达上限的端口",
      `过期 ${summary.expired_ports || 0} · 已达上限 ${summary.quota_ports || 0} · 已停用 ${summary.disabled_ports || 0}`,
    );
  }

  if (diagnosis?.consistency?.available && !diagnosis.consistency.all_match) {
    push("danger", "订阅下发参数与数据面不一致", "按“数据面体检”的一致性表逐项核对 UUID / shortId / SNI。");
  }

  if (!items.length) {
    push("success", "未发现需要立即处理的异常", "继续观察流量与探测记录；出现异常时会在此列出。");
  }

  const order = { danger: 0, warning: 1, info: 2, success: 3 };
  return items.sort((a, b) => order[a.tone] - order[b.tone]);
}

export function summaryTone(ok, total) {
  if (!total) return "neutral";
  if (ok === total) return "success";
  if (ok === 0) return "danger";
  return "warning";
}
