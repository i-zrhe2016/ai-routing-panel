"""Structured, redacted application logging for the control plane.

The logger deliberately has no database or network dependency. It writes one
JSON object per line to stdout/stderr and lets the existing Fluent Bit agent
provide buffering and delivery guarantees.
"""

from __future__ import annotations

import contextvars
import json
import logging as std_logging
import re
import sys
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import PANEL_LOG_LEVEL, PANEL_SLOW_REQUEST_MS


SERVICE_NAME = "control-panel"
SCHEMA_VERSION = "1"
REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|csrf|credential|private[_-]?key|payment|proof|signature)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(?:bearer|basic)\s+[A-Za-z0-9+/=._:-]+|authorization\s*[:=]\s*(?:bearer|basic)\s+[A-Za-z0-9+/=._:-]+|(?:token|password|secret|cookie|csrf)\s*[:=]\s*['\"]?[^\s,;]+"
)
_ACTOR_TYPES = {"anonymous", "admin", "customer", "tenant", "system"}
_ALLOWED_METADATA_KEYS = {
    "automatic",
    "client_error_name",
    "client_source",
    "client_stack",
    "client_method",
    "client_online",
    "client_status",
    "client_url_path",
    "client_user_agent",
    "kind",
    "listen_port",
    "order_no",
    "probe_status",
    "restored",
    "route_status",
    "slug",
    "target",
}

_request_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "panel_request_context",
    default={},
)


class _MaxLevelFilter(std_logging.Filter):
    def __init__(self, maximum: int):
        super().__init__()
        self.maximum = maximum

    def filter(self, record: std_logging.LogRecord) -> bool:
        return record.levelno <= self.maximum


class _CurrentStreamHandler(std_logging.StreamHandler):
    """Resolve the stream at emit time so test capture and workers work."""

    def __init__(self, stream_name: str):
        super().__init__()
        self.stream_name = stream_name

    def emit(self, record: std_logging.LogRecord) -> None:
        self.stream = sys.stderr if self.stream_name == "stderr" else sys.stdout
        super().emit(record)


def _parse_log_level(value: str) -> int:
    level = std_logging._nameToLevel.get(str(value or "INFO").upper(), std_logging.INFO)
    return level if isinstance(level, int) else std_logging.INFO


def _build_logger() -> std_logging.Logger:
    logger = std_logging.getLogger("panel.business")
    logger.setLevel(_parse_log_level(PANEL_LOG_LEVEL))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = std_logging.Formatter("%(message)s")
    stdout_handler = _CurrentStreamHandler("stdout")
    stdout_handler.setLevel(std_logging.INFO)
    stdout_handler.addFilter(_MaxLevelFilter(std_logging.WARNING))
    stdout_handler.setFormatter(formatter)
    stderr_handler = _CurrentStreamHandler("stderr")
    stderr_handler.setLevel(std_logging.ERROR)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    return logger


LOGGER = _build_logger()


def is_valid_request_id(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and len(text) <= MAX_REQUEST_ID_LENGTH and _REQUEST_ID_RE.fullmatch(text) is not None


def new_request_id() -> str:
    return uuid.uuid4().hex


def _redact_text(value: str) -> str:
    return _SENSITIVE_TEXT_RE.sub(_REDACTED, value)


def _sanitize(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY_RE.search(key or ""):
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key))
            for item_key, item_value in value.items()
            if not _SENSITIVE_KEY_RE.search(str(item_key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value[:2000])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value)[:2000])


def _safe_actor_type(value: Any) -> str:
    actor_type = str(value or "anonymous").strip().lower()
    return actor_type if actor_type in _ACTOR_TYPES else "anonymous"


def get_request_context() -> dict[str, Any]:
    return dict(_request_context.get() or {})


def initialize_request_context(request_id: str, method: str = "", endpoint: str = "") -> None:
    _request_context.set(
        {
            "request_id": request_id,
            "actor_type": "anonymous",
            "actor_id": "",
            "method": str(method or "").upper(),
            "endpoint": str(endpoint or ""),
            "business_event_emitted": False,
        }
    )


def set_request_endpoint(endpoint: str = "", method: str = "") -> None:
    context = get_request_context()
    context["endpoint"] = str(endpoint or "")
    if method:
        context["method"] = str(method).upper()
    _request_context.set(context)


def bind_actor(actor_type: str, actor_id: Any = "") -> None:
    context = get_request_context()
    context["actor_type"] = _safe_actor_type(actor_type)
    context["actor_id"] = "" if actor_id is None else str(actor_id)
    _request_context.set(context)


def clear_request_context() -> None:
    _request_context.set({})


def _stacktrace(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _level_for(result: str, explicit: str | None = None) -> int:
    if explicit:
        return _parse_log_level(explicit)
    result_text = str(result or "success").lower()
    if result_text in {"failure", "error", "failed"}:
        return std_logging.ERROR
    if result_text in {"rejected", "denied", "warning", "slow"}:
        return std_logging.WARNING
    return std_logging.INFO


def emit_event(
    event: str,
    *,
    category: str = "business",
    result: str = "success",
    level: str | None = None,
    actor_type: str | None = None,
    actor_id: Any = None,
    resource_type: str = "",
    resource_id: Any = None,
    status_code: int | None = None,
    duration_ms: int | float | None = None,
    error_code: str = "",
    message: str = "",
    metadata: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> None:
    """Emit a schema-compliant event without allowing logging failures to escape."""

    try:
        context = get_request_context()
        resolved_actor_type = _safe_actor_type(actor_type or context.get("actor_type"))
        resolved_actor_id = actor_id if actor_id is not None else context.get("actor_id", "")
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": std_logging.getLevelName(_level_for(result, level)).lower(),
            "service": SERVICE_NAME,
            "category": str(category or "business"),
            "event": str(event or "unknown"),
            "result": str(result or "success"),
            "actor_type": resolved_actor_type,
            "actor_id": "" if resolved_actor_id is None else str(resolved_actor_id),
            "resource_type": str(resource_type or ""),
            "resource_id": "" if resource_id is None else str(resource_id),
            "request_id": str(context.get("request_id") or ""),
            "endpoint": str(context.get("endpoint") or ""),
            "method": str(context.get("method") or ""),
            "status_code": status_code,
            "duration_ms": round(float(duration_ms), 2) if duration_ms is not None else None,
            "error_code": str(error_code or ""),
            "message": _redact_text(str(message or "")[:2000]),
            "metadata": _sanitize_metadata(metadata or {}),
        }
        if exc is not None:
            payload["error_type"] = type(exc).__name__
            payload["stacktrace"] = _redact_text(_stacktrace(exc))
        LOGGER.log(_level_for(result, level), json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        return


def emit_business_event(event: str, **kwargs: Any) -> None:
    context = get_request_context()
    context["business_event_emitted"] = True
    _request_context.set(context)
    emit_event(event, category="business", **kwargs)


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    return {
        str(key): _sanitize(value, str(key))
        for key, value in metadata.items()
        if str(key) in _ALLOWED_METADATA_KEYS and not _SENSITIVE_KEY_RE.search(str(key))
    }


def emit_request_event(*, status_code: int, duration_ms: float, result: str = "success", message: str = "") -> None:
    emit_event(
        "http.request",
        category="request",
        result=result,
        status_code=status_code,
        duration_ms=duration_ms,
        message=message,
    )


def slow_request_threshold_ms() -> int:
    return PANEL_SLOW_REQUEST_MS
