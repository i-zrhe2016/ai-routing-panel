

from ..config import (
    CONTROL_PLANE_BACKUP_XRAY_ENABLED,
)
from ..dns_failover import CloudflareApiError, resolve_public_ip
from ..errors import ValidationError
from ..helpers import (
    format_display_time,
    utc_iso_now,
)



class DnsFailoverService:
    def __init__(self, panel):
        self._panel = panel
    def dns_failover_status(self):
        config = self._panel.dns_failover_manager.config
        resolved_contents = self._panel.resolve_dns_failover_contents()
        with self._panel.connect() as conn:
            row = conn.execute("SELECT * FROM dns_failover_state WHERE singleton_id = 1").fetchone()
        item = dict(row) if row is not None else {}
        current_target = item.get("current_target") or "primary"
        record_content = item.get("current_record_content") or ""
        if current_target not in {"primary", "backup"}:
            inferred = self._panel.dns_failover_manager.current_target_from_content(
                record_content,
                primary_content=resolved_contents["primary_content"],
                backup_content=resolved_contents["backup_content"],
            )
            current_target = inferred if inferred in {"primary", "backup"} else "primary"
        return {
            "enabled": bool(config.enabled),
            "configured": bool(config.enabled and config.configured() and config.record_type_supported() and not resolved_contents["error"]),
            "config_error": self._panel.dns_failover_config_error(),
            "current_target": current_target,
            "current_target_label": self._panel.dns_failover_manager.target_label(current_target),
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
            "last_probe_status_label": self._panel.dns_failover_probe_status_label(item.get("last_probe_status")),
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
        config = self._panel.dns_failover_manager.config
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
        resolved_contents = self._panel.resolve_dns_failover_contents()
        if resolved_contents["error"]:
            return resolved_contents["error"]
        return ""
    def resolve_dns_failover_contents(self):
        config = self._panel.dns_failover_manager.config
        primary_content = str(config.primary_content or "").strip()
        backup_content = str(config.backup_content or "").strip()
        error = ""
        if not primary_content:
            try:
                primary_content = self._panel.data_plane.resolve_public_ip(timeout_seconds=max(config.timeout, 3.0))
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
            self._panel.ensure_dns_failover_schema(conn)
            row = conn.execute("SELECT * FROM dns_failover_state WHERE singleton_id = 1").fetchone()
        return row
    def run_dns_failover_check(self, force=False):
        status = self._panel.dns_failover_status()
        if not status["enabled"]:
            return status
        if not status["configured"]:
            with self._panel.write_lock:
                with self._panel.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._panel.update_dns_failover_state(conn, last_error=status["config_error"])
                    self._panel.write_dns_failover_history(conn, "probe", "skipped", detail=status["config_error"])
                    conn.commit()
            return self._panel.dns_failover_status()

        probe = self._panel.dns_failover_manager.probe_once()
        probe_status = "healthy" if probe["ok"] else "unhealthy"
        switch_result = None
        with self._panel.write_lock:
            with self._panel.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = self._panel.get_dns_failover_state_row(conn)
                current_target = str(row["current_target"] or "").strip() or "primary"
                if current_target not in {"primary", "backup"}:
                    inferred = self._panel.dns_failover_manager.current_target_from_content(
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
                self._panel.update_dns_failover_state(
                    conn,
                    last_probe_status=probe_status,
                    last_probe_checked_at=utc_iso_now(),
                    last_probe_error=probe["error"],
                    consecutive_failures=consecutive_failures,
                    consecutive_successes=consecutive_successes,
                    last_error="",
                )
                detail = probe["error"] or self._panel.dns_failover_probe_status_label(probe_status)
                self._panel.write_dns_failover_history(
                    conn,
                    "probe",
                    "ok" if probe["ok"] else "error",
                    target=current_target,
                    record_content=row["current_record_content"] or "",
                    detail=detail,
                )
                transition = self._panel.dns_failover_manager.evaluate_transition(
                    current_target,
                    consecutive_failures,
                    consecutive_successes,
                    probe["ok"],
                )
                conn.commit()

        if transition is not None:
            switch_result = self._panel.switch_dns_target(transition["target"], reason=transition["reason"], auto=True)
        return self._panel.dns_failover_status() if switch_result is None else switch_result
    def switch_dns_target(self, target, reason="manual_switch", auto=False):
        target = str(target or "").strip().lower()
        if target not in {"primary", "backup"}:
            raise ValidationError("target 仅支持 primary 或 backup。")
        status = self._panel.dns_failover_status()
        if not status["enabled"]:
            raise ValidationError("DNS 故障切换未启用。")
        if not status["configured"]:
            raise ValidationError(status["config_error"] or "DNS 故障切换配置不完整。")

        primary_content = status["primary_content"]
        backup_content = status["backup_content"]

        try:
            record = self._panel.dns_failover_manager.get_record()
        except CloudflareApiError as exc:
            with self._panel.write_lock:
                with self._panel.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._panel.update_dns_failover_state(conn, last_error=str(exc))
                    self._panel.write_dns_failover_history(
                        conn,
                        "switch",
                        "error",
                        target=target,
                        detail=str(exc),
                    )
                    conn.commit()
            raise ValidationError(str(exc)) from exc
        current_content = str(record.get("content") or "").strip()
        current_target = self._panel.dns_failover_manager.current_target_from_content(
            current_content,
            primary_content=primary_content,
            backup_content=backup_content,
        )
        if current_target == target:
            with self._panel.write_lock:
                with self._panel.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._panel.update_dns_failover_state(
                        conn,
                        current_target=target,
                        current_record_content=current_content,
                        current_record_ttl=record.get("ttl"),
                        current_record_proxied=1 if record.get("proxied") else 0,
                        last_error="",
                    )
                    self._panel.write_dns_failover_history(
                        conn,
                        "switch",
                        "noop",
                        target=target,
                        record_content=current_content,
                        detail="DNS 已在目标指向，无需重复更新。",
                    )
                    conn.commit()
            return self._panel.dns_failover_status()

        try:
            updated = self._panel.dns_failover_manager.sync_target(
                target,
                primary_content=primary_content,
                backup_content=backup_content,
            )
        except CloudflareApiError as exc:
            with self._panel.write_lock:
                with self._panel.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._panel.update_dns_failover_state(conn, last_error=str(exc))
                    self._panel.write_dns_failover_history(
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
        updated_target = self._panel.dns_failover_manager.current_target_from_content(
            updated_content,
            primary_content=primary_content,
            backup_content=backup_content,
        )
        with self._panel.write_lock:
            with self._panel.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._panel.update_dns_failover_state(
                    conn,
                    current_target=updated_target if updated_target in {"primary", "backup"} else target,
                    current_record_content=updated_content,
                    current_record_ttl=updated.get("ttl"),
                    current_record_proxied=1 if updated.get("proxied") else 0,
                    last_switch_at=utc_iso_now(),
                    last_switch_reason=reason,
                    last_error="",
                )
                self._panel.write_dns_failover_history(
                    conn,
                    "switch",
                    "ok",
                    target=target,
                    record_content=updated_content,
                    detail="自动切换完成。" if auto else "手动切换完成。",
                )
                conn.commit()
        return self._panel.dns_failover_status()
    def refresh_dns_failover_record_snapshot(self):
        status = self._panel.dns_failover_status()
        if not status["enabled"] or not status["configured"]:
            return status
        try:
            record = self._panel.dns_failover_manager.get_record()
        except CloudflareApiError as exc:
            with self._panel.write_lock:
                with self._panel.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._panel.update_dns_failover_state(conn, last_error=str(exc))
                    conn.commit()
            return self._panel.dns_failover_status()
        current_content = str(record.get("content") or "").strip()
        current_target = self._panel.dns_failover_manager.current_target_from_content(
            current_content,
            primary_content=status["primary_content"],
            backup_content=status["backup_content"],
        )
        with self._panel.write_lock:
            with self._panel.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._panel.update_dns_failover_state(
                    conn,
                    current_target=current_target if current_target in {"primary", "backup"} else "primary",
                    current_record_content=current_content,
                    current_record_ttl=record.get("ttl"),
                    current_record_proxied=1 if record.get("proxied") else 0,
                    last_error="",
                )
                conn.commit()
        return self._panel.dns_failover_status()
