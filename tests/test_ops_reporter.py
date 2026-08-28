import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from components.xray_ops.codex_runner import (
    CodexAnalysisError,
    CodexRunResult,
    CodexRunnerConfig,
)
from components.xray_ops.github_reports import GitHubReportPublisherConfig
from components.xray_ops.prometheus import PrometheusResult
from components.xray_ops.reporter import (
    ReporterConfig,
    ReporterService,
    due_report_date,
    load_route_history,
)
from components.xray_ops.storage import OpsStore


BEIJING = ZoneInfo("Asia/Shanghai")
REPORT_DATE = date(2030, 1, 2)


class RecordingStore(OpsStore):
    def __init__(self, path):
        super().__init__(path)
        self.queries = []

    def query_events(self, start, end, node_role=None):
        self.queries.append(("events", start, end))
        return super().query_events(start, end, node_role)

    def query_samples(self, start, end, node_role=None):
        self.queries.append(("samples", start, end))
        return super().query_samples(start, end, node_role)

    def query_rollups(self, start, end, node_role=None):
        self.queries.append(("rollups", start, end))
        return super().query_rollups(start, end, node_role)

    def query_gaps(self, start, end, node_role=None):
        self.queries.append(("gaps", start, end))
        return super().query_gaps(start, end, node_role)

    def query_collection_runs(self, start, end):
        self.queries.append(("collection_runs", start, end))
        return super().query_collection_runs(start, end)


class EmptyPrometheus:
    def collect(self, _start, _end):
        return PrometheusResult(metrics={}, sources=[])


class ExplodingPrometheus:
    def collect(self, _start, _end):
        raise RuntimeError("Prometheus unavailable")


class FailedCodex:
    def analyze(self, _frozen):
        raise CodexAnalysisError("codex_auth_failed", 2, "authentication failed")


