"""Privacy-preserving Xray traffic attribution snapshots."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .models import AI_DATA_PLANE, NORMAL_DATA_PLANE, format_timestamp, stable_id, utc_now
from .redaction import redact_value
from .storage import OpsStore


DEFAULT_SALT_FILE = "/data/xray-ops/xray-stats-redaction-salt"
DEFAULT_DB_PATH = "/data/xray-ops/ops.db"
DEFAULT_SAMPLE_INTERVAL_SECONDS = 300
DEFAULT_COMMAND_TIMEOUT_SECONDS = 15
DEFAULT_AI_METRICS_PORT = 31097
SERVICE_NAME = "xray_attribution_sampler"
ENTITY_PREFIXES = {"user": "usr", "inbound": "inb"}
DIRECTIONS = ("uplink", "downlink")


class XrayStatsError(RuntimeError):
    """Raised when a stats source cannot be sampled."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.environ.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _split_options(value: str | None) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    return tuple(shlex.split(text))


def _emit(event: str, **fields: Any) -> None:
    payload = {"event": event, "at": format_timestamp(utc_now()), **redact_value(fields)}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


@dataclass(frozen=True, slots=True)
class XrayTrafficSample:
    sample_id: str
    node_role: str
    source_id: str
    observed_at: str
    collected_at: str
    entity_type: str
    entity_ref: str
    direction: str
    value: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "node_role": self.node_role,
            "source_id": self.source_id,
            "observed_at": self.observed_at,
            "collected_at": self.collected_at,
            "entity_type": self.entity_type,
            "entity_ref": self.entity_ref,
            "direction": self.direction,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class XrayStatsSourceConfig:
    source_id: str
    node_role: str
    enabled: bool
    debug_vars_url: str
    ssh_target: str = ""
    ssh_key_file: str = ""
    ssh_known_hosts: str = ""
    ssh_options: tuple[str, ...] = ()
    ssh_bin: str = "ssh"
    container_name: str = ""
    docker_bin: str = "docker"
    metrics_port: int = DEFAULT_AI_METRICS_PORT
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class XrayStatsSamplerConfig:
    db_path: str
    salt_file: str
    salt: str
    sample_interval_seconds: int
    sources: tuple[XrayStatsSourceConfig, ...]

    @classmethod
    def from_env(cls) -> "XrayStatsSamplerConfig":
        enabled = _env_bool("OPS_XRAY_STATS_ENABLED", False)
        timeout = _env_int("OPS_XRAY_STATS_TIMEOUT_SECONDS", DEFAULT_COMMAND_TIMEOUT_SECONDS)
        ssh_bin = str(os.environ.get("OPS_XRAY_STATS_SSH_BIN", "ssh")).strip() or "ssh"
        ssh_key_file = str(os.environ.get("OPS_XRAY_STATS_SSH_KEY_FILE", "")).strip()
        global_ssh_options = _split_options(os.environ.get("OPS_XRAY_STATS_SSH_OPTIONS", ""))
        sources = (
            _source_from_env(
                prefix="OPS_XRAY_STATS_NORMAL",
                default_source_id="normal-xray",
                default_role=NORMAL_DATA_PLANE,
                default_enabled=False,
                global_enabled=enabled,
                global_ssh_key_file=ssh_key_file,
                global_ssh_options=global_ssh_options,
                ssh_bin=ssh_bin,
                timeout_seconds=timeout,
                default_metrics_port=10085,
            ),
            _source_from_env(
                prefix="OPS_XRAY_STATS_AI",
                default_source_id="ai-xray",
                default_role=AI_DATA_PLANE,
                default_enabled=False,
                global_enabled=enabled,
                global_ssh_key_file=ssh_key_file,
                global_ssh_options=global_ssh_options,
                ssh_bin=ssh_bin,
                timeout_seconds=timeout,
                default_metrics_port=DEFAULT_AI_METRICS_PORT,
            ),
        )
        return cls(
            db_path=str(os.environ.get("OPS_DB_PATH", DEFAULT_DB_PATH)).strip() or DEFAULT_DB_PATH,
            salt_file=str(os.environ.get("OPS_XRAY_STATS_REDACTION_SALT_FILE", DEFAULT_SALT_FILE)).strip()
            or DEFAULT_SALT_FILE,
            salt=str(os.environ.get("OPS_XRAY_STATS_REDACTION_SALT", "")).strip(),
            sample_interval_seconds=_env_int(
                "OPS_XRAY_STATS_SAMPLE_INTERVAL_SECONDS", DEFAULT_SAMPLE_INTERVAL_SECONDS, minimum=30
            ),
            sources=sources,
        )


