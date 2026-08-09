"""Scheduled deterministic daily reporter with optional Codex explanations."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from .codex_runner import CodexAnalysisError, CodexRunner, CodexRunnerConfig
from .models import format_timestamp, parse_timestamp, stable_id, utc_now
from .prometheus import DEFAULT_METRICS, PrometheusClient, PrometheusResult
from .redaction import redact_value
from .report_contract import build_report, cleanup_reports, validate_report, write_report_atomic
from .rules import classify_report
from .storage import OpsStore


REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPORT_HOUR = 0
REPORT_MINUTE = 10


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.environ.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _emit(event: str, **fields: Any) -> None:
    payload = {"event": event, "at": format_timestamp(utc_now()), **redact_value(fields)}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _read_secret_file(path: str) -> str:
    if not path:
        return ""
    secret_path = Path(path)
    if not secret_path.is_file():
        raise ValueError(f"secret file is unavailable: {secret_path.name}")
    return secret_path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True, slots=True)
class ReporterConfig:
    db_path: str
    report_dir: str
    lock_path: str
    prometheus_url: str
    prometheus_timeout_seconds: int
    prometheus_bearer_token: str
    ai_routing_report_dir: str
    report_retention_days: int
    scheduler_interval_seconds: int
    force_rules_only: bool
    codex: CodexRunnerConfig

    @classmethod
    def from_env(cls) -> "ReporterConfig":
        token = _read_secret_file(str(os.environ.get("OPS_PROMETHEUS_BEARER_TOKEN_FILE", "")).strip())
        return cls(
            db_path=str(os.environ.get("OPS_DB_PATH", "/data/xray-ops/ops.db")),
            report_dir=str(os.environ.get("OPS_REPORT_DIR", "/data/xray-ops/reports")),
            lock_path=str(os.environ.get("OPS_REPORT_LOCK_PATH", "/data/xray-ops/reporter.lock")),
            prometheus_url=str(os.environ.get("OPS_PROMETHEUS_URL", "http://127.0.0.1:9090")).strip(),
            prometheus_timeout_seconds=_env_int("OPS_PROMETHEUS_TIMEOUT_SECONDS", 10),
            prometheus_bearer_token=token,
            ai_routing_report_dir=str(
                os.environ.get("OPS_AI_ROUTING_REPORT_DIR", "/inputs/ai-routing")
            ).strip(),
            report_retention_days=_env_int("OPS_REPORT_RETENTION_DAYS", 90),
            scheduler_interval_seconds=_env_int("OPS_REPORT_SCHEDULER_INTERVAL_SECONDS", 60, minimum=10),
            force_rules_only=_env_bool("OPS_FORCE_RULES_ONLY", False),
            codex=CodexRunnerConfig.from_env(),
        )


@dataclass(frozen=True, slots=True)
class RouteHistoryResult:
    events: list[dict[str, Any]]
    health: dict[str, Any]


def load_route_history(path: str | Path, start: datetime, end: datetime) -> RouteHistoryResult:
    directory = Path(path)
    if not directory.is_dir():
        return RouteHistoryResult(
            events=[],
            health={
                "source": "ai_routing_history",
                "configured": False,
                "success": False,
                "error_class": "route_history_not_configured",
                "files": 0,
            },
        )
    events: dict[str, dict[str, Any]] = {}
    invalid_files = 0
    inspected_files = 0
    try:
        report_paths = sorted(directory.rglob("*.json"))
    except OSError:
        return RouteHistoryResult(
            events=[],
            health={
                "source": "ai_routing_history",
                "configured": True,
                "success": False,
                "error_class": "route_history_unreadable",
                "files": 0,
            },
        )
    for report_path in report_paths:
        inspected_files += 1
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            invalid_files += 1
            continue
        if not isinstance(payload, dict):
            invalid_files += 1
            continue
        timestamp_value = payload.get("generated_at") or payload.get("window_end")
        try:
            observed_at = parse_timestamp(str(timestamp_value))
        except (TypeError, ValueError):
            invalid_files += 1
            continue
        if not start <= observed_at < end:
            continue
        route_status = payload.get("route_status") if isinstance(payload.get("route_status"), dict) else {}
        ai_target = payload.get("ai_target") if isinstance(payload.get("ai_target"), dict) else {}
        status = str(route_status.get("status", "unknown"))
        reason = str(route_status.get("reason", ""))
        probe_status = str(ai_target.get("probe_status", ""))
        fallback = status == "fallback_to_primary" or probe_status == "all_unreachable"
        event = redact_value(
            {
                "observed_at": format_timestamp(observed_at),
                "status": status,
                "reason": reason,
                "probe_status": probe_status,
                "fallback": fallback,
                "source_file": report_path.name,
            }
        )
        event_id = stable_id("route", event["observed_at"], status, reason, probe_status)
        events[event_id] = event
    return RouteHistoryResult(
        events=sorted(events.values(), key=lambda item: (item["observed_at"], item["status"])),
        health={
            "source": "ai_routing_history",
            "configured": True,
            "success": invalid_files == 0,
            "error_class": "" if invalid_files == 0 else "route_history_partial",
            "files": inspected_files,
            "invalid_files": invalid_files,
        },
    )


def _report_window(report_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_date, datetime_time.min, tzinfo=REPORT_TIMEZONE)
    return start, start + timedelta(days=1)


def due_report_date(now: datetime) -> date:
    local_now = now.astimezone(REPORT_TIMEZONE)
    due_today = datetime.combine(
        local_now.date(),
        datetime_time(REPORT_HOUR, REPORT_MINUTE),
        REPORT_TIMEZONE,
    )
    return local_now.date() - timedelta(days=1 if local_now >= due_today else 2)


def _unavailable_prometheus(error_class: str) -> PrometheusResult:
    return PrometheusResult(
        metrics={metric: [] for metric in DEFAULT_METRICS},
        sources=[
            {
                "source": f"prometheus:{metric}",
                "configured": True,
                "success": False,
                "error_class": error_class,
                "series": 0,
            }
            for metric in DEFAULT_METRICS
        ],
    )


def _collection_health(
    *,
    classification: dict[str, Any],
    prometheus: PrometheusResult,
    route_history: RouteHistoryResult,
) -> dict[str, Any]:
    node_coverage = {
        node["node_role"]: float((node.get("telemetry") or {}).get("coverage_ratio", 0))
        for node in classification["nodes"]
    }
    sources = [route_history.health, *prometheus.sources]
    return {
        "overall_coverage_ratio": round(sum(node_coverage.values()) / max(1, len(node_coverage)), 6),
        "node_coverage": node_coverage,
        "sources": sources,
        "gaps": [
            {
                "node_role": node["node_role"],
                "source": source,
                "error_class": "prometheus_scrape_gap",
            }
            for node in classification["nodes"]
            for source in (node.get("telemetry") or {}).get("missing_sources", [])
        ],
    }


@contextmanager
def report_lock(path: str | Path) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


CodexFactory = Callable[[], CodexRunner]
PrometheusFactory = Callable[[], PrometheusClient]


class ReporterService:
    def __init__(
        self,
        config: ReporterConfig,
        store: OpsStore | None = None,
        codex_factory: CodexFactory | None = None,
        prometheus_factory: PrometheusFactory | None = None,
    ):
        self.config = config
        self.store = store or OpsStore(config.db_path)
        self.codex_factory = codex_factory or (lambda: CodexRunner(config.codex))
        self.prometheus_factory = prometheus_factory or (
            lambda: PrometheusClient(
                config.prometheus_url,
                timeout_seconds=config.prometheus_timeout_seconds,
                bearer_token=config.prometheus_bearer_token,
            )
        )

    def initialize(self) -> None:
        self.store.initialize()

    def _is_complete(self, report_date: str) -> bool:
        run = self.store.latest_successful_report(report_date)
        if not run or not run.get("json_path") or not run.get("markdown_path"):
            return False
        json_path = Path(run["json_path"])
        markdown_path = Path(run["markdown_path"])
        if not json_path.is_file() or not markdown_path.is_file():
            return False
        try:
            report = json.loads(json_path.read_text(encoding="utf-8"))
            validate_report(report)
            return bool(markdown_path.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False

    def run_for_date(
        self,
        report_date: date,
        *,
        rules_only: bool = False,
        skip_if_complete: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        start_local, end_local = _report_window(report_date)
        start_utc = start_local.astimezone(ZoneInfo("UTC"))
        end_utc = end_local.astimezone(ZoneInfo("UTC"))
        started = utc_now()
        run_id = stable_id("report", report_date.isoformat(), format_timestamp(started), os.getpid(), time.monotonic_ns())

        with report_lock(self.config.lock_path):
            if skip_if_complete and self._is_complete(report_date.isoformat()):
                completed = self.store.latest_successful_report(report_date.isoformat())
                if completed and completed.get("json_path"):
                    return json.loads(Path(completed["json_path"]).read_text(encoding="utf-8"))
            self.store.begin_report_run(run_id, report_date.isoformat(), format_timestamp(started))
            try:
                try:
                    prometheus = self.prometheus_factory().collect(start_utc, end_utc)
                except Exception as exc:
                    prometheus = _unavailable_prometheus(
                        str(getattr(exc, "error_class", "prometheus_client_failed"))[:100]
                    )
                try:
                    route_history = load_route_history(self.config.ai_routing_report_dir, start_utc, end_utc)
                except Exception:
                    route_history = RouteHistoryResult(
                        events=[],
                        health={
                            "source": "ai_routing_history",
                            "configured": True,
                            "success": False,
                            "error_class": "route_history_failed",
                            "files": 0,
                        },
                    )
                classification = classify_report(
                    window_start=start_utc,
                    window_end=end_utc,
                    prometheus=prometheus.metrics,
                    route_events=route_history.events,
                )
                collection_health = _collection_health(
                    classification=classification,
                    prometheus=prometheus,
                    route_history=route_history,
                )

                model_analysis = None
                generation_mode = "rules_only"
                codex_status = "skipped"
                codex_error_class = ""
                codex_attempts = 0
                codex_input_metadata = None
                codex_usage = None
                if not (rules_only or self.config.force_rules_only):
                    frozen = {
                        "report_date": report_date.isoformat(),
                        "timezone": "Asia/Shanghai",
                        "rules_version": classification["rules_version"],
                        "rule_parameters": classification["rule_parameters"],
                        "overall_status": classification["overall_status"],
                        "nodes": classification["nodes"],
                        "incidents": classification["incidents"],
                        "evidence": classification["evidence"],
                        "collection_health": collection_health,
                    }
                    try:
                        codex_result = self.codex_factory().analyze(frozen)
                    except CodexAnalysisError as exc:
                        codex_status = "failed"
                        codex_error_class = exc.error_class
                        codex_attempts = exc.attempts
                        if exc.usage:
                            codex_usage = {
                                "status": "available",
                                "provider": "",
                                "model": "",
                                "attempts": exc.attempts,
                                "tokens": exc.usage,
                                "estimated_price": {
                                    "status": "unavailable",
                                    "currency": "USD",
                                    "amount": None,
                                    "reason": "pricing_not_configured",
                                },
                            }
                    except Exception:
                        codex_status = "failed"
                        codex_error_class = "codex_internal_error"
                        codex_attempts = 0
                    else:
                        model_analysis = codex_result.analysis
                        codex_attempts = codex_result.attempts
                        codex_input_metadata = getattr(codex_result, "input_metadata", None) or None
                        if codex_result.usage:
                            codex_usage = {
                                "status": "available",
                                "provider": codex_result.provider,
                                "model": codex_result.model,
                                "attempts": codex_result.attempts,
                                "tokens": codex_result.usage,
                                "estimated_price": {
                                    "status": "unavailable",
                                    "currency": "USD",
                                    "amount": None,
                                    "reason": "pricing_not_configured",
                                },
                            }
                        codex_status = "success"
                        generation_mode = "codex"

                generation_health = {
                    "rules": "success",
                    "codex": codex_status,
                    "codex_attempts": codex_attempts,
                    "codex_error_class": codex_error_class,
                    "codex_input": codex_input_metadata,
                    "codex_usage": codex_usage,
                    "json_schema": "valid",
                    "markdown": "success",
                    "atomic_write": "success",
                }
                report = build_report(
                    report_date=report_date.isoformat(),
                    window_start=start_local,
                    window_end=end_local,
                    classification=classification,
                    collection_health=collection_health,
                    generation_mode=generation_mode,
                    model_analysis=model_analysis,
                    generation_health=generation_health,
                )
                output = write_report_atomic(self.config.report_dir, report)
                self.store.finish_report_run(
                    run_id,
                    status="success",
                    generation_mode=generation_mode,
                    json_path=output["json_path"],
                    markdown_path=output["markdown_path"],
                    payload_digest=output["payload_digest"],
                )
            except Exception as exc:
                self.store.finish_report_run(
                    run_id,
                    status="failed",
                    error_class=type(exc).__name__,
                    detail=str(redact_value(str(exc)))[:500],
                )
                raise

            try:
                deleted = cleanup_reports(
                    self.config.report_dir,
                    self.config.report_retention_days,
                    today=datetime.now(REPORT_TIMEZONE).date(),
                )
            except Exception as exc:
                deleted = 0
                _emit(
                    "report_retention_failed",
                    report_date=report_date.isoformat(),
                    error_class=type(exc).__name__,
                )

        _emit(
            "report_completed",
            run_id=run_id,
            report_date=report_date.isoformat(),
            generation_mode=generation_mode,
            overall_status=classification["overall_status"],
            json_path=output["json_path"],
            markdown_path=output["markdown_path"],
            reports_deleted=deleted,
            duration_seconds=round((utc_now() - started).total_seconds(), 3),
        )
        return report

    def run_forever(self) -> None:
        self.initialize()
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            now = datetime.now(REPORT_TIMEZONE)
            scheduled_report_date = due_report_date(now)
            report_date_text = scheduled_report_date.isoformat()
            try:
                if not self._is_complete(report_date_text):
                    self.store.set_service_heartbeat("daily_reporter", "overdue", report_date_text)
                    self.run_for_date(scheduled_report_date, skip_if_complete=True)
                self.store.set_service_heartbeat("daily_reporter", "healthy", report_date_text)
            except Exception as exc:
                self.store.set_service_heartbeat("daily_reporter", "degraded", type(exc).__name__)
                _emit(
                    "report_scheduler_failed",
                    report_date=report_date_text,
                    error_class=type(exc).__name__,
                    detail=str(exc)[:500],
                )
            deadline = time.monotonic() + self.config.scheduler_interval_seconds
            while not stopping:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic daily Xray operations reports")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    run = subparsers.add_parser("run")
    run.add_argument("--date", required=True)
    run.add_argument("--rules-only", action="store_true")
    subparsers.add_parser("serve")
    health = subparsers.add_parser("healthcheck")
    health.add_argument("--max-age-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = ReporterConfig.from_env()
    store = OpsStore(config.db_path)
    service = ReporterService(config, store=store)
    if args.command == "init-db":
        service.initialize()
        return 0
    if args.command == "run":
        service.run_for_date(date.fromisoformat(args.date), rules_only=args.rules_only)
        return 0
    if args.command == "healthcheck":
        service.initialize()
        heartbeat = store.get_service_heartbeat("daily_reporter")
        if not heartbeat:
            return 1
        age = (utc_now() - parse_timestamp(heartbeat["heartbeat_at"])).total_seconds()
        return 0 if age <= args.max_age_seconds else 1
    service.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _emit("reporter_fatal", error_class=type(exc).__name__, detail=str(exc)[:500])
        raise
