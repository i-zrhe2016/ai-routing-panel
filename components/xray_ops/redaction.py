"""Secret redaction shared by ingestion and model-input assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|authorization)\b"
    r"(\s*[:=]\s*)([^\s,;\]\}\"']+)"
)
URI_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/@\s]+)@")
VLESS_USER_RE = re.compile(r"(?i)\b(vless://)([^@/\s]+)@")
PRIVATE_MATERIAL_RE = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|-----END [A-Z ]*PRIVATE KEY-----|"
    r"reality[_-]?private[_-]?key\s*[:=]|private[_-]?key\s*[:=])"
)
URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"]+")

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "subscription",
    "token",
}
SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "subscription_token",
    "token",
    "uuid",
}


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    replacements: int
    quarantined: bool = False


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if not parsed.query:
        return raw
    changed = False
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            pairs.append((key, "[REDACTED]"))
            changed = True
        else:
            pairs.append((key, value))
    if not changed:
        return raw
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment))


def redact_text(value: str) -> RedactionResult:
    text = str(value)
    if PRIVATE_MATERIAL_RE.search(text):
        return RedactionResult("[QUARANTINED_SECRET_MATERIAL]", 1, quarantined=True)

    replacements = 0

    def replace(pattern: re.Pattern[str], replacement: str | Any, current: str) -> str:
        nonlocal replacements
        updated, count = pattern.subn(replacement, current)
        replacements += count
        return updated

    text = replace(UUID_RE, "[REDACTED_UUID]", text)
    text = replace(BEARER_RE, "Bearer [REDACTED]", text)
    text = replace(
        SECRET_ASSIGNMENT_RE,
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = replace(VLESS_USER_RE, r"\1[REDACTED_UUID]@", text)
    text = replace(URI_USERINFO_RE, r"\1[REDACTED]@", text)

    def replace_url(match: re.Match[str]) -> str:
        nonlocal replacements
        updated = _redact_url(match)
        if updated != match.group(0):
            replacements += 1
        return updated

    text = URL_RE.sub(replace_url, text)
    return RedactionResult(text, replacements)


def redact_value(value: Any, key: str = "") -> Any:
    normalized_key = key.strip().lower().replace("-", "_")
    if normalized_key in SENSITIVE_FIELD_NAMES:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value).text
    if isinstance(value, dict):
        return {str(child_key): redact_value(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value
