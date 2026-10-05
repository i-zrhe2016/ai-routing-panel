import json
import os
from datetime import datetime
from pathlib import Path

from ..config import (
    AI_DOMAIN_MANAGER_CONTAINER_NAME,
    AI_DOMAIN_MANAGER_DOCKER_BIN,
    AI_DOMAIN_MANAGER_EXECUTION_MODE,
    AI_ROUTING_ENABLED,
    XRAY_CONFIG_PATH,
    XRAY_ENV_FILE_PATH,
)
from ..errors import ValidationError
from ..helpers import (
    format_optional_display_time,
    utc_iso_now,
)
from ..observability.logging import emit_business_event
from ..xray.ai_routing.candidates import build_ai_upstream_candidates
from ..xray.ai_routing.launcher import AiDomainManagerRunner
from ..xray.ai_routing.repository import (
    ensure_ai_domain_schema,
    normalize_ai_routing_manual_mode,
    port_scope_policy,
    scope_policy_signature,
)
from ..xray.envfile import load_env_file, read_env_or_file
from ..xray.operation_lock import LockBusyError, exclusive_file_lock


def _ai_candidate_identity(candidate):
    if not isinstance(candidate, dict):
        return None
    host = str(candidate.get("upstream_host", "")).strip().lower()
    try:
        port = int(candidate.get("upstream_port"))
    except (TypeError, ValueError):
        return None
    candidate_type = str(candidate.get("candidate_type", "template")).strip() or "template"
    if not host or port <= 0:
        return None
    return candidate_type, host, port


def _report_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        return parsed.isoformat() if parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


