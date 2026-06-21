import json
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from .config import (
    AI_ROUTING_ENABLED,
    CF_API_TOKEN,
    CF_DNS_RECORD_ID,
    CF_DNS_RECORD_NAME,
    CF_DNS_RECORD_PROXIED,
    CF_DNS_RECORD_TTL,
    CF_DNS_RECORD_TYPE,
    CF_ZONE_ID,
    CONTROL_PLANE_BACKUP_XRAY_ENABLED,
    COMMERCE_AUTO_PORT_END,
    COMMERCE_AUTO_PORT_START,
    COMMERCE_ORDER_EXPIRY_HOURS_DEFAULT,
    DATAPLANE_ACCESS_LOG_PATH,
    DATAPLANE_API_SERVER,
    DATAPLANE_AI_REPORT_PATH,
    DATAPLANE_CONFIG_PATH,
    DATAPLANE_CONTAINER_NAME,
    DATAPLANE_DOCKER_BIN,
    DATAPLANE_DYNAMIC_ROUTING_PATH,
    DATAPLANE_LOCAL_BIN,
    DATAPLANE_PANEL_DB_PATH,
    DATAPLANE_PANEL_PORTS_PATH,
    DATAPLANE_PROBE_HOST,
    DATAPLANE_RESTART_COMMAND,
    DATAPLANE_SSH_BIN,
    DATAPLANE_SSH_OPTIONS,
    DATAPLANE_SSH_TARGET,
    DATAPLANE_XRAY_BIN,
    DATA_DIR,
    DB_PATH,
    DEFAULT_UPSTREAM_HOST,
    DEFAULT_UPSTREAM_PORT,
    DNS_FAILOVER_BACKUP_CONTENT,
    DNS_FAILOVER_BACKUP_LABEL,
    DNS_FAILOVER_ENABLED,
    DNS_FAILOVER_FAILURE_THRESHOLD,
    DNS_FAILOVER_INTERVAL,
    DNS_FAILOVER_PRIMARY_CONTENT,
    DNS_FAILOVER_PROBE_HOST,
    DNS_FAILOVER_PROBE_PORT,
    DNS_FAILOVER_RECOVERY_THRESHOLD,
    DNS_FAILOVER_TIMEOUT,
    LOCAL_TZ,
    MAINTENANCE_INTERVAL,
    PAYMENT_PROOF_MAX_BYTES,
    PAYMENT_PROOFS_DIR,
    PROBE_DASHBOARD_RANGES,
    PROBE_ENABLED,
    PROBE_INTERVAL,
    PROBE_TEST_LISTEN_PORT,
    PROBE_TIMEOUT,
    SEED_LISTEN_PORT,
    XRAY_ACCESS_LOG_PATH,
    XRAY_CLIENT_CONFIG_PATH,
    XRAY_CONFIG_PATH,
    XRAY_DYNAMIC_ROUTING_PATH,
    XRAY_ENV_FILE_PATH,
    XRAY_PANEL_PORTS_PATH,
    XRAY_STATS_QUERY_TIMEOUT,
)
from .dns_failover import CloudflareApiError, DnsFailoverConfig, DnsFailoverManager, resolve_public_ip
from .errors import ValidationError
from .helpers import (
    format_display_time,
    format_input_time,
    generate_access_token,
    generate_subscription_token,
    generate_tenant_password,
    generate_tenant_username,
    human_bytes,
    localize_time,
    normalize_customer_email,
    parse_data_size,
    parse_expiry,
    parse_note,
    parse_port,
    port_is_expired,
    status_payload,
    utc_iso_now,
    utc_now,
)
from .xray.ai_domain_manager import ensure_ai_domain_schema
from .xray.node_control import DataPlaneConfig, DataPlaneController