def _source_from_env(
    *,
    prefix: str,
    default_source_id: str,
    default_role: str,
    default_enabled: bool,
    global_enabled: bool,
    global_ssh_key_file: str,
    global_ssh_options: tuple[str, ...],
    ssh_bin: str,
    timeout_seconds: int,
    default_metrics_port: int,
) -> XrayStatsSourceConfig:
    source_enabled = _env_bool(f"{prefix}_ENABLED", default_enabled)
    return XrayStatsSourceConfig(
        source_id=str(os.environ.get(f"{prefix}_SOURCE_ID", default_source_id)).strip() or default_source_id,
        node_role=str(os.environ.get(f"{prefix}_NODE_ROLE", default_role)).strip() or default_role,
        enabled=global_enabled and source_enabled,
        debug_vars_url=str(os.environ.get(f"{prefix}_DEBUG_VARS_URL", "")).strip(),
        ssh_target=str(os.environ.get(f"{prefix}_SSH_TARGET", "")).strip(),
        ssh_key_file=str(os.environ.get(f"{prefix}_SSH_KEY_FILE", global_ssh_key_file)).strip(),
        ssh_known_hosts=str(os.environ.get(f"{prefix}_KNOWN_HOSTS", "")).strip(),
        ssh_options=(*global_ssh_options, *_split_options(os.environ.get(f"{prefix}_SSH_OPTIONS", ""))),
        ssh_bin=ssh_bin,
        container_name=str(os.environ.get(f"{prefix}_CONTAINER", "")).strip(),
        docker_bin=str(os.environ.get(f"{prefix}_DOCKER_BIN", "docker")).strip() or "docker",
        metrics_port=_env_int(f"{prefix}_METRICS_PORT", default_metrics_port),
        timeout_seconds=timeout_seconds,
    )


