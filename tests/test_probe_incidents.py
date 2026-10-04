import json
import subprocess
import threading
from pathlib import Path

import pytest

from app.probe_incidents import CodexDiagnosisRunner, IncidentStore, IncidentWorker, sanitize_text
from app.storage.sqlite import SQLiteDatabase


def store(tmp_path, failure_threshold=1):
    return IncidentStore(SQLiteDatabase(tmp_path / "panel.db"), tmp_path / "reports", failure_threshold=failure_threshold)


def failure(**extra):
    return dict({"ok": False, "error": "request failed", "stage": "request", "probe_origin": "probe-host"}, **extra)


def test_default_five_failures_qualify_once_and_success_resets(tmp_path):
    incidents = IncidentStore(SQLiteDatabase(tmp_path / "panel.db"), tmp_path / "reports")
    for _ in range(4):
        assert incidents.record("upstream", "node", failure()) is None
        assert incidents.list() == [] and incidents.claim() is None
    with incidents.database.connect() as conn:
        streak = dict(conn.execute("SELECT * FROM probe_failure_streaks").fetchone())
    assert streak["occurrences"] == 4
    incident_id = incidents.record("upstream", "node", failure(error="fifth failure"))
    item = incidents.list()[0]
    assert item["occurrences"] == 5 and item["first_seen_at"] == streak["first_seen_at"]
    assert item["evidence"]["error"] == "fifth failure"
    assert incidents.record("upstream", "node", failure()) == incident_id
    assert incidents.list()[0]["occurrences"] == 6
    assert incidents.claim()["id"] == incident_id and incidents.claim() is None
    incidents.record("upstream", "node", {"ok": True})
    assert incidents.list()[0]["recovered_at"]
    for _ in range(4):
        assert incidents.record("upstream", "node", failure()) is None
    assert incidents.record("upstream", "node", failure()) != incident_id


@pytest.mark.parametrize("count", [1, 2, 4])
def test_transient_success_removes_streak_without_model_job(tmp_path, count):
    incidents = store(tmp_path, 5)
    calls = []
    worker = IncidentWorker(incidents, lambda snapshot: calls.append(snapshot), threading.Event())
    for _ in range(count):
        incidents.record("diagnostics", "node", failure())
    incidents.record("diagnostics", "node", {"ok": True})
    assert not worker.process_one() and not calls and incidents.list() == []
    for _ in range(4):
        incidents.record("diagnostics", "node", failure())
    assert not worker.process_one()


def test_kind_origin_and_target_do_not_combine_unconfirmed_failures(tmp_path):
    incidents = store(tmp_path, 5)
    for _ in range(4):
        incidents.record("upstream", "node", failure())
    incidents.record("upstream", "other-node", failure())
    incidents.record("ai_upstream", "node", failure())
    incidents.record("upstream", "node", failure(management_error=True))
    incidents.record("upstream", "node", failure())
    incidents.record("upstream", "node", failure(probe_origin="another-probe"))
    for _ in range(3):
        incidents.record("upstream", "node", failure(probe_origin="another-probe"))
    assert incidents.list() == [] and incidents.claim() is None
    incident_id = incidents.record("upstream", "node", failure(probe_origin="another-probe"))
    item = incidents.list()[0]
    assert item["id"] == incident_id and item["occurrences"] == 5
    assert item["kind"] == "node_failure" and item["probe_origin"] == "another-probe"
    for _ in range(5):
        incidents.record("upstream", "node", failure(management_error=True))
    assert len(incidents.list()) == 2
    assert all(item["recovered_at"] is None for item in incidents.list())


def test_streak_survives_new_recorders_and_atomic_writers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    for _ in range(3):
        assert store(tmp_path, 5).record("ai_upstream", "node", failure()) is None
    assert store(tmp_path, 5).claim() is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: store(tmp_path, 5).record("ai_upstream", "node", failure()), range(5)))
    incidents = store(tmp_path, 5)
    assert ids.count(None) == 1 and len(set(ids) - {None}) == 1
    assert len(incidents.list()) == 1 and incidents.list()[0]["occurrences"] == 8
    assert incidents.claim() is not None and incidents.claim() is None


