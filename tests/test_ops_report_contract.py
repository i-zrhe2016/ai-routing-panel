import copy
import json
import os
import stat
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from components.xray_ops import report_contract
from components.xray_ops.report_contract import (
    ROOT_FIELDS,
    build_report,
    cleanup_reports,
    render_markdown,
    validate_report,
    write_report_atomic,
)


BEIJING = ZoneInfo("Asia/Shanghai")
REPORT_DATE = date(2030, 1, 2)
START = datetime(2030, 1, 2, tzinfo=BEIJING)
END = START + timedelta(days=1)


def _node(role, status="normal", breaches=None):
    return {
        "node_role": role,
        "status": status,
        "service": {
            "coverage_ratio": 1.0,
            "prometheus_coverage_ratio": 1.0,
            "target_coverage_ratio": 1.0,
            "downtime_intervals": [],
            "restart_events": 0,
        },
        "traffic": {
            "status": "demand_observed",
            "coverage_ratio": 1.0,
            "demand_increases": 2,
            "network_received_bytes": 1024,
            "network_transmitted_bytes": 2048,
            "network_total_bytes": 3072,
            "network_coverage_ratio": 1.0,
            "network_devices": ["eth0"],
            "attribution": [
                {
                    "source_id": "ai-xray",
                    "entity_type": "user",
                    "entity_ref": "usr-0123456789abcdef",
                    "uplink_bytes": 1024,
                    "downlink_bytes": 2048,
                    "total_bytes": 3072,
                    "sample_count": 2,
                    "first_sample_at": "2030-01-01T16:00:00.000000Z",
                    "last_sample_at": "2030-01-01T16:05:00.000000Z",
                    "counter_resets": 0,
                }
            ],
        },
        "telemetry": {"coverage_ratio": 1.0, "missing_sources": [], "recorded_gaps": 0},
        "resources": {"threshold_breaches": breaches or []},
        "matched_rules": [],
    }


def _classification(status="normal"):
    breach = {
        "resource": "cpu",
        "threshold": 95.0,
        "started_at": "2030-01-01T16:00:00.000000Z",
        "ended_at": "2030-01-01T16:15:00.000000Z",
        "duration_seconds": 900,
        "samples": 15,
    }
    return {
        "rules_version": "1.0",
        "rule_parameters": {
            "traffic_gap_seconds": {"normal_data_plane": 600, "ai_data_plane": 3600},
            "correlation_window_seconds": 300,
            "service_sample_interval_seconds": 60,
            "service_down_min_samples": 2,
            "telemetry_min_coverage_ratio": 0.9,
            "resource_duration_seconds": 900,
            "cpu_pressure_percent": 95.0,
            "memory_available_pressure_ratio": 0.1,
            "root_disk_pressure_ratio": 0.9,
        },
        "overall_status": status,
        "nodes": [
            _node("normal_data_plane", status=status, breaches=[breach]),
            _node("ai_data_plane"),
        ],
        "incidents": [],
        "evidence": [
            {
                "evidence_id": "ev-1",
                "node_role": "normal_data_plane",
                "source": "node_samples",
                "observed_at": "2030-01-01T16:00:00.000000Z",
                "evidence_type": "service_state",
                "summary": "服务状态证据",
                "attributes": {},
                "source_quality": "complete",
            }
        ],
    }


def _model_analysis():
    return {
        "executive_summary": "两个节点的确定性规则结果已生成。",
        "node_explanations": [
            {
                "node_role": "normal_data_plane",
                "explanation": "普通节点状态来自固定规则。",
                "evidence_ids": ["ev-1"],
            }
        ],
        "probable_causes": [],
        "recommended_actions": [],
        "uncertainties": [],
    }


def _health():
    return {"overall_coverage_ratio": 1.0, "node_coverage": {}, "sources": [], "gaps": []}


def _generation_health():
    return {
        "rules": "success",
        "codex": "success",
        "codex_attempts": 1,
        "codex_error_class": "",
        "codex_input": None,
        "codex_usage": None,
        "json_schema": "valid",
        "markdown": "success",
        "atomic_write": "success",
    }


def _report(monkeypatch, *, mode="codex", status="normal"):
    monkeypatch.setattr(
        report_contract,
        "utc_now",
        lambda: datetime(2030, 1, 2, 16, 10, 42, tzinfo=timezone.utc),
    )
    return build_report(
        report_date=REPORT_DATE.isoformat(),
        window_start=START,
        window_end=END,
        classification=_classification(status),
        collection_health=_health(),
        generation_mode=mode,
        model_analysis=_model_analysis() if mode == "codex" else None,
        generation_health=_generation_health(),
    )


