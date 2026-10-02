"""Tool-free, bounded OpenRouter structured output shared by AI jobs."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-5-nano"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OpenRouterError(RuntimeError):
    def __init__(self, error_class: str, detail: str, *, retryable: bool = False, usage: dict[str, int] | None = None):
        super().__init__(detail)
        self.error_class = error_class
        self.retryable = retryable
        self.usage = usage or {}


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    api_key: str = field(default="", repr=False)
    api_key_file: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 90
    max_output_tokens: int = 8192

    @classmethod
    def from_env(cls) -> OpenRouterConfig:
        try:
            return cls(
                api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
                api_key_file=os.environ.get("OPENROUTER_API_KEY_FILE", "").strip(),
                model=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
                timeout_seconds=int(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "90")),
                max_output_tokens=int(os.environ.get("OPENROUTER_MAX_OUTPUT_TOKENS", "8192")),
            )
        except ValueError:
            raise OpenRouterError("openrouter_config_invalid", "OpenRouter limits must be integers") from None

    def credential(self) -> str:
        if self.api_key_file:
            try:
                with Path(self.api_key_file).open("r", encoding="utf-8") as secret:
                    value = secret.read(4097).strip()
            except (OSError, UnicodeError):
                raise OpenRouterError(
                    "openrouter_credentials_unavailable", "OpenRouter credential file is unavailable"
                ) from None
        else:
            value = self.api_key.strip()
        if not value:
            raise OpenRouterError("openrouter_credentials_missing", "OpenRouter credential is not configured")
        if len(value) > 4096 or any(char.isspace() for char in value):
            raise OpenRouterError("openrouter_credentials_invalid", "OpenRouter credential format is invalid")
        return value


@dataclass(frozen=True, slots=True)
class OpenRouterResponse:
    output: Any
    usage: dict[str, int]
    model: str
    provider: str = "openrouter"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authorization must never be forwarded to a redirected host.
        return None


def _open_request(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _token_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = raw.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[target] = value
    for container, source, target in (
        ("prompt_tokens_details", "cached_tokens", "cached_input_tokens"),
        ("completion_tokens_details", "reasoning_tokens", "reasoning_output_tokens"),
    ):
        nested = raw.get(container)
        value = nested.get(source) if isinstance(nested, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[target] = value
    return usage


class OpenRouterClient:
    def __init__(self, config: OpenRouterConfig):
        self.config = config

    def complete(self, messages: list[dict[str, Any]], schema: dict[str, Any], schema_name: str) -> OpenRouterResponse:
        config = self.config
        if config.timeout_seconds <= 0 or not 1 <= config.max_output_tokens <= 128000 or not config.model:
            raise OpenRouterError("openrouter_config_invalid", "OpenRouter model and positive limits are required")
        key = config.credential()
        payload = {
            "model": config.model,
            "messages": messages,
            "max_completion_tokens": config.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError):
            raise OpenRouterError("openrouter_request_invalid", "OpenRouter request is not valid JSON") from None
        if len(encoded) > MAX_REQUEST_BYTES:
            raise OpenRouterError("openrouter_request_too_large", "OpenRouter request exceeds the input limit")
        request = urllib.request.Request(
            ENDPOINT,
            data=encoded,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with _open_request(request, timeout=config.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                error_class, retryable = "openrouter_auth_failed", False
            elif status == 429:
                error_class, retryable = "openrouter_rate_limited", True
            elif status >= 500:
                error_class, retryable = "openrouter_unavailable", True
            else:
                error_class, retryable = "openrouter_request_rejected", False
            raise OpenRouterError(
                error_class, f"OpenRouter request failed (HTTP {status})", retryable=retryable
            ) from None
        except TimeoutError:
            raise OpenRouterError("openrouter_timeout", "OpenRouter request timed out", retryable=True) from None
        except (urllib.error.URLError, OSError):
            raise OpenRouterError(
                "openrouter_unavailable", "OpenRouter request is unavailable", retryable=True
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OpenRouterError("openrouter_response_too_large", "OpenRouter response exceeds the output limit")
        usage: dict[str, int] = {}
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise TypeError
            usage = _token_usage(body.get("usage"))
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                raise TypeError
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise OpenRouterError("openrouter_refused", "OpenRouter declined the requested output", usage=usage)
            if choice.get("finish_reason") != "stop":
                raise ValueError
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            output = json.loads(content)
        except (ValueError, TypeError, UnicodeError):
            raise OpenRouterError(
                "openrouter_invalid_output",
                "OpenRouter output is incomplete or invalid JSON",
                retryable=True,
                usage=usage,
            ) from None
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise OpenRouterError(
                "openrouter_invalid_output", "OpenRouter model provenance is missing", retryable=True, usage=usage
            )
        return OpenRouterResponse(output=output, usage=usage, model=model, provider="openrouter")