def test_failure_repeat_recovery_recurrence_and_executor_classification(tmp_path):
    incidents = store(tmp_path)
    first = incidents.record("upstream", "node:443", failure())
    assert incidents.record("upstream", "node:443", failure()) == first
    item = incidents.list()[0]
    assert item["occurrences"] == 2 and item["status"] == "queued"
    assert item["kind"] == "node_failure"
    incidents.record("upstream", "node:443", {"ok": True})
    assert incidents.list()[0]["recovered_at"]
    second = incidents.record("upstream", "node:443", failure(management_error=True))
    assert second != first
    assert incidents.list()[0]["kind"] == "executor_error"
    assert len(incidents.events(first)) == 3


def test_claim_concurrency_and_stale_restart(tmp_path):
    incidents = store(tmp_path)
    incident_id = incidents.record("ai_upstream", "node", failure())
    results = []
    other = store(tmp_path)
    threads = [
        threading.Thread(target=lambda index=index: results.append((incidents if index % 2 else other).claim()))
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(item is not None for item in results) == 1
    assert incidents.list()[0]["status"] == "running"
    incidents.recover_stale(0)
    assert incidents.list()[0]["status"] == "queued"
    assert incidents.claim()["id"] == incident_id


def test_worker_success_failed_model_and_report_path_boundary(tmp_path):
    incidents = store(tmp_path)
    incident_id = incidents.record("dns_failover", "node", failure())
    worker = IncidentWorker(
        incidents,
        lambda snapshot: {
            "diagnosis": "目标服务可能不可用",
            "uncertainty": "缺少服务器端证据",
            "recommended_checks": ["检查服务状态"],
        },
        threading.Event(),
    )
    assert worker.process_one()
    assert incidents.list()[0]["status"] == "completed"
    report = incidents.read_report(incident_id)
    assert report.startswith(f"# 探测故障记录 {incident_id}\n\n生成时间：")
    assert "## 观测事实" in report and "## Codex 分析\n\n目标服务可能不可用" in report
    assert "## 不确定性\n\n缺少服务器端证据" in report
    assert "## 建议检查\n\n- 检查服务状态" in report
    assert (tmp_path / "reports" / f"{incident_id}.md").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        incidents.read_report("../secret")
    (tmp_path / "reports" / f"{incident_id}.md").unlink()
    (tmp_path / "reports" / f"{incident_id}.md").symlink_to(tmp_path / "panel.db")
    with pytest.raises(ValueError):
        incidents.read_report(incident_id)
    incidents.record("diagnostics", "other", failure())
    worker.runner = lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired("codex", 1))
    assert worker.process_one()
    assert incidents.list()[0]["status"] == "failed"
    assert incidents.list()[0]["report_available"]
    assert incidents.list()[0]["diagnosis_error"] == "Codex 分析超时"
    failed_report = incidents.read_report(incidents.list()[0]["id"])
    assert "## 分析不可用\n\nCodex 分析超时" in failed_report
    assert "## 不确定性\n\n未获得模型诊断。观测到的失败不足以确定根因。" in failed_report
    assert "## 建议检查\n\n- 检查目标前，先核对已脱敏的探测阶段和执行器状态。" in failed_report


