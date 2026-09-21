// Shared fixtures for the console tests: a dashboard payload shaped like
// build_dashboard_state(), an insights payload shaped like /api/insights, and a
// fake API client that records the requests the console issues.
export function makeDashboard(overrides = {}) {
  const dashboard = {
    meta: {
      csrf_token: "csrf-x",
      panel_address: "panel.example.com",
      timezone_label: "UTC",
      probe_enabled: true,
      probe_dashboard_url: "/probe-dashboard",
      ai_domain_dashboard_url: "/ai-domain-dashboard",
      grafana_url: "http://grafana.example.com",
      grafana_observability_uid: "xray-observability",
      data_plane_status: {
        configured: true,
        reachable: true,
        xray_running: true,
        label: "数据面",
        management_target: "docker:xray",
        supports_restart: true,
        supports_sync: true,
        api_server: "127.0.0.1:10085",
        config_path: "/etc/xray/config.json",
        access_log_path: "/var/log/xray/access.log",
      },
      ai_node_status: { configured: true, reachable: true, label: "AI 节点", management_target: "root@ai-node" },
      ai_nodes: [
        { node_id: "ai-1", label: "台湾 AI 节点", configured: true, reachable: true, xray_running: true, management_target: "root@ai-node", supports_restart: true, supports_sync: true },
      ],
      ai_routing_status: {
        configured: true,
        status_tone: "ok",
        status_label: "正常",
        manual_mode: "auto",
        manual_mode_label: "自动探测",
        manual_updated_at_display: "2026-09-20 10:00:00",
        report_generated_at_display: "2026-09-21 08:00:00",
        ai_candidates: [
          { index: 0, label: "主 AI 节点", candidate_label: "nat.qq.pw:27166", upstream_host: "nat.qq.pw", upstream_port: 27166, is_reachable: true, selected: true },
          { index: 1, label: "备用 AI 节点", candidate_label: "203.0.113.10:27166", upstream_host: "203.0.113.10", upstream_port: 27166, is_reachable: false, selected: false },
        ],
      },
      dns_failover_status: {
        enabled: true,
        configured: true,
        current_target: "primary",
        current_target_label: "主数据面",
        backup_label: "控制面备用 Xray",
        last_switch_at_display: "2026-09-19 03:00:00",
        consecutive_failures: 0,
        consecutive_successes: 4,
        failure_threshold: 3,
        recovery_threshold: 2,
      },
      traffic_routing: {
        path: "normal_ai",
        label: "数据面→AI 节点直出",
        scenario: "正常：AI 流量经 AI 节点直出",
        entry_node: "普通数据面",
        transit_nodes: ["AI 节点"],
        exit_node: "AI 节点 freedom 直出",
      },
      nodes: [
        { key: "data_plane", label: "数据面", configured: true, reachable: true, xray_running: true, management_target: "docker:xray", supports_restart: true },
        { key: "ai_node", label: "AI 节点", configured: true, reachable: true, xray_running: true, management_target: "root@ai-node", supports_restart: true },
        { key: "control_plane_backup", label: "控制面备用 Xray", configured: true, reachable: true, xray_running: true, management_target: "控制面本机", supports_restart: false },
      ],
    },
    summary: {
      total_ports: 2,
      active_ports: 1,
      disabled_ports: 1,
      expired_ports: 0,
      quota_ports: 0,
      total_connections: 12,
      total_bytes_received: 512,
      total_bytes_sent: 512,
    },
    subscription: { available: true, tenant_count: 1, server: "192.0.2.10:443" },
    ports: [
      {
        id: 1,
        listen_port: 31098,
        status: "active",
        status_label: "运行中",
        note: "客户A",
        traffic_used_display: "1.00 KB",
        traffic_usage_bytes: 1024,
        traffic_limit_bytes: 10485760,
        traffic_limit_display: "10.00 MB",
        traffic_remaining_display: "9.00 MB",
        total_connections: 12,
        total_bytes_received: 512,
        total_bytes_sent: 512,
        today_bytes_received: 128,
        today_bytes_sent: 128,
        expires_at_display: "永久",
        expires_at_input: "",
        traffic_limit_input: "",
        probe_status: "healthy",
        probe_status_label: "端口可达",
        access: {
          tenant_login_url: "https://panel.example.com/login?next=/tenant/abc",
          tenant_username: "tenant_user",
          tenant_password: "tenant_pass",
          tenant_subscription_clash_url: "clash://example/abc",
          tenant_subscription_v2ray_url: "v2ray://example/abc",
          share_link: "vless://uuid@192.0.2.10:443",
        },
      },
      {
        id: 2,
        listen_port: 31099,
        status: "disabled",
        status_label: "已停用",
        note: "客户B",
        traffic_used_display: "0 B",
        total_connections: 0,
        total_bytes_received: 0,
        total_bytes_sent: 0,
        today_bytes_received: 0,
        today_bytes_sent: 0,
        expires_at_display: "永久",
      },
    ],
    commerce: {
      summary: { pending_review_count: 1, enabled_plan_count: 1, customer_count: 3, service_count: 1 },
      settings: { order_expiry_hours: 24, payment_qr_code_url: "https://pay.example.com/qr.png", payment_instructions: "扫码付款", auto_port_start: 31000, auto_port_end: 31999, payment_proof_max_display: "5.00 MB" },
      plans: [
        { id: 7, slug: "basic", name: "基础套餐", description: "测试", price_fen: 1000, price_display: "¥10.00", duration_days: 30, traffic_limit_display: "100.00 GB", enabled: true, status_label: "已上架", sort_order: 0 },
      ],
      orders: [
        {
          id: 3,
          order_no: "ORD-3",
          customer_email: "user@example.com",
          plan_name_snapshot: "基础套餐",
          price_display: "¥10.00",
          status: "payment_submitted",
          status_tone: "info",
          status_label: "待审核",
          kind: "purchase",
          created_at_display: "2026-09-20 12:00:00",
          payer_note: "已转账",
          listen_port: null,
          proof_available: true,
          latest_submission_id: 9,
        },
      ],
    },
  };
  return { ...dashboard, ...overrides };
}

