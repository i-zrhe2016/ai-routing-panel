import json


from ..config import (
    AI_ROUTING_ENABLED,
)
from ..helpers import (
    utc_iso_now,
)
from ..xray.ai_domain_manager import ensure_ai_domain_schema



class AiRoutingService:
    def __init__(self, panel):
        self._panel = panel
    def sync_data_plane_ai_state(self):
        result = {
            "report_synced": False,
            "snapshot_synced": False,
        }
        if self._panel.data_plane.supports_ai_report_pull():
            result["report_synced"] = self._panel.data_plane.sync_ai_report_from_remote()
        if self._panel.data_plane.supports_ai_domains_snapshot_pull():
            snapshot = self._panel.data_plane.read_ai_domains_snapshot_from_remote()
            if snapshot.get("exists"):
                self._panel.replace_ai_domains_snapshot(snapshot.get("ai_domains", []))
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

        return self._panel.apply_state_update(operation)
    def ai_domain_sync_mode_label(self):
        mode = self._panel.data_plane.mode
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
        protocols = self._panel.decode_json_text_list(item.get("protocols", []))
        return {
            "domain": str(item.get("domain", "")).strip(),
            "hits": int(item.get("hits", 0) or 0),
            "classification": str(item.get("classification", "unknown") or "unknown").strip() or "unknown",
            "reason": str(item.get("reason", "") or "").strip(),
            "protocols": protocols,
            "protocols_display": ", ".join(protocols) if protocols else "暂无",
            "first_seen": str(item.get("first_seen") or "").strip() or None,
            "last_seen": str(item.get("last_seen") or "").strip() or None,
            "first_seen_display": self._panel.format_optional_display_time(item.get("first_seen")),
            "last_seen_display": self._panel.format_optional_display_time(item.get("last_seen")),
        }
    def serialize_ai_domain_snapshot_row(self, row):
        item = dict(row)
        protocols = self._panel.decode_json_text_list(item.get("last_protocols", "[]"))
        return {
            **item,
            "classification": str(item.get("classification", "ai") or "ai").strip() or "ai",
            "source_display": self._panel.ai_source_label(item.get("source")),
            "protocols": protocols,
            "protocols_display": ", ".join(protocols) if protocols else "暂无",
            "first_seen_display": self._panel.format_optional_display_time(item.get("first_seen")),
            "last_seen_display": self._panel.format_optional_display_time(item.get("last_seen")),
            "updated_at_display": self._panel.format_optional_display_time(item.get("updated_at")),
            "last_report_window_start_display": self._panel.format_optional_display_time(
                item.get("last_report_window_start")
            ),
            "last_report_window_end_display": self._panel.format_optional_display_time(
                item.get("last_report_window_end")
            ),
        }
    def read_ai_domain_report(self):
        report_path = self._panel.data_plane.config.source_ai_report_path or self._panel.data_plane_ai_report_source_path()
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
            domain_item = self._panel.serialize_ai_report_domain(raw_item)
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
            "generated_at_display": self._panel.format_optional_display_time(payload.get("generated_at")),
            "window_start": str(payload.get("window_start") or "").strip() or None,
            "window_start_display": self._panel.format_optional_display_time(payload.get("window_start")),
            "window_end": str(payload.get("window_end") or "").strip() or None,
            "window_end_display": self._panel.format_optional_display_time(payload.get("window_end")),
            "unique_domains": int(payload.get("unique_domains", len(domains)) or 0),
            "ai_domain_count": len(current_ai_domains),
            "domains": domains,
            "current_ai_domains": current_ai_domains,
            "protocols": [item for item in payload.get("protocols", []) if isinstance(item, dict)],
            "route_status": route_status_code,
            "route_status_reason": route_status_reason,
            "route_status_label": self._panel.ai_route_status_label(route_status_code, route_status_reason),
            "route_status_tone": self._panel.ai_route_status_tone(route_status_code),
            "config_changed": bool(route_status.get("config_changed")),
            "pending_domains_without_classifier": self._panel.normalize_pending_domain_count(
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
        with self._panel.connect() as conn:
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
            "updated_at_display": self._panel.format_optional_display_time(row["updated_at"]),
        }
    def query_top_ai_domains(self, limit=100):
        with self._panel.connect() as conn:
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
        return [self._panel.serialize_ai_domain_snapshot_row(row) for row in rows]
    def query_ai_domain_source_breakdown(self):
        with self._panel.connect() as conn:
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
                "source_display": self._panel.ai_source_label(row["source"]),
                "domain_count": int(row["domain_count"] or 0),
                "total_hits": int(row["total_hits"] or 0),
                "updated_at": row["updated_at"],
                "updated_at_display": self._panel.format_optional_display_time(row["updated_at"]),
            }
            for row in rows
        ]
    def ai_routing_status(self, sync_error=""):
        report = self._panel.read_ai_domain_report()
        aggregate = self._panel.query_ai_domain_aggregate()
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
            "sync_mode_label": self._panel.ai_domain_sync_mode_label(),
            "report_generated_at_display": report["generated_at_display"] if report else "暂无",
            "current_ai_domains": report["ai_domain_count"] if report else 0,
            "total_ai_domains": aggregate["total_ai_domains"],
            "sync_error": str(sync_error or "").strip(),
        }
    def query_ai_domain_overview(self, sync_error=""):
        report = self._panel.read_ai_domain_report()
        aggregate = self._panel.query_ai_domain_aggregate()
        return {
            "available": AI_ROUTING_ENABLED and bool(report or aggregate["total_ai_domains"] > 0),
            "enabled": AI_ROUTING_ENABLED,
            "sync_mode": self._panel.data_plane.mode,
            "sync_mode_label": self._panel.ai_domain_sync_mode_label(),
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
        report = self._panel.read_ai_domain_report()
        aggregate = self._panel.query_ai_domain_aggregate()
        top_ai_domains = self._panel.query_top_ai_domains()
        source_breakdown = self._panel.query_ai_domain_source_breakdown()
        return {
            "available": AI_ROUTING_ENABLED and bool(report or top_ai_domains),
            "enabled": AI_ROUTING_ENABLED,
            "sync_mode": self._panel.data_plane.mode,
            "sync_mode_label": self._panel.ai_domain_sync_mode_label(),
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