@pytest.mark.parametrize(
    ("stored_error", "display_error"),
    [
        ("Codex diagnosis unavailable or invalid", "Codex 暂不可用或返回结果无效"),
        ("Codex diagnosis timed out", "Codex 分析超时"),
        ("unexpected password=dummy-sensitive-value", "unexpected [redacted credential]"),
    ],
)
def test_legacy_error_display_and_failed_report_preserve_stored_history(tmp_path, stored_error, display_error):
    incidents = store(tmp_path)
    incident_id = incidents.record("diagnostics", "legacy-target", failure())
    incidents.finish(incident_id, error=stored_error)
    with incidents.database.connect() as conn:
        before = dict(conn.execute("SELECT * FROM probe_incidents WHERE id=?", (incident_id,)).fetchone())
    before_events = incidents.events(incident_id)
    assert before["status"] == "failed"
    assert before["diagnosis_error"] == sanitize_text(stored_error)
    for _ in range(2):
        item = incidents.list()[0]
        assert item["status"] == "failed" and item["diagnosis_error"] == display_error
        report = incidents.read_report(incident_id)
        assert "## 分析不可用\n\n" + display_error in report
        assert "未获得模型诊断。观测到的失败不足以确定根因。" in report
        assert "dummy-sensitive-value" not in json.dumps(item) + report
    with incidents.database.connect() as conn:
        after = dict(conn.execute("SELECT * FROM probe_incidents WHERE id=?", (incident_id,)).fetchone())
    assert after == before
    assert incidents.events(incident_id) == before_events


def test_sensitive_evidence_and_output_are_removed(tmp_path):
    secret = "vless://11111111-1111-4111-8111-111111111111@example.com:443?pbk=verysecret"
    incidents = store(tmp_path)
    incident_id = incidents.record("upstream", "node", failure(error_code="timeout", raw_config=secret, error=secret))
    assert secret not in json.dumps(incidents.list())
    assert "raw_config" not in incidents.claim()["evidence"]
    assert "11111111" not in sanitize_text(secret)
    synthetic_api_key = "sk-" + "A" * 48
    assert synthetic_api_key not in sanitize_text(synthetic_api_key)
    incidents.finish(incident_id, {"diagnosis": secret, "uncertainty": "Unknown", "recommended_checks": [secret]})
    assert "verysecret" not in incidents.read_report(incident_id)


