"""Parsers for plain Xray logs and Docker json-file records."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import LogEvent, format_timestamp, parse_timestamp, stable_id
from .redaction import redact_text, redact_value


XRAY_TIMESTAMP_RE = re.compile(r"^(?P<date>\d{4}/\d{2}/\d{2}) (?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+")
ACCEPTED_RE = re.compile(r"\baccepted\s+(?P<protocol>[a-z0-9+.-]+):(?P<target>\S+)", re.IGNORECASE)
SEVERITY_RE = re.compile(r"\[(?P<severity>Debug|Info|Warning|Error)\]", re.IGNORECASE)
FATAL_RE = re.compile(
    r"(?i)\b(fatal|panic|out of memory|oom|failed to start|address already in use|permission denied)\b"
)


@dataclass(frozen=True, slots=True)
class ParseResult:
    events: list[LogEvent]
    consumed_bytes: int
    incomplete_bytes: int
    last_event_at: str | None
    last_digest: str


def _parse_xray_timestamp(message: str) -> datetime | None:
    match = XRAY_TIMESTAMP_RE.match(message)
    if match is None:
        return None
    raw = f"{match.group('date')} {match.group('time')}"
    pattern = "%Y/%m/%d %H:%M:%S.%f" if "." in match.group("time") else "%Y/%m/%d %H:%M:%S"
    try:
        return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _split_host_port(target: str) -> tuple[str, int | None]:
    value = target.strip().rstrip(",")
    if value.startswith("[") and "]:" in value:
        host, _, port_text = value[1:].partition("]:")
    elif ":" in value:
        host, _, port_text = value.rpartition(":")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            return value, None
    else:
        return value.lower(), None
    try:
        port = int(port_text)
    except ValueError:
        port = None
    return host.lower(), port


def _severity(message: str, stream: str) -> str:
    match = SEVERITY_RE.search(message)
    if match:
        return match.group("severity").lower()
    if FATAL_RE.search(message):
        return "error"
    if stream == "error":
        return "error"
    return "info"


def _event_type(message: str, stream: str) -> tuple[str, dict[str, Any]]:
    accepted = ACCEPTED_RE.search(message)
    if accepted:
        host, port = _split_host_port(accepted.group("target"))
        return (
            "accepted",
            {
                "protocol": accepted.group("protocol").lower(),
                "target_host": host,
                "target_port": port,
            },
        )
    fatal = bool(FATAL_RE.search(message))
    if fatal or stream == "error" or "[Error]" in message:
        return "xray_error", {"fatal_pattern": fatal}
    return "xray_log", {}


def _decode_docker_record(raw_line: bytes) -> tuple[list[str], datetime | None, str | None]:
    try:
        payload = json.loads(raw_line.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return [raw_line.decode("utf-8", errors="replace")], None, None
    if not isinstance(payload, dict) or not isinstance(payload.get("log"), str):
        return [raw_line.decode("utf-8", errors="replace")], None, None
    outer_time = None
    if payload.get("time"):
        try:
            outer_time = parse_timestamp(str(payload["time"]))
        except (TypeError, ValueError):
            outer_time = None
    messages = payload["log"].rstrip("\n").splitlines() or [""]
    docker_stream = str(payload.get("stream", "")).strip().lower() or None
    return messages, outer_time, docker_stream


def _build_event(
    *,
    node_role: str,
    source_kind: str,
    stream: str,
    message: str,
    outer_time: datetime | None,
    collected_at: datetime,
    source_identity: str,
    source_offset: int,
) -> LogEvent:
    redacted = redact_text(message.strip())
    xray_time = _parse_xray_timestamp(message)
    observed = xray_time or outer_time or collected_at
    timestamp_quality = "event" if xray_time else "container" if outer_time else "unknown"
    event_type, attributes = _event_type(redacted.text, stream)
    if redacted.quarantined:
        event_type = "redaction_quarantined"
        attributes = {"quarantined": True}
    attributes = redact_value(attributes)
    observed_at = format_timestamp(observed)
    digest = hashlib.sha256(
        f"{node_role}\0{stream}\0{observed_at}\0{redacted.text}".encode("utf-8", errors="replace")
    ).hexdigest()
    event_id = stable_id("evt", node_role, stream, observed_at, redacted.text)
    return LogEvent(
        event_id=event_id,
        node_role=node_role,
        source_kind=source_kind,
        stream=stream,
        observed_at=observed_at,
        collected_at=format_timestamp(collected_at),
        timestamp_quality=timestamp_quality,
        severity="warning" if redacted.quarantined else _severity(redacted.text, stream),
        message=redacted.text,
        event_type=event_type,
        attributes=attributes,
        source_identity=source_identity,
        source_offset=source_offset,
        digest=digest,
    )


def parse_log_bytes(
    *,
    node_role: str,
    source_kind: str,
    stream: str,
    data: bytes,
    source_identity: str,
    base_offset: int,
    collected_at: datetime,
    docker_json: bool = False,
) -> ParseResult:
    events: list[LogEvent] = []
    consumed = 0
    last_event_at = None
    last_digest = ""
    raw_lines = data.splitlines(keepends=True)
    for raw_line in raw_lines:
        if not raw_line.endswith((b"\n", b"\r")):
            break
        line_start = base_offset + consumed
        consumed += len(raw_line)
        if docker_json:
            messages, outer_time, docker_stream = _decode_docker_record(raw_line)
        else:
            messages = [raw_line.decode("utf-8", errors="replace").rstrip("\r\n")]
            outer_time = None
            docker_stream = None
        effective_stream = "error" if docker_stream == "stderr" else stream
        for message in messages:
            event = _build_event(
                node_role=node_role,
                source_kind=source_kind,
                stream=effective_stream,
                message=message,
                outer_time=outer_time,
                collected_at=collected_at,
                source_identity=source_identity,
                source_offset=line_start,
            )
            events.append(event)
            last_event_at = event.observed_at
            last_digest = event.digest
    return ParseResult(
        events=events,
        consumed_bytes=consumed,
        incomplete_bytes=max(0, len(data) - consumed),
        last_event_at=last_event_at,
        last_digest=last_digest,
    )