def test_build_report_uses_beijing_timestamps_and_validates_model_evidence(monkeypatch):
    report = _report(monkeypatch)

    assert report["window_start"] == "2030-01-02T00:00:00+08:00"
    assert report["window_end"] == "2030-01-03T00:00:00+08:00"
    assert report["generated_at"] == "2030-01-03T00:10:42+08:00"
    validate_report(report)

    invalid = copy.deepcopy(report)
    invalid["model_analysis"]["node_explanations"][0]["evidence_ids"] = ["ev-unknown"]
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_report(invalid)

    invalid_window = copy.deepcopy(report)
    invalid_window["window_start"] = "2030-01-01T16:00:00Z"
    with pytest.raises(ValueError, match="rendered in Asia/Shanghai"):
        validate_report(invalid_window)


def test_packaged_report_schema_matches_contract_root():
    schema_resource = files("components.xray_ops").joinpath("schemas/daily-report-v1.schema.json")
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))

    assert schema["$schema"].endswith("2020-12/schema")
    assert set(schema["required"]) == ROOT_FIELDS
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    traffic_properties = schema["$defs"]["node"]["properties"]["traffic"]["properties"]
    assert "network_total_bytes" in traffic_properties
    assert "network_devices" in traffic_properties
    assert "attribution" in traffic_properties
    assert files("components.xray_ops").joinpath("schemas/model-analysis.schema.json").is_file()


def test_rules_only_versions_and_markdown_section_order(monkeypatch):
    report = _report(monkeypatch, mode="rules_only")
    markdown = render_markdown(report)

    assert report["prompt_version"] is None
    assert report["model_output_schema_version"] is None
    assert "Codex 不可用" in markdown
    assert "数据面总流量：3.00 KiB" in markdown
    assert "| `user` | `usr-0123456789abcdef` | 1.00 KiB | 2.00 KiB | 3.00 KiB | 2 | 0 |" in markdown
    headings = [
        "## 执行摘要",
        "## 普通数据面",
        "## AI 数据面",
        "## 流量中断",
        "## 故障时间线",
        "## 资源风险",
        "## 采集完整性",
        "## 原因分析和建议",
        "## 证据附录",
    ]
    positions = [markdown.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "| 普通数据面 | cpu |" in markdown


def test_atomic_write_sets_permissions_and_rolls_back_pair_on_failure(tmp_path, monkeypatch):
    original = _report(monkeypatch, status="normal")
    paths = write_report_atomic(tmp_path, original)
    json_path = Path(paths["json_path"])
    markdown_path = Path(paths["markdown_path"])
    original_json = json_path.read_bytes()
    original_markdown = markdown_path.read_bytes()

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(json_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(markdown_path.stat().st_mode) == 0o600
    assert json_path.is_symlink()
    assert markdown_path.is_symlink()
    assert json.loads(original_json)["overall_status"] == "normal"

    replacement = _report(monkeypatch, status="fault")
    real_replace = os.replace
    failed = False

    current_link = tmp_path / ".2030-01-02.current"

    def fail_generation_commit(source, destination):
        nonlocal failed
        if not failed and Path(destination) == current_link:
            failed = True
            raise OSError("injected generation commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(report_contract.os, "replace", fail_generation_commit)
    with pytest.raises(OSError, match="injected"):
        write_report_atomic(tmp_path, replacement)

    assert json_path.read_bytes() == original_json
    assert markdown_path.read_bytes() == original_markdown
    assert not list(tmp_path.glob(".ops-report-*.tmp"))


def test_cleanup_reports_honors_ninety_day_boundary(tmp_path):
    today = date(2030, 4, 10)
    keep = today - timedelta(days=90)
    remove = today - timedelta(days=91)
    for report_date in (keep, remove):
        for suffix in (".json", ".md"):
            (tmp_path / f"{report_date.isoformat()}{suffix}").write_text("report", encoding="utf-8")
    (tmp_path / "not-a-report.json").write_text("keep", encoding="utf-8")

    deleted = cleanup_reports(tmp_path, 90, today)

    assert deleted == 2
    assert (tmp_path / f"{keep.isoformat()}.json").is_file()
    assert not (tmp_path / f"{remove.isoformat()}.json").exists()
    assert (tmp_path / "not-a-report.json").is_file()
