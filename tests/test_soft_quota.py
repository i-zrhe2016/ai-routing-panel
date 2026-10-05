"""Soft-quota behavior: cumulative units, admission and manual state priority."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.helpers import parse_data_size, status_payload
from app.state.traffic import TrafficService
from app.xray.apply import XrayApplyService


def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE ports(id INTEGER PRIMARY KEY, listen_port INTEGER, enabled INTEGER,
            expires_at TEXT, traffic_limit_bytes INTEGER, updated_at TEXT);
        CREATE TABLE traffic_totals(listen_port INTEGER PRIMARY KEY,
            total_bytes_sent INTEGER, total_bytes_received INTEGER);
        CREATE TABLE traffic_daily(listen_port INTEGER, total_bytes_sent INTEGER,
            total_bytes_received INTEGER);
    """
    )
    return conn


def test_100g_boundary_keeps_credentials_and_enabled_while_manual_disable_wins():
    quota = parse_data_size("100G", "quota")
    assert quota == 107374182400
    conn = connection()
    for ident, usage, enabled in [(1, quota - 1, 1), (2, quota, 1), (3, quota + 1, 1), (4, quota, 0)]:
        conn.execute('INSERT INTO ports VALUES (?, ?, ?, NULL, ?, "")', (ident, 31000 + ident, enabled, quota))
        conn.execute("INSERT INTO traffic_totals VALUES (?, ?, ?)", (31000 + ident, usage // 2, usage - usage // 2))
    svc = XrayApplyService(ports_service=SimpleNamespace(cleanup_expired_ports_in_tx=lambda _: 0))
    assert svc.disable_auto_stopped_ports_in_tx(conn) == 0
    payload = svc.render_panel_ports_payload(conn)
    assert payload["ports"] == [31001, 31002, 31003]
    limits = payload["accountLimits"]
    assert limits["rateBitsPerSecond"] == 5_000_000
    assert [a["port"] for a in limits["accounts"] if a["throttled"]] == [31002, 31003]
    assert conn.execute("SELECT enabled FROM ports WHERE id=2").fetchone()[0] == 1
    assert status_payload(True, None, quota, quota - 1)["code"] == "active"
    throttled = status_payload(True, None, quota, quota)
    assert throttled["code"] == "throttled"
    assert throttled["rateBitsPerSecond"] == 5_000_000
    assert status_payload(False, None, quota, quota)["code"] == "disabled"
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert status_payload(True, expired, quota, quota)["code"] == "expired"


def test_reset_never_enables_manual_or_historical_disabled_account():
    conn = connection()
    conn.execute('INSERT INTO ports VALUES (1,31001,0,NULL,100,"")')
    conn.execute("INSERT INTO traffic_totals VALUES (31001,75,75)")
    conn.execute("INSERT INTO traffic_daily VALUES (31001,75,75)")
    conn.commit()
    svc = TrafficService(renderer=SimpleNamespace(apply_mutation=lambda operation: operation(conn)))
    assert svc.reset_port_traffic(1) is False
    assert conn.execute("SELECT enabled FROM ports").fetchone()[0] == 0
    assert tuple(conn.execute("SELECT total_bytes_sent,total_bytes_received FROM traffic_totals").fetchone()) == (0, 0)


def test_increased_quota_removes_manifest_limit_without_resetting_usage():
    conn = connection()
    conn.execute('INSERT INTO ports VALUES (1,31001,1,NULL,100,"")')
    conn.execute("INSERT INTO traffic_totals VALUES (31001,75,75)")
    svc = XrayApplyService(ports_service=SimpleNamespace(cleanup_expired_ports_in_tx=lambda _: 0))
    assert svc.render_panel_ports_payload(conn)["accountLimits"]["accounts"][0]["throttled"]
    conn.execute("UPDATE ports SET traffic_limit_bytes=200 WHERE id=1")
    assert not svc.render_panel_ports_payload(conn)["accountLimits"]["accounts"][0]["throttled"]
    assert tuple(conn.execute("SELECT total_bytes_sent,total_bytes_received FROM traffic_totals").fetchone()) == (
        75,
        75,
    )
