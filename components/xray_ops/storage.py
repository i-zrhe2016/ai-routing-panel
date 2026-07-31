"""SQLite persistence for collector evidence and report runs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import (
    CollectionBatch,
    LogCursor,
    LogEvent,
    NodeSample,
    five_minute_bucket,
    format_timestamp,
    parse_timestamp,
    stable_id,
    utc_now,
)


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS collection_cursors (
    node_role TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    stream TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    source_identity TEXT NOT NULL DEFAULT '',
    offset INTEGER NOT NULL DEFAULT 0 CHECK (offset >= 0),
    last_event_at TEXT,
    last_digest TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (node_role, source_kind, stream)
);

CREATE TABLE IF NOT EXISTS raw_log_events (
    event_id TEXT PRIMARY KEY,
    node_role TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    stream TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    timestamp_quality TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    event_type TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_offset INTEGER NOT NULL,
    digest TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_events_semantic_dedupe
ON raw_log_events(node_role, stream, observed_at, digest);

CREATE INDEX IF NOT EXISTS idx_raw_events_window
ON raw_log_events(node_role, observed_at, event_type);

CREATE TABLE IF NOT EXISTS node_samples (
    sample_id TEXT PRIMARY KEY,
    node_role TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    service_running INTEGER,
    service_health TEXT,
    service_started_at TEXT,
    exit_code INTEGER,
    oom_killed INTEGER,
    restart_count INTEGER,
    cpu_usage_percent REAL,
    memory_available_ratio REAL,
    load1 REAL,
    root_disk_usage_ratio REAL,
    network_rx_bytes INTEGER,
    network_tx_bytes INTEGER,
    attributes_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_samples_window
ON node_samples(node_role, observed_at);

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    status TEXT NOT NULL,
    source_results_json TEXT NOT NULL,
    error_class TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_heartbeat
ON collection_runs(heartbeat_at);

CREATE TABLE IF NOT EXISTS telemetry_gaps (
    gap_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_role TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    error_class TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_telemetry_gaps_window
ON telemetry_gaps(node_role, started_at, ended_at);

CREATE TABLE IF NOT EXISTS rollups_5m (
    node_role TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    service_observed_count INTEGER NOT NULL DEFAULT 0,
    service_down_count INTEGER NOT NULL DEFAULT 0,
    cpu_sum REAL NOT NULL DEFAULT 0,
    cpu_count INTEGER NOT NULL DEFAULT 0,
    cpu_max REAL,
    memory_sum REAL NOT NULL DEFAULT 0,
    memory_count INTEGER NOT NULL DEFAULT 0,
    memory_min REAL,
    load1_sum REAL NOT NULL DEFAULT 0,
    load1_count INTEGER NOT NULL DEFAULT 0,
    load1_max REAL,
    disk_sum REAL NOT NULL DEFAULT 0,
    disk_count INTEGER NOT NULL DEFAULT 0,
    disk_max REAL,
    network_rx_first INTEGER,
    network_rx_last INTEGER,
    network_tx_first INTEGER,
    network_tx_last INTEGER,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (node_role, bucket_start)
);

CREATE TABLE IF NOT EXISTS report_runs (
    run_id TEXT PRIMARY KEY,
    report_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    generation_mode TEXT,
    json_path TEXT,
    markdown_path TEXT,
    payload_digest TEXT,
    error_class TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_report_runs_date
ON report_runs(report_date, started_at DESC);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service TEXT PRIMARY KEY,
    heartbeat_at TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_bool(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _decode_json_row(row: sqlite3.Row, json_fields: Iterable[str]) -> dict[str, Any]:
    result = dict(row)
    for field in json_fields:
        raw = result.get(field)
        result[field.removesuffix("_json")] = json.loads(raw) if raw else {}
        result.pop(field, None)
    return result


class OpsStore:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        if self.path != ":memory:":
            Path(self.path).chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"ops database schema {current} is newer than supported {SCHEMA_VERSION}")
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def get_cursor(self, node_role: str, source_kind: str, stream: str) -> LogCursor | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM collection_cursors
                WHERE node_role = ? AND source_kind = ? AND stream = ?
                """,
                (node_role, source_kind, stream),
            ).fetchone()
        return LogCursor(**dict(row)) if row else None

    def set_cursor_if_absent(self, cursor: LogCursor) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO collection_cursors (
                    node_role, source_kind, stream, source_path, source_identity,
                    offset, last_event_at, last_digest, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.node_role,
                    cursor.source_kind,
                    cursor.stream,
                    cursor.source_path,
                    cursor.source_identity,
                    cursor.offset,
                    cursor.last_event_at,
                    cursor.last_digest,
                    cursor.updated_at,
                ),
            )
            return result.rowcount == 1

    def latest_sample(self, node_role: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM node_samples
                WHERE node_role = ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (node_role,),
            ).fetchone()
        return _decode_json_row(row, ("attributes_json",)) if row else None

    def commit_collection(self, batch: CollectionBatch) -> None:
        source_payload = [asdict(result) for result in batch.source_results]
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collection_runs (
                    run_id, started_at, ended_at, heartbeat_at, status, source_results_json, error_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.run_id,
                    batch.started_at,
                    batch.ended_at,
                    batch.ended_at,
                    batch.status,
                    _json(source_payload),
                    "" if batch.status != "failed" else "all_sources_failed",
                ),
            )
            for event in batch.events:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_log_events (
                        event_id, node_role, source_kind, stream, observed_at, collected_at,
                        timestamp_quality, severity, message, event_type, attributes_json,
                        source_identity, source_offset, digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.node_role,
                        event.source_kind,
                        event.stream,
                        event.observed_at,
                        event.collected_at,
                        event.timestamp_quality,
                        event.severity,
                        event.message,
                        event.event_type,
                        _json(event.attributes),
                        event.source_identity,
                        event.source_offset,
                        event.digest,
                    ),
                ).rowcount
                if inserted:
                    self._rollup_event(connection, event)
            for sample in batch.samples:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO node_samples (
                        sample_id, node_role, observed_at, collected_at, service_running,
                        service_health, service_started_at, exit_code, oom_killed, restart_count,
                        cpu_usage_percent, memory_available_ratio, load1, root_disk_usage_ratio,
                        network_rx_bytes, network_tx_bytes, attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sample.sample_id,
                        sample.node_role,
                        sample.observed_at,
                        sample.collected_at,
                        _optional_bool(sample.service_running),
                        sample.service_health,
                        sample.service_started_at,
                        sample.exit_code,
                        _optional_bool(sample.oom_killed),
                        sample.restart_count,
                        sample.cpu_usage_percent,
                        sample.memory_available_ratio,
                        sample.load1,
                        sample.root_disk_usage_ratio,
                        sample.network_rx_bytes,
                        sample.network_tx_bytes,
                        _json(sample.attributes),
                    ),
                ).rowcount
                if inserted:
                    self._rollup_sample(connection, sample)
            for cursor in batch.cursor_updates:
                connection.execute(
                    """
                    INSERT INTO collection_cursors (
                        node_role, source_kind, stream, source_path, source_identity,
                        offset, last_event_at, last_digest, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_role, source_kind, stream) DO UPDATE SET
                        source_path = excluded.source_path,
                        source_identity = excluded.source_identity,
                        offset = excluded.offset,
                        last_event_at = COALESCE(excluded.last_event_at, collection_cursors.last_event_at),
                        last_digest = CASE
                            WHEN excluded.last_digest = '' THEN collection_cursors.last_digest
                            ELSE excluded.last_digest
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cursor.node_role,
                        cursor.source_kind,
                        cursor.stream,
                        cursor.source_path,
                        cursor.source_identity,
                        cursor.offset,
                        cursor.last_event_at,
                        cursor.last_digest,
                        cursor.updated_at,
                    ),
                )
            for source_result in batch.source_results:
                if source_result.status == "success":
                    continue
                gap_id = stable_id("gap", batch.run_id, source_result.node_role, source_result.source)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO telemetry_gaps (
                        gap_id, run_id, node_role, source, started_at, ended_at, error_class, detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        gap_id,
                        batch.run_id,
                        source_result.node_role,
                        source_result.source,
                        batch.started_at,
                        batch.ended_at,
                        source_result.error_class or "source_failed",
                        source_result.detail[:500],
                    ),
                )

    @staticmethod
    def _rollup_event(connection: sqlite3.Connection, event: LogEvent) -> None:
        accepted = 1 if event.event_type == "accepted" else 0
        error = 1 if event.event_type in {"xray_error", "redaction_quarantined"} else 0
        connection.execute(
            """
            INSERT INTO rollups_5m (node_role, bucket_start, accepted_count, error_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(node_role, bucket_start) DO UPDATE SET
                accepted_count = rollups_5m.accepted_count + excluded.accepted_count,
                error_count = rollups_5m.error_count + excluded.error_count
            """,
            (event.node_role, five_minute_bucket(parse_timestamp(event.observed_at)), accepted, error),
        )

    @staticmethod
    def _rollup_sample(connection: sqlite3.Connection, sample: NodeSample) -> None:
        service_observed = 1 if sample.service_running is not None else 0
        service_down = 1 if sample.service_running is False else 0
        cpu_count = 1 if sample.cpu_usage_percent is not None else 0
        memory_count = 1 if sample.memory_available_ratio is not None else 0
        load_count = 1 if sample.load1 is not None else 0
        disk_count = 1 if sample.root_disk_usage_ratio is not None else 0
        connection.execute(
            """
            INSERT INTO rollups_5m (
                node_role, bucket_start, sample_count, service_observed_count, service_down_count,
                cpu_sum, cpu_count, cpu_max, memory_sum, memory_count, memory_min,
                load1_sum, load1_count, load1_max, disk_sum, disk_count, disk_max,
                network_rx_first, network_rx_last, network_tx_first, network_tx_last
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_role, bucket_start) DO UPDATE SET
                sample_count = rollups_5m.sample_count + 1,
                service_observed_count = rollups_5m.service_observed_count + excluded.service_observed_count,
                service_down_count = rollups_5m.service_down_count + excluded.service_down_count,
                cpu_sum = rollups_5m.cpu_sum + excluded.cpu_sum,
                cpu_count = rollups_5m.cpu_count + excluded.cpu_count,
                cpu_max = CASE
                    WHEN excluded.cpu_max IS NULL THEN rollups_5m.cpu_max
                    WHEN rollups_5m.cpu_max IS NULL THEN excluded.cpu_max
                    ELSE MAX(rollups_5m.cpu_max, excluded.cpu_max)
                END,
                memory_sum = rollups_5m.memory_sum + excluded.memory_sum,
                memory_count = rollups_5m.memory_count + excluded.memory_count,
                memory_min = CASE
                    WHEN excluded.memory_min IS NULL THEN rollups_5m.memory_min
                    WHEN rollups_5m.memory_min IS NULL THEN excluded.memory_min
                    ELSE MIN(rollups_5m.memory_min, excluded.memory_min)
                END,
                load1_sum = rollups_5m.load1_sum + excluded.load1_sum,
                load1_count = rollups_5m.load1_count + excluded.load1_count,
                load1_max = CASE
                    WHEN excluded.load1_max IS NULL THEN rollups_5m.load1_max
                    WHEN rollups_5m.load1_max IS NULL THEN excluded.load1_max
                    ELSE MAX(rollups_5m.load1_max, excluded.load1_max)
                END,
                disk_sum = rollups_5m.disk_sum + excluded.disk_sum,
                disk_count = rollups_5m.disk_count + excluded.disk_count,
                disk_max = CASE
                    WHEN excluded.disk_max IS NULL THEN rollups_5m.disk_max
                    WHEN rollups_5m.disk_max IS NULL THEN excluded.disk_max
                    ELSE MAX(rollups_5m.disk_max, excluded.disk_max)
                END,
                network_rx_first = COALESCE(rollups_5m.network_rx_first, excluded.network_rx_first),
                network_rx_last = COALESCE(excluded.network_rx_last, rollups_5m.network_rx_last),
                network_tx_first = COALESCE(rollups_5m.network_tx_first, excluded.network_tx_first),
                network_tx_last = COALESCE(excluded.network_tx_last, rollups_5m.network_tx_last)
            """,
            (
                sample.node_role,
                five_minute_bucket(parse_timestamp(sample.observed_at)),
                service_observed,
                service_down,
                sample.cpu_usage_percent or 0,
                cpu_count,
                sample.cpu_usage_percent,
                sample.memory_available_ratio or 0,
                memory_count,
                sample.memory_available_ratio,
                sample.load1 or 0,
                load_count,
                sample.load1,
                sample.root_disk_usage_ratio or 0,
                disk_count,
                sample.root_disk_usage_ratio,
                sample.network_rx_bytes,
                sample.network_rx_bytes,
                sample.network_tx_bytes,
                sample.network_tx_bytes,
            ),
        )

    def query_events(self, start: str, end: str, node_role: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM raw_log_events WHERE observed_at >= ? AND observed_at < ?"
        params: list[Any] = [start, end]
        if node_role:
            sql += " AND node_role = ?"
            params.append(node_role)
        sql += " ORDER BY observed_at, event_id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_decode_json_row(row, ("attributes_json",)) for row in rows]

    def query_samples(self, start: str, end: str, node_role: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM node_samples WHERE observed_at >= ? AND observed_at < ?"
        params: list[Any] = [start, end]
        if node_role:
            sql += " AND node_role = ?"
            params.append(node_role)
        sql += " ORDER BY observed_at, sample_id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_decode_json_row(row, ("attributes_json",)) for row in rows]

    def query_gaps(self, start: str, end: str, node_role: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM telemetry_gaps WHERE ended_at > ? AND started_at < ?"
        params: list[Any] = [start, end]
        if node_role:
            sql += " AND node_role = ?"
            params.append(node_role)
        sql += " ORDER BY started_at, gap_id"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def query_collection_runs(self, start: str, end: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM collection_runs
                WHERE ended_at >= ? AND started_at < ?
                ORDER BY started_at, run_id
                """,
                (start, end),
            ).fetchall()
        return [_decode_json_row(row, ("source_results_json",)) for row in rows]

    def query_rollups(self, start: str, end: str, node_role: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM rollups_5m WHERE bucket_start >= ? AND bucket_start < ?"
        params: list[Any] = [start, end]
        if node_role:
            sql += " AND node_role = ?"
            params.append(node_role)
        sql += " ORDER BY bucket_start"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def latest_collection_heartbeat(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(heartbeat_at) AS heartbeat_at FROM collection_runs").fetchone()
        return str(row["heartbeat_at"]) if row and row["heartbeat_at"] else None

    def set_service_heartbeat(self, service: str, status: str, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO service_heartbeats (service, heartbeat_at, status, detail)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(service) DO UPDATE SET
                    heartbeat_at = excluded.heartbeat_at,
                    status = excluded.status,
                    detail = excluded.detail
                """,
                (service, format_timestamp(utc_now()), status, detail[:500]),
            )

    def get_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM service_heartbeats WHERE service = ?",
                (service,),
            ).fetchone()
        return dict(row) if row else None

    def begin_report_run(self, run_id: str, report_date: str, started_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE report_runs
                SET completed_at = ?, status = 'failed', error_class = 'report_interrupted',
                    detail = 'superseded by a later run after the report lock was acquired'
                WHERE report_date = ? AND status = 'running'
                """,
                (started_at, report_date),
            )
            connection.execute(
                """
                INSERT INTO report_runs (run_id, report_date, started_at, status)
                VALUES (?, ?, ?, 'running')
                """,
                (run_id, report_date, started_at),
            )

    def finish_report_run(
        self,
        run_id: str,
        *,
        status: str,
        generation_mode: str | None = None,
        json_path: str | None = None,
        markdown_path: str | None = None,
        payload_digest: str | None = None,
        error_class: str = "",
        detail: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE report_runs
                SET completed_at = ?, status = ?, generation_mode = ?, json_path = ?,
                    markdown_path = ?, payload_digest = ?, error_class = ?, detail = ?
                WHERE run_id = ?
                """,
                (
                    format_timestamp(utc_now()),
                    status,
                    generation_mode,
                    json_path,
                    markdown_path,
                    payload_digest,
                    error_class,
                    detail[:1000],
                    run_id,
                ),
            )

    def latest_successful_report(self, report_date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM report_runs
                WHERE report_date = ? AND status = 'success'
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (report_date,),
            ).fetchone()
        return dict(row) if row else None

    def cleanup(self, raw_days: int = 7, rollup_days: int = 90) -> dict[str, int]:
        now = utc_now()
        raw_cutoff = format_timestamp(now - timedelta(days=raw_days))
        rollup_cutoff = format_timestamp(now - timedelta(days=rollup_days))
        deleted: dict[str, int] = {}
        with self.connect() as connection:
            for table, field, cutoff in (
                ("raw_log_events", "observed_at", raw_cutoff),
                ("node_samples", "observed_at", raw_cutoff),
                ("rollups_5m", "bucket_start", rollup_cutoff),
                ("telemetry_gaps", "ended_at", rollup_cutoff),
                ("collection_runs", "ended_at", rollup_cutoff),
                ("report_runs", "started_at", rollup_cutoff),
            ):
                result = connection.execute(f"DELETE FROM {table} WHERE {field} < ?", (cutoff,))
                deleted[table] = result.rowcount
        return deleted
