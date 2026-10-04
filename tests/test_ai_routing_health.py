"""Durable routing decisions and independent health-cycle regressions."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from app.xray.ai_routing import artifact, manager, repository, runner
from app.xray.operation_lock import LockBusyError, exclusive_file_lock


@pytest.fixture
def health_workspace(tmp_path, monkeypatch):
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(manager, "utc_now", lambda: now)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = tmp_path / "panel.db"
    with sqlite3.connect(database) as conn:
        repository.ensure_ai_domain_schema(conn)
        conn.execute(
            "CREATE TABLE ports (listen_port INTEGER, upstream_host TEXT, upstream_port INTEGER, "
            "note TEXT, updated_at TEXT, enabled INTEGER)"
        )
        conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT)")
    args = SimpleNamespace(
        panel_db_path=database,
        config_out=runtime / "config.json",
        classification_state_path=runtime / "decisions.json",
        log_state_path=tmp_path / "log-state.json",
        log_path=tmp_path / "access.log",
        lookback_seconds=3600,
        dynamic_routing_path=runtime / "dynamic-routing.json",
        proxy_template_path=tmp_path / "absent-template.json",
        panel_route_listen_port=None,
        ai_upstream_candidates=[{"upstream_host": "ai.example.com", "upstream_port": 443}],
        ai_upstream_probe_timeout_seconds=1,
        ai_upstream_probe_server_name="",
        manual_mode="",
        manual_lock_held=False,
        render_script="app.xray.render_config",
        env_file=tmp_path / "xray.env",
        client_out=runtime / "client.json",
        share_out=runtime / "share.txt",
        data_plane_external_reloader_enabled=False,
        data_plane_config_path="",
        restart_command="",
        restart_container_name="",
        docker_timeout_seconds=5,
        report_output_dir=tmp_path / "reports",
        codex_classifier_enabled=False,
        openai_classifier_enabled=False,
        batch_size=50,
        interval_seconds=3600,
        health_interval_seconds=30,
        routing_only=False,
    )
    args.env_file.write_text(
        """XRAY_LISTEN_HOST=0.0.0.0