export function makeInsights(overrides = {}) {
  const dates = ["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"];
  const insights = {
    generated_at: "2026-09-21T09:00:00+00:00",
    hosts: [
      { key: "data_plane", role: "data_plane", label: "数据面", configured: true, reachable: true, xray_running: true, management_target: "docker:xray", api_server: "127.0.0.1:10085", config_path: "/etc/xray/config.json", access_log_path: "/var/log/xray/access.log", supports_restart: true, supports_sync: true, last_error: "" },
      { key: "ai_node:ai-1", role: "ai_node", label: "台湾 AI 节点", configured: true, reachable: true, xray_running: true, management_target: "root@ai-node", api_server: "", config_path: "", access_log_path: "", supports_restart: true, supports_sync: true, last_error: "" },
    ],
    traffic: {
      days: 4,
      range_start: dates[0],
      range_end: dates[3],
      dates,
      totals: { bytes_sent: 300, bytes_received: 600, connections: 6, total_bytes: 900 },
      series: { bytes_sent: [100, 100, 100, 0], bytes_received: [200, 200, 200, 0], connections: [2, 2, 2, 0], total_bytes: [300, 300, 300, 0] },
      ports: [
        {
          listen_port: 31098,
          note: "客户A",
          enabled: true,
          totals: { bytes_sent: 300, bytes_received: 600, connections: 6, total_bytes: 900 },
          today: { bytes_sent: 0, bytes_received: 0, connections: 0, total_bytes: 0 },
          series: { bytes_sent: [100, 100, 100, 0], bytes_received: [200, 200, 200, 0], connections: [2, 2, 2, 0], total_bytes: [300, 300, 300, 0] },
        },
      ],
    },
    probes: {
      ports: [
        {
          listen_port: 31098,
          note: "客户A",
          enabled: true,
          status: "unhealthy",
          status_label: "端口不可达",
          checked_at_display: "2026-09-21 08:55:00",
          failure_reason: "connection refused",
          total_checks: 4,
          healthy_count: 3,
          unhealthy_count: 1,
          uptime_ratio: "75.0",
          checks: [
            { status: "healthy", checked_at_display: "2026-09-21 08:40:00", failure_reason: "" },
            { status: "healthy", checked_at_display: "2026-09-21 08:45:00", failure_reason: "" },
            { status: "healthy", checked_at_display: "2026-09-21 08:50:00", failure_reason: "" },
            { status: "unhealthy", checked_at_display: "2026-09-21 08:55:00", failure_reason: "connection refused" },
          ],
        },
      ],
      recent_failures: [
        { listen_port: 31098, note: "客户A", checked_at: "2026-09-21T08:55:00+00:00", checked_at_display: "2026-09-21 08:55:00", failure_reason: "connection refused" },
      ],
      total_checks: 4,
      unhealthy_checks: 1,
      uptime_ratio: "75.0",
    },
    failover_events: {
      total_events: 2,
      events: [
        { id: 2, event_type: "switch", event_type_label: "切换", event_status: "ok", status_label: "成功", target: "backup", target_label: "控制面备用 Xray", detail: "自动切换完成。", created_at_display: "2026-09-20 03:00:00" },
        { id: 1, event_type: "probe", event_type_label: "探测", event_status: "error", status_label: "失败", target: "primary", target_label: "主数据面", detail: "probe failed", created_at_display: "2026-09-20 02:59:00" },
      ],
    },
  };
  return { ...insights, ...overrides };
}