class XrayStatsRedactor:
    def __init__(self, salt: bytes):
        if not salt:
            raise ValueError("redaction salt must not be empty")
        self._salt = salt

    @classmethod
    def from_config(cls, config: XrayStatsSamplerConfig) -> "XrayStatsRedactor":
        if config.salt:
            return cls(config.salt.encode("utf-8"))
        return cls(_load_or_create_salt(config.salt_file))

    def ref(self, entity_type: str, raw_value: str) -> str:
        prefix = ENTITY_PREFIXES.get(entity_type)
        if not prefix:
            raise ValueError("unsupported xray stats entity type")
        digest = hmac.new(
            self._salt,
            f"{entity_type}\0{raw_value}".encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest()
        return f"{prefix}-{digest[:16]}"


def _load_or_create_salt(path: str) -> bytes:
    salt_path = Path(path)
    if salt_path.is_file():
        value = salt_path.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("xray stats redaction salt file is empty")
        return value.encode("utf-8")
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(salt_path.parent, 0o700)
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    descriptor = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.write("\n")
    return value.encode("utf-8")


def parse_xray_debug_vars(
    payload: dict[str, Any],
    *,
    source_id: str,
    node_role: str,
    observed_at: datetime,
    redactor: XrayStatsRedactor,
) -> list[XrayTrafficSample]:
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return []
    observed = format_timestamp(observed_at)
    collected = format_timestamp(utc_now())
    samples: list[XrayTrafficSample] = []
    for entity_type in ("user", "inbound"):
        entities = stats.get(entity_type)
        if not isinstance(entities, dict):
            continue
        for raw_entity, counters in sorted(entities.items(), key=lambda item: str(item[0])):
            if not isinstance(counters, dict):
                continue
            raw_text = str(raw_entity)
            if not raw_text:
                continue
            entity_ref = redactor.ref(entity_type, raw_text)
            for direction in DIRECTIONS:
                try:
                    value = int(counters.get(direction))
                except (TypeError, ValueError):
                    continue
                if value < 0:
                    continue
                sample_id = stable_id(
                    "xstat",
                    node_role,
                    source_id,
                    observed,
                    entity_type,
                    entity_ref,
                    direction,
                    value,
                )
                samples.append(
                    XrayTrafficSample(
                        sample_id=sample_id,
                        node_role=node_role,
                        source_id=source_id,
                        observed_at=observed,
                        collected_at=collected,
                        entity_type=entity_type,
                        entity_ref=entity_ref,
                        direction=direction,
                        value=value,
                    )
                )
    return samples


def _http_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    if not url:
        raise XrayStatsError("debug vars URL is not configured")
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(8 * 1024 * 1024)
    except OSError as exc:
        raise XrayStatsError("debug vars HTTP request failed") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XrayStatsError("debug vars response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise XrayStatsError("debug vars response has invalid shape")
    return payload


def _ssh_command(source: XrayStatsSourceConfig, remote_command: str) -> list[str]:
    if not source.ssh_target:
        raise XrayStatsError("SSH target is not configured")
    command = [
        source.ssh_bin,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={source.timeout_seconds}",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if source.ssh_key_file:
        command.extend(["-i", source.ssh_key_file])
    if source.ssh_known_hosts:
        command.extend(["-o", f"UserKnownHostsFile={source.ssh_known_hosts}"])
    command.extend(source.ssh_options)
    command.extend([source.ssh_target, remote_command])
    return command


def _remote_debug_vars_command(source: XrayStatsSourceConfig) -> str:
    if source.container_name:
        script = (
            "set -eu; "
            "container=$1; port=$2; path=$3; docker_bin=$4; timeout=$5; "
            'ip="$($docker_bin inspect "$container" --format '
            "'{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')\"; "
            'test -n "$ip"; '
            'exec curl -fsS --max-time "$timeout" "http://$ip:$port$path"'
        )
        return " ".join(
            [
                "sh",
                "-c",
                shlex.quote(script),
                "sh",
                shlex.quote(source.container_name),
                shlex.quote(str(source.metrics_port)),
                shlex.quote(_debug_vars_path(source.debug_vars_url)),
                shlex.quote(source.docker_bin),
                shlex.quote(str(source.timeout_seconds)),
            ]
        )
    if not source.debug_vars_url:
        raise XrayStatsError("debug vars URL or container name is required for SSH sampling")
    script = 'set -eu; url=$1; timeout=$2; exec curl -fsS --max-time "$timeout" "$url"'
    return " ".join(
        [
            "sh",
            "-c",
            shlex.quote(script),
            "sh",
            shlex.quote(source.debug_vars_url),
            shlex.quote(str(source.timeout_seconds)),
        ]
    )


def _debug_vars_path(url: str) -> str:
    if not url:
        return "/debug/vars"
    parsed = urlsplit(url)
    path = parsed.path or "/debug/vars"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def fetch_xray_debug_vars(source: XrayStatsSourceConfig) -> dict[str, Any]:
    if source.ssh_target:
        command = _ssh_command(source, _remote_debug_vars_command(source))
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=source.timeout_seconds + 5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise XrayStatsError("SSH stats command failed") from exc
        if result.returncode != 0:
            raise XrayStatsError("SSH stats command returned non-zero")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise XrayStatsError("SSH stats response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise XrayStatsError("SSH stats response has invalid shape")
        return payload
    return _http_json(source.debug_vars_url, source.timeout_seconds)


class XrayStatsSamplerService:
    def __init__(
        self,
        config: XrayStatsSamplerConfig,
        *,
        store: OpsStore | None = None,
        redactor: XrayStatsRedactor | None = None,
    ):
        self.config = config
        self.store = store or OpsStore(config.db_path)
        self.redactor = redactor or XrayStatsRedactor.from_config(config)

    def initialize(self) -> None:
        self.store.initialize()

    def sample_once(self) -> dict[str, Any]:
        self.initialize()
        enabled_sources = [source for source in self.config.sources if source.enabled]
        if not enabled_sources:
            self.store.set_service_heartbeat(SERVICE_NAME, "idle", "no enabled xray stats sources")
            return {"status": "idle", "sources": 0, "samples": 0, "failures": 0}

        total_samples = 0
        failures = 0
        for source in enabled_sources:
            observed_at = utc_now()
            try:
                payload = fetch_xray_debug_vars(source)
                samples = parse_xray_debug_vars(
                    payload,
                    source_id=source.source_id,
                    node_role=source.node_role,
                    observed_at=observed_at,
                    redactor=self.redactor,
                )
                self.store.insert_xray_traffic_samples(sample.as_dict() for sample in samples)
            except Exception as exc:
                failures += 1
                _emit(
                    "xray_stats_sample_failed",
                    source_id=source.source_id,
                    node_role=source.node_role,
                    error_class=type(exc).__name__,
                )
                continue
            total_samples += len(samples)
            _emit(
                "xray_stats_sample_collected",
                source_id=source.source_id,
                node_role=source.node_role,
                samples=len(samples),
            )
        status = "healthy" if failures == 0 else "degraded"
        detail = f"sources={len(enabled_sources)} samples={total_samples} failures={failures}"
        self.store.set_service_heartbeat(SERVICE_NAME, status, detail)
        return {
            "status": status,
            "sources": len(enabled_sources),
            "samples": total_samples,
            "failures": failures,
        }

    def run_forever(self) -> None:
        self.initialize()
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            try:
                self.sample_once()
            except Exception as exc:
                self.store.set_service_heartbeat(SERVICE_NAME, "degraded", type(exc).__name__)
                _emit("xray_stats_sampler_failed", error_class=type(exc).__name__)
            deadline = time.monotonic() + self.config.sample_interval_seconds
            while not stopping:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect privacy-preserving Xray traffic attribution snapshots")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("serve")
    health = subparsers.add_parser("healthcheck")
    health.add_argument("--max-age-seconds", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = XrayStatsSamplerConfig.from_env()
    store = OpsStore(config.db_path)
    service = XrayStatsSamplerService(config, store=store)
    if args.command == "run":
        result = service.sample_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] in {"healthy", "idle"} else 1
    if args.command == "healthcheck":
        service.initialize()
        heartbeat = store.get_service_heartbeat(SERVICE_NAME)
        if not heartbeat:
            return 1
        if heartbeat.get("status") == "degraded":
            return 1
        age = (utc_now() - datetime.fromisoformat(str(heartbeat["heartbeat_at"]).replace("Z", "+00:00"))).total_seconds()
        return 0 if age <= args.max_age_seconds else 1
    service.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _emit("xray_stats_fatal", error_class=type(exc).__name__)
        raise