XRAY_LISTEN_PORT=443
XRAY_PUBLIC_HOST=panel.example.com
XRAY_PUBLIC_PORT=31098
XRAY_CLIENT_UUID=11111111-1111-1111-1111-111111111111
XRAY_FLOW=xtls-rprx-vision
XRAY_REALITY_PRIVATE_KEY=synthetic-private-key
XRAY_REALITY_PUBLIC_KEY=synthetic-public-key
XRAY_REALITY_SHORT_ID=0123456789abcdef
XRAY_SERVER_NAME=www.example.com
XRAY_DEST=www.example.com:443
XRAY_FINGERPRINT=chrome
XRAY_LOGLEVEL=warning
XRAY_NODE_TAG=health-test
"""
    )
    (runtime / "panel-ports.json").write_text(json.dumps({"ports": [31098, 32001]}))
    controller = mock.Mock()
    controller.mode = "local"
    controller.is_configured.return_value = True
    controller.supports_sync.return_value = False
    controller.supports_restart.return_value = True
    controller.supports_logs.return_value = False
    controller.restart.return_value = True
    controller.probe_outbound.return_value = {"ok": True, "method": "tcp", "management_error": False}
    monkeypatch.setattr(manager, "build_data_plane_controller", lambda _args: controller)
    monkeypatch.setattr(manager, "build_probe_runner", lambda **_kwargs: controller)
    return args, controller, now


def test_classification_cache_retains_ai_and_non_ai_after_json_loss(tmp_path):
    database = tmp_path / "panel.db"
    database.touch()
    decisions = {
        "domains": {
            "custom-ai.example": {"classification": "ai", "reason": "AI service", "source": "codex", "model": "test"},
            "ordinary.example": {"classification": "not_ai", "reason": "ordinary service", "source": "codex"},
        }
    }
    repository.save_classifications(database, decisions)
    recovered = repository.load_classifications(database)
    assert recovered["custom-ai.example"]["classification"] == "ai"
    assert recovered["ordinary.example"]["classification"] == "not_ai"
    assert recovered["custom-ai.example"]["source"] == "codex"
    repository.save_classifications(database, {"domains": {}})
    assert repository.load_classifications(database) == recovered


def test_classification_cache_recovers_legacy_ai_without_accepting_bad_rows(tmp_path):
    database = tmp_path / "panel.db"
    with sqlite3.connect(database) as conn:
        repository.ensure_ai_domain_schema(conn)
        conn.execute(
            "INSERT INTO ai_domains(domain, classification, reason, updated_at) VALUES (?, ?, ?, ?)",
            ("legacy-ai.example", "ai", "historical classification", "2026-09-01T00:00:00+00:00"),
        )
    repository.save_classifications(
        database,
        {
            "domains": {
                "not a domain": {"classification": "ai"},
                "unknown.example": {"classification": "unknown"},
                "malformed.example": {"classification": ["ai"]},
            }
        },
    )
    recovered = repository.load_classifications(database)
    assert set(recovered) == {"legacy-ai.example"}
    assert recovered["legacy-ai.example"]["reason"] == "historical classification"


def test_health_fallback_and_recovery_preserve_metrics_cursor_and_observations(health_workspace, monkeypatch):
    args, controller, now = health_workspace
    decisions = {
        "domains": {
            "custom-ai.example": {"classification": "ai", "reason": "known AI", "source": "codex"},
            "ordinary.example": {"classification": "not_ai", "reason": "ordinary", "source": "codex"},
        }
    }
    repository.save_classifications(args.panel_db_path, decisions)
    args.classification_state_path.write_text(json.dumps(decisions))
    args.log_state_path.write_text('{"log_inode":"abc","log_offset":987,"events":[]}')
    cursor_before = args.log_state_path.read_bytes()
    events = [{"seen_at": now - timedelta(minutes=10), "protocol": "tcp", "domain": "custom-ai.example"}] * 2
    events += [{"seen_at": now - timedelta(minutes=5), "protocol": "tcp", "domain": "ordinary.example"}]
    baseline = artifact.build_domain_report(
        {"events": events},
        now - timedelta(hours=1),
        now,
        decisions,
        None,
        None,
        {"status": "idle", "pending_domains_without_classifier": ["pending.example"]},
    )
    repository.save_ai_domains_to_panel_db(args.panel_db_path, baseline, decisions)
    artifact.write_domain_report(args.report_output_dir, baseline)
    history_before = {path.name: path.read_bytes() for path in (args.report_output_dir / "history").iterdir()}
    monkeypatch.setattr(manager, "sync_log", mock.Mock(side_effect=AssertionError("health must not consume logs")))
    monkeypatch.setattr(manager, "classify_pending_domains", mock.Mock(side_effect=AssertionError("must reuse cache")))
    monkeypatch.setattr(
        manager, "save_ai_domains_to_panel_db", mock.Mock(side_effect=AssertionError("no observations"))
    )
    for reachable, expected in [(True, "applied"), (False, "fallback_to_primary"), (True, "applied")]:
        controller.probe_outbound.return_value = {
            "ok": reachable,
            "method": "tcp",
            "error": "" if reachable else "timeout",
        }
        manager.run_once(args, routing_only=True)
        config = json.loads(args.config_out.read_text())
        assert [item["port"] for item in config["inbounds"]] == [31098, 32001]
        assert config["outbounds"][0] == {"protocol": "freedom", "tag": "direct"}
        ai_rules = [
            rule for rule in config.get("routing", {}).get("rules", []) if rule.get("outboundTag") == "ai_proxy"
        ]
        assert bool(ai_rules) == reachable
        if reachable:
            assert ai_rules[0]["domain"] == ["domain:custom-ai.example"]
        current = json.loads((args.report_output_dir / "latest.json").read_text())
        assert current["route_status"]["status"] == expected
        assert current["route_status"]["health_interval_seconds"] == 30
        assert current["route_status"]["classification_interval_seconds"] == 3600
        assert current["route_status"]["pending_domains_without_classifier"] == ["pending.example"]
        for key in ("generated_at", "window_start", "window_end", "unique_domains", "ai_domains", "protocols"):
            assert current[key] == baseline[key]
        assert [item["hits"] for item in current["domains"]] == [2, 1]
        assert current["domains"][0]["traffic_route"]["outbound_tag"] == ("ai_proxy" if reachable else "direct")
        assert current["domains"][1]["traffic_route"]["outbound_tag"] == "direct"
        assert args.log_state_path.read_bytes() == cursor_before
        if args.classification_state_path.exists():
            args.classification_state_path.unlink()
    assert controller.restart.call_count == 3
    assert {path.name: path.read_bytes() for path in (args.report_output_dir / "history").iterdir()} == history_before
    with sqlite3.connect(args.panel_db_path) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(hits) FROM ai_domain_observations").fetchone() == (1, 2)
        assert conn.execute("SELECT domain, total_hits FROM ai_domains").fetchall() == [("custom-ai.example", 2)]


@pytest.mark.parametrize("failure_stage", ["render", "sync", "restart"])
def test_new_classifications_survive_external_apply_failure(health_workspace, monkeypatch, failure_stage):
    args, controller, now = health_workspace
    args.log_path.write_text("2026/10/02 11:50:00 accepted tcp:new-model.example:443 [panel-31098 -> direct]\n")

    def classify(decisions, _path, domains, _args):
        assert domains == {"new-model.example"}
        decisions["domains"]["new-model.example"] = {
            "classification": "ai",
            "source": "codex",
            "reason": "AI service",
            "classified_at": now.isoformat(),
        }
        return []

    monkeypatch.setattr(manager, "classify_pending_domains", classify)
    if failure_stage == "render":
        monkeypatch.setattr(manager, "rerender_config", mock.Mock(side_effect=RuntimeError("remote render failure")))
    elif failure_stage == "sync":
        controller.supports_sync.return_value = True
        controller.sync_generated_files.side_effect = RuntimeError("remote sync failure")
    else:
        controller.restart.return_value = False
    with pytest.raises(RuntimeError):
        manager.run_once(args)
    args.classification_state_path.unlink()
    recovered = manager.load_routing_decisions(args)
    assert recovered["domains"]["new-model.example"]["classification"] == "ai"
    assert json.loads(args.log_state_path.read_text())["log_offset"] == args.log_path.stat().st_size
    if failure_stage != "render":
        assert args.config_out.with_name("config.json.pending-apply").exists()


def test_health_retries_pending_restart_and_respects_forced_fallback(health_workspace):
    args, controller, _now = health_workspace
    repository.save_classifications(args.panel_db_path, {"domains": {"known-ai.example": {"classification": "ai"}}})
    controller.restart.side_effect = [False, True, True]
    with pytest.raises(RuntimeError, match="重载失败"):
        manager.run_once(args, routing_only=True)
    pending = args.config_out.with_name("config.json.pending-apply")
    assert pending.exists()
    manager.run_once(args, routing_only=True)
    assert not pending.exists()
    report = json.loads((args.report_output_dir / "latest.json").read_text())
    assert report["route_status"]["config_retried"] is True
    args.manual_mode = "forced_fallback"
    controller.probe_outbound.reset_mock()
    manager.run_once(args, routing_only=True)
    controller.probe_outbound.assert_not_called()
    assert not args.dynamic_routing_path.exists()
    assert (
        json.loads((args.report_output_dir / "latest.json").read_text())["route_status"]["status"] == "manual_fallback"
    )


def test_health_runs_during_slow_classification_and_analysis_lock_excludes_second_analyzer(
    health_workspace, monkeypatch
):
    args, _controller, _now = health_workspace
    repository.save_classifications(args.panel_db_path, {"domains": {"known-ai.example": {"classification": "ai"}}})
    entered = threading.Event()
    release = threading.Event()

    def blocked_classifier(*_args):
        entered.set()
        assert release.wait(5), "test never released classifier"
        return []

    monkeypatch.setattr(manager, "classify_pending_domains", blocked_classifier)
    with ThreadPoolExecutor(max_workers=1) as executor:
        analysis = executor.submit(manager.analyze_domains, args)
        try:
            assert entered.wait(5), "analysis never reached classifier"
            with pytest.raises(LockBusyError):
                manager.analyze_domains(args)
            manager.run_once(args, routing_only=True)
            assert not analysis.done()
            assert args.dynamic_routing_path.exists()
        finally:
            release.set()
        analysis.result(timeout=5)


def test_manual_lock_still_excludes_health_application(health_workspace):
    args, _controller, _now = health_workspace
    lock = args.config_out.with_name(".ai-domain-manager-manual.lock")
    with exclusive_file_lock(lock), pytest.raises(LockBusyError):
        manager.run_once(args, routing_only=True)


def test_resident_scheduler_probes_while_hourly_classifier_is_blocked(health_workspace, monkeypatch):
    args, _controller, _now = health_workspace
    repository.save_classifications(args.panel_db_path, {"domains": {"known-ai.example": {"classification": "ai"}}})
    entered = threading.Event()
    release = threading.Event()
    health_finished = threading.Event()

    def classify(*_args):
        entered.set()
        assert release.wait(5), "scheduler failed to run health while classifying"
        return []

    def run(_args, *, routing_only):
        if routing_only:
            assert entered.wait(5), "analysis worker never started"
        try:
            result = manager.run_once(_args, routing_only=routing_only)
            if routing_only:
                assert not release.is_set()
                assert _args.dynamic_routing_path.exists()
                health_finished.set()
            return result
        finally:
            if routing_only and not health_finished.is_set():
                release.set()

    class StopAfterHealth:
        def is_set(self):
            return release.is_set()

        def wait(self, seconds):
            assert seconds == 30
            assert health_finished.is_set()
            release.set()

    monkeypatch.setattr(manager, "classify_pending_domains", classify)
    monkeypatch.setattr(runner, "run_once", run)
    runner.run_scheduler(args, StopAfterHealth())
    assert health_finished.is_set()


def test_full_analysis_recovers_history_without_reclassification_or_history_deletion(health_workspace, monkeypatch):
    args, _controller, now = health_workspace
    from app.xray.ai_routing import classifier

    decisions = {"domains": {"custom-ai.example": {"classification": "ai", "source": "codex", "reason": "AI"}}}
    previous = artifact.build_domain_report(
        {"events": [{"seen_at": now - timedelta(minutes=10), "protocol": "tcp", "domain": "custom-ai.example"}]},
        now - timedelta(hours=1),
        now,
        decisions,
        None,
        None,
        {"status": "idle"},
    )
    repository.save_ai_domains_to_panel_db(args.panel_db_path, previous, decisions)
    # Deliberately leave both JSON and the new cache absent: the old ai_domains table must recover the rule.
    args.codex_classifier_enabled = True
    monkeypatch.setattr(
        classifier, "classify_domains_via_codex", mock.Mock(side_effect=AssertionError("history is known"))
    )
    manager.run_once(args)
    rules = json.loads(args.dynamic_routing_path.read_text())["routing"]["rules"][0]["domain"]
    assert "domain:custom-ai.example" in rules
    with sqlite3.connect(args.panel_db_path) as conn:
        assert conn.execute("SELECT total_hits FROM ai_domains WHERE domain='custom-ai.example'").fetchone() == (1,)
        assert conn.execute(
            "SELECT classification FROM ai_domain_classifications WHERE domain='custom-ai.example'"
        ).fetchone() == ("ai",)


@pytest.mark.parametrize("json_payload", [None, "{bad json", "[]", '{"domains":[]}'])
def test_corrupt_json_uses_valid_durable_cache(health_workspace, json_payload):
    args, _controller, _now = health_workspace
    repository.save_classifications(args.panel_db_path, {"domains": {"cached.example": {"classification": "not_ai"}}})
    if json_payload is not None:
        args.classification_state_path.write_text(json_payload)
    assert manager.load_routing_decisions(args)["domains"]["cached.example"]["classification"] == "not_ai"


def test_older_json_and_cache_write_cannot_override_newer_classification(health_workspace):
    args, _controller, _now = health_workspace
    newer = {"domains": {"changed.example": {"classification": "not_ai", "classified_at": "2026-10-02T11:00:00+00:00"}}}
    older = {"domains": {"changed.example": {"classification": "ai", "classified_at": "2026-10-01T12:00:00+00:00"}}}
    repository.save_classifications(args.panel_db_path, newer)
    args.classification_state_path.write_text(json.dumps(older))
    assert manager.load_routing_decisions(args)["domains"]["changed.example"]["classification"] == "not_ai"
    repository.save_classifications(args.panel_db_path, older)
    assert repository.load_classifications(args.panel_db_path)["changed.example"]["classification"] == "not_ai"


@pytest.mark.parametrize("initial_analysis_busy", [False, True])
def test_scheduler_runs_health_every_30_seconds_and_analysis_hourly(monkeypatch, initial_analysis_busy):
    clock = [0]
    calls = []

    class Stop:
        def is_set(self):
            return clock[0] > 3600

        def wait(self, seconds):
            assert seconds == 30
            clock[0] += seconds

    class ImmediateExecutor:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, operation, *args, **kwargs):
            from concurrent.futures import Future

            result = Future()
            result.set_result(operation(*args, **kwargs))
            return result

    monkeypatch.setattr(runner, "ThreadPoolExecutor", ImmediateExecutor)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])

    def run(_args, *, routing_only):
        calls.append((clock[0], routing_only))
        if initial_analysis_busy and clock[0] == 0 and not routing_only:
            raise LockBusyError("busy")

    monkeypatch.setattr(runner, "run_once", run)
    runner.run_scheduler(SimpleNamespace(interval_seconds=3600, health_interval_seconds=30, routing_only=False), Stop())
    assert [timestamp for timestamp, routing_only in calls if not routing_only] == (
        [0, 30] if initial_analysis_busy else [0, 3600]
    )
    assert [timestamp for timestamp, routing_only in calls if routing_only] == list(range(0, 3601, 30))


@pytest.mark.parametrize(
    "routing_only, failure, expected",
    [(False, None, 0), (True, None, 0), (False, LockBusyError("busy"), 75), (True, RuntimeError("failure"), 1)],
)
def test_once_cli_preserves_exit_codes_and_supports_routing_only(monkeypatch, routing_only, failure, expected):
    args = SimpleNamespace(
        once=True,
        routing_only=routing_only,
        interval_seconds=3600,
        health_interval_seconds=30,
        lookback_seconds=3600,
        batch_size=50,
        ai_upstream_candidates=[{}],
    )
    monkeypatch.setattr(runner, "build_args", lambda: args)
    run = mock.Mock(side_effect=failure)
    monkeypatch.setattr(runner, "run_once", run)
    assert runner.main() == expected
    run.assert_called_once_with(args, routing_only=routing_only)


def test_executor_failure_preserves_applied_route_selection_and_pending_state(health_workspace):
    args, controller, _now = health_workspace
    repository.save_classifications(args.panel_db_path, {'domains': {'known-ai.example': {'classification': 'ai'}}})
    manager.run_once(args, routing_only=True)
    before = args.dynamic_routing_path.read_bytes()
    previous = json.loads((args.report_output_dir / 'latest.json').read_text())['ai_target']
    previous.update(selected_index=1, selected_number=2, upstream_host='prior.example', upstream_port=8443, failover_active=True)
    report = json.loads((args.report_output_dir / 'latest.json').read_text())
    report['ai_target'] = previous
    (args.report_output_dir / 'latest.json').write_text(json.dumps(report))
    pending = args.config_out.with_name('config.json.pending-apply')
    pending.touch()
    controller.probe_outbound.return_value = {'ok': False, 'management_error': True,
        'method': 'vless_reality', 'error': 'probe_transport_failed'}
    manager.run_once(args, routing_only=True)
    assert args.dynamic_routing_path.read_bytes() == before
    result = json.loads((args.report_output_dir / 'latest.json').read_text())
    assert result['ai_target']['upstream_host'] == 'prior.example'
    assert result['ai_target']['selected_index'] == 1
    assert result['ai_target']['selection_preserved'] is True
    assert result['route_status']['status'] == 'probe_error'
    assert result['route_status']['config_retried'] is True


def test_manual_selection_does_not_succeed_on_executor_failure(health_workspace):
    args, controller, _now = health_workspace
    args.manual_mode = "primary"
    controller.probe_outbound.return_value = {"ok": False, "management_error": True,
        "error": "probe_transport_failed", "method": "vless_reality"}
    with pytest.raises(RuntimeError, match="保留此前人工模式"):
        manager.run_once(args, routing_only=True)
    with sqlite3.connect(args.panel_db_path) as conn:
        assert conn.execute("SELECT value FROM app_state WHERE key='ai_routing_manual_mode'").fetchone() is None
