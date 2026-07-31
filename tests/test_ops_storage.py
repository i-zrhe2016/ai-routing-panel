import stat
from dataclasses import replace
from datetime import datetime, timezone

from components.xray_ops.models import (
    CollectionBatch,
    LogCursor,
    NodeSample,
    SourceResult,
    format_timestamp,
)
from components.xray_ops.parsing import parse_log_bytes
from components.xray_ops.storage import OpsStore


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


def test_database_file_is_private(tmp_path):
    database = tmp_path / "ops.db"
    store = OpsStore(database)
    store.initialize()

    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def _event():
    result = parse_log_bytes(
        node_role="normal_data_plane",
        source_kind="file",
        stream="access",
        data=b"2030/01/02 12:00:00 [Info] proxy accepted tcp:example.com:443\n",
        source_identity="1:2",
        base_offset=0,
        collected_at=NOW,
    )
    return result.events[0]


def test_collection_commit_is_atomic_and_semantically_deduplicated(tmp_path):
    store = OpsStore(tmp_path / "ops.db")
    store.initialize()
    event = _event()
    sample = NodeSample(
        sample_id="sample-1",
        node_role="normal_data_plane",
        observed_at=format_timestamp(NOW),
        collected_at=format_timestamp(NOW),
        service_running=True,
        service_health="healthy",
        service_started_at=None,
        exit_code=0,
        oom_killed=False,
        restart_count=1,
        cpu_usage_percent=25.0,
        memory_available_ratio=0.5,
        load1=0.4,
        root_disk_usage_ratio=0.7,
        network_rx_bytes=100,
        network_tx_bytes=200,
        attributes={"cpu_total_ticks": 1000, "cpu_idle_ticks": 800},
    )
    cursor = LogCursor(
        node_role="normal_data_plane",
        source_kind="file",
        stream="access",
        source_path="/var/log/xray/access.log",
        source_identity="1:2",
        offset=64,
        last_event_at=event.observed_at,
        last_digest=event.digest,
        updated_at=format_timestamp(NOW),
    )
    store.commit_collection(
        CollectionBatch(
            run_id="run-1",
            started_at=format_timestamp(NOW),
            ended_at=format_timestamp(NOW),
            status="partial",
            events=[event],
            samples=[sample],
            cursor_updates=[cursor],
            source_results=[
                SourceResult("normal_data_plane", "log:access", "success", events_read=1),
                SourceResult(
                    "ai_data_plane",
                    "node_snapshot",
                    "failed",
                    error_class="ssh_timeout",
                    detail="timeout",
                ),
            ],
        )
    )

    duplicate = replace(event, event_id="different-id", source_identity="rotated:identity", source_offset=999)
    store.commit_collection(
        CollectionBatch(
            run_id="run-2",
            started_at=format_timestamp(NOW),
            ended_at=format_timestamp(NOW),
            status="success",
            events=[duplicate],
            source_results=[SourceResult("normal_data_plane", "log:access", "success")],
        )
    )

    start = "2030-01-02T00:00:00Z"
    end = "2030-01-03T00:00:00Z"
    assert len(store.query_events(start, end)) == 1
    assert len(store.query_samples(start, end)) == 1
    assert len(store.query_gaps(start, end)) == 1
    assert len(store.query_collection_runs(start, end)) == 2
    assert len(store.query_rollups(start, end)) == 1
    assert store.get_cursor("normal_data_plane", "file", "access") == cursor
    assert store.latest_collection_heartbeat() == format_timestamp(NOW)


def test_report_run_lifecycle(tmp_path):
    store = OpsStore(tmp_path / "ops.db")
    store.initialize()

    store.begin_report_run("report-1", "2030-01-02", format_timestamp(NOW))
    store.finish_report_run(
        "report-1",
        status="success",
        generation_mode="rules_only",
        json_path="/reports/2030-01-02.json",
        markdown_path="/reports/2030-01-02.md",
        payload_digest="abc",
    )

    result = store.latest_successful_report("2030-01-02")
    assert result is not None
    assert result["generation_mode"] == "rules_only"
    assert result["payload_digest"] == "abc"


def test_new_report_run_marks_crash_leftover_as_interrupted(tmp_path):
    store = OpsStore(tmp_path / "ops.db")
    store.initialize()
    store.begin_report_run("stale", "2030-01-02", format_timestamp(NOW))

    store.begin_report_run("replacement", "2030-01-02", format_timestamp(NOW))

    with store.connect() as connection:
        stale = connection.execute("SELECT * FROM report_runs WHERE run_id = 'stale'").fetchone()
        replacement = connection.execute("SELECT * FROM report_runs WHERE run_id = 'replacement'").fetchone()
    assert stale["status"] == "failed"
    assert stale["error_class"] == "report_interrupted"
    assert replacement["status"] == "running"