def test_runner_bounded_isolated_invocation_and_invalid_output(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    (home / "config.toml").write_text('model = "provider-default"')
    monkeypatch.setattr("app.probe_incidents.shutil.which", lambda name: "/usr/bin/" + name)
    commands = []

    def run(command, **kwargs):
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0)
        commands.append((command, kwargs))
        workdir = Path(next(arg.split("src=", 1)[1].split(",dst=", 1)[0] for arg in command if "dst=/work" in arg))
        (workdir / "report.json").write_text(
            json.dumps(
                {
                    "diagnosis": "Observed failure",
                    "uncertainty": "Unknown root cause",
                    "recommended_checks": ["Check network"],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0)

    runner = CodexDiagnosisRunner(home, image="test-image", work_root=tmp_path / "work", timeout=5, execute=run)
    assert runner({"safe": "snapshot"})["uncertainty"] == "Unknown root cause"
    command, kwargs = commands[0]
    assert "--read-only" in command and "--cap-drop" in command and "--interactive" in command
    assert "features.shell_tool=false" in command[-1]
    assert "features.plugins=false" in command[-1]
    assert kwargs["timeout"] == 5
    assert "SSH_AUTH_SOCK" not in kwargs["env"] and "CF_API_TOKEN" not in kwargs["env"]
    prompt = json.loads(kwargs["input"])
    assert prompt["observed_facts"] == {"safe": "snapshot"}
    assert "Write all prose in diagnosis, uncertainty and every recommended_checks item in Simplified Chinese (简体中文)" in prompt["task"]
    assert "Keep JSON keys unchanged" in prompt["task"]
    assert "hostnames, protocol names and error codes" in prompt["task"]
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    runner.execute = lambda *a, **k: subprocess.CompletedProcess(a, 1)
    with pytest.raises(RuntimeError, match="Codex exited unsuccessfully"):
        runner({})


@pytest.mark.parametrize(
    "payload", ["not json", "{}", '{"diagnosis":"d","uncertainty":"u","recommended_checks":[]}', "[1]"]
)
def test_runner_rejects_malformed_model_output_and_always_removes_container(tmp_path, monkeypatch, payload):
    monkeypatch.setattr("app.probe_incidents.shutil.which", lambda _: "/usr/bin/docker")
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            workdir = Path(next(arg.split("src=", 1)[1].split(",dst=", 1)[0] for arg in command if "dst=/work" in arg))
            (workdir / "report.json").write_text(payload)
        return subprocess.CompletedProcess(command, 0)

    runner = CodexDiagnosisRunner("/private/curated", image="test-image", work_root=tmp_path / "work", execute=execute)
    with pytest.raises((RuntimeError, json.JSONDecodeError)):
        runner({})
    assert calls[-1][:3] == ["/usr/bin/docker" if runner.docker_bin == "/usr/bin/docker" else "docker", "rm", "-f"]
    assert calls[-1][-1] == calls[0][calls[0].index("--name") + 1]
    assert list((tmp_path / "work").iterdir()) == []


def test_timeout_cleanup_mount_isolation_and_private_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("app.probe_incidents.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setenv("CF_API_TOKEN", "dummy-sensitive-value")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/socket")
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 2)
        return subprocess.CompletedProcess(command, 0)

    runner = CodexDiagnosisRunner(
        "/private/curated",
        image="immutable-image",
        work_root=tmp_path / "work",
        host_work_root="/host/safe-work",
        timeout=2,
        execute=execute,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner({"source": "upstream"})
    command, kwargs = calls[0]
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 2
    assert mounts[0] == "type=bind,src=/private/curated,dst=/input-codex,readonly"
    assert mounts[1].startswith("type=bind,src=/host/safe-work/")
    assert "docker.sock" not in str(command) and "/root/.ssh" not in str(command)
    assert "--read-only" in command and "no-new-privileges" in command
    assert kwargs["env"].keys() == {"PATH"}
    assert calls[-1][0][1:3] == ["rm", "-f"]


def test_size_bound_unavailable_cli_and_observer_sources(tmp_path, monkeypatch):
    incidents = store(tmp_path)
    for source in ("upstream", "ai_upstream", "dns_failover", "diagnostics"):
        incidents.record(source, source, failure())
    assert {item["source"] for item in incidents.list()} == {"upstream", "ai_upstream", "dns_failover", "diagnostics"}
    incident_id = incidents.list()[0]["id"]
    report = tmp_path / "reports" / f"{incident_id}.md"
    report.write_bytes(b"x" * (128 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        incidents.read_report(incident_id)
    monkeypatch.setattr("app.probe_incidents.shutil.which", lambda _: None)
    worker = IncidentWorker(
        incidents, CodexDiagnosisRunner("/safe", image="image", work_root=tmp_path / "work"), threading.Event()
    )
    assert worker.process_one()
    assert incidents.list()[-1]["status"] == "failed"
    assert incidents.list()[-1]["diagnosis_error"] == "Codex 暂不可用或返回结果无效"
    assert "## 分析不可用\n\nCodex 暂不可用或返回结果无效" in incidents.read_report(incidents.list()[-1]["id"])


def test_nested_sensitive_evidence_is_excluded_and_json_key_values_redacted(tmp_path):
    incidents = store(tmp_path)
    incident_id = incidents.record("upstream", "node", failure(error={"password": "secret-value"}))
    assert "error" not in incidents.list()[0]["evidence"]
    diagnosis = '{"privateKey":"short-secret", "shortId":"0123456789abcdef", "password": "secret-value"}'
    incidents.finish(
        incident_id, {"diagnosis": diagnosis, "uncertainty": "Unknown", "recommended_checks": ["Observe only"]}
    )
    report = incidents.read_report(incident_id)
    assert "short-secret" not in report and "0123456789abcdef" not in report and "secret-value" not in report


def test_repeated_failure_is_atomic_across_independent_recorders(tmp_path):
    incidents = store(tmp_path)
    other = store(tmp_path)
    results = []
    threads = [
        threading.Thread(
            target=lambda index=index: results.append(
                (incidents if index % 2 else other).record("upstream", "same-target", failure())
            )
        )
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    assert len(incidents.list()) == 1 and incidents.list()[0]["occurrences"] == 8
    assert len(incidents.events(results[0])) == 8


@pytest.mark.parametrize("source", ["upstream", "ai_upstream", "dns_failover", "diagnostics"])
def test_protocol_observations_reach_durable_store_then_recover(tmp_path, source):
    from app.xray.protocol_probe import ProbeConfig, ProtocolProbeRunner
    from scripts.xray_protocol_probe import result

    incidents = store(tmp_path)
    runner = ProtocolProbeRunner(ProbeConfig(ssh_target="root@dedicated-probe"), observation_hook=incidents.record)
    runner._probe_outbound = lambda *_: result(False, False, "protocol_request_failed", "request")
    runner.probe_outbound(None, source=source, target="public-target:443")
    item = incidents.list()[0]
    assert item["source"] == source and item["target"] == "public-target:443"
    assert item["probe_origin"] == "root@dedicated-probe" and item["evidence"]["checked_at"]
    assert item["kind"] == "node_failure" and item["status"] == "queued"
    runner._probe_outbound = lambda *_: result(True, False, "", "request")
    runner.probe_outbound(None, source=source, target="public-target:443")
    assert incidents.list()[0]["recovered_at"]


def test_separate_ai_manager_observer_uses_shared_database_and_queue_failure_is_independent(tmp_path):
    from app.probe_incidents import database_incident_observer
    from app.xray.protocol_probe import ProbeConfig, ProtocolProbeRunner

    incidents = store(tmp_path)
    observer = database_incident_observer(tmp_path / "panel.db")
    for _ in range(5):
        observer("ai_upstream", "ai-target", failure(management_error=True))
    assert incidents.list()[0]["source"] == "ai_upstream"
    assert incidents.list()[0]["kind"] == "executor_error"
    broken = database_incident_observer(tmp_path / "not-a-directory" / "panel.db")
    (tmp_path / "not-a-directory").write_text("file")
    runner = ProtocolProbeRunner(ProbeConfig(), observation_hook=broken)
    result = runner.probe_outbound(None, source="ai_upstream", target="ai-target")
    assert result["management_error"] and result["error_code"] == "probe_executor_unconfigured"


def test_legacy_queue_requires_new_consecutive_proof_and_preserves_accepted_jobs(tmp_path):
    incidents = store(tmp_path)
    queued = incidents.record("upstream", "legacy-queued", failure())
    completed = incidents.record("upstream", "legacy-completed", failure())
    incidents.finish(completed, {"diagnosis": "历史诊断", "uncertainty": "历史证据", "recommended_checks": ["检查"]})
    failed = incidents.record("upstream", "legacy-failed", failure())
    incidents.finish(failed, error="Codex diagnosis timed out")
    running = incidents.record("upstream", "legacy-running", failure())
    with incidents.database.transaction() as conn:
        conn.execute("UPDATE probe_incidents SET status='running',claimed_at=0 WHERE id=?", (running,))
        conn.execute("UPDATE probe_incidents SET occurrences=99 WHERE id=?", (queued,))
        conn.execute("ALTER TABLE probe_incidents DROP COLUMN diagnosis_qualified")
        conn.execute("DROP TABLE probe_failure_streaks")
    before = {item["id"]: item for item in incidents.list()}
    before_events = {item_id: incidents.events(item_id) for item_id in before}
    report = incidents.read_report(completed)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: store(tmp_path, 5), range(3)))
    migrated = store(tmp_path, 5)
    for item in migrated.list():
        qualified = item.pop("diagnosis_qualified")
        assert item == before[item["id"]]
        assert qualified == (item["id"] != queued)
        assert migrated.events(item["id"]) == before_events[item["id"]]
    assert migrated.read_report(completed) == report and migrated.claim() is None
    migrated.recover_stale(0)
    assert migrated.claim()["id"] == running and migrated.claim() is None
    for _ in range(4):
        assert migrated.record("upstream", "legacy-queued", failure()) == queued
        assert migrated.claim() is None
    assert migrated.record("upstream", "legacy-queued", failure()) == queued
    assert migrated.claim()["id"] == queued
    legacy = next(item for item in migrated.list() if item["id"] == queued)
    assert legacy["occurrences"] == 104 and legacy["first_seen_at"] == before[queued]["first_seen_at"]
    assert len(migrated.events(queued)) == len(before_events[queued]) + 5


@pytest.mark.parametrize("threshold", ["0", "-1", "1.5", "bad"])
def test_invalid_ai_observer_threshold_is_contained_by_probe_boundary(tmp_path, monkeypatch, threshold):
    from app.probe_incidents import database_incident_observer
    from app.xray.protocol_probe import ProbeConfig, ProtocolProbeRunner

    monkeypatch.setenv("INCIDENT_FAILURE_THRESHOLD", threshold)
    observer = database_incident_observer(tmp_path / "panel.db")
    with pytest.raises(ValueError, match="INCIDENT_FAILURE_THRESHOLD"):
        observer("ai_upstream", "node", failure())
    runner = ProtocolProbeRunner(ProbeConfig(), observation_hook=observer)
    result = runner.probe_outbound(None, source="ai_upstream", target="node")
    assert result["management_error"] and result["error_code"] == "probe_executor_unconfigured"


def test_worker_loop_recovers_stale_claim_without_blocking_new_probe_records(tmp_path):
    incidents = store(tmp_path)
    first = incidents.record("upstream", "first", failure())
    incidents.claim()
    with incidents.database.transaction() as conn:
        conn.execute("UPDATE probe_incidents SET claimed_at=0 WHERE id=?", (first,))
    started, release, stop = threading.Event(), threading.Event(), threading.Event()

    def diagnose(_snapshot):
        started.set()
        assert release.wait(3)
        stop.set()
        return {
            "diagnosis": "Request failed",
            "uncertainty": "Root cause unknown",
            "recommended_checks": ["Inspect target"],
        }

    worker = IncidentWorker(incidents, diagnose, stop)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert started.wait(3)
        second = incidents.record("dns_failover", "second", failure())
        assert second != first and thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    statuses = {item["id"]: item["status"] for item in incidents.list()}
    assert statuses[first] == "completed" and statuses[second] == "queued"


def test_executor_node_transitions_preserve_kind_health_and_separate_jobs(tmp_path):
    incidents = store(tmp_path)
    executor_id = incidents.record("upstream", "target", failure(management_error=True, probe_origin="first-probe"))
    incidents.finish(
        executor_id,
        {
            "diagnosis": "Executor unavailable",
            "uncertainty": "Target health unknown",
            "recommended_checks": ["Inspect executor"],
        },
    )
    node_id = incidents.record("upstream", "target", failure(management_error=False, probe_origin="second-probe"))
    assert node_id != executor_id
    by_id = {item["id"]: item for item in incidents.list()}
    assert by_id[executor_id]["recovered_at"]
    assert by_id[executor_id]["kind"] == "executor_error" and by_id[executor_id]["status"] == "completed"
    assert by_id[node_id]["kind"] == "node_failure" and by_id[node_id]["status"] == "queued"
    assert incidents.record("upstream", "target", failure(probe_origin="third-probe")) == node_id
    node = next(item for item in incidents.list() if item["id"] == node_id)
    assert node["probe_origin"] == node["evidence"]["probe_origin"] == "third-probe"
    second_executor = incidents.record(
        "upstream", "target", failure(management_error=True, probe_origin="fourth-probe")
    )
    assert second_executor not in (executor_id, node_id)
    assert incidents.record("upstream", "target", failure(management_error=True)) == second_executor
    by_id = {item["id"]: item for item in incidents.list()}
    assert by_id[node_id]["recovered_at"] is None  # executor failure cannot prove target recovery
    assert by_id[node_id]["occurrences"] == 2 and by_id[second_executor]["occurrences"] == 2
    incidents.record("upstream", "target", {"ok": True, "management_error": False})
    assert all(item["recovered_at"] for item in incidents.list())
    assert incidents.record("upstream", "target", failure()) not in by_id


def test_existing_open_incident_index_migrates_without_losing_records(tmp_path):
    incidents = store(tmp_path)
    executor = incidents.record("ai_upstream", "target", failure(management_error=True))
    with incidents.database.transaction() as conn:
        conn.execute("DROP INDEX IF EXISTS probe_incidents_open_kind")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS probe_incidents_open ON probe_incidents(source,target) WHERE recovered_at IS NULL"
        )
    reloaded = store(tmp_path)
    node = reloaded.record("ai_upstream", "target", failure())
    assert executor != node and len(reloaded.list()) == 2
    with reloaded.database.connect() as conn:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list('probe_incidents')")}
    assert "probe_incidents_open" not in indexes and "probe_incidents_open_kind" in indexes


def test_runtime_cache_can_exceed_report_limit_without_relaxing_report_limit(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr("app.probe_incidents.shutil.which", lambda _: "/usr/bin/docker")
    home = tmp_path / "auth"
    home.mkdir()
    for name in ("auth.json", "config.toml"):
        (home / name).write_text("{}")
    payload = json.dumps(
        {
            "diagnosis": "Observed request failure",
            "uncertainty": "Root cause unknown",
            "recommended_checks": ["Inspect target"],
        }
    )
    oversized = False
    outputs = []

    def execute(command, **kwargs):
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0)
        workdir = Path(next(arg.split("src=", 1)[1].split(",dst=", 1)[0] for arg in command if "dst=/work" in arg))
        runtime = tmp_path / ("oversized-runtime" if oversized else "runtime")
        runtime.mkdir()
        fake_cli = runtime / "codex-fixture"
        # Run the actual supervisor with a hermetic CLI fixture: sizeable cache
        # is legitimate, whereas oversized model output must never reach host work.
        fake_cli.write_text(
            f"#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\n"
            "assert 'web_search=\"disabled\"' in sys.argv, 'Snapshot diagnosis must disable hosted tools'\n"
            "(Path(os.environ['CODEX_HOME'])/'runtime-cache').write_bytes(b'x'*(8*1024*1024))\n"
            f"Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text({'x' * (128 * 1024 + 1) if oversized else payload!r})\n"
        )
        fake_cli.chmod(0o700)
        script = (
            command[-1]
            .replace("/input-codex/", str(home) + "/")
            .replace("/tmp/codex-home", str(runtime / "home"))
            .replace("/usr/local/bin/codex", str(fake_cli))
            .replace("/tmp/report.json", str(runtime / "report.json"))
            .replace("/work/report.json", str(workdir / "report.json"))
        )
        if "--ulimit" in command:
            file_limit = int(command[command.index("--ulimit") + 1].split("=", 1)[1].split(":", 1)[0])
            script = (
                f"import resource; resource.setrlimit(resource.RLIMIT_FSIZE,({file_limit},{file_limit}));\n" + script
            )
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=kwargs["input"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        outputs.append((workdir / "report.json").exists())
        assert (runtime / "home" / "runtime-cache").stat().st_size == 8 * 1024 * 1024
        return result

    runner = CodexDiagnosisRunner("/private/curated", image="test-image", work_root=tmp_path / "work", execute=execute)
    assert runner({})["uncertainty"] == "Root cause unknown"
    oversized = True
    with pytest.raises(RuntimeError, match="Codex exited unsuccessfully"):
        runner({})
    assert outputs == [True, False]
