import json
import subprocess
from pathlib import Path

import pytest

from components.xray_ops import codex_runner
from components.xray_ops.codex_runner import (
    DISABLED_CODEX_FEATURES,
    CodexAnalysisError,
    CodexRunner,
    CodexRunnerConfig,
    _extract_usage,
    _resolve_cli,
    _select_model_input,
    validate_model_analysis,
)


def _analysis(evidence_id="ev-1"):
    return {
        "executive_summary": "规则结果已完成。",
        "node_explanations": [
            {
                "node_role": "normal_data_plane",
                "explanation": "普通数据面的规则状态保持不变。",
                "evidence_ids": [evidence_id],
            }
        ],
        "probable_causes": [
            {
                "node_role": "normal_data_plane",
                "cause": "证据显示遥测不足。",
                "confidence": "medium",
                "evidence_ids": [evidence_id],
            }
        ],
        "recommended_actions": [
            {
                "node_role": "normal_data_plane",
                "action": "人工核对采集链路。",
                "priority": "medium",
                "evidence_ids": [evidence_id],
            }
        ],
        "uncertainties": [
            {
                "node_role": "normal_data_plane",
                "description": "缺失来源尚未恢复。",
                "evidence_ids": [evidence_id],
            }
        ],
    }


def _config(tmp_path, *, attempts=2, timeout=3):
    source = tmp_path / "source-home"
    source.mkdir()
    (source / "auth.json").write_text('{"tokens": "seed"}', encoding="utf-8")
    (source / "config.toml").write_text('model = "must-not-be-copied"', encoding="utf-8")
    executable = tmp_path / "codex"
    executable.write_text("placeholder", encoding="utf-8")
    return CodexRunnerConfig(
        source_home=source,
        runtime_home=tmp_path / "runtime-home",
        workdir=tmp_path / "workdir",
        timeout_seconds=timeout,
        max_attempts=attempts,
        codex_bin=str(executable),
    )


def _frozen():
    return {
        "overall_status": "unknown",
        "evidence": [
            {
                "evidence_id": "ev-1",
                "node_role": "normal_data_plane",
                "summary": "遥测不足",
            }
        ],
    }


def test_validate_model_analysis_rejects_non_string_and_unknown_evidence():
    invalid_text = _analysis()
    invalid_text["probable_causes"][0]["cause"] = 123
    with pytest.raises(ValueError, match="cause"):
        validate_model_analysis(invalid_text, {"ev-1"})

    empty_evidence = _analysis()
    empty_evidence["recommended_actions"][0]["evidence_ids"] = []
    with pytest.raises(ValueError, match="non-empty"):
        validate_model_analysis(empty_evidence, {"ev-1"})

    with pytest.raises(ValueError, match="unknown evidence"):
        validate_model_analysis(_analysis("ev-unknown"), {"ev-1"})

    with pytest.raises(ValueError, match="different node_role"):
        validate_model_analysis(_analysis(), {"ev-1": "ai_data_plane"})


def test_runner_uses_isolated_config_and_structured_output(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(_analysis()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ignored", stderr="progress")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak-to-child")
    monkeypatch.setattr(codex_runner.subprocess, "run", fake_run)

    result = CodexRunner(config).analyze(_frozen())

    assert result.attempts == 1
    assert result.analysis["executive_summary"] == "规则结果已完成。"
    command = captured["command"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert 'model_provider="openai"' in command
    assert "--output-schema" in command
    assert "--model" not in command and "-m" not in command
    disabled_features = {
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--disable"
    }
    assert disabled_features == set(DISABLED_CODEX_FEATURES)
    assert 'web_search="disabled"' in command
    assert json.loads(captured["kwargs"]["input"])["overall_status"] == "unknown"
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
    assert (config.runtime_home / "auth.json").is_file()
    assert not (config.runtime_home / "config.toml").exists()
    assert not list(config.workdir.glob("model-analysis-*.json"))


def test_runner_retries_invalid_evidence_then_succeeds(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = 0

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        evidence_id = "ev-unknown" if calls == 1 else "ev-1"
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(_analysis(evidence_id)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(codex_runner.subprocess, "run", fake_run)

    result = CodexRunner(config).analyze(_frozen())

    assert result.attempts == 2
    assert calls == 2


@pytest.mark.parametrize(
    ("failure", "expected", "attempts"),
    [
        ("timeout", "codex_timeout", 2),
        ("auth", "codex_auth_failed", 1),
        ("rate", "codex_rate_limited", 2),
    ],
)
def test_runner_classifies_terminal_failures(tmp_path, monkeypatch, failure, expected, attempts):
    config = _config(tmp_path)

    def fake_run(command, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, config.timeout_seconds)
        stderr = "401 unauthorized; login required" if failure == "auth" else "429 rate limit"
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)

    monkeypatch.setattr(codex_runner.subprocess, "run", fake_run)

    with pytest.raises(CodexAnalysisError) as caught:
        CodexRunner(config).analyze(_frozen())

    assert caught.value.error_class == expected
    assert caught.value.attempts == attempts


def test_model_input_selection_is_deterministic_and_bounded():
    frozen = _frozen()
    frozen["evidence"] = [
        {"evidence_id": "ev-2", "node_role": "normal_data_plane", "summary": "x" * 100},
        frozen["evidence"][0],
    ]
    first, metadata = _select_model_input(frozen, 420)
    second, second_metadata = _select_model_input({**frozen, "evidence": list(reversed(frozen["evidence"]))}, 420)

    assert first == second
    assert len(first.encode()) <= 420
    assert metadata == second_metadata
    assert metadata["evidence_available"] == 2
    assert metadata["evidence_truncated"] is True
    assert json.loads(first)["model_input_metadata"]["prompt_version"] == codex_runner.PROMPT_VERSION


def test_auth_failure_is_not_retried(tmp_path, monkeypatch):
    config = _config(tmp_path, attempts=3)
    sleeps = []
    calls = 0

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="401 unauthorized")

    monkeypatch.setattr(codex_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(codex_runner.time, "sleep", sleeps.append)
    with pytest.raises(CodexAnalysisError) as caught:
        CodexRunner(config).analyze(_frozen())

    assert caught.value.error_class == "codex_auth_failed"
    assert caught.value.attempts == 1
    assert calls == 1
    assert sleeps == []


def test_rate_limit_retries_with_bounded_backoff(tmp_path, monkeypatch):
    config = _config(tmp_path, attempts=3)
    sleeps = []
    monkeypatch.setattr(
        codex_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="429 rate limit"),
    )
    monkeypatch.setattr(codex_runner.time, "sleep", sleeps.append)

    with pytest.raises(CodexAnalysisError) as caught:
        CodexRunner(config).analyze(_frozen())

    assert caught.value.attempts == 3
    assert sleeps == [1.0, 2.0]


def test_bundled_codex_is_discovered_from_path(tmp_path, monkeypatch):
    executable = tmp_path / "codex"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    config = CodexRunnerConfig(
        source_home=tmp_path / "source",
        runtime_home=tmp_path / "runtime",
        workdir=tmp_path / "workdir",
    )

    assert _resolve_cli(config) == [str(executable)]


def test_extract_usage_from_codex_json_events():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 7465,
                        "cached_input_tokens": 12,
                        "cache_write_input_tokens": 3,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    },
                }
            ),
        ]
    )

    assert _extract_usage(stdout) == {
        "input_tokens": 7465,
        "cached_input_tokens": 12,
        "cache_write_input_tokens": 3,
        "output_tokens": 5,
        "reasoning_output_tokens": 2,
        "total_tokens": 7473,
    }
    assert _extract_usage("not json") is None