class SuccessfulCodex:
    def analyze(self, frozen):
        evidence_id = next(
            item["evidence_id"] for item in frozen["evidence"] if item["node_role"] == "normal_data_plane"
        )
        return CodexRunResult(
            analysis={
                "executive_summary": "确定性结果显示遥测不足，未伪造节点状态。",
                "node_explanations": [
                    {
                        "node_role": "normal_data_plane",
                        "explanation": "普通节点缺少足够遥测。",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "probable_causes": [],
                "recommended_actions": [],
                "uncertainties": [],
            },
            attempts=1,
        )


def _config(tmp_path, route_dir=None):
    return ReporterConfig(
        db_path=str(tmp_path / "ops.db"),
        report_dir=str(tmp_path / "reports"),
        lock_path=str(tmp_path / "reporter.lock"),
        prometheus_url="http://127.0.0.1:9090",
        prometheus_timeout_seconds=1,
        prometheus_bearer_token="",
        ai_routing_report_dir=str(route_dir or (tmp_path / "missing-route-history")),
        report_retention_days=90,
        scheduler_interval_seconds=60,
        xray_stats_window_padding_seconds=900,
        force_rules_only=False,
        github_reports=GitHubReportPublisherConfig(),
        codex=CodexRunnerConfig(
            source_home=tmp_path / "codex-seed",
            runtime_home=tmp_path / "codex-runtime",
            workdir=tmp_path / "codex-workdir",
        ),
    )


def test_reporter_uses_beijing_day_and_degrades_optional_sources(tmp_path):
    config = _config(tmp_path)
    store = RecordingStore(config.db_path)
    service = ReporterService(
        config,
        store=store,
        prometheus_factory=lambda: ExplodingPrometheus(),
        codex_factory=lambda: FailedCodex(),
    )

    report = service.run_for_date(REPORT_DATE)

    assert store.queries == []
    assert report["window_start"] == "2030-01-02T00:00:00+08:00"
    assert report["window_end"] == "2030-01-03T00:00:00+08:00"
    assert report["generation_mode"] == "rules_only"
    assert report["generation_health"]["codex"] == "failed"
    assert report["generation_health"]["codex_attempts"] == 2
    assert report["generation_health"]["codex_error_class"] == "codex_auth_failed"
    prometheus_sources = [
        source for source in report["collection_health"]["sources"] if source["source"].startswith("prometheus:")
    ]
    assert prometheus_sources
    assert all(source["error_class"] == "prometheus_client_failed" for source in prometheus_sources)
    json_path = Path(config.report_dir) / "2030-01-02.json"
    markdown_path = Path(config.report_dir) / "2030-01-02.md"
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    assert "Codex 不可用" in markdown_path.read_text(encoding="utf-8")
    assert store.latest_successful_report("2030-01-02")["generation_mode"] == "rules_only"


def test_reporter_codex_success_is_kept_separate_from_rules(tmp_path):
    config = _config(tmp_path)
    service = ReporterService(
        config,
        store=OpsStore(config.db_path),
        prometheus_factory=lambda: EmptyPrometheus(),
        codex_factory=lambda: SuccessfulCodex(),
    )

    report = service.run_for_date(REPORT_DATE)

    assert report["generation_mode"] == "codex"
    assert report["overall_status"] == "unknown"
    assert report["model_analysis"]["executive_summary"].startswith("确定性结果")
    assert "确定性结果显示遥测不足" in (Path(config.report_dir) / "2030-01-02.md").read_text(encoding="utf-8")


def test_route_history_fallback_is_loaded_and_classified(tmp_path):
    route_dir = tmp_path / "route-history"
    route_dir.mkdir()
    (route_dir / "inside.json").write_text(
        json.dumps(
            {
                "generated_at": "2030-01-02T00:05:00+08:00",
                "route_status": {"status": "fallback_to_primary", "reason": "ai_upstream_unreachable"},
                "ai_target": {"probe_status": "all_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    (route_dir / "end-exclusive.json").write_text(
        json.dumps(
            {
                "generated_at": "2030-01-03T00:00:00+08:00",
                "route_status": {"status": "fallback_to_primary", "reason": "outside"},
                "ai_target": {"probe_status": "all_unreachable"},
            }
        ),
        encoding="utf-8",
    )
    start = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
    history = load_route_history(route_dir, start, start + timedelta(days=1))
    assert len(history.events) == 1
    assert history.events[0]["fallback"] is True

    config = _config(tmp_path, route_dir=route_dir)
    report = ReporterService(
        config,
        store=OpsStore(config.db_path),
        prometheus_factory=lambda: EmptyPrometheus(),
    ).run_for_date(REPORT_DATE, rules_only=True)

    ai_node = next(node for node in report["nodes"] if node["node_role"] == "ai_data_plane")
    assert ai_node["status"] == "fault"
    assert any(rule["rule_id"] == "OPS-F004" for rule in ai_node["matched_rules"])


def test_skip_if_complete_prevents_duplicate_scheduler_run(tmp_path):
    config = _config(tmp_path)
    store = OpsStore(config.db_path)
    service = ReporterService(
        config,
        store=store,
        prometheus_factory=lambda: EmptyPrometheus(),
    )

    first = service.run_for_date(REPORT_DATE, rules_only=True)
    second = service.run_for_date(REPORT_DATE, rules_only=True, skip_if_complete=True)

    assert second == first
    with store.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM report_runs WHERE report_date = ?",
            (REPORT_DATE.isoformat(),),
        ).fetchone()[0]
    assert count == 1


def test_due_report_date_switches_at_beijing_0010():
    before = datetime(2030, 1, 3, 0, 9, 59, tzinfo=BEIJING)
    at_due = datetime(2030, 1, 3, 0, 10, 0, tzinfo=BEIJING)

    assert due_report_date(before) == date(2030, 1, 1)
    assert due_report_date(at_due) == date(2030, 1, 2)
