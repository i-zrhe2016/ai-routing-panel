from datetime import datetime, timedelta, timezone

from ..config import (
    LOCAL_TZ,
    XRAY_ACCESS_LOG_PATH,
)
from ..errors import ValidationError
from ..helpers import (
    utc_iso_now,
    utc_now,
)
from ._constants import XRAY_ACCESS_LOG_LINE_RE


def _state_flag(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class TrafficService:
    """Traffic synchronization using explicit storage, node and mutation ports."""

    def __init__(
        self,
        repository=None,
        node_controller=None,
        stats_reader=None,
        renderer=None,
        write_lock=None,
    ):
        self.repository = repository
        self.node_controller = node_controller
        self.stats_reader = stats_reader if stats_reader is not None else repository
        self.renderer = renderer if renderer is not None else repository
        self.write_lock = write_lock

    def ensure_traffic_schema(self, conn):
        conn.executescript(
            """
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
            """
        )

    def sync_traffic_state(self):
        with self.write_lock:
            return self.sync_traffic_state_locked()

    def sync_traffic_state_locked(self):
        with self.repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            log_updates = self.sync_xray_access_log_in_tx(conn)
            byte_updates = self.sync_xray_traffic_stats_in_tx(conn)
            conn.commit()
            return {
                "connection_updates": log_updates,
                "byte_updates": byte_updates,
            }

    def sync_xray_access_log_in_tx(self, conn):
        current_offset = int(self.repository.get_state(conn, "xray_access_log_offset", "0"))
        recorded_inode = self.repository.get_state(conn, "xray_access_log_inode", "")
        skip_until_newline = _state_flag(
            self.repository.get_state(conn, "xray_access_log_skip_until_newline", "0")
        )
        current_inode = ""
        new_offset = 0
        lines = []

        if self.node_controller.supports_logs():
            try:
                if skip_until_newline:
                    payload = self.node_controller.read_access_log_delta(
                        recorded_inode,
                        current_offset,
                        skip_until_newline=True,
                    )
                else:
                    payload = self.node_controller.read_access_log_delta(recorded_inode, current_offset)
            except RuntimeError:
                return 0
            if not payload["exists"]:
                return 0
            current_inode = payload["inode"]
            new_offset = int(payload["offset"])
            lines = str(payload["data"]).splitlines()
            skip_until_newline = _state_flag(payload.get("skip_until_newline"))
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
            skip_until_newline = False

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

        self.repository.set_state(conn, "xray_access_log_inode", current_inode)
        self.repository.set_state(conn, "xray_access_log_offset", str(new_offset))
        self.repository.set_state(
            conn,
            "xray_access_log_skip_until_newline",
            "1" if skip_until_newline else "0",
        )
        return len(aggregates)

    def parse_xray_access_log_line(self, line):
        match = XRAY_ACCESS_LOG_LINE_RE.match(line.strip())
        if match is None:
            return None

        tag = str(match.group("tag") or "").strip()
        if tag.startswith("unified-"):
            email = str(match.group("email") or "")
            if not email.startswith("panel-user-"):
                return None
            tag = "panel-" + email.removeprefix("panel-user-")
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
        stats = self.stats_reader.read_xray_traffic_stats()
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

        return self.renderer.apply_mutation(operation)

    def query_traffic_series(self, days=14):
        """Read-only daily traffic series for the console history charts.

        Reads only ``traffic_daily`` and ``ports``; it never triggers a sync or
        a render, so an admin refresh cannot change node state. The window is
        clamped to [1, 30] days and every port is aligned to the same date list
        so the SPA can plot a dense series without client-side gap filling.
        """
        try:
            day_count = int(days)
        except (TypeError, ValueError):
            day_count = 14
        day_count = max(1, min(day_count, 30))
        end_date = datetime.now(LOCAL_TZ).date()
        start_date = end_date - timedelta(days=day_count - 1)
        dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(day_count)]

        with self.repository.connect() as conn:
            port_rows = conn.execute(
                """
                SELECT listen_port, note, enabled
                FROM ports
                ORDER BY listen_port ASC
                """
            ).fetchall()
            daily_rows = conn.execute(
                """
                SELECT listen_port, stat_date, total_connections, total_bytes_sent, total_bytes_received
                FROM traffic_daily
                WHERE stat_date >= ?
                """,
                (start_date.isoformat(),),
            ).fetchall()

        daily = {}
        for row in daily_rows:
            daily[(int(row["listen_port"]), str(row["stat_date"]))] = {
                "connections": int(row["total_connections"] or 0),
                "bytes_sent": int(row["total_bytes_sent"] or 0),
                "bytes_received": int(row["total_bytes_received"] or 0),
            }

        ports = []
        fleet_series = {
            "bytes_sent": [0] * day_count,
            "bytes_received": [0] * day_count,
            "connections": [0] * day_count,
            "total_bytes": [0] * day_count,
        }
        for port_row in port_rows:
            listen_port = int(port_row["listen_port"])
            series = {
                "bytes_sent": [],
                "bytes_received": [],
                "connections": [],
                "total_bytes": [],
            }
            totals = {"bytes_sent": 0, "bytes_received": 0, "connections": 0}
            for index, stat_date in enumerate(dates):
                point = daily.get((listen_port, stat_date), {"connections": 0, "bytes_sent": 0, "bytes_received": 0})
                total_bytes = point["bytes_sent"] + point["bytes_received"]
                series["bytes_sent"].append(point["bytes_sent"])
                series["bytes_received"].append(point["bytes_received"])
                series["connections"].append(point["connections"])
                series["total_bytes"].append(total_bytes)
                totals["bytes_sent"] += point["bytes_sent"]
                totals["bytes_received"] += point["bytes_received"]
                totals["connections"] += point["connections"]
                fleet_series["bytes_sent"][index] += point["bytes_sent"]
                fleet_series["bytes_received"][index] += point["bytes_received"]
                fleet_series["connections"][index] += point["connections"]
                fleet_series["total_bytes"][index] += total_bytes
            ports.append(
                {
                    "listen_port": listen_port,
                    "note": port_row["note"] or "",
                    "enabled": bool(port_row["enabled"]),
                    "totals": {**totals, "total_bytes": totals["bytes_sent"] + totals["bytes_received"]},
                    "today": {
                        "bytes_sent": series["bytes_sent"][-1],
                        "bytes_received": series["bytes_received"][-1],
                        "connections": series["connections"][-1],
                        "total_bytes": series["total_bytes"][-1],
                    },
                    "series": series,
                }
            )

        ports.sort(key=lambda item: item["totals"]["total_bytes"], reverse=True)
        fleet_totals = {
            "bytes_sent": sum(fleet_series["bytes_sent"]),
            "bytes_received": sum(fleet_series["bytes_received"]),
            "connections": sum(fleet_series["connections"]),
            "total_bytes": sum(fleet_series["total_bytes"]),
        }
        return {
            "days": day_count,
            "range_start": dates[0],
            "range_end": dates[-1],
            "dates": dates,
            "totals": fleet_totals,
            "series": fleet_series,
            "ports": ports,
        }
