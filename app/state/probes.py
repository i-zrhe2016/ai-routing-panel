import socket
from datetime import datetime, timezone

from ..config import (
    DATAPLANE_PROBE_HOST,
    PROBE_DASHBOARD_RANGES,
    PROBE_ENABLED,
    PROBE_TEST_LISTEN_PORT,
    PROBE_TIMEOUT,
)
from ..helpers import (
    localize_time,
    utc_iso_now,
    utc_now,
)
from ..observability.logging import emit_business_event


class ProbesService:
    """Upstream reachability sampling and dashboard aggregation."""

    def __init__(self, repository=None, write_lock=None):
        self.repository = repository
        self.write_lock = write_lock

    def ensure_probe_schema(self, conn):
        conn.executescript(
            """
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
            """
        )

    def run_upstream_probes(self):
        if not PROBE_ENABLED:
            return 0

        with self.repository.connect() as conn:
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
            if not reachable:
                emit_business_event(
                    "probe.failed",
                    result="failure",
                    actor_type="system",
                    resource_type="port",
                    resource_id=row["listen_port"],
                    error_code="unreachable",
                    message=failure_reason,
                    metadata={"listen_port": row["listen_port"]},
                )

        with self.write_lock:
            with self.repository.connect() as conn:
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
        with self.repository.connect() as conn:
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

    def query_probe_overview(self, per_port_limit=48, recent_failure_limit=12):
        """Read-only probe availability summary for the console.

        Returns one bounded, oldest-first check window per configured port plus
        the newest failures, so the console can draw an availability strip and
        list what actually failed without scanning ``upstream_probe_history``
        itself.
        """
        try:
            history_limit = max(1, min(int(per_port_limit), 240))
        except (TypeError, ValueError):
            history_limit = 48
        try:
            failure_limit = max(1, min(int(recent_failure_limit), 50))
        except (TypeError, ValueError):
            failure_limit = 12

        with self.repository.connect() as conn:
            port_rows = conn.execute(
                """
                SELECT listen_port, note, enabled
                FROM ports
                ORDER BY listen_port ASC
                """
            ).fetchall()
            history_rows = conn.execute(
                """
                SELECT listen_port, is_reachable, checked_at, failure_reason
                FROM upstream_probe_history
                ORDER BY checked_at DESC, id DESC
                LIMIT ?
                """,
                (history_limit * max(len(port_rows), 1) * 4,),
            ).fetchall()

        by_port = {}
        for row in history_rows:
            bucket = by_port.setdefault(int(row["listen_port"]), [])
            if len(bucket) < history_limit:
                bucket.append(row)

        ports = []
        total_checks = 0
        unhealthy_checks = 0
        recent_failures = []
        for port_row in port_rows:
            listen_port = int(port_row["listen_port"])
            rows = list(reversed(by_port.get(listen_port, [])))
            checks = []
            healthy_count = 0
            for row in rows:
                reachable = bool(row["is_reachable"])
                healthy_count += 1 if reachable else 0
                checked_local = localize_time(row["checked_at"])
                checks.append(
                    {
                        "status": "healthy" if reachable else "unhealthy",
                        "checked_at": row["checked_at"],
                        "checked_at_display": (
                            checked_local.strftime("%Y-%m-%d %H:%M:%S") if checked_local else "暂无"
                        ),
                        "failure_reason": row["failure_reason"] or "",
                    }
                )
            count = len(checks)
            unhealthy_count_port = count - healthy_count
            total_checks += count
            unhealthy_checks += unhealthy_count_port
            latest = checks[-1] if checks else None
            ports.append(
                {
                    "listen_port": listen_port,
                    "note": port_row["note"] or "",
                    "enabled": bool(port_row["enabled"]),
                    "status": latest["status"] if latest else "unknown",
                    "status_label": (
                        ("端口可达" if latest["status"] == "healthy" else "端口不可达") if latest else "未检测"
                    ),
                    "checked_at_display": latest["checked_at_display"] if latest else "暂无",
                    "failure_reason": latest["failure_reason"] if latest else "",
                    "checks": checks,
                    "total_checks": count,
                    "healthy_count": healthy_count,
                    "unhealthy_count": unhealthy_count_port,
                    "uptime_ratio": f"{(healthy_count / count * 100):.1f}" if count else "0.0",
                }
            )
            if latest and latest["status"] == "unhealthy":
                recent_failures.append(
                    {
                        "listen_port": listen_port,
                        "note": port_row["note"] or "",
                        "checked_at": latest["checked_at"],
                        "checked_at_display": latest["checked_at_display"],
                        "failure_reason": latest["failure_reason"],
                    }
                )

        recent_failures.sort(key=lambda item: item["checked_at"], reverse=True)
        return {
            "ports": ports,
            "recent_failures": recent_failures[:failure_limit],
            "total_checks": total_checks,
            "unhealthy_checks": unhealthy_checks,
            "uptime_ratio": (
                f"{((total_checks - unhealthy_checks) / total_checks * 100):.1f}" if total_checks else "0.0"
            ),
        }
