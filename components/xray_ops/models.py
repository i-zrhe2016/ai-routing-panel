"""Shared value objects for the Xray operations services."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc
NORMAL_DATA_PLANE = "normal_data_plane"
AI_DATA_PLANE = "ai_data_plane"
NODE_ROLES = (NORMAL_DATA_PLANE, AI_DATA_PLANE)


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return f"{prefix}-{digest.hexdigest()[:24]}"


def five_minute_bucket(value: datetime) -> str:
    normalized = value.astimezone(UTC).replace(
        minute=(value.astimezone(UTC).minute // 5) * 5,
        second=0,
        microsecond=0,
    )
    return format_timestamp(normalized)


@dataclass(frozen=True, slots=True)
class LogCursor:
    node_role: str
    source_kind: str
    stream: str
    source_path: str
    source_identity: str
    offset: int
    last_event_at: str | None
    last_digest: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LogEvent:
    event_id: str
    node_role: str
    source_kind: str
    stream: str
    observed_at: str
    collected_at: str
    timestamp_quality: str
    severity: str
    message: str
    event_type: str
    attributes: dict[str, Any]
    source_identity: str
    source_offset: int
    digest: str


@dataclass(frozen=True, slots=True)
class NodeSample:
    sample_id: str
    node_role: str
    observed_at: str
    collected_at: str
    service_running: bool | None
    service_health: str | None
    service_started_at: str | None
    exit_code: int | None
    oom_killed: bool | None
    restart_count: int | None
    cpu_usage_percent: float | None
    memory_available_ratio: float | None
    load1: float | None
    root_disk_usage_ratio: float | None
    network_rx_bytes: int | None
    network_tx_bytes: int | None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceResult:
    node_role: str
    source: str
    status: str
    bytes_read: int = 0
    events_read: int = 0
    error_class: str = ""
    detail: str = ""


@dataclass(slots=True)
class CollectionBatch:
    run_id: str
    started_at: str
    ended_at: str
    status: str
    events: list[LogEvent] = field(default_factory=list)
    samples: list[NodeSample] = field(default_factory=list)
    cursor_updates: list[LogCursor] = field(default_factory=list)
    source_results: list[SourceResult] = field(default_factory=list)