XRAY_ACCESS_LOG_LINE_RE = re.compile(
    r"^(?P<seen_at>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) .* \[(?P<tag>[^\]]+) >> [^\]]+\]$"
)
PLAN_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PanelState:
    def __init__(self):
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PAYMENT_PROOFS_DIR.mkdir(parents=True, exist_ok=True)
        XRAY_PANEL_PORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        XRAY_ACCESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.data_plane = DataPlaneController(
            DataPlaneConfig(
                role="data_plane",
                label="数据面",
                api_server=DATAPLANE_API_SERVER,
                xray_bin=DATAPLANE_XRAY_BIN,
                local_bin=DATAPLANE_LOCAL_BIN,
                docker_bin=DATAPLANE_DOCKER_BIN,
                container_name=DATAPLANE_CONTAINER_NAME,
                restart_command=DATAPLANE_RESTART_COMMAND,
                ssh_target=DATAPLANE_SSH_TARGET,
                ssh_bin=DATAPLANE_SSH_BIN,
                ssh_options=DATAPLANE_SSH_OPTIONS,
                config_path=self.data_plane_config_path(),
                dynamic_routing_path=DATAPLANE_DYNAMIC_ROUTING_PATH.strip(),
                ai_report_path=DATAPLANE_AI_REPORT_PATH.strip(),
                panel_db_path=DATAPLANE_PANEL_DB_PATH.strip(),
                access_log_path=DATAPLANE_ACCESS_LOG_PATH.strip() or str(XRAY_ACCESS_LOG_PATH),
                panel_ports_path=DATAPLANE_PANEL_PORTS_PATH.strip(),
                source_config_path=XRAY_CONFIG_PATH,
                source_dynamic_routing_path=XRAY_DYNAMIC_ROUTING_PATH,
                source_ai_report_path=self.data_plane_ai_report_source_path(),
                source_panel_ports_path=XRAY_PANEL_PORTS_PATH,
                upstream_host=DEFAULT_UPSTREAM_HOST,
                upstream_port=DEFAULT_UPSTREAM_PORT,
            )
        )
        self.dns_failover_manager = DnsFailoverManager(
            DnsFailoverConfig(
                enabled=DNS_FAILOVER_ENABLED,
                interval=DNS_FAILOVER_INTERVAL,
                timeout=DNS_FAILOVER_TIMEOUT,
                failure_threshold=DNS_FAILOVER_FAILURE_THRESHOLD,
                recovery_threshold=DNS_FAILOVER_RECOVERY_THRESHOLD,
                probe_host=DNS_FAILOVER_PROBE_HOST,
                probe_port=DNS_FAILOVER_PROBE_PORT,
                api_token=CF_API_TOKEN,
                zone_id=CF_ZONE_ID,
                record_id=CF_DNS_RECORD_ID,
                record_type=CF_DNS_RECORD_TYPE,
                record_name=CF_DNS_RECORD_NAME,
                record_proxied=CF_DNS_RECORD_PROXIED,
                record_ttl=CF_DNS_RECORD_TTL,
                primary_content=DNS_FAILOVER_PRIMARY_CONTENT,
                backup_content=DNS_FAILOVER_BACKUP_CONTENT,
                backup_label=DNS_FAILOVER_BACKUP_LABEL,
            )
        )

    def data_plane_config_path(self):
        explicit = DATAPLANE_CONFIG_PATH.strip()
        if explicit:
            return explicit
        if DATAPLANE_SSH_TARGET:
            return str(XRAY_CONFIG_PATH)
        if DATAPLANE_LOCAL_BIN:
            return str(XRAY_CONFIG_PATH)
        return "/etc/xray/config.json"

    def data_plane_ai_report_source_path(self):
        return XRAY_ENV_FILE_PATH.parent / "reports" / "hourly-domains" / "latest.json"

    def data_plane_status(self):
        return self.data_plane.status_summary()

    def dns_failover_status(self):
        config = self.dns_failover_manager.config
        resolved_contents = self.resolve_dns_failover_contents()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dns_failover_state WHERE singleton_id = 1").fetchone()
        item = dict(row) if row is not None else {}
        current_target = item.get("current_target") or "primary"
        record_content = item.get("current_record_content") or ""
        if current_target not in {"primary", "backup"}:
            inferred = self.dns_failover_manager.current_target_from_content(
                record_content,
                primary_content=resolved_contents["primary_content"],
                backup_content=resolved_contents["backup_content"],
            )
            current_target = inferred if inferred in {"primary", "backup"} else "primary"
        return {
            "enabled": bool(config.enabled),
            "configured": bool(config.enabled and config.configured() and config.record_type_supported() and not resolved_contents["error"]),
            "config_error": self.dns_failover_config_error(),
            "current_target": current_target,
            "current_target_label": self.dns_failover_manager.target_label(current_target),
            "record_name": config.record_name,
            "record_type": config.record_type,
            "record_content": record_content,
            "primary_content": resolved_contents["primary_content"],
            "backup_content": resolved_contents["backup_content"],
            "record_ttl": item.get("current_record_ttl") or (1 if config.record_proxied else config.record_ttl),
            "record_proxied": bool(item.get("current_record_proxied"))
            if item.get("current_record_proxied") is not None
            else bool(config.record_proxied),
            "probe_host": config.probe_host,
            "probe_port": config.probe_port,
            "failure_threshold": config.failure_threshold,
            "recovery_threshold": config.recovery_threshold,
            "last_probe_status": item.get("last_probe_status") or "unknown",
            "last_probe_status_label": self.dns_failover_probe_status_label(item.get("last_probe_status")),
            "last_probe_checked_at": item.get("last_probe_checked_at"),
            "last_probe_checked_at_display": format_display_time(item.get("last_probe_checked_at"))
            if item.get("last_probe_checked_at")
            else "暂无",
            "last_probe_error": item.get("last_probe_error") or "",
            "consecutive_failures": int(item.get("consecutive_failures") or 0),
            "consecutive_successes": int(item.get("consecutive_successes") or 0),
            "last_switch_at": item.get("last_switch_at"),
            "last_switch_at_display": format_display_time(item.get("last_switch_at")) if item.get("last_switch_at") else "暂无",
            "last_switch_reason": item.get("last_switch_reason") or "",
            "last_error": item.get("last_error") or "",
            "backup_label": config.backup_label,
            "control_plane_backup_xray_enabled": bool(CONTROL_PLANE_BACKUP_XRAY_ENABLED),
            "control_plane_backup_xray_label": "控制面备用 Xray",
            "fast_propagation_note": "已使用低 TTL；非代理记录建议保持 60 秒以尽快生效。",
        }

    def dns_failover_config_error(self):
        config = self.dns_failover_manager.config
        if not config.enabled:
            return ""
        if not config.record_type_supported():
            return "当前仅支持 A、AAAA、CNAME 记录。"
        missing = []
        if not config.probe_host:
            missing.append("DNS_FAILOVER_PROBE_HOST")
        if not config.probe_port:
            missing.append("DNS_FAILOVER_PROBE_PORT")
        if not config.api_token:
            missing.append("CF_API_TOKEN")
        if not config.zone_id:
            missing.append("CF_ZONE_ID")
        if not config.record_id:
            missing.append("CF_DNS_RECORD_ID")
        if not config.record_name:
            missing.append("CF_DNS_RECORD_NAME")
        if missing:
            return "缺少配置: " + ", ".join(missing)
        resolved_contents = self.resolve_dns_failover_contents()
        if resolved_contents["error"]:
            return resolved_contents["error"]
        return ""

    def resolve_dns_failover_contents(self):
        config = self.dns_failover_manager.config
        primary_content = str(config.primary_content or "").strip()
        backup_content = str(config.backup_content or "").strip()
        error = ""
        if not primary_content:
            try:
                primary_content = self.data_plane.resolve_public_ip(timeout_seconds=max(config.timeout, 3.0))
            except Exception as exc:
                error = f"主数据面公网 IP 自动获取失败: {exc}"
        if not backup_content and not error and CONTROL_PLANE_BACKUP_XRAY_ENABLED:
            try:
                backup_content = resolve_public_ip(timeout=max(config.timeout, 3.0))
            except Exception as exc:
                error = str(exc)
        if not backup_content and not error:
            error = "请设置 DNS_FAILOVER_BACKUP_CONTENT，或启用 CONTROL_PLANE_BACKUP_XRAY_ENABLED。"
        return {
            "primary_content": primary_content,
            "backup_content": backup_content,
            "error": error,
        }

    def dns_failover_probe_status_label(self, status):
        if status == "healthy":
            return "探测成功"
        if status == "unhealthy":
            return "探测失败"
        return "未检测"

    def connect(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init_db(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listen_port INTEGER NOT NULL UNIQUE,
                    upstream_host TEXT NOT NULL,
                    upstream_port INTEGER NOT NULL,
                    tenant_token TEXT NOT NULL DEFAULT '',
                    subscription_token TEXT NOT NULL DEFAULT '',
                    tenant_username TEXT NOT NULL DEFAULT '',
                    tenant_password TEXT NOT NULL DEFAULT '',
                    expires_at TEXT,
                    traffic_limit_bytes INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS traffic_totals (
                    listen_port INTEGER PRIMARY KEY,
                    total_connections INTEGER NOT NULL DEFAULT 0,
                    total_bytes_sent INTEGER NOT NULL DEFAULT 0,
                    total_bytes_received INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT
                );

                CREATE TABLE IF NOT EXISTS traffic_daily (
                    listen_port INTEGER NOT NULL,
                    stat_date TEXT NOT NULL,
                    total_connections INTEGER NOT NULL DEFAULT 0,
                    total_bytes_sent INTEGER NOT NULL DEFAULT 0,
                    total_bytes_received INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (listen_port, stat_date)
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS upstream_probes (
                    listen_port INTEGER PRIMARY KEY,
                    is_reachable INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    failure_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS upstream_probe_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listen_port INTEGER NOT NULL,
                    is_reachable INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    failure_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS ai_domains (
                    domain TEXT PRIMARY KEY,
                    classification TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    first_seen TEXT,
                    last_seen TEXT,
                    total_hits INTEGER NOT NULL DEFAULT 0,
                    last_protocols TEXT NOT NULL DEFAULT '[]',
                    last_report_window_start TEXT,
                    last_report_window_end TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_domain_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    hits INTEGER NOT NULL,
                    classification TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    protocols TEXT NOT NULL DEFAULT '[]',
                    first_seen TEXT,
                    last_seen TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_domain_observations_window
                ON ai_domain_observations(domain, window_start, window_end);

                CREATE INDEX IF NOT EXISTS idx_ai_domain_observations_domain
                ON ai_domain_observations(domain);

                CREATE TABLE IF NOT EXISTS dns_failover_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    current_target TEXT NOT NULL DEFAULT 'primary',
                    current_record_content TEXT NOT NULL DEFAULT '',
                    current_record_ttl INTEGER,
                    current_record_proxied INTEGER,
                    last_probe_status TEXT NOT NULL DEFAULT 'unknown',
                    last_probe_checked_at TEXT,
                    last_probe_error TEXT NOT NULL DEFAULT '',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    consecutive_successes INTEGER NOT NULL DEFAULT 0,
                    last_switch_at TEXT,
                    last_switch_reason TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS dns_failover_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    event_status TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    record_content TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            self.ensure_port_schema(conn)
            self.ensure_dns_failover_schema(conn)
            self.ensure_commerce_schema(conn)

    def ensure_port_schema(self, conn):
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ports)").fetchall()}
        if "tenant_token" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN tenant_token TEXT NOT NULL DEFAULT ''")
        if "subscription_token" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN subscription_token TEXT NOT NULL DEFAULT ''")
        if "tenant_username" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN tenant_username TEXT NOT NULL DEFAULT ''")
        if "tenant_password" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN tenant_password TEXT NOT NULL DEFAULT ''")
        if "traffic_limit_bytes" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN traffic_limit_bytes INTEGER")
        if "customer_id" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN customer_id INTEGER")
        if "service_subscription_id" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN service_subscription_id INTEGER")
        if "source_order_id" not in columns:
            conn.execute("ALTER TABLE ports ADD COLUMN source_order_id INTEGER")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ports_tenant_token
            ON ports(tenant_token)
            WHERE tenant_token != ''
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ports_subscription_token
            ON ports(subscription_token)
            WHERE subscription_token != ''
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ports_tenant_username
            ON ports(tenant_username)
            WHERE tenant_username != ''
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ports_customer_id ON ports(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ports_service_subscription_id ON ports(service_subscription_id)")
        self.cleanup_expired_ports_in_tx(conn)
        self.ensure_port_tokens_in_tx(conn)
        self.ensure_port_credentials_in_tx(conn)

    def ensure_dns_failover_schema(self, conn):
        conn.execute(
            """
            INSERT INTO dns_failover_state (singleton_id)
            VALUES (1)
            ON CONFLICT(singleton_id) DO NOTHING
            """
        )

    def ensure_commerce_schema(self, conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price_fen INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                duration_days INTEGER NOT NULL,
                traffic_limit_bytes INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                service_subscription_id INTEGER,
                status TEXT NOT NULL,
                plan_name_snapshot TEXT NOT NULL,
                plan_description_snapshot TEXT NOT NULL DEFAULT '',
                price_fen_snapshot INTEGER NOT NULL,
                currency_snapshot TEXT NOT NULL DEFAULT 'CNY',
                duration_days_snapshot INTEGER NOT NULL,
                traffic_limit_bytes_snapshot INTEGER NOT NULL,
                expires_at TEXT,
                fulfilled_at TEXT,
                cancelled_at TEXT,
                rejection_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY(plan_id) REFERENCES plans(id)
            );

            CREATE TABLE IF NOT EXISTS service_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                port_id INTEGER,
                source_order_id INTEGER NOT NULL,
                latest_order_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY(plan_id) REFERENCES plans(id),
                FOREIGN KEY(port_id) REFERENCES ports(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS order_payment_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                proof_image_path TEXT NOT NULL,
                payer_note TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL,
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT,
                review_status TEXT NOT NULL DEFAULT 'submitted',
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_service_subscription_id ON orders(service_subscription_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_subscriptions_customer_id ON service_subscriptions(customer_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_service_subscriptions_port_id ON service_subscriptions(port_id) WHERE port_id IS NOT NULL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_payment_submissions_order_id ON order_payment_submissions(order_id)"
        )
        self.ensure_commerce_settings_in_tx(conn)

    def ensure_commerce_settings_in_tx(self, conn):
        defaults = {
            "commerce_payment_qr_code_url": "",
            "commerce_payment_instructions": "请扫码支付宝付款后上传截图，并填写付款备注以便人工审核。",
            "commerce_order_expiry_hours": str(COMMERCE_ORDER_EXPIRY_HOURS_DEFAULT),
        }
        for key, value in defaults.items():
            if self.get_state(conn, key, None) is None:
                self.set_state(conn, key, value)

    def get_commerce_settings(self):
        with self.connect() as conn:
            self.ensure_commerce_settings_in_tx(conn)
            return self.serialize_commerce_settings(conn)

    def serialize_commerce_settings(self, conn):
        payment_qr_code_url = str(self.get_state(conn, "commerce_payment_qr_code_url", "") or "").strip()
        payment_instructions = str(
            self.get_state(conn, "commerce_payment_instructions", "") or ""
        ).strip()
        try:
            order_expiry_hours = int(
                str(self.get_state(conn, "commerce_order_expiry_hours", COMMERCE_ORDER_EXPIRY_HOURS_DEFAULT) or COMMERCE_ORDER_EXPIRY_HOURS_DEFAULT)
            )
        except ValueError:
            order_expiry_hours = COMMERCE_ORDER_EXPIRY_HOURS_DEFAULT
        order_expiry_hours = max(order_expiry_hours, 1)
        return {
            "payment_qr_code_url": payment_qr_code_url,
            "payment_instructions": payment_instructions,
            "order_expiry_hours": order_expiry_hours,
            "auto_port_start": COMMERCE_AUTO_PORT_START,
            "auto_port_end": COMMERCE_AUTO_PORT_END,
            "payment_proof_max_bytes": PAYMENT_PROOF_MAX_BYTES,
            "payment_proof_max_display": human_bytes(PAYMENT_PROOF_MAX_BYTES),
        }

    def update_commerce_settings(self, payload):
        instructions = str(payload.get("payment_instructions", "") or "").strip()
        if len(instructions) > 1000:
            raise ValidationError("付款说明不能超过 1000 个字符。")
        payment_qr_code_url = str(payload.get("payment_qr_code_url", "") or "").strip()
        raw_expiry = str(payload.get("order_expiry_hours", "") or "").strip()
        try:
            order_expiry_hours = int(raw_expiry)
        except ValueError as exc:
            raise ValidationError("订单有效期必须是正整数小时。") from exc
        if order_expiry_hours <= 0:
            raise ValidationError("订单有效期必须是正整数小时。")

        def operation(conn):
            self.set_state(conn, "commerce_payment_qr_code_url", payment_qr_code_url)
            self.set_state(conn, "commerce_payment_instructions", instructions)
            self.set_state(conn, "commerce_order_expiry_hours", str(order_expiry_hours))
            return self.serialize_commerce_settings(conn)

        return self.apply_state_update(operation)

    def generate_unique_port_token(self, conn, column_name):
        if column_name not in {"tenant_token", "subscription_token"}:
            raise ValueError("unsupported port token column")
        for _ in range(16):
            token = generate_access_token()
            row = conn.execute(
                f"SELECT 1 FROM ports WHERE {column_name} = ? LIMIT 1",
                (token,),
            ).fetchone()
            if row is None:
                return token
        raise RuntimeError(f"无法为 {column_name} 生成唯一 token。")

    def generate_unique_tenant_username(self, conn):
        for _ in range(16):
            username = generate_tenant_username()
            row = conn.execute(
                "SELECT 1 FROM ports WHERE tenant_username = ? LIMIT 1",
                (username,),
            ).fetchone()
            if row is None:
                return username
        raise RuntimeError("无法生成唯一租户用户名。")

    def ensure_port_tokens_in_tx(self, conn):
        rows = conn.execute(
            """
            SELECT id, tenant_token, subscription_token
            FROM ports
            """
        ).fetchall()
        for row in rows:
            updates = {}
            if not str(row["tenant_token"] or "").strip():
                updates["tenant_token"] = self.generate_unique_port_token(conn, "tenant_token")
            if not str(row["subscription_token"] or "").strip():
                updates["subscription_token"] = self.generate_unique_port_token(conn, "subscription_token")
            if not updates:
                continue
            assignments = ", ".join(f"{column} = ?" for column in updates)
            values = list(updates.values()) + [row["id"]]
            conn.execute(f"UPDATE ports SET {assignments} WHERE id = ?", values)

    def ensure_port_credentials_in_tx(self, conn):
        rows = conn.execute(
            """
            SELECT id, tenant_username, tenant_password
            FROM ports
            """
        ).fetchall()
        for row in rows:
            updates = {}
            if not str(row["tenant_username"] or "").strip():
                updates["tenant_username"] = self.generate_unique_tenant_username(conn)
            if not str(row["tenant_password"] or "").strip():
                updates["tenant_password"] = generate_tenant_password()
            if not updates:
                continue
            assignments = ", ".join(f"{column} = ?" for column in updates)
            values = list(updates.values()) + [row["id"]]
            conn.execute(f"UPDATE ports SET {assignments} WHERE id = ?", values)

    def ensure_subscription_token_in_tx(self, conn):
        token = str(self.get_state(conn, "subscription_token", "") or "").strip()
        if token:
            return token
        token = generate_subscription_token()
        self.set_state(conn, "subscription_token", token)
        return token

    def normalize_upstream_targets(self):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ports
                SET upstream_host = ?, upstream_port = ?
                WHERE upstream_host != ? OR upstream_port != ?
                """,
                (
                    DEFAULT_UPSTREAM_HOST,
                    DEFAULT_UPSTREAM_PORT,
                    DEFAULT_UPSTREAM_HOST,
                    DEFAULT_UPSTREAM_PORT,
                ),
            )
            conn.commit()

    def seed_defaults(self):
        if not SEED_LISTEN_PORT:
            return
        listen_port = parse_port(SEED_LISTEN_PORT, "默认监听端口")
        with self.connect() as conn:
            exists = conn.execute("SELECT COUNT(*) FROM ports").fetchone()[0]
            if exists:
                return
            now = utc_iso_now()
            conn.execute(
                """
                INSERT INTO ports (
                    listen_port, upstream_host, upstream_port, tenant_token, subscription_token,
                    tenant_username, tenant_password,
                    expires_at, enabled, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?)
                """,
                (
                    listen_port,
                    DEFAULT_UPSTREAM_HOST,
                    DEFAULT_UPSTREAM_PORT,
                    self.generate_unique_port_token(conn, "tenant_token"),
                    self.generate_unique_port_token(conn, "subscription_token"),
                    self.generate_unique_tenant_username(conn),
                    generate_tenant_password(),
                    "默认初始化端口",
                    now,
                    now,
                ),
            )

    def bootstrap(self):
        self.init_db()
        self.seed_defaults()
        self.normalize_upstream_targets()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.cleanup_expired_ports_in_tx(conn)
            self.ensure_subscription_token_in_tx(conn)
            self.ensure_port_tokens_in_tx(conn)
            self.ensure_port_credentials_in_tx(conn)
            conn.commit()
        self.sync_traffic_state()
        self.disable_auto_stopped_ports(reload_xray=False)
        self.write_current_config()
        try:
            self.refresh_dns_failover_record_snapshot()
        except Exception:
            pass

    def get_state(self, conn, key, default=None):
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return row["value"]

    def set_state(self, conn, key, value):
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )

    def sync_traffic_state(self):
        with self.write_lock:
            return self.sync_traffic_state_locked()

    def sync_traffic_state_locked(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            log_updates = self.sync_xray_access_log_in_tx(conn)
            byte_updates = self.sync_xray_traffic_stats_in_tx(conn)
            conn.commit()
            return {
                "connection_updates": log_updates,
                "byte_updates": byte_updates,
            }

    def sync_xray_access_log_in_tx(self, conn):
        current_offset = int(self.get_state(conn, "xray_access_log_offset", "0"))
        recorded_inode = self.get_state(conn, "xray_access_log_inode", "")
        current_inode = ""
        new_offset = 0
        lines = []

        if self.data_plane.supports_logs():
            try:
                payload = self.data_plane.read_access_log_delta(recorded_inode, current_offset)
            except RuntimeError:
                return 0
            if not payload["exists"]:
                return 0
            current_inode = payload["inode"]
            new_offset = int(payload["offset"])
            lines = str(payload["data"]).splitlines()
        else:
            if not XRAY_ACCESS_LOG_PATH.exists():
                return 0

            stat = XRAY_ACCESS_LOG_PATH.stat()
            current_inode = str(stat.st_ino)
            if recorded_inode != current_inode or stat.st_size < current_offset:
                current_offset = 0

            with XRAY_ACCESS_LOG_PATH.open("r", encoding="utf-8", errors="ignore") as handle:
                handle.seek(current_offset)
                lines = handle.readlines()
                new_offset = handle.tell()

        aggregates = {}
        for line in lines:
            parsed = self.parse_xray_access_log_line(line)
            if parsed is None:
                continue
            listen_port, stat_date, seen_at = parsed
            item = aggregates.setdefault(
                (listen_port, stat_date),
                {
                    "connections": 0,
                    "last_seen": seen_at,
                },
            )
            item["connections"] += 1
            if seen_at > item["last_seen"]:
                item["last_seen"] = seen_at

        for (listen_port, stat_date), item in aggregates.items():
            conn.execute(
                """
                INSERT INTO traffic_totals (
                    listen_port, total_connections, total_bytes_sent, total_bytes_received, last_seen
                ) VALUES (?, ?, 0, 0, ?)
                ON CONFLICT(listen_port) DO UPDATE SET
                    total_connections = total_connections + excluded.total_connections,
                    last_seen = CASE
                        WHEN traffic_totals.last_seen IS NULL OR traffic_totals.last_seen < excluded.last_seen
                        THEN excluded.last_seen
                        ELSE traffic_totals.last_seen
                    END
                """,
                (
                    listen_port,
                    item["connections"],
                    item["last_seen"],
                ),
            )
            conn.execute(
                """
                INSERT INTO traffic_daily (
                    listen_port, stat_date, total_connections, total_bytes_sent, total_bytes_received
                ) VALUES (?, ?, ?, 0, 0)
                ON CONFLICT(listen_port, stat_date) DO UPDATE SET
                    total_connections = total_connections + excluded.total_connections
                """,
                (
                    listen_port,
                    stat_date,
                    item["connections"],
                ),
            )

        self.set_state(conn, "xray_access_log_inode", current_inode)
        self.set_state(conn, "xray_access_log_offset", str(new_offset))
        return len(aggregates)

    def parse_xray_access_log_line(self, line):
        match = XRAY_ACCESS_LOG_LINE_RE.match(line.strip())
        if match is None:
            return None

        tag = str(match.group("tag") or "").strip()
        if not tag.startswith("panel-"):
            return None

        try:
            listen_port = int(tag.removeprefix("panel-"))
        except ValueError:
            return None

        timestamp_text = match.group("seen_at")
        timestamp_format = "%Y/%m/%d %H:%M:%S.%f" if "." in timestamp_text else "%Y/%m/%d %H:%M:%S"
        try:
            seen_local = datetime.strptime(timestamp_text, timestamp_format).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            return None
        seen_at = seen_local.astimezone(timezone.utc).isoformat(timespec="seconds")
        stat_date = seen_at[:10]
        return listen_port, stat_date, seen_at

    def sync_xray_traffic_stats_in_tx(self, conn):
        stats = self.read_xray_traffic_stats()
        if not stats:
            return 0

        now_text = utc_iso_now()
        stat_date = now_text[:10]
        for listen_port, item in stats.items():
            last_seen = now_text if item["bytes_sent"] or item["bytes_received"] else None
            conn.execute(
                """
                INSERT INTO traffic_totals (
                    listen_port, total_connections, total_bytes_sent, total_bytes_received, last_seen
                ) VALUES (?, 0, ?, ?, ?)
                ON CONFLICT(listen_port) DO UPDATE SET
                    total_bytes_sent = total_bytes_sent + excluded.total_bytes_sent,
                    total_bytes_received = total_bytes_received + excluded.total_bytes_received,
                    last_seen = CASE
                        WHEN excluded.last_seen IS NULL THEN traffic_totals.last_seen
                        WHEN traffic_totals.last_seen IS NULL OR traffic_totals.last_seen < excluded.last_seen
                        THEN excluded.last_seen
                        ELSE traffic_totals.last_seen
                    END
                """,
                (
                    listen_port,
                    item["bytes_sent"],
                    item["bytes_received"],
                    last_seen,
                ),
            )
            conn.execute(
                """
                INSERT INTO traffic_daily (
                    listen_port, stat_date, total_connections, total_bytes_sent, total_bytes_received
                ) VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(listen_port, stat_date) DO UPDATE SET
                    total_bytes_sent = total_bytes_sent + excluded.total_bytes_sent,
                    total_bytes_received = total_bytes_received + excluded.total_bytes_received
                """,
                (
                    listen_port,
                    stat_date,
                    item["bytes_sent"],
                    item["bytes_received"],
                ),
            )
        return len(stats)

    def query_ports(self):
        today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(t.total_connections, 0) AS total_connections,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received,
                    t.last_seen AS last_seen,
                    pr.is_reachable AS probe_is_reachable,
                    pr.checked_at AS probe_checked_at,
                    pr.failure_reason AS probe_failure_reason,
                    COALESCE(d.total_connections, 0) AS today_connections,
                    COALESCE(d.total_bytes_sent, 0) AS today_bytes_sent,
                    COALESCE(d.total_bytes_received, 0) AS today_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                LEFT JOIN upstream_probes pr ON pr.listen_port = p.listen_port
                LEFT JOIN traffic_daily d ON d.listen_port = p.listen_port AND d.stat_date = ?
                ORDER BY p.listen_port ASC
                """,
                (today,),
            ).fetchall()

        return [self.serialize_port_row(row) for row in rows]

    def serialize_port_row(self, row):
        item = dict(row)
        item["expires_at_display"] = format_display_time(item["expires_at"])
        item["expires_at_input"] = format_input_time(item["expires_at"])
        item["last_seen_display"] = format_display_time(item["last_seen"]) if item["last_seen"] else "暂无"
        item["probe_checked_at_display"] = (
            format_display_time(item["probe_checked_at"]) if item["probe_checked_at"] else "暂无"
        )
        item["probe_status"] = "unknown"
        item["probe_status_label"] = "未检测"
        item["probe_failure_reason"] = item["probe_failure_reason"] or ""
        if item["probe_is_reachable"] is not None:
            if int(item["probe_is_reachable"]):
                item["probe_status"] = "healthy"
                item["probe_status_label"] = "端口可达"
            else:
                item["probe_status"] = "unhealthy"
                item["probe_status_label"] = "端口不可达"
        item["traffic_usage_bytes"] = int(item["total_bytes_sent"]) + int(item["total_bytes_received"])
        item["traffic_limit_display"] = (
            human_bytes(item["traffic_limit_bytes"]) if item["traffic_limit_bytes"] is not None else "无限制"
        )
        item["traffic_limit_input"] = (
            human_bytes(item["traffic_limit_bytes"]) if item["traffic_limit_bytes"] is not None else ""
        )
        item["traffic_used_display"] = human_bytes(item["traffic_usage_bytes"])
        if item["traffic_limit_bytes"] is None:
            item["traffic_remaining_display"] = "无限制"
        else:
            item["traffic_remaining_display"] = human_bytes(
                max(int(item["traffic_limit_bytes"]) - item["traffic_usage_bytes"], 0)
            )
        status = status_payload(
            bool(item["enabled"]),
            item["expires_at"],
            item["traffic_limit_bytes"],
            item["traffic_usage_bytes"],
        )
        item["status"] = status["code"]
        item["status_label"] = status["label"]
        return item

    def get_subscription_token(self):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                token = self.ensure_subscription_token_in_tx(conn)
                conn.commit()
                return token

    def rotate_subscription_token(self):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                token = generate_subscription_token()
                self.set_state(conn, "subscription_token", token)
                conn.commit()
                return token

    def get_port_subscription_record(self, listen_port):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.id,
                    p.listen_port,
                    p.note,
                    p.enabled,
                    p.expires_at,
                    p.traffic_limit_bytes,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                WHERE p.listen_port = ?
                LIMIT 1
                """,
                (listen_port,),
            ).fetchone()

        if row is None:
            return None

        item = dict(row)
        item["traffic_usage_bytes"] = int(item["total_bytes_sent"]) + int(item["total_bytes_received"])
        status = status_payload(
            bool(item["enabled"]),
            item["expires_at"],
            item["traffic_limit_bytes"],
            item["traffic_usage_bytes"],
        )
        item["status"] = status["code"]
        item["status_label"] = status["label"]
        return item

    def query_summary(self, ports):
        summary = {
            "total_ports": len(ports),
            "active_ports": 0,
            "expired_ports": 0,
            "quota_ports": 0,
            "disabled_ports": 0,
            "total_connections": 0,
            "total_bytes_sent": 0,
            "total_bytes_received": 0,
        }
        for port in ports:
            summary["total_connections"] += port["total_connections"]
            summary["total_bytes_sent"] += port["total_bytes_sent"]
            summary["total_bytes_received"] += port["total_bytes_received"]
            if port["status"] == "active":
                summary["active_ports"] += 1
            elif port["status"] == "expired":
                summary["expired_ports"] += 1
            elif port["status"] == "quota":
                summary["quota_ports"] += 1
            else:
                summary["disabled_ports"] += 1
        return summary

    def validate_customer_password(self, password, confirm_password=""):
        raw_password = str(password or "")
        if len(raw_password) < 8:
            raise ValidationError("密码至少需要 8 个字符。")
        if len(raw_password) > 200:
            raise ValidationError("密码不能超过 200 个字符。")
        if confirm_password != "" and raw_password != str(confirm_password or ""):
            raise ValidationError("两次输入的密码不一致。")
        return raw_password

    def slugify_plan_value(self, value):
        slug = PLAN_SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")
        if not slug:
            raise ValidationError("套餐 slug 不能为空。")
        if len(slug) > 80:
            raise ValidationError("套餐 slug 不能超过 80 个字符。")
        return slug

    def parse_bool_like(self, value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() not in {"", "0", "false", "off", "no"}

    def validate_plan_payload(self, payload):
        name = str(payload.get("name", "") or "").strip()
        if not name:
            raise ValidationError("套餐名称不能为空。")
        if len(name) > 80:
            raise ValidationError("套餐名称不能超过 80 个字符。")
        description = str(payload.get("description", "") or "").strip()
        if len(description) > 1000:
            raise ValidationError("套餐说明不能超过 1000 个字符。")
        slug_source = str(payload.get("slug", "") or "").strip() or name
        slug = self.slugify_plan_value(slug_source)

        try:
            price_fen = int(str(payload.get("price_fen", "") or "").strip())
        except ValueError as exc:
            raise ValidationError("套餐价格必须是正整数分。") from exc
        if price_fen <= 0:
            raise ValidationError("套餐价格必须大于 0。")

        try:
            duration_days = int(str(payload.get("duration_days", "") or "").strip())
        except ValueError as exc:
            raise ValidationError("套餐时长必须是正整数天。") from exc
        if duration_days <= 0:
            raise ValidationError("套餐时长必须大于 0。")

        traffic_limit_bytes = parse_data_size(payload.get("traffic_limit"), "套餐流量")
        if traffic_limit_bytes is None:
            raise ValidationError("套餐流量不能为空。")

        try:
            sort_order = int(str(payload.get("sort_order", "0") or "0").strip())
        except ValueError as exc:
            raise ValidationError("排序值必须是整数。") from exc

        return {
            "slug": slug,
            "name": name,
            "description": description,
            "price_fen": price_fen,
            "currency": "CNY",
            "duration_days": duration_days,
            "traffic_limit_bytes": traffic_limit_bytes,
            "enabled": 1 if self.parse_bool_like(payload.get("enabled", True)) else 0,
            "sort_order": sort_order,
        }

    def serialize_plan_row(self, row):
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["price_display"] = f"¥{int(item['price_fen']) / 100:.2f}"
        item["traffic_limit_display"] = human_bytes(item["traffic_limit_bytes"])
        item["status_label"] = "上架中" if item["enabled"] else "已下架"
        return item

    def query_plans(self, public_only=False):
        with self.connect() as conn:
            sql = """
                SELECT
                    id,
                    slug,
                    name,
                    description,
                    price_fen,
                    currency,
                    duration_days,
                    traffic_limit_bytes,
                    enabled,
                    sort_order,
                    created_at,
                    updated_at
                FROM plans
            """
            params = []
            if public_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY enabled DESC, sort_order ASC, id ASC"
            rows = conn.execute(sql, params).fetchall()
        return [self.serialize_plan_row(row) for row in rows]

    def get_plan_by_slug(self, slug, public_only=False):
        with self.connect() as conn:
            sql = """
                SELECT
                    id,
                    slug,
                    name,
                    description,
                    price_fen,
                    currency,
                    duration_days,
                    traffic_limit_bytes,
                    enabled,
                    sort_order,
                    created_at,
                    updated_at
                FROM plans
                WHERE slug = ?
            """
            params = [str(slug or "").strip()]
            if public_only:
                sql += " AND enabled = 1"
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return self.serialize_plan_row(row)

    def get_plan_by_id(self, plan_id):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    slug,
                    name,
                    description,
                    price_fen,
                    currency,
                    duration_days,
                    traffic_limit_bytes,
                    enabled,
                    sort_order,
                    created_at,
                    updated_at
                FROM plans
                WHERE id = ?
                """,
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return self.serialize_plan_row(row)

    def create_plan(self, payload):
        def operation(conn):
            now = utc_iso_now()
            conn.execute(
                """
                INSERT INTO plans (
                    slug, name, description, price_fen, currency,
                    duration_days, traffic_limit_bytes, enabled, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["slug"],
                    payload["name"],
                    payload["description"],
                    payload["price_fen"],
                    payload["currency"],
                    payload["duration_days"],
                    payload["traffic_limit_bytes"],
                    payload["enabled"],
                    payload["sort_order"],
                    now,
                    now,
                ),
            )

        self.apply_state_update(operation)

    def update_plan(self, plan_id, payload):
        def operation(conn):
            existing = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if existing is None:
                raise ValidationError("套餐不存在。")
            conn.execute(
                """
                UPDATE plans
                SET slug = ?, name = ?, description = ?, price_fen = ?, currency = ?,
                    duration_days = ?, traffic_limit_bytes = ?, enabled = ?, sort_order = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["slug"],
                    payload["name"],
                    payload["description"],
                    payload["price_fen"],
                    payload["currency"],
                    payload["duration_days"],
                    payload["traffic_limit_bytes"],
                    payload["enabled"],
                    payload["sort_order"],
                    utc_iso_now(),
                    plan_id,
                ),
            )

        self.apply_state_update(operation)

    def create_customer(self, email, password):
        normalized_email = normalize_customer_email(email)
        raw_password = self.validate_customer_password(password)

        def operation(conn):
            now = utc_iso_now()
            password_hash = generate_password_hash(raw_password)
            conn.execute(
                """
                INSERT INTO customers (
                    email, password_hash, status, created_at, updated_at
                ) VALUES (?, ?, 'active', ?, ?)
                """,
                (normalized_email, password_hash, now, now),
            )

        self.apply_state_update(operation)

    def get_customer_by_email(self, email):
        normalized_email = normalize_customer_email(email)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, email, password_hash, status, created_at, updated_at, last_login_at
                FROM customers
                WHERE email = ?
                LIMIT 1
                """,
                (normalized_email,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_customer_by_id(self, customer_id):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, email, password_hash, status, created_at, updated_at, last_login_at
                FROM customers
                WHERE id = ?
                LIMIT 1
                """,
                (customer_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def touch_customer_login(self, customer_id):
        def operation(conn):
            conn.execute(
                "UPDATE customers SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_iso_now(), utc_iso_now(), customer_id),
            )

        self.apply_state_update(operation)

    def order_status_label(self, status):
        mapping = {
            "pending_payment": "待付款",
            "payment_submitted": "待审核",
            "payment_rejected": "已驳回",
            "fulfilled": "已开通",
            "cancelled": "已取消",
            "expired": "已过期",
        }
        return mapping.get(str(status or "").strip(), "未知")

    def order_status_tone(self, status):
        mapping = {
            "pending_payment": "warn",
            "payment_submitted": "warn",
            "payment_rejected": "bad",
            "fulfilled": "ok",
            "cancelled": "bad",
            "expired": "bad",
        }
        return mapping.get(str(status or "").strip(), "warn")

    def generate_unique_order_no(self, conn):
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        for _ in range(16):
            candidate = f"ODR{date_prefix}{generate_subscription_token(8).upper()}"
            row = conn.execute("SELECT 1 FROM orders WHERE order_no = ? LIMIT 1", (candidate,)).fetchone()
            if row is None:
                return candidate
        raise RuntimeError("无法生成唯一订单号。")

    def compute_order_deadline_in_tx(self, conn, base_dt=None):
        settings = self.serialize_commerce_settings(conn)
        expires_dt = (base_dt or utc_now()) + timedelta(hours=int(settings["order_expiry_hours"]))
        return expires_dt.isoformat(timespec="seconds")

    def expire_pending_orders_in_tx(self, conn):
        now_text = utc_iso_now()
        conn.execute(
            """
            UPDATE orders
            SET status = 'expired', updated_at = ?
            WHERE status IN ('pending_payment', 'payment_rejected')
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now_text, now_text),
        )

    def expire_pending_orders(self):
        def operation(conn):
            self.expire_pending_orders_in_tx(conn)

        self.apply_state_update(operation)

    def create_order(self, customer_id, plan_id, kind="new_purchase", service_subscription_id=None):
        def operation(conn):
            nonlocal service_subscription_id
            self.expire_pending_orders_in_tx(conn)
            customer = conn.execute(
                "SELECT id, email, status FROM customers WHERE id = ? LIMIT 1",
                (customer_id,),
            ).fetchone()
            if customer is None or customer["status"] != "active":
                raise ValidationError("客户账号不可用。")

            plan = conn.execute(
                """
                SELECT id, slug, name, description, price_fen, currency, duration_days, traffic_limit_bytes, enabled
                FROM plans
                WHERE id = ?
                LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            if plan is None:
                raise ValidationError("套餐不存在。")
            if not int(plan["enabled"]):
                raise ValidationError("套餐已下架。")

            kind_text = str(kind or "new_purchase").strip()
            if kind_text not in {"new_purchase", "renewal"}:
                raise ValidationError("订单类型不支持。")

            if kind_text == "renewal":
                if service_subscription_id is None:
                    raise ValidationError("续费订单缺少服务实例。")
                service_row = self.get_service_subscription_row_in_tx(conn, service_subscription_id, customer_id=customer_id)
                if service_row is None:
                    raise ValidationError("服务实例不存在。")
                if int(service_row["plan_id"]) != int(plan["id"]):
                    raise ValidationError("v1 仅支持按原套餐续费。")
                if not service_row["renewal_allowed"]:
                    raise ValidationError("当前服务未到续费窗口，仅已过期或流量用尽的服务可续费。")
                existing = conn.execute(
                    """
                    SELECT 1
                    FROM orders
                    WHERE service_subscription_id = ?
                      AND status IN ('pending_payment', 'payment_submitted', 'payment_rejected')
                    LIMIT 1
                    """,
                    (service_subscription_id,),
                ).fetchone()
                if existing is not None:
                    raise ValidationError("当前服务已有未完成的续费订单。")
            else:
                service_subscription_id = None

            now_text = utc_iso_now()
            order_no = self.generate_unique_order_no(conn)
            conn.execute(
                """
                INSERT INTO orders (
                    order_no, customer_id, plan_id, kind, service_subscription_id, status,
                    plan_name_snapshot, plan_description_snapshot, price_fen_snapshot, currency_snapshot,
                    duration_days_snapshot, traffic_limit_bytes_snapshot, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_no,
                    customer_id,
                    plan["id"],
                    kind_text,
                    service_subscription_id,
                    plan["name"],
                    plan["description"],
                    plan["price_fen"],
                    plan["currency"],
                    plan["duration_days"],
                    plan["traffic_limit_bytes"],
                    self.compute_order_deadline_in_tx(conn),
                    now_text,
                    now_text,
                ),
            )
            return order_no

        return self.apply_state_update(operation)

    def payment_proof_extension_from_bytes(self, payload):
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if payload.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            return "webp"
        return ""

    def save_payment_proof_file(self, order_no, file_storage):
        content = file_storage.read()
        if not content:
            raise ValidationError("请上传支付截图。")
        if len(content) > PAYMENT_PROOF_MAX_BYTES:
            raise ValidationError(f"支付截图不能超过 {human_bytes(PAYMENT_PROOF_MAX_BYTES)}。")
        extension = self.payment_proof_extension_from_bytes(content)
        if extension not in {"png", "jpg", "webp"}:
            raise ValidationError("支付截图只支持 PNG、JPG、WEBP。")
        target_dir = PAYMENT_PROOFS_DIR / str(order_no)
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{utc_now().strftime('%Y%m%dT%H%M%S')}-{generate_subscription_token(6)}.{extension}"
        relative_path = Path("payment-proofs") / str(order_no) / filename
        absolute_path = PAYMENT_PROOFS_DIR.parent / relative_path
        absolute_path.write_bytes(content)
        return relative_path.as_posix()

    def submit_order_payment_submission(self, customer_id, order_no, file_storage, payer_note):
        note = str(payer_note or "").strip()
        if len(note) > 300:
            raise ValidationError("付款备注不能超过 300 个字符。")
        relative_path = self.save_payment_proof_file(order_no, file_storage)

        def operation(conn):
            self.expire_pending_orders_in_tx(conn)
            order = conn.execute(
                """
                SELECT id, order_no, customer_id, status
                FROM orders
                WHERE order_no = ? AND customer_id = ?
                LIMIT 1
                """,
                (order_no, customer_id),
            ).fetchone()
            if order is None:
                raise ValidationError("订单不存在。")
            if order["status"] not in {"pending_payment", "payment_rejected"}:
                raise ValidationError("当前订单状态不允许重新提交支付凭证。")
            now_text = utc_iso_now()
            conn.execute(
                """
                INSERT INTO order_payment_submissions (
                    order_id, proof_image_path, payer_note, submitted_at, review_note, reviewed_at, review_status
                ) VALUES (?, ?, ?, ?, '', NULL, 'submitted')
                """,
                (order["id"], relative_path, note, now_text),
            )
            conn.execute(
                """
                UPDATE orders
                SET status = 'payment_submitted', rejection_reason = '', updated_at = ?
                WHERE id = ?
                """,
                (now_text, order["id"]),
            )

        try:
            self.apply_state_update(operation)
        except Exception:
            (PAYMENT_PROOFS_DIR.parent / relative_path).unlink(missing_ok=True)
            raise

    def serialize_order_row(self, row):
        item = dict(row)
        latest_submission_id = item.get("latest_submission_id")
        item["status_label"] = self.order_status_label(item.get("status"))
        item["status_tone"] = self.order_status_tone(item.get("status"))
        item["price_display"] = f"¥{int(item.get('price_fen_snapshot') or 0) / 100:.2f}"
        item["traffic_limit_display"] = human_bytes(item.get("traffic_limit_bytes_snapshot") or 0)
        item["expires_at_display"] = format_display_time(item.get("expires_at")) if item.get("expires_at") else "暂无"
        item["fulfilled_at_display"] = (
            format_display_time(item.get("fulfilled_at")) if item.get("fulfilled_at") else "暂无"
        )
        item["created_at_display"] = format_display_time(item.get("created_at")) if item.get("created_at") else "暂无"
        item["updated_at_display"] = format_display_time(item.get("updated_at")) if item.get("updated_at") else "暂无"
        item["payer_note"] = str(item.get("payer_note") or "").strip()
        item["review_note"] = str(item.get("review_note") or "").strip()
        item["rejection_reason"] = str(item.get("rejection_reason") or "").strip()
        item["proof_available"] = latest_submission_id is not None
        item["latest_submission_id"] = latest_submission_id
        item["proof_review_status"] = str(item.get("proof_review_status") or "").strip()
        item["proof_submitted_at_display"] = (
            format_display_time(item.get("proof_submitted_at")) if item.get("proof_submitted_at") else "暂无"
        )
        item["proof_reviewed_at_display"] = (
            format_display_time(item.get("proof_reviewed_at")) if item.get("proof_reviewed_at") else "暂无"
        )
        return item

    def get_orders_base_query(self):
        return """
            SELECT
                o.id,
                o.order_no,
                o.customer_id,
                o.plan_id,
                o.kind,
                o.service_subscription_id,
                o.status,
                o.plan_name_snapshot,
                o.plan_description_snapshot,
                o.price_fen_snapshot,
                o.currency_snapshot,
                o.duration_days_snapshot,
                o.traffic_limit_bytes_snapshot,
                o.expires_at,
                o.fulfilled_at,
                o.cancelled_at,
                o.rejection_reason,
                o.created_at,
                o.updated_at,
                c.email AS customer_email,
                p.slug AS plan_slug,
                s.port_id AS port_id,
                pt.listen_port AS listen_port,
                ops.id AS latest_submission_id,
                ops.payer_note AS payer_note,
                ops.submitted_at AS proof_submitted_at,
                ops.review_note AS review_note,
                ops.reviewed_at AS proof_reviewed_at,
                ops.review_status AS proof_review_status
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            JOIN plans p ON p.id = o.plan_id
            LEFT JOIN service_subscriptions s ON s.id = o.service_subscription_id
            LEFT JOIN ports pt ON pt.id = s.port_id
            LEFT JOIN order_payment_submissions ops
                ON ops.id = (
                    SELECT id
                    FROM order_payment_submissions
                    WHERE order_id = o.id
                    ORDER BY id DESC
                    LIMIT 1
                )
        """

    def query_customer_orders(self, customer_id):
        self.expire_pending_orders()
        with self.connect() as conn:
            rows = conn.execute(
                self.get_orders_base_query()
                + """
                WHERE o.customer_id = ?
                ORDER BY o.id DESC
                """,
                (customer_id,),
            ).fetchall()
        return [self.serialize_order_row(row) for row in rows]

    def get_customer_order(self, customer_id, order_no):
        self.expire_pending_orders()
        with self.connect() as conn:
            row = conn.execute(
                self.get_orders_base_query()
                + """
                WHERE o.customer_id = ? AND o.order_no = ?
                LIMIT 1
                """,
                (customer_id, order_no),
            ).fetchone()
        if row is None:
            return None
        return self.serialize_order_row(row)

    def query_admin_orders(self, status_filter=""):
        self.expire_pending_orders()
        with self.connect() as conn:
            sql = self.get_orders_base_query()
            params = []
            if status_filter:
                sql += " WHERE o.status = ?"
                params.append(status_filter)
            sql += " ORDER BY o.id DESC LIMIT 100"
            rows = conn.execute(sql, params).fetchall()
        return [self.serialize_order_row(row) for row in rows]

    def get_admin_order(self, order_id):
        self.expire_pending_orders()
        with self.connect() as conn:
            row = conn.execute(
                self.get_orders_base_query()
                + """
                WHERE o.id = ?
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            return None
        return self.serialize_order_row(row)

    def get_payment_submission_record(self, submission_id):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    ops.id,
                    ops.order_id,
                    ops.proof_image_path,
                    o.customer_id
                FROM order_payment_submissions ops
                JOIN orders o ON o.id = ops.order_id
                WHERE ops.id = ?
                LIMIT 1
                """,
                (submission_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def query_commerce_overview(self):
        self.expire_pending_orders()
        with self.connect() as conn:
            customer_count = int(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
            enabled_plan_count = int(conn.execute("SELECT COUNT(*) FROM plans WHERE enabled = 1").fetchone()[0])
            service_count = int(conn.execute("SELECT COUNT(*) FROM service_subscriptions").fetchone()[0])
            pending_review_count = int(
                conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'payment_submitted'").fetchone()[0]
            )
        return {
            "customer_count": customer_count,
            "enabled_plan_count": enabled_plan_count,
            "service_count": service_count,
            "pending_review_count": pending_review_count,
        }

    def get_service_subscription_row_in_tx(self, conn, service_subscription_id, customer_id=None):
        today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        sql = """
            SELECT
                s.id,
                s.customer_id,
                s.plan_id,
                s.port_id,
                s.source_order_id,
                s.latest_order_id,
                s.created_at,
                s.updated_at,
                p.name AS plan_name,
                p.slug AS plan_slug,
                pt.listen_port,
                pt.note,
                pt.enabled,
                pt.expires_at,
                pt.traffic_limit_bytes,
                pt.tenant_token,
                pt.subscription_token,
                pt.tenant_username,
                pt.tenant_password,
                COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                COALESCE(t.total_bytes_received, 0) AS total_bytes_received,
                COALESCE(d.total_bytes_sent, 0) AS today_bytes_sent,
                COALESCE(d.total_bytes_received, 0) AS today_bytes_received,
                t.last_seen AS last_seen
            FROM service_subscriptions s
            JOIN plans p ON p.id = s.plan_id
            LEFT JOIN ports pt ON pt.id = s.port_id
            LEFT JOIN traffic_totals t ON t.listen_port = pt.listen_port
            LEFT JOIN traffic_daily d ON d.listen_port = pt.listen_port AND d.stat_date = ?
            WHERE s.id = ?
        """
        params = [today, service_subscription_id]
        if customer_id is not None:
            sql += " AND s.customer_id = ?"
            params.append(customer_id)
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["traffic_usage_bytes"] = int(item.get("total_bytes_sent") or 0) + int(item.get("total_bytes_received") or 0)
        status = status_payload(
            bool(item.get("enabled")),
            item.get("expires_at"),
            item.get("traffic_limit_bytes"),
            item["traffic_usage_bytes"],
        )
        item["status"] = status["code"]
        item["status_label"] = status["label"]
        item["renewal_allowed"] = item["status"] in {"expired", "quota"}
        item["expires_at_display"] = format_display_time(item.get("expires_at"))
        item["traffic_limit_display"] = (
            human_bytes(item.get("traffic_limit_bytes")) if item.get("traffic_limit_bytes") is not None else "无限制"
        )
        item["traffic_used_display"] = human_bytes(item["traffic_usage_bytes"])
        item["last_seen_display"] = format_display_time(item.get("last_seen")) if item.get("last_seen") else "暂无"
        return item

    def query_customer_service_subscriptions(self, customer_id):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM service_subscriptions WHERE customer_id = ? ORDER BY id DESC",
                (customer_id,),
            ).fetchall()
            return [self.get_service_subscription_row_in_tx(conn, row["id"], customer_id=customer_id) for row in rows]

    def get_customer_service_subscription(self, customer_id, service_subscription_id):
        with self.connect() as conn:
            return self.get_service_subscription_row_in_tx(conn, service_subscription_id, customer_id=customer_id)

    def allocate_auto_port_in_tx(self, conn):
        if COMMERCE_AUTO_PORT_START is None or COMMERCE_AUTO_PORT_END is None:
            raise ValidationError("商业化自动分配端口范围未配置。")
        rows = conn.execute(
            """
            SELECT listen_port
            FROM ports
            WHERE listen_port BETWEEN ? AND ?
            ORDER BY listen_port ASC
            """,
            (COMMERCE_AUTO_PORT_START, COMMERCE_AUTO_PORT_END),
        ).fetchall()
        used = {int(row["listen_port"]) for row in rows}
        for listen_port in range(COMMERCE_AUTO_PORT_START, COMMERCE_AUTO_PORT_END + 1):
            if listen_port not in used:
                return listen_port
        raise ValidationError("自动分配端口范围已耗尽，请扩容可售端口区间。")

    def compute_service_expiry(self, duration_days):
        return (utc_now() + timedelta(days=int(duration_days))).isoformat(timespec="seconds")

    def build_service_note(self, customer_email, plan_name):
        base = f"{plan_name} / {customer_email}"
        if len(base) <= 200:
            return base
        return base[:200]

    def reset_port_usage_in_tx(self, conn, listen_port):
        conn.execute(
            """
            UPDATE traffic_totals
            SET total_bytes_sent = 0, total_bytes_received = 0
            WHERE listen_port = ?
            """,
            (listen_port,),
        )
        conn.execute(
            """
            UPDATE traffic_daily
            SET total_bytes_sent = 0, total_bytes_received = 0
            WHERE listen_port = ?
            """,
            (listen_port,),
        )

    def mark_latest_payment_submission_reviewed_in_tx(self, conn, order_id, review_status, review_note):
        latest_submission = conn.execute(
            """
            SELECT id
            FROM order_payment_submissions
            WHERE order_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (order_id,),
        ).fetchone()
        if latest_submission is None:
            raise ValidationError("订单缺少支付凭证。")
        conn.execute(
            """
            UPDATE order_payment_submissions
            SET review_status = ?, review_note = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (review_status, str(review_note or "").strip(), utc_iso_now(), latest_submission["id"]),
        )

    def fulfill_order(self, order_id, review_note=""):
        note_text = str(review_note or "").strip()
        if len(note_text) > 300:
            raise ValidationError("审核备注不能超过 300 个字符。")

        with self.write_lock:
            self.sync_traffic_state_locked()
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self.expire_pending_orders_in_tx(conn)
                    order = conn.execute(
                        """
                        SELECT
                            o.*,
                            c.email AS customer_email
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE o.id = ?
                        LIMIT 1
                        """,
                        (order_id,),
                    ).fetchone()
                    if order is None:
                        raise ValidationError("订单不存在。")
                    if order["status"] != "payment_submitted":
                        raise ValidationError("当前订单状态不允许审核开通。")

                    now_text = utc_iso_now()
                    service_expires_at = self.compute_service_expiry(order["duration_days_snapshot"])
                    service_subscription_id = order["service_subscription_id"]

                    if order["kind"] == "new_purchase":
                        listen_port = self.allocate_auto_port_in_tx(conn)
                        conn.execute(
                            """
                            INSERT INTO ports (
                                listen_port, upstream_host, upstream_port,
                                tenant_token, subscription_token, tenant_username, tenant_password,
                                expires_at, traffic_limit_bytes, enabled, note,
                                customer_id, service_subscription_id, source_order_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, ?, ?, ?)
                            """,
                            (
                                listen_port,
                                DEFAULT_UPSTREAM_HOST,
                                DEFAULT_UPSTREAM_PORT,
                                self.generate_unique_port_token(conn, "tenant_token"),
                                self.generate_unique_port_token(conn, "subscription_token"),
                                self.generate_unique_tenant_username(conn),
                                generate_tenant_password(),
                                service_expires_at,
                                order["traffic_limit_bytes_snapshot"],
                                self.build_service_note(order["customer_email"], order["plan_name_snapshot"]),
                                order["customer_id"],
                                order["id"],
                                now_text,
                                now_text,
                            ),
                        )
                        port_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                        conn.execute(
                            """
                            INSERT INTO service_subscriptions (
                                customer_id, plan_id, port_id, source_order_id, latest_order_id, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                order["customer_id"],
                                order["plan_id"],
                                port_id,
                                order["id"],
                                order["id"],
                                now_text,
                                now_text,
                            ),
                        )
                        service_subscription_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                        conn.execute(
                            """
                            UPDATE ports
                            SET service_subscription_id = ?
                            WHERE id = ?
                            """,
                            (service_subscription_id, port_id),
                        )
                    else:
                        service_row = self.get_service_subscription_row_in_tx(
                            conn,
                            order["service_subscription_id"],
                            customer_id=order["customer_id"],
                        )
                        if service_row is None or service_row.get("port_id") is None:
                            raise ValidationError("续费目标服务不存在。")
                        port_id = int(service_row["port_id"])
                        conn.execute(
                            """
                            UPDATE ports
                            SET expires_at = ?, traffic_limit_bytes = ?, enabled = 1, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                service_expires_at,
                                order["traffic_limit_bytes_snapshot"],
                                now_text,
                                port_id,
                            ),
                        )
                        self.reset_port_usage_in_tx(conn, service_row["listen_port"])
                        conn.execute(
                            """
                            UPDATE service_subscriptions
                            SET latest_order_id = ?, plan_id = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                order["id"],
                                order["plan_id"],
                                now_text,
                                order["service_subscription_id"],
                            ),
                        )
                        service_subscription_id = order["service_subscription_id"]

                    self.mark_latest_payment_submission_reviewed_in_tx(conn, order["id"], "approved", note_text)
                    conn.execute(
                        """
                        UPDATE orders
                        SET status = 'fulfilled',
                            fulfilled_at = ?,
                            rejection_reason = '',
                            service_subscription_id = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_text, service_subscription_id, now_text, order["id"]),
                    )
                    self.disable_auto_stopped_ports_in_tx(conn)
                    self.persist_and_reload(conn, reload_xray=True)
                except Exception:
                    conn.rollback()
                    raise

    def reject_order(self, order_id, review_note=""):
        note_text = str(review_note or "").strip()
        if not note_text:
            raise ValidationError("驳回订单时必须填写原因。")
        if len(note_text) > 300:
            raise ValidationError("驳回原因不能超过 300 个字符。")

        def operation(conn):
            self.expire_pending_orders_in_tx(conn)
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id = ? LIMIT 1",
                (order_id,),
            ).fetchone()
            if order is None:
                raise ValidationError("订单不存在。")
            if order["status"] != "payment_submitted":
                raise ValidationError("当前订单状态不允许驳回。")
            self.mark_latest_payment_submission_reviewed_in_tx(conn, order["id"], "rejected", note_text)
            conn.execute(
                """
                UPDATE orders
                SET status = 'payment_rejected', rejection_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (note_text, utc_iso_now(), order["id"]),
            )

        self.apply_state_update(operation)

    def cancel_order(self, order_id, review_note=""):
        note_text = str(review_note or "").strip()
        if len(note_text) > 300:
            raise ValidationError("取消备注不能超过 300 个字符。")

        def operation(conn):
            self.expire_pending_orders_in_tx(conn)
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id = ? LIMIT 1",
                (order_id,),
            ).fetchone()
            if order is None:
                raise ValidationError("订单不存在。")
            if order["status"] in {"fulfilled", "cancelled", "expired"}:
                raise ValidationError("当前订单状态不允许取消。")
            conn.execute(
                """
                UPDATE orders
                SET status = 'cancelled', cancelled_at = ?, rejection_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (utc_iso_now(), note_text, utc_iso_now(), order["id"]),
            )

        self.apply_state_update(operation)

    def sync_data_plane_ai_state(self):
        result = {
            "report_synced": False,
            "snapshot_synced": False,
        }
        if self.data_plane.supports_ai_report_pull():
            result["report_synced"] = self.data_plane.sync_ai_report_from_remote()
        if self.data_plane.supports_ai_domains_snapshot_pull():
            snapshot = self.data_plane.read_ai_domains_snapshot_from_remote()
            if snapshot.get("exists"):
                self.replace_ai_domains_snapshot(snapshot.get("ai_domains", []))
                result["snapshot_synced"] = True
        return result

    def replace_ai_domains_snapshot(self, rows):
        def operation(conn):
            ensure_ai_domain_schema(conn)
            conn.execute("DELETE FROM ai_domain_observations")
            conn.execute("DELETE FROM ai_domains")

            payloads = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                domain = str(row.get("domain", "")).strip()
                if not domain:
                    continue
                last_protocols = row.get("last_protocols", "[]")
                if isinstance(last_protocols, list):
                    last_protocols = json.dumps(last_protocols, ensure_ascii=True)
                else:
                    last_protocols = str(last_protocols or "[]")
                payloads.append(
                    (
                        domain,
                        str(row.get("classification", "ai") or "ai").strip() or "ai",
                        str(row.get("reason", "") or "").strip(),
                        str(row.get("source", "") or "").strip(),
                        str(row.get("model", "") or "").strip(),
                        str(row.get("first_seen") or "").strip() or None,
                        str(row.get("last_seen") or "").strip() or None,
                        int(row.get("total_hits", 0) or 0),
                        last_protocols,
                        str(row.get("last_report_window_start") or "").strip() or None,
                        str(row.get("last_report_window_end") or "").strip() or None,
                        str(row.get("updated_at") or "").strip() or utc_iso_now(),
                    )
                )

            if payloads:
                conn.executemany(
                    """
                    INSERT INTO ai_domains (
                        domain,
                        classification,
                        reason,
                        source,
                        model,
                        first_seen,
                        last_seen,
                        total_hits,
                        last_protocols,
                        last_report_window_start,
                        last_report_window_end,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payloads,
                )
            return len(payloads)

        return self.apply_state_update(operation)

    def format_optional_display_time(self, value, default="暂无"):
        localized = localize_time(value)
        if localized is None:
            return default
        return localized.strftime("%Y-%m-%d %H:%M:%S")

    def ai_domain_sync_mode_label(self):
        mode = self.data_plane.mode
        if mode == "ssh":
            return "远端镜像"
        if mode in {"local", "docker"}:
            return "本地运行"
        return "本地缓存"

    def ai_route_status_label(self, status, reason=""):
        status_text = str(status or "").strip().lower()
        if status_text == "applied":
            return "已应用 AI 路由"
        if status_text == "idle":
            return "当前窗口无 AI 域名"
        if status_text == "pending_proxy_template":
            return "等待代理模板"
        if status_text == "disabled":
            return "AI 路由未启用"
        if reason:
            return reason
        return "未知状态"

    def ai_route_status_tone(self, status):
        status_text = str(status or "").strip().lower()
        if status_text == "applied":
            return "ok"
        if status_text in {"idle", "disabled"}:
            return "warn"
        return "bad"

    def ai_source_label(self, value):
        text = str(value or "").strip()
        return text or "未标记"

    def decode_json_text_list(self, value):
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def serialize_ai_report_domain(self, item):
        protocols = self.decode_json_text_list(item.get("protocols", []))
        return {
            "domain": str(item.get("domain", "")).strip(),
            "hits": int(item.get("hits", 0) or 0),
            "classification": str(item.get("classification", "unknown") or "unknown").strip() or "unknown",
            "reason": str(item.get("reason", "") or "").strip(),
            "protocols": protocols,
            "protocols_display": ", ".join(protocols) if protocols else "暂无",
            "first_seen": str(item.get("first_seen") or "").strip() or None,
            "last_seen": str(item.get("last_seen") or "").strip() or None,
            "first_seen_display": self.format_optional_display_time(item.get("first_seen")),
            "last_seen_display": self.format_optional_display_time(item.get("last_seen")),
        }

    def serialize_ai_domain_snapshot_row(self, row):
        item = dict(row)
        protocols = self.decode_json_text_list(item.get("last_protocols", "[]"))
        return {
            **item,
            "classification": str(item.get("classification", "ai") or "ai").strip() or "ai",
            "source_display": self.ai_source_label(item.get("source")),
            "protocols": protocols,
            "protocols_display": ", ".join(protocols) if protocols else "暂无",
            "first_seen_display": self.format_optional_display_time(item.get("first_seen")),
            "last_seen_display": self.format_optional_display_time(item.get("last_seen")),
            "updated_at_display": self.format_optional_display_time(item.get("updated_at")),
            "last_report_window_start_display": self.format_optional_display_time(
                item.get("last_report_window_start")
            ),
            "last_report_window_end_display": self.format_optional_display_time(
                item.get("last_report_window_end")
            ),
        }

    def read_ai_domain_report(self):
        report_path = self.data_plane.config.source_ai_report_path or self.data_plane_ai_report_source_path()
        if not report_path.is_file():
            return None
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        route_status = payload.get("route_status", {})
        if not isinstance(route_status, dict):
            route_status = {}
        route_status_code = str(route_status.get("status", "unknown") or "unknown").strip() or "unknown"
        route_status_reason = str(route_status.get("reason", "") or "").strip()

        domains = []
        for raw_item in payload.get("domains", []):
            if not isinstance(raw_item, dict):
                continue
            domain_item = self.serialize_ai_report_domain(raw_item)
            if domain_item["domain"]:
                domains.append(domain_item)
        current_ai_domains = [item for item in domains if item["classification"] == "ai"]

        ai_target = payload.get("ai_target")
        if not isinstance(ai_target, dict):
            ai_target = None
        panel_target = payload.get("panel_target")
        if not isinstance(panel_target, dict):
            panel_target = None

        return {
            "generated_at": str(payload.get("generated_at") or "").strip() or None,
            "generated_at_display": self.format_optional_display_time(payload.get("generated_at")),
            "window_start": str(payload.get("window_start") or "").strip() or None,
            "window_start_display": self.format_optional_display_time(payload.get("window_start")),
            "window_end": str(payload.get("window_end") or "").strip() or None,
            "window_end_display": self.format_optional_display_time(payload.get("window_end")),
            "unique_domains": int(payload.get("unique_domains", len(domains)) or 0),
            "ai_domain_count": len(current_ai_domains),
            "domains": domains,
            "current_ai_domains": current_ai_domains,
            "protocols": [item for item in payload.get("protocols", []) if isinstance(item, dict)],
            "route_status": route_status_code,
            "route_status_reason": route_status_reason,
            "route_status_label": self.ai_route_status_label(route_status_code, route_status_reason),
            "route_status_tone": self.ai_route_status_tone(route_status_code),
            "config_changed": bool(route_status.get("config_changed")),
            "pending_domains_without_classifier": self.normalize_pending_domain_count(
                route_status.get("pending_domains_without_classifier", 0)
            ),
            "ai_target": ai_target,
            "panel_target": panel_target,
        }

    def normalize_pending_domain_count(self, value):
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def query_ai_domain_aggregate(self):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_ai_domains,
                    COALESCE(SUM(total_hits), 0) AS total_hits,
                    MAX(updated_at) AS updated_at
                FROM ai_domains
                """
            ).fetchone()
        return {
            "total_ai_domains": int(row["total_ai_domains"] or 0),
            "total_hits": int(row["total_hits"] or 0),
            "updated_at": row["updated_at"],
            "updated_at_display": self.format_optional_display_time(row["updated_at"]),
        }

    def query_top_ai_domains(self, limit=100):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    domain,
                    classification,
                    reason,
                    source,
                    model,
                    first_seen,
                    last_seen,
                    total_hits,
                    last_protocols,
                    last_report_window_start,
                    last_report_window_end,
                    updated_at
                FROM ai_domains
                ORDER BY total_hits DESC, last_seen DESC, domain ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self.serialize_ai_domain_snapshot_row(row) for row in rows]

    def query_ai_domain_source_breakdown(self):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN TRIM(source) = '' THEN ''
                        ELSE source
                    END AS source,
                    COUNT(*) AS domain_count,
                    COALESCE(SUM(total_hits), 0) AS total_hits,
                    MAX(updated_at) AS updated_at
                FROM ai_domains
                GROUP BY source
                ORDER BY total_hits DESC, domain_count DESC, source ASC
                """
            ).fetchall()
        return [
            {
                "source": row["source"],
                "source_display": self.ai_source_label(row["source"]),
                "domain_count": int(row["domain_count"] or 0),
                "total_hits": int(row["total_hits"] or 0),
                "updated_at": row["updated_at"],
                "updated_at_display": self.format_optional_display_time(row["updated_at"]),
            }
            for row in rows
        ]

    def ai_routing_status(self, sync_error=""):
        report = self.read_ai_domain_report()
        aggregate = self.query_ai_domain_aggregate()
        configured = AI_ROUTING_ENABLED
        if not configured:
            status_code = "disabled"
            status_label = "AI 路由未启用"
            tone = "warn"
        elif report is not None:
            status_code = report["route_status"]
            status_label = report["route_status_label"]
            tone = report["route_status_tone"]
        elif sync_error:
            status_code = "sync_error"
            status_label = "AI 路由同步失败"
            tone = "bad"
        else:
            status_code = "waiting_report"
            status_label = "等待 AI 路由报告"
            tone = "warn"
        return {
            "configured": configured,
            "status": status_code,
            "status_label": status_label,
            "status_tone": tone,
            "sync_mode_label": self.ai_domain_sync_mode_label(),
            "report_generated_at_display": report["generated_at_display"] if report else "暂无",
            "current_ai_domains": report["ai_domain_count"] if report else 0,
            "total_ai_domains": aggregate["total_ai_domains"],
            "sync_error": str(sync_error or "").strip(),
        }

    def query_ai_domain_overview(self, sync_error=""):
        report = self.read_ai_domain_report()
        aggregate = self.query_ai_domain_aggregate()
        return {
            "available": AI_ROUTING_ENABLED and bool(report or aggregate["total_ai_domains"] > 0),
            "enabled": AI_ROUTING_ENABLED,
            "sync_mode": self.data_plane.mode,
            "sync_mode_label": self.ai_domain_sync_mode_label(),
            "sync_error": str(sync_error or "").strip(),
            "report_available": report is not None,
            "current_ai_domains": report["ai_domain_count"] if report else 0,
            "unique_domains": report["unique_domains"] if report else 0,
            "report_generated_at_display": (
                report["generated_at_display"] if report else "暂无"
            ),
            "route_status": report["route_status"] if report else "unknown",
            "route_status_label": report["route_status_label"] if report else "暂无报告",
            "route_status_tone": report["route_status_tone"] if report else "warn",
            "total_ai_domains": aggregate["total_ai_domains"],
            "total_hits": aggregate["total_hits"],
            "aggregate_updated_at_display": aggregate["updated_at_display"],
        }

    def get_ai_domain_dashboard(self, sync_error=""):
        report = self.read_ai_domain_report()
        aggregate = self.query_ai_domain_aggregate()
        top_ai_domains = self.query_top_ai_domains()
        source_breakdown = self.query_ai_domain_source_breakdown()
        return {
            "available": AI_ROUTING_ENABLED and bool(report or top_ai_domains),
            "enabled": AI_ROUTING_ENABLED,
            "sync_mode": self.data_plane.mode,
            "sync_mode_label": self.ai_domain_sync_mode_label(),
            "sync_error": str(sync_error or "").strip(),
            "report": (
                report
                if report is not None
                else {
                    "generated_at_display": "暂无",
                    "window_start_display": "暂无",
                    "window_end_display": "暂无",
                    "unique_domains": 0,
                    "ai_domain_count": 0,
                    "domains": [],
                    "current_ai_domains": [],
                    "route_status": "unknown",
                    "route_status_label": "暂无报告",
                    "route_status_tone": "warn",
                    "config_changed": False,
                    "pending_domains_without_classifier": 0,
                    "ai_target": None,
                    "panel_target": None,
                }
            ),
            "aggregate": aggregate,
            "top_ai_domains": top_ai_domains,
            "source_breakdown": source_breakdown,
        }

    def validate_port_payload(self, form):
        return {
            "listen_port": parse_port(form.get("listen_port"), "监听端口"),
            "upstream_host": DEFAULT_UPSTREAM_HOST,
            "upstream_port": DEFAULT_UPSTREAM_PORT,
            "expires_at": parse_expiry(form.get("expires_at")),
            "traffic_limit_bytes": parse_data_size(form.get("traffic_limit"), "流量上限"),
            "note": parse_note(form.get("note")),
        }

    def create_port(self, payload):
        def operation(conn):
            now = utc_iso_now()
            conn.execute(
                """
                INSERT INTO ports (
                    listen_port, upstream_host, upstream_port, tenant_token, subscription_token,
                    tenant_username, tenant_password,
                    expires_at, traffic_limit_bytes, enabled, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    payload["listen_port"],
                    payload["upstream_host"],
                    payload["upstream_port"],
                    self.generate_unique_port_token(conn, "tenant_token"),
                    self.generate_unique_port_token(conn, "subscription_token"),
                    self.generate_unique_tenant_username(conn),
                    generate_tenant_password(),
                    payload["expires_at"],
                    payload["traffic_limit_bytes"],
                    payload["note"],
                    now,
                    now,
                ),
            )

        self.apply_mutation(operation)

    def update_port(self, port_id, payload):
        def operation(conn):
            now = utc_iso_now()
            existing = conn.execute("SELECT id FROM ports WHERE id = ?", (port_id,)).fetchone()
            if existing is None:
                raise ValidationError("端口记录不存在。")
            conn.execute(
                """
                UPDATE ports
                SET listen_port = ?, upstream_host = ?, upstream_port = ?, expires_at = ?, traffic_limit_bytes = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["listen_port"],
                    payload["upstream_host"],
                    payload["upstream_port"],
                    payload["expires_at"],
                    payload["traffic_limit_bytes"],
                    payload["note"],
                    now,
                    port_id,
                ),
            )

        self.apply_mutation(operation)

    def toggle_port(self, port_id):
        def operation(conn):
            row = conn.execute(
                "SELECT id, listen_port, enabled, expires_at, traffic_limit_bytes FROM ports WHERE id = ?",
                (port_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            next_enabled = 0 if row["enabled"] else 1
            if next_enabled and row["expires_at"]:
                expires_at = datetime.fromisoformat(row["expires_at"])
                if expires_at <= utc_now():
                    raise ValidationError("端口已过期，请先修改到期时间再启用。")
            if next_enabled and row["traffic_limit_bytes"] is not None:
                usage_bytes = self.get_port_usage_bytes(conn, row["listen_port"])
                if usage_bytes >= int(row["traffic_limit_bytes"]):
                    raise ValidationError("端口已达到流量上限，请先提高上限再启用。")
            conn.execute(
                "UPDATE ports SET enabled = ?, updated_at = ? WHERE id = ?",
                (next_enabled, utc_iso_now(), port_id),
            )

        self.apply_mutation(operation)

    def delete_port(self, port_id):
        def operation(conn):
            row = conn.execute(
                "SELECT listen_port, customer_id, service_subscription_id FROM ports WHERE id = ?",
                (port_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            if row["customer_id"] is not None or row["service_subscription_id"] is not None:
                raise ValidationError("商业化服务端口不能直接删除，请通过业务流程处理。")
            self.delete_port_in_tx(conn, port_id, row["listen_port"])

        self.apply_mutation(operation)

    def disable_expired_ports(self, reload_xray=True):
        return self.disable_auto_stopped_ports(reload_xray=reload_xray)

    def run_upstream_probes(self):
        if not PROBE_ENABLED:
            return 0

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT listen_port
                FROM ports
                WHERE enabled = 1
                ORDER BY listen_port ASC
                """
            ).fetchall()

        results = []
        for row in rows:
            checked_at = utc_iso_now()
            reachable = 0
            failure_reason = ""
            try:
                with socket.create_connection(
                    (DATAPLANE_PROBE_HOST, int(row["listen_port"])),
                    timeout=PROBE_TIMEOUT,
                ):
                    reachable = 1
            except OSError as exc:
                failure_reason = str(exc)[:200]
            results.append((row["listen_port"], reachable, checked_at, failure_reason))

        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for item in results:
                    conn.execute(
                        """
                        INSERT INTO upstream_probes (listen_port, is_reachable, checked_at, failure_reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(listen_port) DO UPDATE SET
                            is_reachable = excluded.is_reachable,
                            checked_at = excluded.checked_at,
                            failure_reason = excluded.failure_reason
                        """,
                        item,
                    )
                    conn.execute(
                        """
                        INSERT INTO upstream_probe_history (
                            listen_port, is_reachable, checked_at, failure_reason
                        ) VALUES (?, ?, ?, ?)
                        """,
                        item,
                    )
                cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 3600
                conn.execute(
                    """
                    DELETE FROM upstream_probe_history
                    WHERE strftime('%s', checked_at) < ?
                    """,
                    (int(cutoff),),
                )
                conn.commit()
        return len(results)

    def get_probe_dashboard(self, range_key):
        active_range_key = range_key if range_key in PROBE_DASHBOARD_RANGES else "24h"
        active_range = PROBE_DASHBOARD_RANGES[active_range_key]
        since_dt = utc_now().timestamp() - active_range["hours"] * 3600
        with self.connect() as conn:
            if PROBE_TEST_LISTEN_PORT is not None:
                test_port = conn.execute(
                    """
                    SELECT listen_port, upstream_host, upstream_port, note, enabled
                    FROM ports
                    WHERE listen_port = ?
                    LIMIT 1
                    """,
                    (PROBE_TEST_LISTEN_PORT,),
                ).fetchone()
            else:
                test_port = conn.execute(
                    """
                    SELECT listen_port, upstream_host, upstream_port, note, enabled
                    FROM ports
                    WHERE enabled = 1
                    ORDER BY listen_port ASC
                    LIMIT 1
                    """
                ).fetchone()
            if test_port is None:
                return {
                    "test_port": None,
                    "summary": None,
                    "chart_points": [],
                    "recent_checks": [],
                    "requested_test_port": PROBE_TEST_LISTEN_PORT,
                    "range_key": active_range_key,
                    "range_label": active_range["label"],
                    "range_options": self.probe_dashboard_range_options(active_range_key),
                }

            history_rows = conn.execute(
                """
                SELECT is_reachable, checked_at, failure_reason
                FROM upstream_probe_history
                WHERE listen_port = ?
                ORDER BY checked_at DESC
                LIMIT 300
                """,
                (test_port["listen_port"],),
            ).fetchall()

        filtered_rows = []
        for row in history_rows:
            checked_local = localize_time(row["checked_at"])
            if checked_local is None:
                continue
            if checked_local.timestamp() >= since_dt:
                filtered_rows.append(row)

        history = list(reversed(filtered_rows[:120]))
        chart_points = []
        healthy_count = 0
        unhealthy_count = 0
        last_success = None
        last_failure = None
        for index, row in enumerate(history):
            checked_local = localize_time(row["checked_at"])
            is_reachable = bool(row["is_reachable"])
            if is_reachable:
                healthy_count += 1
                last_success = checked_local
            else:
                unhealthy_count += 1
                last_failure = checked_local
            chart_points.append(
                {
                    "x": index,
                    "y": 1 if is_reachable else 0,
                    "label": checked_local.strftime("%m-%d %H:%M:%S") if checked_local else "",
                    "status": "healthy" if is_reachable else "unhealthy",
                }
            )

        recent_checks = []
        for row in filtered_rows[:12]:
            checked_local = localize_time(row["checked_at"])
            recent_checks.append(
                {
                    "status": "healthy" if row["is_reachable"] else "unhealthy",
                    "status_label": "可达" if row["is_reachable"] else "不可达",
                    "checked_at_display": checked_local.strftime("%Y-%m-%d %H:%M:%S") if checked_local else "暂无",
                    "failure_reason": row["failure_reason"] or "",
                }
            )

        total_checks = healthy_count + unhealthy_count
        uptime_ratio = (healthy_count / total_checks * 100) if total_checks else 0.0
        current_status = "unknown"
        current_status_label = "未检测"
        if filtered_rows:
            current_status = "healthy" if filtered_rows[0]["is_reachable"] else "unhealthy"
            current_status_label = "端口可达" if filtered_rows[0]["is_reachable"] else "端口不可达"

        return {
            "test_port": {
                "listen_port": test_port["listen_port"],
                "fixed": PROBE_TEST_LISTEN_PORT is not None,
                "enabled": bool(test_port["enabled"]),
            },
            "summary": {
                "current_status": current_status,
                "current_status_label": current_status_label,
                "total_checks": total_checks,
                "healthy_count": healthy_count,
                "unhealthy_count": unhealthy_count,
                "uptime_ratio": f"{uptime_ratio:.1f}",
                "last_success_display": last_success.strftime("%Y-%m-%d %H:%M:%S") if last_success else "暂无",
                "last_failure_display": last_failure.strftime("%Y-%m-%d %H:%M:%S") if last_failure else "暂无",
            },
            "chart_points": chart_points,
            "recent_checks": recent_checks,
            "requested_test_port": PROBE_TEST_LISTEN_PORT,
            "range_key": active_range_key,
            "range_label": active_range["label"],
            "range_options": self.probe_dashboard_range_options(active_range_key),
        }

    def probe_dashboard_range_options(self, active_range_key):
        return [
            {
                "key": key,
                "label": config["label"],
                "active": key == active_range_key,
            }
            for key, config in PROBE_DASHBOARD_RANGES.items()
        ]

    def disable_auto_stopped_ports(self, reload_xray=True):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                changed = self.disable_auto_stopped_ports_in_tx(conn)
                if changed:
                    self.persist_and_reload(conn, reload_xray=reload_xray)
                else:
                    conn.commit()
                return changed

    def apply_mutation(self, operation):
        with self.write_lock:
            self.sync_traffic_state_locked()
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = operation(conn)
                    self.disable_auto_stopped_ports_in_tx(conn)
                    self.persist_and_reload(conn, reload_xray=True)
                    return result
                except Exception:
                    conn.rollback()
                    raise

    def apply_state_update(self, operation):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = operation(conn)
                    conn.commit()
                    return result
                except Exception:
                    conn.rollback()
                    raise

    def write_dns_failover_history(self, conn, event_type, event_status, target="", record_content="", detail=""):
        conn.execute(
            """
            INSERT INTO dns_failover_history (
                event_type, event_status, target, record_content, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                event_status,
                target,
                record_content,
                detail[:500],
                utc_iso_now(),
            ),
        )
        conn.execute(
            """
            DELETE FROM dns_failover_history
            WHERE id NOT IN (
                SELECT id FROM dns_failover_history ORDER BY id DESC LIMIT 500
            )
            """
        )

    def update_dns_failover_state(self, conn, **values):
        allowed = {
            "current_target",
            "current_record_content",
            "current_record_ttl",
            "current_record_proxied",
            "last_probe_status",
            "last_probe_checked_at",
            "last_probe_error",
            "consecutive_failures",
            "consecutive_successes",
            "last_switch_at",
            "last_switch_reason",
            "last_error",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE dns_failover_state SET {assignments} WHERE singleton_id = 1",
            list(updates.values()),
        )

    def get_dns_failover_state_row(self, conn):
        row = conn.execute("SELECT * FROM dns_failover_state WHERE singleton_id = 1").fetchone()
        if row is None:
            self.ensure_dns_failover_schema(conn)
            row = conn.execute("SELECT * FROM dns_failover_state WHERE singleton_id = 1").fetchone()
        return row

    def run_dns_failover_check(self, force=False):
        status = self.dns_failover_status()
        if not status["enabled"]:
            return status
        if not status["configured"]:
            with self.write_lock:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self.update_dns_failover_state(conn, last_error=status["config_error"])
                    self.write_dns_failover_history(conn, "probe", "skipped", detail=status["config_error"])
                    conn.commit()
            return self.dns_failover_status()

        probe = self.dns_failover_manager.probe_once()
        probe_status = "healthy" if probe["ok"] else "unhealthy"
        switch_result = None
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = self.get_dns_failover_state_row(conn)
                current_target = str(row["current_target"] or "").strip() or "primary"
                if current_target not in {"primary", "backup"}:
                    inferred = self.dns_failover_manager.current_target_from_content(
                        row["current_record_content"],
                        primary_content=status["primary_content"],
                        backup_content=status["backup_content"],
                    )
                    current_target = inferred if inferred in {"primary", "backup"} else "primary"
                consecutive_failures = int(row["consecutive_failures"] or 0)
                consecutive_successes = int(row["consecutive_successes"] or 0)
                if probe["ok"]:
                    consecutive_successes += 1
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    consecutive_successes = 0
                self.update_dns_failover_state(
                    conn,
                    last_probe_status=probe_status,
                    last_probe_checked_at=utc_iso_now(),
                    last_probe_error=probe["error"],
                    consecutive_failures=consecutive_failures,
                    consecutive_successes=consecutive_successes,
                    last_error="",
                )
                detail = probe["error"] or self.dns_failover_probe_status_label(probe_status)
                self.write_dns_failover_history(
                    conn,
                    "probe",
                    "ok" if probe["ok"] else "error",
                    target=current_target,
                    record_content=row["current_record_content"] or "",
                    detail=detail,
                )
                transition = self.dns_failover_manager.evaluate_transition(
                    current_target,
                    consecutive_failures,
                    consecutive_successes,
                    probe["ok"],
                )
                conn.commit()

        if transition is not None:
            switch_result = self.switch_dns_target(transition["target"], reason=transition["reason"], auto=True)
        return self.dns_failover_status() if switch_result is None else switch_result

    def switch_dns_target(self, target, reason="manual_switch", auto=False):
        target = str(target or "").strip().lower()
        if target not in {"primary", "backup"}:
            raise ValidationError("target 仅支持 primary 或 backup。")
        status = self.dns_failover_status()
        if not status["enabled"]:
            raise ValidationError("DNS 故障切换未启用。")
        if not status["configured"]:
            raise ValidationError(status["config_error"] or "DNS 故障切换配置不完整。")

        primary_content = status["primary_content"]
        backup_content = status["backup_content"]

        try:
            record = self.dns_failover_manager.get_record()
        except CloudflareApiError as exc:
            with self.write_lock:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self.update_dns_failover_state(conn, last_error=str(exc))
                    self.write_dns_failover_history(
                        conn,
                        "switch",
                        "error",
                        target=target,
                        detail=str(exc),
                    )
                    conn.commit()
            raise ValidationError(str(exc)) from exc
        current_content = str(record.get("content") or "").strip()
        current_target = self.dns_failover_manager.current_target_from_content(
            current_content,
            primary_content=primary_content,
            backup_content=backup_content,
        )
        if current_target == target:
            with self.write_lock:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self.update_dns_failover_state(
                        conn,
                        current_target=target,
                        current_record_content=current_content,
                        current_record_ttl=record.get("ttl"),
                        current_record_proxied=1 if record.get("proxied") else 0,
                        last_error="",
                    )
                    self.write_dns_failover_history(
                        conn,
                        "switch",
                        "noop",
                        target=target,
                        record_content=current_content,
                        detail="DNS 已在目标指向，无需重复更新。",
                    )
                    conn.commit()
            return self.dns_failover_status()

        try:
            updated = self.dns_failover_manager.sync_target(
                target,
                primary_content=primary_content,
                backup_content=backup_content,
            )
        except CloudflareApiError as exc:
            with self.write_lock:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self.update_dns_failover_state(conn, last_error=str(exc))
                    self.write_dns_failover_history(
                        conn,
                        "switch",
                        "error",
                        target=target,
                        record_content=current_content,
                        detail=str(exc),
                    )
                    conn.commit()
            raise ValidationError(str(exc)) from exc

        updated_content = str(updated.get("content") or "").strip()
        updated_target = self.dns_failover_manager.current_target_from_content(
            updated_content,
            primary_content=primary_content,
            backup_content=backup_content,
        )
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.update_dns_failover_state(
                    conn,
                    current_target=updated_target if updated_target in {"primary", "backup"} else target,
                    current_record_content=updated_content,
                    current_record_ttl=updated.get("ttl"),
                    current_record_proxied=1 if updated.get("proxied") else 0,
                    last_switch_at=utc_iso_now(),
                    last_switch_reason=reason,
                    last_error="",
                )
                self.write_dns_failover_history(
                    conn,
                    "switch",
                    "ok",
                    target=target,
                    record_content=updated_content,
                    detail="自动切换完成。" if auto else "手动切换完成。",
                )
                conn.commit()
        return self.dns_failover_status()

    def refresh_dns_failover_record_snapshot(self):
        status = self.dns_failover_status()
        if not status["enabled"] or not status["configured"]:
            return status
        try:
            record = self.dns_failover_manager.get_record()
        except CloudflareApiError as exc:
            with self.write_lock:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self.update_dns_failover_state(conn, last_error=str(exc))
                    conn.commit()
            return self.dns_failover_status()
        current_content = str(record.get("content") or "").strip()
        current_target = self.dns_failover_manager.current_target_from_content(
            current_content,
            primary_content=status["primary_content"],
            backup_content=status["backup_content"],
        )
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.update_dns_failover_state(
                    conn,
                    current_target=current_target if current_target in {"primary", "backup"} else "primary",
                    current_record_content=current_content,
                    current_record_ttl=record.get("ttl"),
                    current_record_proxied=1 if record.get("proxied") else 0,
                    last_error="",
                )
                conn.commit()
        return self.dns_failover_status()

    def rotate_port_tenant_token(self, port_id):
        def operation(conn):
            row = conn.execute("SELECT id FROM ports WHERE id = ?", (port_id,)).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            token = self.generate_unique_port_token(conn, "tenant_token")
            conn.execute(
                "UPDATE ports SET tenant_token = ?, updated_at = ? WHERE id = ?",
                (token, utc_iso_now(), port_id),
            )
            return token

        return self.apply_state_update(operation)

    def rotate_port_subscription_token(self, port_id):
        def operation(conn):
            row = conn.execute("SELECT id FROM ports WHERE id = ?", (port_id,)).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            token = self.generate_unique_port_token(conn, "subscription_token")
            conn.execute(
                "UPDATE ports SET subscription_token = ?, updated_at = ? WHERE id = ?",
                (token, utc_iso_now(), port_id),
            )
            return token

        return self.apply_state_update(operation)

    def rotate_port_tenant_credentials(self, port_id):
        def operation(conn):
            row = conn.execute("SELECT id FROM ports WHERE id = ?", (port_id,)).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            username = self.generate_unique_tenant_username(conn)
            password = generate_tenant_password()
            conn.execute(
                "UPDATE ports SET tenant_username = ?, tenant_password = ?, updated_at = ? WHERE id = ?",
                (username, password, utc_iso_now(), port_id),
            )
            return {"tenant_username": username, "tenant_password": password}

        return self.apply_state_update(operation)

    def get_port_usage_bytes(self, conn, listen_port):
        row = conn.execute(
            """
            SELECT
                COALESCE(total_bytes_sent, 0) + COALESCE(total_bytes_received, 0) AS usage_bytes
            FROM traffic_totals
            WHERE listen_port = ?
            """,
            (listen_port,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["usage_bytes"])

    def delete_port_in_tx(self, conn, port_id, listen_port):
        conn.execute("DELETE FROM ports WHERE id = ?", (port_id,))
        conn.execute("DELETE FROM traffic_totals WHERE listen_port = ?", (listen_port,))
        conn.execute("DELETE FROM traffic_daily WHERE listen_port = ?", (listen_port,))
        conn.execute("DELETE FROM upstream_probes WHERE listen_port = ?", (listen_port,))
        conn.execute("DELETE FROM upstream_probe_history WHERE listen_port = ?", (listen_port,))

    def cleanup_expired_ports_in_tx(self, conn):
        rows = conn.execute(
            """
            SELECT id, listen_port
            FROM ports
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
              AND COALESCE(customer_id, 0) = 0
              AND COALESCE(service_subscription_id, 0) = 0
            """,
            (utc_iso_now(),),
        ).fetchall()
        for row in rows:
            self.delete_port_in_tx(conn, row["id"], row["listen_port"])
        return len(rows)

    def get_port_by_tenant_token(self, tenant_token):
        today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(t.total_connections, 0) AS total_connections,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received,
                    t.last_seen AS last_seen,
                    pr.is_reachable AS probe_is_reachable,
                    pr.checked_at AS probe_checked_at,
                    pr.failure_reason AS probe_failure_reason,
                    COALESCE(d.total_connections, 0) AS today_connections,
                    COALESCE(d.total_bytes_sent, 0) AS today_bytes_sent,
                    COALESCE(d.total_bytes_received, 0) AS today_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                LEFT JOIN upstream_probes pr ON pr.listen_port = p.listen_port
                LEFT JOIN traffic_daily d ON d.listen_port = p.listen_port AND d.stat_date = ?
                WHERE p.tenant_token = ?
                LIMIT 1
                """,
                (today, tenant_token),
            ).fetchone()

        if row is None:
            return None
        return self.serialize_port_row(row)

    def get_port_by_tenant_username(self, tenant_username):
        today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(t.total_connections, 0) AS total_connections,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received,
                    t.last_seen AS last_seen,
                    pr.is_reachable AS probe_is_reachable,
                    pr.checked_at AS probe_checked_at,
                    pr.failure_reason AS probe_failure_reason,
                    COALESCE(d.total_connections, 0) AS today_connections,
                    COALESCE(d.total_bytes_sent, 0) AS today_bytes_sent,
                    COALESCE(d.total_bytes_received, 0) AS today_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                LEFT JOIN upstream_probes pr ON pr.listen_port = p.listen_port
                LEFT JOIN traffic_daily d ON d.listen_port = p.listen_port AND d.stat_date = ?
                WHERE p.tenant_username = ?
                LIMIT 1
                """,
                (today, str(tenant_username or "").strip()),
            ).fetchone()

        if row is None:
            return None
        return self.serialize_port_row(row)

    def get_port_subscription_record_by_token(self, subscription_token):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.id,
                    p.listen_port,
                    p.note,
                    p.enabled,
                    p.expires_at,
                    p.traffic_limit_bytes,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                WHERE p.subscription_token = ?
                LIMIT 1
                """,
                (subscription_token,),
            ).fetchone()

        if row is None:
            return None

        item = dict(row)
        item["traffic_usage_bytes"] = int(item["total_bytes_sent"]) + int(item["total_bytes_received"])
        status = status_payload(
            bool(item["enabled"]),
            item["expires_at"],
            item["traffic_limit_bytes"],
            item["traffic_usage_bytes"],
        )
        item["status"] = status["code"]
        item["status_label"] = status["label"]
        return item

    def reset_port_traffic(self, port_id):
        def operation(conn):
            row = conn.execute(
                """
                SELECT
                    p.id,
                    p.listen_port,
                    p.enabled,
                    p.expires_at,
                    p.traffic_limit_bytes,
                    COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                    COALESCE(t.total_bytes_received, 0) AS total_bytes_received
                FROM ports p
                LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
                WHERE p.id = ?
                """,
                (port_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")

            conn.execute(
                """
                UPDATE traffic_totals
                SET total_bytes_sent = 0, total_bytes_received = 0
                WHERE listen_port = ?
                """,
                (row["listen_port"],),
            )
            conn.execute(
                """
                UPDATE traffic_daily
                SET total_bytes_sent = 0, total_bytes_received = 0
                WHERE listen_port = ?
                """,
                (row["listen_port"],),
            )

            now_dt = utc_now()
            expired = False
            if row["expires_at"]:
                expired = datetime.fromisoformat(row["expires_at"]) <= now_dt
            usage_bytes = int(row["total_bytes_sent"]) + int(row["total_bytes_received"])
            quota_reached = row["traffic_limit_bytes"] is not None and usage_bytes >= int(row["traffic_limit_bytes"])

            next_enabled = int(row["enabled"])
            restored = False
            if quota_reached and not expired:
                next_enabled = 1
                restored = True

            conn.execute(
                "UPDATE ports SET enabled = ?, updated_at = ? WHERE id = ?",
                (next_enabled, now_dt.isoformat(timespec="seconds"), port_id),
            )
            return restored

        return self.apply_mutation(operation)

    def disable_auto_stopped_ports_in_tx(self, conn):
        now_dt = utc_now()
        now_text = now_dt.isoformat(timespec="seconds")
        cleaned = self.cleanup_expired_ports_in_tx(conn)
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.listen_port,
                p.expires_at,
                p.traffic_limit_bytes,
                COALESCE(t.total_bytes_sent, 0) AS total_bytes_sent,
                COALESCE(t.total_bytes_received, 0) AS total_bytes_received
            FROM ports p
            LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
            WHERE p.enabled = 1
            """,
        ).fetchall()
        changed = 0
        for row in rows:
            usage_bytes = int(row["total_bytes_sent"]) + int(row["total_bytes_received"])
            quota_reached = row["traffic_limit_bytes"] is not None and usage_bytes >= int(row["traffic_limit_bytes"])
            if quota_reached:
                conn.execute(
                    "UPDATE ports SET enabled = 0, updated_at = ? WHERE id = ?",
                    (now_text, row["id"]),
                )
                changed += 1
        return changed + cleaned

    def persist_and_reload(self, conn, reload_xray):
        previous_panel_ports = XRAY_PANEL_PORTS_PATH.read_text(encoding="utf-8") if XRAY_PANEL_PORTS_PATH.exists() else None
        previous_config = XRAY_CONFIG_PATH.read_text(encoding="utf-8") if XRAY_CONFIG_PATH.exists() else None
        panel_ports_payload = self.render_panel_ports_payload(conn)
        self.write_json_file(XRAY_PANEL_PORTS_PATH, panel_ports_payload)
        try:
            self.render_xray_config()
            self.xray_config_test()
            if reload_xray:
                self.restart_data_plane()
        except Exception:
            if previous_panel_ports is None:
                XRAY_PANEL_PORTS_PATH.unlink(missing_ok=True)
            else:
                XRAY_PANEL_PORTS_PATH.write_text(previous_panel_ports, encoding="utf-8")
            if previous_config is None:
                XRAY_CONFIG_PATH.unlink(missing_ok=True)
            else:
                XRAY_CONFIG_PATH.write_text(previous_config, encoding="utf-8")
            if self.data_plane.supports_sync():
                try:
                    self.data_plane.sync_generated_files(validate_config=True)
                except RuntimeError:
                    pass
            raise
        conn.commit()

    def render_panel_ports_payload(self, conn):
        rows = conn.execute(
            """
            SELECT
                p.listen_port
            FROM ports
            AS p
            LEFT JOIN traffic_totals t ON t.listen_port = p.listen_port
            WHERE p.enabled = 1
              AND (p.expires_at IS NULL OR p.expires_at > ?)
              AND (
                    p.traffic_limit_bytes IS NULL
                    OR COALESCE(t.total_bytes_sent, 0) + COALESCE(t.total_bytes_received, 0) < p.traffic_limit_bytes
                  )
            ORDER BY p.listen_port ASC
            """,
            (utc_iso_now(),),
        ).fetchall()
        return {
            "ports": [int(row["listen_port"]) for row in rows],
        }

    def write_current_config(self):
        with self.connect() as conn:
            self.write_json_file(XRAY_PANEL_PORTS_PATH, self.render_panel_ports_payload(conn))
        self.render_xray_config()
        self.xray_config_test()

    def write_json_file(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def render_xray_config(self):
        self.sync_data_plane_dynamic_routing()
        share_path = XRAY_CONFIG_PATH.parent / "client-share.txt"
        self.run_command(
            [
                sys.executable,
                "-m",
                "app.xray.render_config",
                "--env-file",
                str(XRAY_ENV_FILE_PATH),
                "--config-out",
                str(XRAY_CONFIG_PATH),
                "--client-out",
                str(XRAY_CLIENT_CONFIG_PATH),
                "--share-out",
                str(share_path),
                "--panel-ports-file",
                str(XRAY_PANEL_PORTS_PATH),
            ],
            "Xray 配置渲染失败",
        )

    def sync_data_plane_dynamic_routing(self):
        if not self.data_plane.supports_dynamic_routing_pull():
            return False
        return self.data_plane.sync_dynamic_routing_from_remote()

    def sync_data_plane_artifacts(self):
        if not self.data_plane.supports_sync():
            return []
        return self.data_plane.sync_generated_files(validate_config=True)

    def xray_config_test(self):
        if self.data_plane.supports_sync():
            self.sync_data_plane_artifacts()
            return
        self.data_plane.test_config()

    def restart_data_plane(self):
        return self.data_plane.restart()

    def data_plane_configured(self):
        return self.data_plane.is_configured()

    def data_plane_running(self):
        return self.data_plane.is_running()

    def read_xray_traffic_stats(self):
        if not self.data_plane_running():
            return {}
        try:
            completed = self.data_plane.run_statsquery(
                XRAY_STATS_QUERY_TIMEOUT,
                "inbound>>>panel-",
            )
            if completed is None:
                return {}
            payload = json.loads(completed.stdout or "{}")
        except (RuntimeError, json.JSONDecodeError):
            return {}

        counters = {}
        for item in payload.get("stat", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            parts = name.split(">>>")
            if len(parts) != 4 or parts[0] != "inbound" or parts[2] != "traffic":
                continue
            tag = parts[1]
            if not tag.startswith("panel-"):
                continue
            try:
                listen_port = int(tag.removeprefix("panel-"))
                value = int(item.get("value", 0) or 0)
            except (TypeError, ValueError):
                continue
            counter = counters.setdefault(
                listen_port,
                {
                    "bytes_sent": 0,
                    "bytes_received": 0,
                },
            )
            if parts[3] == "uplink":
                counter["bytes_received"] += value
            elif parts[3] == "downlink":
                counter["bytes_sent"] += value
        return counters

    def restart_data_plane_or_raise(self):
        if not self.data_plane.is_configured():
            raise ValidationError("数据面未配置。")
        if not self.data_plane.supports_restart():
            raise ValidationError("当前数据面未配置可用的重启方式。")
        restarted = self.data_plane.restart()
        if not restarted:
            raise ValidationError("当前数据面不可重启。")
        return self.data_plane.status_summary()

    def run_command(self, command, error_prefix, timeout=None):
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
        if completed.returncode == 0:
            return completed
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise RuntimeError(f"{error_prefix}: {detail}")

    def maintenance_loop(self):
        last_probe_at = 0.0
        last_dns_failover_at = 0.0
        while not self.stop_event.wait(MAINTENANCE_INTERVAL):
            try:
                self.sync_traffic_state()
                self.disable_auto_stopped_ports(reload_xray=True)
                now_monotonic = time.monotonic()
                if PROBE_ENABLED and now_monotonic - last_probe_at >= PROBE_INTERVAL:
                    self.run_upstream_probes()
                    last_probe_at = now_monotonic
                if (
                    self.dns_failover_manager.config.enabled
                    and now_monotonic - last_dns_failover_at >= self.dns_failover_manager.config.interval
                ):
                    self.run_dns_failover_check()
                    last_dns_failover_at = now_monotonic
            except Exception:
                continue

    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.sync_traffic_state()
