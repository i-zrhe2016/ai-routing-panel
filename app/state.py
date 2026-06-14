import os
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone

from .config import (
    DATA_DIR,
    DB_PATH,
    DEFAULT_UPSTREAM_HOST,
    DEFAULT_UPSTREAM_PORT,
    GENERATED_STREAM_CONFIG,
    LOCAL_TZ,
    MAINTENANCE_INTERVAL,
    NGINX_CONFIG_PATH,
    NGINX_PID_PATH,
    PROBE_DASHBOARD_RANGES,
    PROBE_ENABLED,
    PROBE_INTERVAL,
    PROBE_TEST_LISTEN_PORT,
    PROBE_TIMEOUT,
    PROXY_CONNECT_TIMEOUT,
    PROXY_TIMEOUT,
    SEED_LISTEN_PORT,
    STREAM_ACCESS_LOG,
    STREAM_LISTEN_BACKLOG,
    STREAM_LISTEN_FASTOPEN,
    STREAM_LISTEN_SO_KEEPALIVE,
    STREAM_PROXY_SOCKET_KEEPALIVE,
    STREAMS_DIR,
)
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
    parse_data_size,
    parse_expiry,
    parse_note,
    parse_port,
    status_payload,
    utc_iso_now,
    utc_now,
)


class PanelState:
    def __init__(self):
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STREAMS_DIR.mkdir(parents=True, exist_ok=True)
        STREAM_ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)

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
                """
            )
            self.ensure_port_schema(conn)

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
        self.cleanup_expired_ports_in_tx(conn)
        self.ensure_port_tokens_in_tx(conn)
        self.ensure_port_credentials_in_tx(conn)

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
        self.sync_traffic_logs()
        self.disable_auto_stopped_ports(reload_nginx=False)
        self.write_current_config()
        self.start_nginx()

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

    def sync_traffic_logs(self):
        if not STREAM_ACCESS_LOG.exists():
            return 0

        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                stat = STREAM_ACCESS_LOG.stat()
                current_inode = str(stat.st_ino)
                current_offset = int(self.get_state(conn, "stream_log_offset", "0"))
                recorded_inode = self.get_state(conn, "stream_log_inode", "")

                if recorded_inode != current_inode or stat.st_size < current_offset:
                    current_offset = 0

                aggregates = {}
                with STREAM_ACCESS_LOG.open("r", encoding="utf-8", errors="ignore") as handle:
                    handle.seek(current_offset)
                    for line in handle:
                        parsed = self.parse_stream_log_line(line)
                        if parsed is None:
                            continue
                        listen_port, bytes_sent, bytes_received, stat_date, seen_at = parsed
                        item = aggregates.setdefault(
                            (listen_port, stat_date),
                            {
                                "connections": 0,
                                "bytes_sent": 0,
                                "bytes_received": 0,
                                "last_seen": seen_at,
                            },
                        )
                        item["connections"] += 1
                        item["bytes_sent"] += bytes_sent
                        item["bytes_received"] += bytes_received
                        if seen_at > item["last_seen"]:
                            item["last_seen"] = seen_at
                    new_offset = handle.tell()

                for (listen_port, stat_date), item in aggregates.items():
                    conn.execute(
                        """
                        INSERT INTO traffic_totals (
                            listen_port, total_connections, total_bytes_sent, total_bytes_received, last_seen
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(listen_port) DO UPDATE SET
                            total_connections = total_connections + excluded.total_connections,
                            total_bytes_sent = total_bytes_sent + excluded.total_bytes_sent,
                            total_bytes_received = total_bytes_received + excluded.total_bytes_received,
                            last_seen = CASE
                                WHEN traffic_totals.last_seen IS NULL OR traffic_totals.last_seen < excluded.last_seen
                                THEN excluded.last_seen
                                ELSE traffic_totals.last_seen
                            END
                        """,
                        (
                            listen_port,
                            item["connections"],
                            item["bytes_sent"],
                            item["bytes_received"],
                            item["last_seen"],
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO traffic_daily (
                            listen_port, stat_date, total_connections, total_bytes_sent, total_bytes_received
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(listen_port, stat_date) DO UPDATE SET
                            total_connections = total_connections + excluded.total_connections,
                            total_bytes_sent = total_bytes_sent + excluded.total_bytes_sent,
                            total_bytes_received = total_bytes_received + excluded.total_bytes_received
                        """,
                        (
                            listen_port,
                            stat_date,
                            item["connections"],
                            item["bytes_sent"],
                            item["bytes_received"],
                        ),
                    )

                self.set_state(conn, "stream_log_inode", current_inode)
                self.set_state(conn, "stream_log_offset", str(new_offset))
                conn.commit()
                return len(aggregates)

    def parse_stream_log_line(self, line):
        parts = line.strip().split("\t")
        if len(parts) < 4:
            return None
        try:
            seen_at = datetime.fromisoformat(parts[0]).astimezone(timezone.utc).isoformat(timespec="seconds")
            listen_port = int(parts[1])
            bytes_sent = int(parts[2])
            bytes_received = int(parts[3])
        except (ValueError, IndexError):
            return None
        stat_date = seen_at[:10]
        return listen_port, bytes_sent, bytes_received, stat_date, seen_at

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
                item["probe_status_label"] = "后端可达"
            else:
                item["probe_status"] = "unhealthy"
                item["probe_status_label"] = "后端不可达"
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
            row = conn.execute("SELECT listen_port FROM ports WHERE id = ?", (port_id,)).fetchone()
            if row is None:
                raise ValidationError("端口记录不存在。")
            self.delete_port_in_tx(conn, port_id, row["listen_port"])

        self.apply_mutation(operation)

    def disable_expired_ports(self, reload_nginx=True):
        return self.disable_auto_stopped_ports(reload_nginx=reload_nginx)

    def run_upstream_probes(self):
        if not PROBE_ENABLED:
            return 0

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT listen_port, upstream_host, upstream_port
                FROM ports
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
                    (row["upstream_host"], int(row["upstream_port"])),
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
            current_status_label = "后端可达" if filtered_rows[0]["is_reachable"] else "后端不可达"

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

    def disable_auto_stopped_ports(self, reload_nginx=True):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                changed = self.disable_auto_stopped_ports_in_tx(conn)
                if changed:
                    self.persist_and_reload(conn, reload_nginx=reload_nginx)
                else:
                    conn.commit()
                return changed

    def apply_mutation(self, operation):
        with self.write_lock:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = operation(conn)
                    self.disable_auto_stopped_ports_in_tx(conn)
                    self.persist_and_reload(conn, reload_nginx=True)
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
            WHERE expires_at IS NOT NULL AND expires_at <= ?
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

    def mark_stream_log_consumed(self, conn):
        if not STREAM_ACCESS_LOG.exists():
            return
        stat = STREAM_ACCESS_LOG.stat()
        self.set_state(conn, "stream_log_inode", str(stat.st_ino))
        self.set_state(conn, "stream_log_offset", str(stat.st_size))

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

            self.mark_stream_log_consumed(conn)
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

    def persist_and_reload(self, conn, reload_nginx):
        previous_config = GENERATED_STREAM_CONFIG.read_text(encoding="utf-8") if GENERATED_STREAM_CONFIG.exists() else None
        config_text = self.render_stream_config(conn)
        GENERATED_STREAM_CONFIG.write_text(config_text, encoding="utf-8")
        try:
            self.nginx_config_test()
            if reload_nginx and self.nginx_running():
                self.nginx_reload()
        except Exception:
            if previous_config is None:
                GENERATED_STREAM_CONFIG.unlink(missing_ok=True)
            else:
                GENERATED_STREAM_CONFIG.write_text(previous_config, encoding="utf-8")
            raise
        conn.commit()

    def render_stream_config(self, conn):
        rows = conn.execute(
            """
            SELECT
                p.listen_port,
                p.upstream_host,
                p.upstream_port
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
        blocks = [
            "# Generated by xray-routing-panel.",
            "# Do not edit this file manually.",
            "",
        ]
        for row in rows:
            listen_options = [str(row["listen_port"]), "reuseport"]
            if STREAM_LISTEN_BACKLOG > 0:
                listen_options.append(f"backlog={STREAM_LISTEN_BACKLOG}")
            if STREAM_LISTEN_FASTOPEN > 0:
                listen_options.append(f"fastopen={STREAM_LISTEN_FASTOPEN}")
            if STREAM_LISTEN_SO_KEEPALIVE:
                listen_options.append(f"so_keepalive={STREAM_LISTEN_SO_KEEPALIVE}")
            blocks.extend(
                [
                    "server {",
                    f"    listen {' '.join(listen_options)};",
                    f"    proxy_connect_timeout {PROXY_CONNECT_TIMEOUT};",
                    f"    proxy_timeout {PROXY_TIMEOUT};",
                    "    proxy_socket_keepalive on;" if STREAM_PROXY_SOCKET_KEEPALIVE else "",
                    f"    proxy_pass {row['upstream_host']}:{row['upstream_port']};",
                    "}",
                    "",
                ]
            )
        return "\n".join(line for line in blocks if line).strip() + "\n"

    def write_current_config(self):
        with self.connect() as conn:
            GENERATED_STREAM_CONFIG.write_text(self.render_stream_config(conn), encoding="utf-8")
        self.nginx_config_test()

    def nginx_config_test(self):
        self.run_command(["nginx", "-c", str(NGINX_CONFIG_PATH), "-t"], "nginx 配置校验失败")

    def start_nginx(self):
        self.run_command(["nginx", "-c", str(NGINX_CONFIG_PATH)], "nginx 启动失败")

    def nginx_reload(self):
        self.run_command(["nginx", "-c", str(NGINX_CONFIG_PATH), "-s", "reload"], "nginx 重载失败")

    def nginx_stop(self):
        if not self.nginx_running():
            return
        try:
            self.run_command(["nginx", "-c", str(NGINX_CONFIG_PATH), "-s", "quit"], "nginx 停止失败")
        except RuntimeError:
            pass

    def nginx_pid(self):
        if not NGINX_PID_PATH.exists():
            return None
        try:
            pid = int(NGINX_PID_PATH.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None

    def nginx_running(self):
        return self.nginx_pid() is not None

    def run_command(self, command, error_prefix):
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode == 0:
            return completed
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise RuntimeError(f"{error_prefix}: {detail}")

    def maintenance_loop(self):
        last_probe_at = 0.0
        while not self.stop_event.wait(MAINTENANCE_INTERVAL):
            try:
                self.sync_traffic_logs()
                self.disable_auto_stopped_ports(reload_nginx=True)
                now_monotonic = time.monotonic()
                if PROBE_ENABLED and now_monotonic - last_probe_at >= PROBE_INTERVAL:
                    self.run_upstream_probes()
                    last_probe_at = now_monotonic
            except Exception:
                continue

    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.sync_traffic_logs()
        self.nginx_stop()
