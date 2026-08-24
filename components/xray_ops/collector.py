"""Control-plane-only SSH evidence collector service."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from .models import (
    AI_DATA_PLANE,
    NORMAL_DATA_PLANE,
    CollectionBatch,
    LogCursor,
    LogEvent,
    NodeSample,
    SourceResult,
    format_timestamp,
    parse_timestamp,
    stable_id,
    utc_now,
)
from .parsing import parse_log_bytes
from .redaction import redact_value
from .remote import RemoteCommandError, SshExecutor
from .storage import OpsStore


DEFAULT_NORMAL_TARGET = "root@100.65.108.93"
DEFAULT_AI_TARGET = ""
DEFAULT_SSH_KEY_PATH = "/run/secrets/fleet_ssh_key"
DEFAULT_NORMAL_KNOWN_HOSTS = "/root/.ssh/known_hosts"
DEFAULT_AI_KNOWN_HOSTS = "/root/.ssh/known_hosts_ai"
DEFAULT_SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "PreferredAuthentications=publickey",
    "-o",
    "PasswordAuthentication=no",
    "-o",
    "KbdInteractiveAuthentication=no",
    "-o",
    "ChallengeResponseAuthentication=no",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "ConnectTimeout=8",
    "-o",
    "ServerAliveInterval=5",
    "-o",
    "ServerAliveCountMax=1",
)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.environ.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _split_options(value: str) -> tuple[str, ...]:
    return tuple(shlex.split(str(value or "")))


def _validate_ssh_options(options: tuple[str, ...]) -> None:
    forced_keys = {
        "batchmode",
        "challengeresponseauthentication",
        "identityfile",
        "identitiesonly",
        "kbdinteractiveauthentication",
        "passwordauthentication",
        "preferredauthentications",
        "stricthostkeychecking",
        "userknownhostsfile",
    }
    index = 0
    while index < len(options):
        token = str(options[index])
        if token == "-i":
            raise ValueError("ops collector owns the SSH identity file")
        if token.startswith("-i") and len(token) > 2:
            raise ValueError("ops collector owns the SSH identity file")
        if token != "-o":
            index += 1
            continue
        if index + 1 >= len(options):
            raise ValueError("ops collector received an incomplete SSH option")
        key = str(options[index + 1]).split("=", 1)[0].strip().lower()
        if key in forced_keys:
            raise ValueError(f"ops collector owns SSH option: {key}")
        index += 2


def _emit(event: str, **fields: Any) -> None:
    payload = {"event": event, "at": format_timestamp(utc_now()), **redact_value(fields)}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


@dataclass(frozen=True, slots=True)
class LogStreamConfig:
    stream: str
    path: str
    source_kind: str = "file"
    docker_json: bool = False
    dynamic_container_path: bool = False


@dataclass(frozen=True, slots=True)
class NodeConfig:
    role: str
    target: str
    ssh_options: tuple[str, ...]
    service_kind: str
    service_name: str
    docker_bin: str
    streams: tuple[LogStreamConfig, ...]


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    db_path: str
    interval_seconds: int
    command_timeout_seconds: int
    max_bytes_per_stream: int
    raw_retention_days: int
    rollup_retention_days: int
    nodes: tuple[NodeConfig, ...]
    ssh_bin: str = "ssh"

    @classmethod
    def from_env(cls) -> "CollectorConfig":
        key_path = str(os.environ.get("OPS_SSH_KEY_PATH", DEFAULT_SSH_KEY_PATH)).strip() or DEFAULT_SSH_KEY_PATH
        key_options = ("-i", key_path)
        global_options = _split_options(os.environ.get("OPS_SSH_OPTIONS", ""))
        normal_custom_options = (
            *global_options,
            *_split_options(os.environ.get("OPS_NORMAL_NODE_SSH_OPTIONS", os.environ.get("DATAPLANE_SSH_OPTIONS", ""))),
        )
        ai_custom_options = (
            *global_options,
            *_split_options(os.environ.get("OPS_AI_NODE_SSH_OPTIONS", os.environ.get("AI_NODE_SSH_OPTIONS", ""))),
        )
        _validate_ssh_options(tuple(normal_custom_options))
        _validate_ssh_options(tuple(ai_custom_options))

        normal_options = (
            *normal_custom_options,
            *key_options,
            *DEFAULT_SSH_OPTIONS,
            "-o",
            f"UserKnownHostsFile={os.environ.get('OPS_NORMAL_KNOWN_HOSTS', DEFAULT_NORMAL_KNOWN_HOSTS).strip() or DEFAULT_NORMAL_KNOWN_HOSTS}",
        )
        ai_options = (
            *ai_custom_options,
            *key_options,
            *DEFAULT_SSH_OPTIONS,
            "-o",
            f"UserKnownHostsFile={os.environ.get('OPS_AI_KNOWN_HOSTS', DEFAULT_AI_KNOWN_HOSTS).strip() or DEFAULT_AI_KNOWN_HOSTS}",
        )

        normal_access = str(
            os.environ.get(
                "OPS_NORMAL_ACCESS_LOG_PATH",
                os.environ.get("DATAPLANE_ACCESS_LOG_PATH", "/var/log/xray/access.log"),
            )
        ).strip()
        normal_error = str(os.environ.get("OPS_NORMAL_ERROR_LOG_PATH", "/var/log/xray/error.log")).strip()
        normal_streams = tuple(
            stream
            for stream in (
                LogStreamConfig("access", normal_access) if normal_access else None,
                LogStreamConfig("error", normal_error) if normal_error else None,
            )
            if stream is not None
        )
        ai_streams = (
            LogStreamConfig(
                stream="container",
                path="",
                source_kind="docker_json",
                docker_json=True,
                dynamic_container_path=True,
            ),
        )
        nodes = (
            NodeConfig(
                role=NORMAL_DATA_PLANE,
                target=str(
                    os.environ.get(
                        "OPS_NORMAL_NODE_SSH_TARGET",
                        os.environ.get("DATAPLANE_SSH_TARGET", DEFAULT_NORMAL_TARGET),
                    )
                ).strip(),
                ssh_options=tuple(normal_options),
                service_kind=str(os.environ.get("OPS_NORMAL_SERVICE_KIND", "docker")).strip().lower(),
                service_name=str(
                    os.environ.get(
                        "OPS_NORMAL_SERVICE_NAME",
                        os.environ.get("DATAPLANE_CONTAINER_NAME", "xray-reality-local"),
                    )
                ).strip(),
                docker_bin=str(os.environ.get("OPS_NORMAL_DOCKER_BIN", "docker")).strip(),
                streams=normal_streams,
            ),
            NodeConfig(
                role=AI_DATA_PLANE,
                target=str(
                    os.environ.get(
                        "OPS_AI_NODE_SSH_TARGET",
                        os.environ.get("AI_NODE_SSH_TARGET", DEFAULT_AI_TARGET),
                    )
                ).strip(),
                ssh_options=tuple(ai_options),
                service_kind=str(os.environ.get("OPS_AI_SERVICE_KIND", "docker")).strip().lower(),
                service_name=str(
                    os.environ.get(
                        "OPS_AI_SERVICE_NAME",
                        os.environ.get("AI_NODE_CONTAINER_NAME", "xray-ai-node"),
                    )
                ).strip(),
                docker_bin=str(os.environ.get("OPS_AI_DOCKER_BIN", "docker")).strip(),
                streams=ai_streams,
            ),
        )
        return cls(
            db_path=str(os.environ.get("OPS_DB_PATH", "/data/xray-ops/ops.db")),
            interval_seconds=_env_int("OPS_COLLECTION_INTERVAL_SECONDS", 60, minimum=30),
            command_timeout_seconds=_env_int("OPS_REMOTE_COMMAND_TIMEOUT_SECONDS", 20),
            max_bytes_per_stream=_env_int("OPS_MAX_BYTES_PER_STREAM", 16 * 1024 * 1024, minimum=1024),
            raw_retention_days=_env_int("OPS_RAW_RETENTION_DAYS", 7),
            rollup_retention_days=_env_int("OPS_ROLLUP_RETENTION_DAYS", 90),
            nodes=nodes,
            ssh_bin=str(os.environ.get("OPS_SSH_BIN", "ssh")).strip() or "ssh",
        )

    def validate_nodes(self) -> None:
        missing = [node.role for node in self.nodes if not node.target]
        if missing:
            raise ValueError(f"missing SSH target for: {', '.join(missing)}")
        invalid = [node.role for node in self.nodes if node.service_kind not in {"docker", "systemd"}]
        if invalid:
            raise ValueError(f"unsupported service kind for: {', '.join(invalid)}")


@dataclass(slots=True)
class NodeCollection:
    events: list[LogEvent] = field(default_factory=list)
    samples: list[NodeSample] = field(default_factory=list)
    cursors: list[LogCursor] = field(default_factory=list)
    results: list[SourceResult] = field(default_factory=list)


ExecutorFactory = Callable[[NodeConfig], SshExecutor]


class CollectorService:
    def __init__(
        self,
        config: CollectorConfig,
        store: OpsStore | None = None,
        executor_factory: ExecutorFactory | None = None,
    ):
        self.config = config
        self.store = store or OpsStore(config.db_path)
        self.executor_factory = executor_factory or self._default_executor

    def _default_executor(self, node: NodeConfig) -> SshExecutor:
        return SshExecutor(
            target=node.target,
            ssh_bin=self.config.ssh_bin,
            ssh_options=node.ssh_options,
            timeout_seconds=self.config.command_timeout_seconds,
        )

    def initialize(self) -> None:
        self.store.initialize()

    def _sample_from_snapshot(self, node: NodeConfig, payload: dict[str, Any], now) -> NodeSample:
        service = payload.get("service") if isinstance(payload.get("service"), dict) else {}
        host = payload.get("host") if isinstance(payload.get("host"), dict) else {}
        previous = self.store.latest_sample(node.role)
        previous_attributes = previous.get("attributes", {}) if previous else {}

        cpu_total = host.get("cpu_total_ticks")
        cpu_idle = host.get("cpu_idle_ticks")
        previous_total = previous_attributes.get("cpu_total_ticks")
        previous_idle = previous_attributes.get("cpu_idle_ticks")
        cpu_usage = None
        if all(isinstance(value, int) for value in (cpu_total, cpu_idle, previous_total, previous_idle)):
            total_delta = cpu_total - previous_total
            idle_delta = cpu_idle - previous_idle
            if total_delta > 0 and 0 <= idle_delta <= total_delta:
                cpu_usage = round((1 - idle_delta / total_delta) * 100, 3)

        memory_total = host.get("memory_total_bytes")
        memory_available = host.get("memory_available_bytes")
        memory_ratio = None
        if isinstance(memory_total, int) and memory_total > 0 and isinstance(memory_available, int):
            memory_ratio = round(max(0.0, min(1.0, memory_available / memory_total)), 6)

        disk_total = host.get("root_disk_total_bytes")
        disk_used = host.get("root_disk_used_bytes")
        disk_ratio = None
        if isinstance(disk_total, int) and disk_total > 0 and isinstance(disk_used, int):
            disk_ratio = round(max(0.0, min(1.0, disk_used / disk_total)), 6)

        started_at = service.get("started_at")
        if started_at:
            try:
                started_at = format_timestamp(parse_timestamp(str(started_at)))
            except (TypeError, ValueError):
                started_at = None

        timestamp = format_timestamp(now)
        attributes = redact_value(
            {
                "cpu_total_ticks": cpu_total,
                "cpu_idle_ticks": cpu_idle,
                "service_status": service.get("status"),
                "service_error_class": service.get("error_class", ""),
                "service_error": service.get("error", ""),
            }
        )
        return NodeSample(
            sample_id=stable_id("sample", node.role, timestamp),
            node_role=node.role,
            observed_at=timestamp,
            collected_at=timestamp,
            service_running=service.get("running") if isinstance(service.get("running"), bool) else None,
            service_health=str(service.get("health")) if service.get("health") is not None else None,
            service_started_at=started_at,
            exit_code=service.get("exit_code") if isinstance(service.get("exit_code"), int) else None,
            oom_killed=service.get("oom_killed") if isinstance(service.get("oom_killed"), bool) else None,
            restart_count=service.get("restart_count") if isinstance(service.get("restart_count"), int) else None,
            cpu_usage_percent=cpu_usage,
            memory_available_ratio=memory_ratio,
            load1=float(host["load1"]) if isinstance(host.get("load1"), (int, float)) else None,
            root_disk_usage_ratio=disk_ratio,
            network_rx_bytes=host.get("network_rx_bytes") if isinstance(host.get("network_rx_bytes"), int) else None,
            network_tx_bytes=host.get("network_tx_bytes") if isinstance(host.get("network_tx_bytes"), int) else None,
            attributes=attributes,
        )

    def _collect_stream(
        self,
        node: NodeConfig,
        executor: SshExecutor,
        stream: LogStreamConfig,
        path: str,
        now,
    ) -> tuple[list[LogEvent], LogCursor | None, SourceResult]:
        source_name = f"log:{stream.stream}"
        cursor = self.store.get_cursor(node.role, stream.source_kind, stream.stream)
        identity = cursor.source_identity if cursor else ""
        offset = cursor.offset if cursor else 0
        payload = executor.read_file_delta(path, identity, offset, self.config.max_bytes_per_stream)
        if not payload.get("exists"):
            return [], None, SourceResult(
                node_role=node.role,
                source=source_name,
                status="failed",
                error_class="log_missing",
                detail="configured log source is not available",
            )

        events: list[LogEvent] = []
        bytes_read = 0
        last_event_at = cursor.last_event_at if cursor else None
        last_digest = cursor.last_digest if cursor else ""
        cursor_override: dict[str, Any] | None = None
        for segment in payload.get("segments", []):
            try:
                data = base64.b64decode(segment.get("data_base64", ""), validate=True)
            except (ValueError, TypeError) as exc:
                raise RemoteCommandError("remote_output_invalid", "log segment was not valid base64") from exc
            parsed = parse_log_bytes(
                node_role=node.role,
                source_kind=stream.source_kind,
                stream=stream.stream,
                data=data,
                source_identity=str(segment.get("identity", "")),
                base_offset=int(segment.get("start_offset", 0)),
                collected_at=now,
                docker_json=stream.docker_json,
            )
            events.extend(parsed.events)
            bytes_read += len(data)
            if parsed.last_event_at:
                last_event_at = parsed.last_event_at
                last_digest = parsed.last_digest
            if parsed.incomplete_bytes:
                cursor_override = {
                    "path": str(segment.get("path", path)),
                    "identity": str(segment.get("identity", "")),
                    "offset": int(segment.get("start_offset", 0)) + parsed.consumed_bytes,
                }
                break

        final_cursor = cursor_override or payload.get("cursor")
        cursor_update = None
        if isinstance(final_cursor, dict):
            cursor_update = LogCursor(
                node_role=node.role,
                source_kind=stream.source_kind,
                stream=stream.stream,
                source_path=str(final_cursor.get("path", path)),
                source_identity=str(final_cursor.get("identity", identity)),
                offset=max(0, int(final_cursor.get("offset", offset))),
                last_event_at=last_event_at,
                last_digest=last_digest,
                updated_at=format_timestamp(now),
            )
        rotation_gap = bool(payload.get("rotation_gap"))
        return events, cursor_update, SourceResult(
            node_role=node.role,
            source=source_name,
            status="partial" if rotation_gap else "success",
            bytes_read=bytes_read,
            events_read=len(events),
            error_class="rotation_gap" if rotation_gap else "",
            detail="prior rotation segment was unavailable" if rotation_gap else "",
        )

    def _collect_node(self, node: NodeConfig, now) -> NodeCollection:
        output = NodeCollection()
        executor = self.executor_factory(node)
        snapshot: dict[str, Any] | None = None
        try:
            snapshot = executor.node_snapshot(node.service_kind, node.service_name, node.docker_bin)
            output.samples.append(self._sample_from_snapshot(node, snapshot, now))
            output.results.append(SourceResult(node.role, "node_snapshot", "success"))
        except RemoteCommandError as exc:
            output.results.append(
                SourceResult(node.role, "node_snapshot", "failed", error_class=exc.error_class, detail=exc.detail)
            )

        for stream in node.streams:
            path = stream.path
            if stream.dynamic_container_path:
                path = str((snapshot or {}).get("container_log_path") or "")
            if not path:
                output.results.append(
                    SourceResult(
                        node.role,
                        f"log:{stream.stream}",
                        "failed",
                        error_class="log_path_unavailable",
                        detail="container log path was not available from node snapshot",
                    )
                )
                continue
            try:
                events, cursor, result = self._collect_stream(node, executor, stream, path, now)
                output.events.extend(events)
                if cursor:
                    output.cursors.append(cursor)
                output.results.append(result)
            except RemoteCommandError as exc:
                output.results.append(
                    SourceResult(
                        node.role,
                        f"log:{stream.stream}",
                        "failed",
                        error_class=exc.error_class,
                        detail=exc.detail,
                    )
                )
        return output

    def collect_once(self) -> CollectionBatch:
        self.config.validate_nodes()
        self.initialize()
        started = utc_now()
        run_id = stable_id("collect", format_timestamp(started), os.getpid(), time.monotonic_ns())
        combined = NodeCollection()
        with ThreadPoolExecutor(max_workers=len(self.config.nodes)) as pool:
            futures = {pool.submit(self._collect_node, node, started): node for node in self.config.nodes}
            for future in as_completed(futures):
                node = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # isolate one node from the other
                    result = NodeCollection(
                        results=[
                            SourceResult(
                                node.role,
                                "collector_internal",
                                "failed",
                                error_class="collector_internal_error",
                                detail=str(redact_value(str(exc)))[:500],
                            )
                        ]
                    )
                combined.events.extend(result.events)
                combined.samples.extend(result.samples)
                combined.cursors.extend(result.cursors)
                combined.results.extend(result.results)

        successes = sum(result.status == "success" for result in combined.results)
        failures = sum(result.status != "success" for result in combined.results)
        status = "success" if failures == 0 else "partial" if successes else "failed"
        ended = utc_now()
        batch = CollectionBatch(
            run_id=run_id,
            started_at=format_timestamp(started),
            ended_at=format_timestamp(ended),
            status=status,
            events=combined.events,
            samples=combined.samples,
            cursor_updates=combined.cursors,
            source_results=combined.results,
        )
        self.store.commit_collection(batch)
        _emit(
            "collection_completed",
            run_id=run_id,
            status=status,
            events=len(batch.events),
            samples=len(batch.samples),
            failed_sources=failures,
            duration_seconds=round((ended - started).total_seconds(), 3),
        )
        return batch

    def _backfill_stream(
        self,
        node: NodeConfig,
        executor: SshExecutor,
        stream: LogStreamConfig,
        path: str,
        cutoff,
    ) -> tuple[int, int]:
        files = executor.list_log_files(path)
        inserted_events = 0
        bytes_read = 0
        last_event_at = None
        last_digest = ""
        current_cursor_offset: int | None = None
        for file_info in files:
            offset = 0
            while True:
                payload = executor.read_log_chunk(
                    str(file_info["path"]),
                    offset,
                    self.config.max_bytes_per_stream,
                )
                if not payload.get("exists"):
                    break
                data = base64.b64decode(payload.get("data_base64", ""), validate=True)
                if not data:
                    break
                now = utc_now()
                parsed = parse_log_bytes(
                    node_role=node.role,
                    source_kind=stream.source_kind,
                    stream=stream.stream,
                    data=data,
                    source_identity=str(payload.get("identity", file_info.get("identity", ""))),
                    base_offset=offset,
                    collected_at=now,
                    docker_json=stream.docker_json,
                )
                selected = [event for event in parsed.events if parse_timestamp(event.observed_at) >= cutoff]
                if selected:
                    run_id = stable_id(
                        "backfill",
                        node.role,
                        stream.stream,
                        file_info.get("identity", ""),
                        offset,
                    )
                    result = SourceResult(
                        node.role,
                        f"backfill:{stream.stream}",
                        "success",
                        bytes_read=len(data),
                        events_read=len(selected),
                    )
                    self.store.commit_collection(
                        CollectionBatch(
                            run_id=run_id,
                            started_at=format_timestamp(now),
                            ended_at=format_timestamp(now),
                            status="success",
                            events=selected,
                            source_results=[result],
                        )
                    )
                    inserted_events += len(selected)
                    last_event_at = selected[-1].observed_at
                    last_digest = selected[-1].digest
                bytes_read += len(data)
                consumed = parsed.consumed_bytes
                if consumed <= 0:
                    break
                offset += consumed
                if payload.get("eof") and parsed.incomplete_bytes == 0:
                    break
            if file_info.get("current"):
                current_cursor_offset = offset

        current = next((item for item in files if item.get("current")), None)
        if current:
            self.store.set_cursor_if_absent(
                LogCursor(
                    node_role=node.role,
                    source_kind=stream.source_kind,
                    stream=stream.stream,
                    source_path=str(current["path"]),
                    source_identity=str(current["identity"]),
                    offset=min(int(current["size"]), max(0, current_cursor_offset or 0)),
                    last_event_at=last_event_at,
                    last_digest=last_digest,
                    updated_at=format_timestamp(utc_now()),
                )
            )
        return inserted_events, bytes_read

    def backfill(self, hours: int) -> dict[str, Any]:
        self.config.validate_nodes()
        self.initialize()
        cutoff = utc_now() - timedelta(hours=hours)
        summary: dict[str, Any] = {"hours": hours, "cutoff": format_timestamp(cutoff), "nodes": {}}
        for node in self.config.nodes:
            executor = self.executor_factory(node)
            node_summary = {"events": 0, "bytes": 0, "errors": []}
            try:
                snapshot = executor.node_snapshot(node.service_kind, node.service_name, node.docker_bin)
            except RemoteCommandError as exc:
                snapshot = {}
                node_summary["errors"].append({"source": "node_snapshot", "error_class": exc.error_class})
            for stream in node.streams:
                path = stream.path
                if stream.dynamic_container_path:
                    path = str(snapshot.get("container_log_path") or "")
                if not path:
                    node_summary["errors"].append(
                        {"source": stream.stream, "error_class": "log_path_unavailable"}
                    )
                    continue
                try:
                    events, read_bytes = self._backfill_stream(node, executor, stream, path, cutoff)
                    node_summary["events"] += events
                    node_summary["bytes"] += read_bytes
                except (RemoteCommandError, ValueError) as exc:
                    node_summary["errors"].append(
                        {
                            "source": stream.stream,
                            "error_class": getattr(exc, "error_class", "backfill_failed"),
                        }
                    )
            summary["nodes"][node.role] = node_summary
        self.collect_once()
        _emit("backfill_completed", **summary)
        return summary

    def run_forever(self) -> None:
        self.config.validate_nodes()
        self.initialize()
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_cleanup = 0.0
        while not stopping:
            cycle_started = time.monotonic()
            try:
                self.collect_once()
                if cycle_started - last_cleanup >= 3600:
                    deleted = self.store.cleanup(
                        raw_days=self.config.raw_retention_days,
                        rollup_days=self.config.rollup_retention_days,
                    )
                    _emit("collector_retention_completed", deleted=deleted)
                    last_cleanup = cycle_started
            except Exception as exc:
                _emit("collection_cycle_failed", error_class=type(exc).__name__, detail=str(exc)[:500])
            remaining = self.config.interval_seconds - (time.monotonic() - cycle_started)
            deadline = time.monotonic() + max(0.0, remaining)
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Xray operations evidence over SSH")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("once")
    subparsers.add_parser("run")
    backfill = subparsers.add_parser("backfill")
    backfill.add_argument("--hours", type=int, default=48)
    health = subparsers.add_parser("healthcheck")
    health.add_argument("--max-age-seconds", type=int, default=180)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = CollectorConfig.from_env()
    store = OpsStore(config.db_path)
    service = CollectorService(config, store=store)
    if args.command == "init-db":
        service.initialize()
        return 0
    if args.command == "once":
        service.collect_once()
        return 0
    if args.command == "backfill":
        if args.hours <= 0:
            raise ValueError("--hours must be positive")
        service.backfill(args.hours)
        return 0
    if args.command == "healthcheck":
        service.initialize()
        heartbeat = store.latest_collection_heartbeat()
        if not heartbeat:
            return 1
        age = (utc_now() - parse_timestamp(heartbeat)).total_seconds()
        return 0 if age <= args.max_age_seconds else 1
    service.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _emit("collector_fatal", error_class=type(exc).__name__, detail=str(exc)[:500])
        raise