class AiRoutingService:
    """AI route state and report orchestration with explicit collaborators."""

    def __init__(self, repository=None, node_controller=None, manager_runner=None):
        self.repository = repository
        self.node_controller = node_controller
        self.manager_runner = manager_runner or AiDomainManagerRunner(
            execution_mode=AI_DOMAIN_MANAGER_EXECUTION_MODE,
            container_name=AI_DOMAIN_MANAGER_CONTAINER_NAME,
            docker_bin=AI_DOMAIN_MANAGER_DOCKER_BIN,
        )

    def ensure_ai_schema(self, conn):
        ensure_ai_domain_schema(conn)

    def sync_data_plane_ai_state(self):
        before = self.read_ai_domain_report()
        result = {
            "report_synced": False,
            "snapshot_synced": False,
        }
        try:
            if self.node_controller.supports_ai_report_pull():
                result["report_synced"] = self.node_controller.sync_ai_report_from_remote()
            if self.node_controller.supports_ai_domains_snapshot_pull():
                snapshot = self.node_controller.read_ai_domains_snapshot_from_remote()
                if snapshot.get("exists"):
                    self.replace_ai_domains_snapshot(snapshot.get("ai_domains", []))
                    result["snapshot_synced"] = True
        except Exception as exc:
            emit_business_event(
                "ai_routing.changed",
                result="failure",
                actor_type="system",
                error_code="sync_failed",
                exc=exc,
            )
            raise
        after = self.read_ai_domain_report()
        before_signature = (before or {}).get("generated_at"), (before or {}).get("route_status")
        after_signature = (after or {}).get("generated_at"), (after or {}).get("route_status")
        if (result["report_synced"] or result["snapshot_synced"]) and before_signature != after_signature:
            emit_business_event(
                "ai_routing.changed",
                actor_type="system",
                metadata={"route_status": (after or {}).get("route_status", "unknown")},
            )
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

        return self.repository.apply_state_update(operation)

    def ai_domain_sync_mode_label(self):
        mode = self.node_controller.mode
        if mode == "ssh":
            return "远端镜像"
        if mode in {"local", "docker"}:
            return "本地运行"
        return "本地缓存"

    def ai_routing_manual_state(self):
        report = self.read_ai_domain_report()
        report_candidates = []
        if isinstance(report, dict):
            target = report.get("ai_target")
            if isinstance(target, dict):
                report_candidates = [item for item in target.get("candidates", []) if isinstance(item, dict)]
        configured_candidates = self._configured_ai_candidates()
        configuration_known = configured_candidates is not None
        candidates = configured_candidates if configuration_known else report_candidates
        report_selected_index = None
        if isinstance(report, dict):
            target = report.get("ai_target")
            if isinstance(target, dict):
                try:
                    report_selected_index = int(target.get("selected_index"))
                except (TypeError, ValueError):
                    report_selected_index = None
        report_candidate_by_identity = {}
        for report_candidate in report_candidates:
            identity = _ai_candidate_identity(report_candidate)
            if identity is not None:
                report_candidate_by_identity[identity] = report_candidate
        with self.repository.connect() as conn:
            mode = str(self.repository.get_state(conn, "ai_routing_manual_mode", "auto") or "auto").strip().lower()
            updated_at = str(self.repository.get_state(conn, "ai_routing_manual_updated_at", "") or "").strip()
        if mode not in {"auto", "primary", "backup", "forced_fallback"}:
            mode = "auto"
        database_path = getattr(self.repository, "path", None)
        if configuration_known and database_path is not None:
            normalized_mode = normalize_ai_routing_manual_mode(database_path, len(candidates))
            if normalized_mode != mode:
                mode = normalized_mode
                updated_at = utc_iso_now()
        selected_index = {"primary": 0, "backup": 1}.get(mode)
        if selected_index is None and report_selected_index is not None:
            if configuration_known:
                try:
                    report_selected_candidate = report_candidates[report_selected_index]
                except (IndexError, TypeError):
                    report_selected_candidate = None
                selected_identity = _ai_candidate_identity(report_selected_candidate)
                selected_index = next(
                    (
                        index
                        for index, candidate in enumerate(candidates)
                        if _ai_candidate_identity(candidate) == selected_identity
                    ),
                    None,
                )
            else:
                selected_index = report_selected_index
        probe_fields = (
            "is_reachable",
            "failure_reason",
            "checked_at",
            "probe_method",
            "probe_management_error",
        )
        for index, candidate in enumerate(candidates):
            report_candidate = report_candidate_by_identity.get(_ai_candidate_identity(candidate))
            if report_candidate is not None:
                for field in probe_fields:
                    if field in report_candidate:
                        candidate[field] = report_candidate[field]
            candidate["index"] = index
            candidate["number"] = index + 1
            candidate["selected"] = selected_index == index
            candidate["label"] = (
                "主 AI 节点" if index == 0 else ("备用 AI 节点" if index == 1 else f"AI 节点 {index + 1}")
            )
        return {
            "mode": mode,
            "mode_label": {
                "auto": "自动探测",
                "primary": "人工指定主 AI",
                "backup": "人工指定备用 AI",
                "forced_fallback": "人工强制回退",
            }[mode],
            "updated_at": updated_at,
            "updated_at_display": format_optional_display_time(updated_at) if updated_at else "暂无",
            "candidates": candidates,
            "candidate_count": len(candidates),
        }

    def _configured_ai_candidates(self):
        try:
            values = load_env_file(XRAY_ENV_FILE_PATH)
            # Docker-mode manager runs have their own environment and shared
            # env file; accepting panel-only overrides there would make the
            # displayed candidate list differ from the applied configuration.
            use_process_env = AI_DOMAIN_MANAGER_EXECUTION_MODE == "local"

            def configured_value(name, default=""):
                if use_process_env:
                    return read_env_or_file(name, default, values)
                return str(values.get(name, default) or default).strip()

            upstream_values = {
                name: configured_value(name)
                for name in (
                    "AI_UPSTREAM_HOST",
                    "AI_UPSTREAM_PORT",
                    "AI_UPSTREAMS",
                    "AI_UPSTREAM_FALLBACKS",
                    "AI_UPSTREAM_FALLBACK_URL",
                )
            }
            if not any(upstream_values.values()):
                return []
            candidates = build_ai_upstream_candidates(
                upstream_values["AI_UPSTREAM_HOST"] or "upstream.example.com",
                int(upstream_values["AI_UPSTREAM_PORT"] or "27166"),
                upstreams_raw=upstream_values["AI_UPSTREAMS"],
                fallbacks_raw=upstream_values["AI_UPSTREAM_FALLBACKS"],
                fallback_share_url=upstream_values["AI_UPSTREAM_FALLBACK_URL"],
                promote_fallback=configured_value(
                    "AI_UPSTREAM_FALLBACK_AS_PRIMARY",
                    "0",
                ).lower()
                not in {"0", "false", "no", "off", ""},
            )
        except (OSError, ValueError, TypeError):
            return None
        return [
            {
                "upstream_host": item.get("upstream_host", ""),
                "upstream_port": int(item.get("upstream_port", 0) or 0),
                "candidate_type": item.get("candidate_type", "template"),
                "candidate_label": item.get("candidate_label", ""),
                "is_reachable": None,
                "selected": False,
            }
            for item in candidates
        ]

    def _trigger_ai_domain_manager(self, manual_mode=None):
        return self.manager_runner.run(manual_mode=manual_mode)

    def ai_routing_traffic_scope(self):
        with self.repository.connect() as conn:
            scope = self.repository.get_state(conn, "ai_routing_traffic_scope", "classified")
        return "all" if scope == "all" else "classified"

    def set_ai_routing_traffic_scope(self, scope):
        scope = str(scope or "").strip().lower()
        if scope not in {"classified", "all"}:
            raise ValidationError("AI 流量范围仅支持 classified 或 all。")
        if not AI_ROUTING_ENABLED or not self.node_controller.is_configured():
            raise ValidationError("AI 路由或数据面未配置，无法应用流量范围。")
        if scope == "all" and not self.ai_routing_manual_state()["candidates"]:
            raise ValidationError("当前未配置可用 AI 节点，无法转发全部流量。")
        try:
            with exclusive_file_lock(self._manual_mode_lock_path()):
                previous = self.ai_routing_traffic_scope()
                self.manager_runner.run(traffic_scope=scope)
                def operation(conn):
                    self.repository.set_state(conn, "ai_routing_traffic_scope", scope)
                    self.repository.set_state(conn, "ai_routing_scope_updated_at", utc_iso_now())
                try:
                    self.repository.apply_state_update(operation)
                except Exception:
                    if previous != scope:
                        try:
                            self.manager_runner.run(traffic_scope=previous)
                        except Exception as rollback_exc:  # noqa: BLE001 - preserve state-commit failure
                            emit_business_event("ai_routing.scope_switched", result="failure", actor_type="system",
                                                error_code="state_commit_rollback_failed", message=str(rollback_exc))
                    raise
        except LockBusyError as exc:
            raise RuntimeError("AI 路由正在应用配置，请稍后重试。") from exc
        emit_business_event("ai_routing.scope_switched", actor_type="admin", resource_type="ai_routing",
                            metadata={"traffic_scope": scope})
        return self.ai_routing_status()

    def ai_routing_port_policy(self):
        with self.repository.connect() as conn:
            return port_scope_policy(conn, self.ai_routing_traffic_scope())

    def set_ai_routing_port_scope(self, port_id, scope):
        scope = str(scope or '').strip().lower()
        if type(port_id) is not int or port_id <= 0 or scope not in {'all', 'classified', 'inherit'}:
            raise ValidationError('端口转发范围仅支持 all、classified 或 inherit。')
        if not AI_ROUTING_ENABLED or not self.node_controller.is_configured():
            raise ValidationError('AI 路由或数据面未配置，无法应用流量范围。')
        try:
            with exclusive_file_lock(self._manual_mode_lock_path()):
                policy = self.ai_routing_port_policy()
                previous = next((row for row in policy if row['id'] == port_id), None)
                if previous is None:
                    raise ValidationError('端口账号不存在。')
                effective = self.ai_routing_traffic_scope() if scope == 'inherit' else scope
                if effective == 'all' and not self.ai_routing_manual_state()['candidates']:
                    raise ValidationError('当前未配置可用 AI 节点，无法转发全部流量。')
                self.manager_runner.run(port_id=port_id, port_traffic_scope=scope)
                def operation(conn):
                    changed = conn.execute('UPDATE ports SET ai_traffic_scope=? WHERE id=?',
                                           (None if scope == 'inherit' else scope, port_id)).rowcount
                    if changed != 1:
                        raise ValidationError('端口账号已变更，请刷新后重试。')
                try:
                    self.repository.apply_state_update(operation)
                except Exception:
                    try:
                        self.manager_runner.run(port_id=port_id, port_traffic_scope=(
                            'inherit' if previous['inherited'] else previous['traffic_scope']))
                    except Exception as rollback_exc:  # noqa: BLE001 - retain original commit failure
                        emit_business_event('ai_routing.scope_switched', result='failure', actor_type='system',
                                            error_code='state_commit_rollback_failed', message=str(rollback_exc))
                    raise
        except LockBusyError as exc:
            raise RuntimeError('AI 路由正在应用配置，请稍后重试。') from exc
        emit_business_event('ai_routing.scope_switched', actor_type='admin', resource_type='port', resource_id=str(port_id))
        return self.ai_routing_status()

    def _manual_mode_lock_path(self):
        configured = os.environ.get("AI_DOMAIN_MANAGER_MANUAL_LOCK_PATH", "").strip()
        if configured:
            return configured
        return XRAY_CONFIG_PATH.with_name(".ai-domain-manager-manual.lock")

    def set_ai_routing_manual_mode(self, mode):
        mode = str(mode or "").strip().lower()
        if mode not in {"auto", "primary", "backup", "forced_fallback"}:
            raise ValidationError("AI 路由模式仅支持 auto、primary、backup 或 forced_fallback。")
        if not AI_ROUTING_ENABLED:
            raise ValidationError("AI 路由未启用。")

        if mode in {"primary", "backup"}:
            candidate_state = self.ai_routing_manual_state()
            candidate_count = len(candidate_state["candidates"])
            if candidate_count == 0:
                raise ValidationError("当前未配置可用 AI 节点，无法固定 AI 节点。")
            if mode == "backup" and candidate_count < 2:
                raise ValidationError("当前只配置了一个 AI 节点，无法固定备用节点。")

        updated_at = utc_iso_now()

        def operation(conn):
            self.repository.set_state(conn, "ai_routing_manual_mode", mode)
            self.repository.set_state(conn, "ai_routing_manual_updated_at", updated_at)

        if mode == "forced_fallback" and not self.node_controller.is_configured():
            raise ValidationError("数据面未配置，无法应用 AI 回退。")
        try:
            # Hold a second shared gate across the child manager and the final
            # panel-state commit.  Scheduled manager runs take this gate before
            # their apply lock, preventing an old-mode run from racing the
            # short interval between node apply and database commit.
            with exclusive_file_lock(self._manual_mode_lock_path()):
                previous_mode = self.ai_routing_manual_state()["mode"]
                # Keep the persisted mode unchanged while the manager applies
                # the requested mode explicitly.  This prevents a concurrent
                # resident manager from observing a half-applied mode and
                # makes a failed request leave both database and node on the
                # old route.
                self._trigger_ai_domain_manager(mode)
                try:
                    self.repository.apply_state_update(operation)
                except Exception:
                    # The node is already on the requested route.  If the
                    # final panel state commit fails, make a best-effort
                    # compensating apply so the database and running node do
                    # not silently diverge.
                    if previous_mode != mode:
                        try:
                            self._trigger_ai_domain_manager(previous_mode)
                        except Exception as rollback_exc:  # noqa: BLE001 - preserve the original state-commit error
                            emit_business_event(
                                "ai_routing.manual_switched",
                                result="failure",
                                actor_type="system",
                                error_code="state_commit_rollback_failed",
                                message=str(rollback_exc),
                            )
                    raise
        except LockBusyError as exc:
            raise RuntimeError("AI 路由正在应用配置，请稍后重试。") from exc
        emit_business_event(
            "ai_routing.manual_switched",
            actor_type="admin",
            resource_type="ai_routing",
            metadata={"mode": mode},
        )
        return self.ai_routing_manual_state()

    def ai_route_status_label(self, status, reason=""):
        status_text = str(status or "").strip().lower()
        if status_text == "applied":
            return "已应用 AI 路由"
        if status_text == "fallback_to_primary":
            return "AI 节点不可达，已回退主链路"
        if status_text == "probe_error":
            return "AI 节点探测异常，保留此前路由"
        if status_text == "manual_fallback":
            return "人工强制回退到主链路"
        if status_text == "manual_target_unreachable":
            return "人工指定 AI 不可达，已停用动态路由"
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
        if status_text in {"applied", "manual_selected"}:
            return "ok"
        if status_text in {
            "fallback_to_primary",
            "probe_error",
            "manual_fallback",
            "manual_target_unreachable",
            "idle",
            "disabled",
        }:
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
        raw_route = item.get("traffic_route")
        raw_route = raw_route if isinstance(raw_route, dict) else {}
        outbound = str(raw_route.get("outbound_tag", "unknown"))
        status = str(raw_route.get("status", "unknown"))
        mode = "per_port" if outbound == "per_port" else "ai" if outbound == "ai_proxy" else (
            "fallback" if outbound == "direct" and status in {"fallback_to_primary", "manual_fallback", "manual_target_unreachable", "probe_error"}
            else "direct" if outbound == "direct" else "unknown"
        )
        return {
            "domain": str(item.get("domain", "")).strip(),
            "hits": int(item.get("hits", 0) or 0),
            "classification": str(item.get("classification", "unknown") or "unknown").strip() or "unknown",
            "reason": str(item.get("reason", "") or "").strip(),
            "source": str(item.get("source", "") or "").strip(),
            "traffic_route": {"outbound_tag": outbound, "mode": mode, "reason": str(raw_route.get("reason", "") or "")},
            "protocols": protocols,
            "protocols_display": ", ".join(protocols) if protocols else "暂无",
            "first_seen": str(item.get("first_seen") or "").strip() or None,
            "last_seen": str(item.get("last_seen") or "").strip() or None,
            "first_seen_display": format_optional_display_time(item.get("first_seen")),
            "last_seen_display": format_optional_display_time(item.get("last_seen")),
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
            "first_seen_display": format_optional_display_time(item.get("first_seen")),
            "last_seen_display": format_optional_display_time(item.get("last_seen")),
            "updated_at_display": format_optional_display_time(item.get("updated_at")),
            "last_report_window_start_display": format_optional_display_time(
                item.get("last_report_window_start")
            ),
            "last_report_window_end_display": format_optional_display_time(
                item.get("last_report_window_end")
            ),
        }

    def read_ai_domain_report(self):
        report_path = self.node_controller.config.source_ai_report_path or (
            XRAY_ENV_FILE_PATH.parent / "reports" / "hourly-domains" / "latest.json"
        )
        report_path = Path(report_path)
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

        generated_at = _report_timestamp(payload.get("generated_at"))
        config_apply_status = route_status.get("config_apply_status")
        if not isinstance(config_apply_status, str) or config_apply_status not in {
            "direct", "unchanged", "delegated", "unmanaged", "not_needed"
        }:
            config_apply_status = "unknown"

        return {
            "generated_at": generated_at,
            "routing_checked_at": _report_timestamp(payload.get("routing_checked_at")) or generated_at,
            "config_apply_status": config_apply_status,
            "traffic_scope": route_status.get("traffic_scope", "classified"),
            "requested_port_scopes": route_status.get("requested_port_scopes", []),
            "applied_port_scopes": route_status.get("applied_port_scopes", []),
            "applied_traffic_scope": route_status.get("applied_traffic_scope", "classified" if route_status_code == "applied" else "direct"),
            "route_preserved": route_status.get("route_preserved") is True,
            "generated_at_display": format_optional_display_time(generated_at),
            "window_start": str(payload.get("window_start") or "").strip() or None,
            "window_start_display": format_optional_display_time(payload.get("window_start")),
            "window_end": str(payload.get("window_end") or "").strip() or None,
            "window_end_display": format_optional_display_time(payload.get("window_end")),
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
            "config_retried": bool(route_status.get("config_retried")),
            "health_interval_seconds": route_status.get("health_interval_seconds"),
            "classification_interval_seconds": route_status.get("classification_interval_seconds"),
            "known_ai_domains": self.normalize_pending_domain_count(route_status.get("known_ai_domains", 0)),
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
        with self.repository.connect() as conn:
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
            "updated_at_display": format_optional_display_time(row["updated_at"]),
        }

    def query_top_ai_domains(self, limit=100):
        with self.repository.connect() as conn:
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
        with self.repository.connect() as conn:
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
                "updated_at_display": format_optional_display_time(row["updated_at"]),
            }
            for row in rows
        ]

    def ai_routing_status(self, sync_error=""):
        report = self.read_ai_domain_report()
        aggregate = self.query_ai_domain_aggregate()
        manual = self.ai_routing_manual_state()
        configured = AI_ROUTING_ENABLED
        if manual["mode"] == "forced_fallback" and configured:
            status_code = "manual_fallback"
            status_label = "人工强制回退到主链路"
            tone = "warn"
        elif not configured:
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
        probe_stamps = [stamp for candidate in manual["candidates"] if (stamp := _report_timestamp(candidate.get("checked_at")))]
        latest_probe = max(probe_stamps, key=lambda stamp: datetime.fromisoformat(stamp).timestamp(), default=None)
        cache = self.query_classification_cache_summary()
        scope = self.ai_routing_traffic_scope()
        report_scope = report.get("traffic_scope", "classified") if report else None
        applied_scope = report.get("applied_traffic_scope", "classified") if report else "unknown"
        scope_confirmed = bool(report and report["generated_at"] and status_code == report["route_status"] and report["config_apply_status"] in {"direct", "unchanged"} and not sync_error)
        port_policy = self.ai_routing_port_policy()
        signature = scope_policy_signature(port_policy)
        policy_confirmed = bool(scope_confirmed and report_scope == scope and report.get('requested_port_scopes', []) == signature)
        applied_policy = report.get('applied_port_scopes', []) if report else []
        port_rows = []
        for row in port_policy:
            match = next((item for item in applied_policy if item.get('id') == row['id'] and item.get('listen_port') == row['listen_port']), None)
            applied_port_scope = (applied_scope if applied_scope in {'direct', 'unknown'} else match.get('traffic_scope', 'unknown') if match else 'unknown') if scope_confirmed else 'unknown'
            confirmed = policy_confirmed and (applied_port_scope == row['traffic_scope'] or applied_port_scope == 'direct')
            port_rows.append({**row, 'applied_traffic_scope': applied_port_scope,
                              'scope_apply_state': 'inactive' if not row['active'] else 'confirmed' if confirmed else 'pending'})
        if not scope_confirmed:
            applied_scope = "unknown"
        return {
            "configured": configured,
            "port_scopes": port_rows,
            "traffic_scope": scope,
            "reported_traffic_scope": report_scope,
            "applied_traffic_scope": applied_scope,
            "scope_apply_state": "confirmed" if scope_confirmed and report_scope == scope else "pending",
            "route_preserved": report.get("route_preserved", False) if report else False,
            "status": status_code,
            "status_label": status_label,
            "status_tone": tone,
            "route_status": report["route_status"] if report else "unknown",
            "route_status_reason": report["route_status_reason"] if report else "",
            "sync_mode_label": self.ai_domain_sync_mode_label(),
            "report_generated_at": report["generated_at"] if report else None,
            "config_apply_status": (
                report["config_apply_status"] if report and status_code == report["route_status"] else "unknown"
            ),
            "report_generated_at_display": report["generated_at_display"] if report else "暂无",
            "routing_checked_at_display": format_optional_display_time(report["routing_checked_at"]) if report else "暂无",
            "current_ai_domains": report["ai_domain_count"] if report else 0,
            "total_ai_domains": aggregate["total_ai_domains"],
            "manual_mode": manual["mode"],
            "manual_mode_label": manual["mode_label"],
            "manual_updated_at": manual["updated_at"],
            "manual_updated_at_display": manual["updated_at_display"],
            "ai_candidates": manual["candidates"],
            "ai_candidate_count": manual["candidate_count"],
            "config_changed": report["config_changed"] if report else False,
            "config_retried": report["config_retried"] if report else False,
            "probe_method": (report.get("ai_target") or {}).get("probe_method", "") if report else "",
            "last_probe_at_display": format_optional_display_time(latest_probe),
            "health_interval_seconds": report.get("health_interval_seconds") if report else None,
            "classification_interval_seconds": report.get("classification_interval_seconds") if report else None,
            "window_start_display": report["window_start_display"] if report else "暂无",
            "window_end_display": report["window_end_display"] if report else "暂无",
            "pending_domains_without_classifier": report["pending_domains_without_classifier"] if report else 0,
            "cached_ai_domains_count": max(report["known_ai_domains"] if report else 0, cache["ai_domains"]),
            "classification_cache": cache,
            "recent_domains": sorted(report["domains"], key=lambda domain: domain["hits"], reverse=True)[:20] if report else [],
            "sync_error": str(sync_error or "").strip(),
        }

    def query_classification_cache_summary(self):
        with self.repository.connect() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_domain_classifications'").fetchone()
            if exists:
                rows = conn.execute("SELECT classification, COUNT(*) AS count FROM ai_domain_classifications GROUP BY classification").fetchall()
                counts = {row["classification"]: int(row["count"]) for row in rows}
                return {"status": "available", "total_domains": sum(counts.values()), "ai_domains": counts.get("ai", 0), "non_ai_domains": counts.get("not_ai", 0)}
            count = conn.execute("SELECT COUNT(*) FROM ai_domains").fetchone()[0]
            return {"status": "legacy", "total_domains": count, "ai_domains": count, "non_ai_domains": 0}

    def query_ai_domain_overview(self, sync_error=""):
        report = self.read_ai_domain_report()
        aggregate = self.query_ai_domain_aggregate()
        return {
            "available": AI_ROUTING_ENABLED and bool(report or aggregate["total_ai_domains"] > 0),
            "enabled": AI_ROUTING_ENABLED,
            "sync_mode": self.node_controller.mode,
            "sync_mode_label": self.ai_domain_sync_mode_label(),
            "sync_error": str(sync_error or "").strip(),
            "report_available": report is not None,
            "current_ai_domains": report["ai_domain_count"] if report else 0,
            "unique_domains": report["unique_domains"] if report else 0,
            "report_generated_at_display": (report["generated_at_display"] if report else "暂无"),
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
            "sync_mode": self.node_controller.mode,
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
                    "config_retried": False,
                    "pending_domains_without_classifier": 0,
                    "ai_target": None,
                    "panel_target": None,
                }
            ),
            "aggregate": aggregate,
            "top_ai_domains": top_ai_domains,
            "source_breakdown": source_breakdown,
        }