export function makeDiagnosis() {
  return {
    generated_at: "2026-09-21T09:00:00+00:00",
    data_plane_mode: "docker",
    management_target: "docker:xray",
    subscription_profile_available: true,
    subscription_error: "",
    node_host: "192.0.2.10",
    server_name: "www.example.com",
    ports: [
      { listen_port: 31098, note: "客户A", tcp_reachable: true, tcp_error: "", reality: { ok: true, cert_subject_cn: "example.com" } },
      { listen_port: 31099, note: "客户B", tcp_reachable: false, tcp_error: "connection refused", reality: null },
    ],
    consistency: {
      available: true,
      source: "docker",
      error: "",
      all_match: false,
      fields: [
        { field: "UUID", subscription: "1111", data_plane: ["1111"], match: true },
        { field: "Reality shortId", subscription: "abcd", data_plane: ["ef01"], match: false },
      ],
    },
    summary: { ports_total: 2, ports_tcp_ok: 1, ports_reality_ok: 1, consistency_available: true, consistency_ok: false },
  };
}

export function createFakeApi({ dashboard, insights, diagnosis, failure } = {}) {
  const calls = [];
  const api = {
    calls,
    get: async (url) => {
      calls.push({ method: "GET", url });
      if (failure && failure.url === url) throw new Error(failure.message);
      if (url.startsWith("/api/insights")) return { ok: true, insights: insights ?? makeInsights() };
      return { ok: true, dashboard: dashboard ?? makeDashboard() };
    },
    post: async (url, json) => {
      calls.push({ method: "POST", url, json });
      if (url === "/api/data-plane/diagnose") {
        return { ok: true, message: "体检完成。", dashboard: dashboard ?? makeDashboard(), diagnosis: diagnosis ?? makeDiagnosis() };
      }
      return { ok: true, message: "操作成功。", dashboard: dashboard ?? makeDashboard() };
    },
    put: async (url, json) => {
      calls.push({ method: "PUT", url, json });
      return { ok: true, message: "保存成功。", dashboard: dashboard ?? makeDashboard() };
    },
    del: async (url) => {
      calls.push({ method: "DELETE", url });
      return { ok: true, message: "已删除。", dashboard: dashboard ?? makeDashboard() };
    },
  };
  return api;
}
