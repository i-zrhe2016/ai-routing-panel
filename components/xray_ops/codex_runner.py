"""Isolated, schema-constrained Codex CLI invocation for report explanations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redaction import redact_value


MODEL_OUTPUT_SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "2.0"
MODEL_PROMPT = (
    "分析标准输入中的 Xray 每日运维证据。规则状态、规则编号、规则阈值、事件时间和证据 ID 已由程序冻结，"
    "不得修改、删除或重新定性。仅用中文解释已确定的结果，区分事实与推测；原因、建议和不确定项"
    "必须引用存在的 evidence_ids。不要执行命令，不要访问文件，不要输出 Markdown。"
)
DEFAULT_MAX_INPUT_BYTES = 64 * 1024
RETRYABLE_ERRORS = {"codex_timeout", "codex_rate_limited", "codex_process_failed", "codex_exec_failed", "codex_invalid_output"}
ROOT_FIELDS = {
    "executive_summary",
    "node_explanations",
    "probable_causes",
    "recommended_actions",
    "uncertainties",
}
NODE_ROLES = {"normal_data_plane", "ai_data_plane"}
DISABLED_CODEX_FEATURES = (
    "apps",
    "goals",
    "hooks",
    "multi_agent",
    "remote_plugin",
    "shell_snapshot",
    "shell_tool",
    "unified_exec",
)


@dataclass(frozen=True, slots=True)
class CodexRunnerConfig:
    source_home: Path
    runtime_home: Path
    workdir: Path
    timeout_seconds: int = 180
    max_attempts: int = 2
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    retry_backoff_seconds: float = 1.0
    codex_bin: str = ""
    model: str = ""
    model_provider: str = ""
    provider_base_url: str = ""
    provider_wire_api: str = "responses"
    provider_requires_openai_auth: bool = True

    @classmethod
    def from_env(cls) -> "CodexRunnerConfig":
        return cls(
            source_home=Path(os.environ.get("OPS_CODEX_SOURCE_HOME", "/host-codex-home")),
            runtime_home=Path(os.environ.get("OPS_CODEX_HOME", "/data/xray-ops/codex-home")),
            workdir=Path(os.environ.get("OPS_CODEX_WORKDIR", "/tmp/xray-ops-codex")),
            timeout_seconds=int(os.environ.get("OPS_CODEX_TIMEOUT_SECONDS", "180")),
            max_attempts=int(os.environ.get("OPS_CODEX_MAX_ATTEMPTS", "2")),
            max_input_bytes=int(os.environ.get("OPS_CODEX_MAX_INPUT_BYTES", str(DEFAULT_MAX_INPUT_BYTES))),
            retry_backoff_seconds=float(os.environ.get("OPS_CODEX_RETRY_BACKOFF_SECONDS", "1")),
            codex_bin=os.environ.get("OPS_CODEX_BIN", "").strip(),
            model=os.environ.get("OPS_CODEX_MODEL", "").strip(),
            model_provider=os.environ.get("OPS_CODEX_MODEL_PROVIDER", "").strip(),
            provider_base_url=os.environ.get("OPS_CODEX_PROVIDER_BASE_URL", "").strip(),
            provider_wire_api=os.environ.get("OPS_CODEX_PROVIDER_WIRE_API", "responses").strip(),
            provider_requires_openai_auth=os.environ.get(
                "OPS_CODEX_PROVIDER_REQUIRES_OPENAI_AUTH", "1"
            ).strip().lower()
            not in {"0", "false", "no"},
        )


def _provider_config_args(config: CodexRunnerConfig) -> list[str]:
    values = (config.model_provider, config.provider_base_url)
    if not any(values):
        return []
    if not all(values):
        raise CodexAnalysisError(
            "codex_provider_invalid", 0, "provider name and base URL must be configured together"
        )
    if not config.model_provider.replace("_", "").replace("-", "").isalnum():
        raise CodexAnalysisError("codex_provider_invalid", 0, "provider name is invalid")
    parsed = urllib.parse.urlsplit(config.provider_base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CodexAnalysisError("codex_provider_invalid", 0, "provider base URL must be an HTTPS URL")
    if config.provider_wire_api not in {"responses", "chat"}:
        raise CodexAnalysisError("codex_provider_invalid", 0, "provider wire API is invalid")
    provider = config.model_provider
    settings = {
        "model_provider": provider,
        f"model_providers.{provider}.name": provider,
        f"model_providers.{provider}.base_url": config.provider_base_url,
        f"model_providers.{provider}.wire_api": config.provider_wire_api,
        f"model_providers.{provider}.requires_openai_auth": config.provider_requires_openai_auth,
    }
    if config.model:
        settings["model"] = config.model
    args: list[str] = []
    for name, value in settings.items():
        rendered = json.dumps(value, ensure_ascii=False)
        args.extend(("-c", f"{name}={rendered}"))
    return args


def _extract_usage(stdout: str) -> dict[str, int] | None:
    usage: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = event.get("usage") if isinstance(event, dict) and event.get("type") == "turn.completed" else None
        if not isinstance(raw, dict):
            continue
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            value = raw.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[name] = usage.get(name, 0) + value
    if not usage:
        return None
    usage["total_tokens"] = sum(
        usage.get(name, 0)
        for name in ("input_tokens", "cache_write_input_tokens", "output_tokens")
    )
    return usage


def _merge_usage(total: dict[str, int] | None, current: dict[str, int] | None) -> dict[str, int] | None:
    if current is None:
        return total
    merged = dict(total or {})
    for name, value in current.items():
        if name != "total_tokens":
            merged[name] = merged.get(name, 0) + value
    merged["total_tokens"] = sum(
        merged.get(name, 0)
        for name in ("input_tokens", "cache_write_input_tokens", "output_tokens")
    )
    return merged


@dataclass(frozen=True, slots=True)
class CodexRunResult:
    analysis: dict[str, Any]
    attempts: int
    input_metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None
    provider: str = ""
    model: str = ""


class CodexAnalysisError(RuntimeError):
    def __init__(
        self,
        error_class: str,
        attempts: int,
        detail: str,
        usage: dict[str, Any] | None = None,
    ):
        super().__init__(detail)
        self.error_class = error_class
        self.attempts = attempts
        self.detail = detail
        self.usage = usage


def _resolve_cli(config: CodexRunnerConfig) -> list[str]:
    if config.codex_bin:
        resolved = shutil.which(config.codex_bin) or config.codex_bin
        if not Path(resolved).is_file() and shutil.which(resolved) is None:
            raise CodexAnalysisError("codex_cli_unavailable", 0, "configured Codex binary was not found")
        return [resolved]

    resolved = shutil.which("codex")
    if resolved:
        return [resolved]
    raise CodexAnalysisError("codex_cli_unavailable", 0, "Codex CLI was not found")


def _seed_runtime_home(config: CodexRunnerConfig) -> None:
    config.runtime_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config.runtime_home, 0o700)
    target = config.runtime_home / "auth.json"
    if not target.is_file():
        source = config.source_home / "auth.json"
        if source.is_file():
            temporary = config.runtime_home / ".auth.json.seed"
            try:
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    if not target.is_file():
        raise CodexAnalysisError("codex_auth_missing", 0, "Codex auth seed is unavailable")
    os.chmod(target, 0o600)


def _validate_analysis_items(
    items: list[Any],
    *,
    expected_keys: set[str],
    text_field: str,
    text_limit: int,
    allowed_evidence: set[str] | dict[str, str],
) -> None:
    for item in items:
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError("model output item has an invalid shape")
        if item.get("node_role") not in NODE_ROLES:
            raise ValueError("model output contains an invalid node_role")
        text_value = item.get(text_field)
        if not isinstance(text_value, str) or not text_value.strip() or len(text_value) > text_limit:
            raise ValueError(f"model output {text_field} must be a non-empty bounded string")
        evidence_ids = item.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not all(isinstance(value, str) for value in evidence_ids):
            raise ValueError("model output evidence_ids must be an array of strings")
        if not evidence_ids or len(evidence_ids) > 50 or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("model output evidence_ids must be non-empty, unique, and bounded")
        if not set(evidence_ids) <= set(allowed_evidence):
            raise ValueError("model output references unknown evidence IDs")
        if isinstance(allowed_evidence, dict) and any(
            allowed_evidence[evidence_id] != item["node_role"] for evidence_id in evidence_ids
        ):
            raise ValueError("model output references evidence from a different node_role")


def validate_model_analysis(
    payload: Any,
    allowed_evidence: set[str] | dict[str, str],
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != ROOT_FIELDS:
        raise ValueError("model output root fields do not match the schema")
    if (
        not isinstance(payload.get("executive_summary"), str)
        or not payload["executive_summary"].strip()
        or len(payload["executive_summary"]) > 2000
    ):
        raise ValueError("model output executive_summary must be a non-empty string")
    for section_name in ROOT_FIELDS - {"executive_summary"}:
        if not isinstance(payload.get(section_name), list):
            raise ValueError(f"model output {section_name} must be an array")
    if len(payload["node_explanations"]) > 10:
        raise ValueError("model output contains too many node explanations")
    for section_name in ("probable_causes", "recommended_actions", "uncertainties"):
        if len(payload[section_name]) > 20:
            raise ValueError(f"model output {section_name} contains too many items")
    _validate_analysis_items(
        payload["node_explanations"],
        expected_keys={"node_role", "explanation", "evidence_ids"},
        text_field="explanation",
        text_limit=2000,
        allowed_evidence=allowed_evidence,
    )
    _validate_analysis_items(
        payload["probable_causes"],
        expected_keys={"node_role", "cause", "evidence_ids", "confidence"},
        text_field="cause",
        text_limit=1000,
        allowed_evidence=allowed_evidence,
    )
    _validate_analysis_items(
        payload["recommended_actions"],
        expected_keys={"node_role", "action", "priority", "evidence_ids"},
        text_field="action",
        text_limit=1000,
        allowed_evidence=allowed_evidence,
    )
    _validate_analysis_items(
        payload["uncertainties"],
        expected_keys={"node_role", "description", "evidence_ids"},
        text_field="description",
        text_limit=1000,
        allowed_evidence=allowed_evidence,
    )
    if any(item["confidence"] not in {"low", "medium", "high"} for item in payload["probable_causes"]):
        raise ValueError("model output contains an invalid confidence")
    if any(item["priority"] not in {"low", "medium", "high"} for item in payload["recommended_actions"]):
        raise ValueError("model output contains an invalid priority")
    return redact_value(payload)


def _select_model_input(frozen_evidence: dict[str, Any], max_bytes: int) -> tuple[str, dict[str, Any]]:
    """Select evidence deterministically while keeping the complete JSON under max_bytes."""
    if max_bytes <= 0:
        raise ValueError("model input byte cap must be positive")
    redacted = redact_value(frozen_evidence)
    evidence = redacted.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    candidates = sorted(
        (item for item in evidence if isinstance(item, dict) and item.get("evidence_id")),
        key=lambda item: (str(item.get("node_role", "")), str(item["evidence_id"])),
    )
    base = dict(redacted)
    base["evidence"] = []
    metadata = {
        "prompt_version": PROMPT_VERSION,
        "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
        "byte_cap": max_bytes,
        "evidence_available": len(candidates),
        "evidence_selected": 0,
        "evidence_truncated": False,
        "input_bytes": 0,
    }
    base["model_input_metadata"] = metadata

    def encode() -> bytes:
        return json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    encoded = encode()
    if len(encoded) > max_bytes:
        raise ValueError("model input byte cap is too small for required rule context")
    for item in candidates:
        base["evidence"].append(item)
        metadata["evidence_selected"] = len(base["evidence"])
        metadata["evidence_truncated"] = metadata["evidence_selected"] < len(candidates)
        candidate = encode()
        if len(candidate) > max_bytes:
            base["evidence"].pop()
            metadata["evidence_selected"] = len(base["evidence"])
            metadata["evidence_truncated"] = True
            break
        encoded = candidate
    metadata["evidence_truncated"] = len(base["evidence"]) < len(candidates)
    # Reach a stable byte count because the count itself is included in the payload.
    for _ in range(4):
        metadata["input_bytes"] = len(encode())
    encoded = encode()
    if len(encoded) > max_bytes:
        raise ValueError("model input metadata exceeds byte cap")
    metadata["input_bytes"] = len(encoded)
    encoded = encode()
    return encoded.decode("utf-8"), dict(metadata)


def _classify_failure(returncode: int, stderr: str, stdout: str) -> str:
    text = f"{stderr}\n{stdout}".lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "codex_rate_limited"
    if "401" in text or "unauthorized" in text or "login" in text or "authentication" in text:
        return "codex_auth_failed"
    if returncode != 0:
        return "codex_process_failed"
    return "codex_invalid_output"


class CodexRunner:
    def __init__(self, config: CodexRunnerConfig):
        self.config = config

    @property
    def schema_path(self) -> Path:
        return Path(__file__).with_name("schemas") / "model-analysis.schema.json"

    def analyze(self, frozen_evidence: dict[str, Any]) -> CodexRunResult:
        if self.config.timeout_seconds <= 0 or self.config.max_attempts <= 0 or self.config.retry_backoff_seconds < 0:
            raise CodexAnalysisError("codex_config_invalid", 0, "Codex timeout and attempts must be positive and backoff non-negative")
        _seed_runtime_home(self.config)
        command_base = _resolve_cli(self.config)
        if not self.schema_path.is_file():
            raise CodexAnalysisError("codex_schema_missing", 0, "model output schema is unavailable")
        self.config.workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config.workdir, 0o700)

        try:
            stdin_payload, input_metadata = _select_model_input(frozen_evidence, self.config.max_input_bytes)
        except ValueError as exc:
            raise CodexAnalysisError("codex_input_too_large", 0, str(exc)) from exc
        selected = json.loads(stdin_payload)
        allowed_evidence = {
            str(item.get("evidence_id")): str(item.get("node_role", ""))
            for item in selected.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        prompt = MODEL_PROMPT
        provider_config_args = _provider_config_args(self.config)
        if not provider_config_args:
            provider_config_args = ["-c", 'model_provider="openai"']
        last_error_class = "codex_process_failed"
        accumulated_usage: dict[str, int] | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            output_path = self.config.workdir / f"model-analysis-{os.getpid()}-{time.monotonic_ns()}.json"
            command = [
                *command_base,
                "exec",
                "--json",
                "-C",
                str(self.config.workdir),
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                *provider_config_args,
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-c",
                'web_search="disabled"',
                *(item for feature in DISABLED_CODEX_FEATURES for item in ("--disable", feature)),
                "--output-schema",
                str(self.schema_path),
                "--output-last-message",
                str(output_path),
                prompt,
            ]
            env = {
                "CODEX_HOME": str(self.config.runtime_home),
                "HOME": os.environ.get("HOME", "/tmp"),
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            }
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "no_proxy",
                "SSL_CERT_FILE",
                "CODEX_CA_CERTIFICATE",
            ):
                if name in os.environ:
                    env[name] = os.environ[name]
            try:
                completed = subprocess.run(
                    command,
                    input=stdin_payload,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.config.timeout_seconds,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                last_error_class = "codex_timeout"
                output_path.unlink(missing_ok=True)
            except OSError:
                last_error_class = "codex_exec_failed"
                output_path.unlink(missing_ok=True)
            else:
                accumulated_usage = _merge_usage(accumulated_usage, _extract_usage(completed.stdout))
                if completed.returncode != 0 or not output_path.is_file():
                    last_error_class = _classify_failure(completed.returncode, completed.stderr, completed.stdout)
                    output_path.unlink(missing_ok=True)
                else:
                    try:
                        raw = output_path.read_text(encoding="utf-8").strip()
                        parsed = json.loads(raw)
                        analysis = validate_model_analysis(parsed, allowed_evidence)
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                        last_error_class = "codex_invalid_output"
                        output_path.unlink(missing_ok=True)
                    else:
                        output_path.unlink(missing_ok=True)
                        return CodexRunResult(
                            analysis=analysis,
                            attempts=attempt,
                            input_metadata=input_metadata,
                            usage=accumulated_usage,
                            provider=self.config.model_provider,
                            model=self.config.model,
                        )
            if last_error_class not in RETRYABLE_ERRORS or attempt >= self.config.max_attempts:
                raise CodexAnalysisError(
                    last_error_class,
                    attempt,
                    "Codex analysis did not succeed",
                    usage=accumulated_usage,
                )
            time.sleep(min(self.config.retry_backoff_seconds * (2 ** (attempt - 1)), 30.0))
        raise CodexAnalysisError(
            last_error_class,
            self.config.max_attempts,
            "Codex analysis did not succeed",
            usage=accumulated_usage,
        )
