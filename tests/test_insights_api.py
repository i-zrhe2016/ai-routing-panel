import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import LOCAL_TZ
from app.helpers import utc_iso_now


def load_panel_module(temp_root):
    """Boot the panel against a temporary data/xray sandbox.

    Mirrors the harness in ``test_tenant_panel`` so the insights endpoint is
    exercised through the real Flask app factory and the real SQLite database.
    """
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    reports_dir = xray_dir / "reports" / "hourly-domains"
    for directory in (data_dir, logs_dir, runtime_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    client_config_path = temp_root / "client-test.json"
    client_config_path.write_text(json.dumps({"outbounds": []}), encoding="utf-8")

    env_file_path = xray_dir / ".env"
    env_file_path.write_text(
        "\n".join(
            [
                "XRAY_LISTEN_HOST=0.0.0.0",
                "XRAY_LISTEN_PORT=443",
                "XRAY_PUBLIC_HOST=panel.example.com",
                "XRAY_CLIENT_UUID=11111111-1111-1111-1111-111111111111",
                "XRAY_FLOW=xtls-rprx-vision",
                "XRAY_REALITY_PRIVATE_KEY=private-key-example",
                "XRAY_REALITY_PUBLIC_KEY=public-key-example",
                "XRAY_REALITY_SHORT_ID=0123456789abcdef",
                "XRAY_SERVER_NAME=www.example.com",
                "XRAY_DEST=www.example.com:443",
                "XRAY_FINGERPRINT=chrome",
                "XRAY_LOGLEVEL=warning",
                "XRAY_NODE_TAG=test-node",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["XRAY_ENV_FILE_PATH"] = str(env_file_path)
    os.environ["XRAY_CONFIG_PATH"] = str(runtime_dir / "config.json")
    os.environ["XRAY_PANEL_PORTS_PATH"] = str(runtime_dir / "panel-ports.json")
    os.environ["XRAY_ACCESS_LOG_PATH"] = str(logs_dir / "access.log")
    os.environ["DATAPLANE_CONTAINER_NAME"] = "test-xray-container"
    os.environ["XRAY_CLIENT_CONFIG_PATH"] = str(client_config_path)
    os.environ["PANEL_PUBLIC_URL"] = "http://panel.example.com"
    os.environ["SEED_LISTEN_PORT"] = ""
    os.environ["PANEL_SECRET_KEY"] = "test-secret-key"
    os.environ["PROBE_ENABLED"] = "0"
    os.environ["PROBE_TEST_LISTEN_PORT"] = ""

    for module_name in sorted(
        (
            name
            for name in sys.modules
            if name == "app.panel"
            or name == "app.config"
            or name.startswith("app.config.")
            or name == "app.state"
            or name.startswith("app.state.")
            or name == "app.web"
            or name.startswith("app.web.")
        ),
        key=len,
        reverse=True,
    ):
        sys.modules.pop(module_name, None)
    module = importlib.import_module("app.panel")
    module = importlib.reload(module)
    module.state.render_xray_config = lambda: None
    module.state.xray_config_test = lambda: None
    module.state.restart_data_plane = lambda: True
    module.state.data_plane_configured = lambda: False
    module.state.data_plane_running = lambda: False
    module.state.init_db()
    return module


class InsightsApiTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root)
        self.client = self.panel.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def create_port(self, listen_port, note=""):
        payload = self.panel.state.validate_port_payload(
            {"listen_port": listen_port, "traffic_limit": "10G", "note": note}
        )
        self.panel.state.create_port(payload)

    def seed_daily_traffic(self, listen_port, day_offset, sent, received, connections=1):
        stat_date = (datetime.now(LOCAL_TZ).date() - timedelta(days=day_offset)).isoformat()
        with self.panel.state.connect() as conn:
            conn.execute(
                """
                INSERT INTO traffic_daily (
                    listen_port, stat_date, total_connections, total_bytes_sent, total_bytes_received
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(listen_port, stat_date) DO UPDATE SET
                    total_connections = total_connections + excluded.total_connections,
                    total_bytes_sent = total_bytes_sent + excluded.total_bytes_sent,
                    total_bytes_received = total_bytes_received + excluded.total_bytes_received
                """,
                (listen_port, stat_date, connections, sent, received),
            )
            conn.commit()

    def seed_probe_history(self, listen_port, results):
        with self.panel.state.connect() as conn:
            for index, reachable in enumerate(results):
                conn.execute(
                    """
                    INSERT INTO upstream_probe_history (listen_port, is_reachable, checked_at, failure_reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        listen_port,
                        1 if reachable else 0,
                        utc_iso_now(),
                        "" if reachable else "connection refused",
                    ),
                )
            conn.commit()

    def test_insights_endpoint_reports_empty_history_without_error(self):
        response = self.client.get("/api/insights")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        insights = payload["insights"]
        self.assertEqual(set(insights), {"generated_at", "hosts", "traffic", "probes", "failover_events"})
        self.assertEqual(insights["traffic"]["ports"], [])
        self.assertEqual(insights["probes"]["ports"], [])
        self.assertEqual(insights["failover_events"]["events"], [])
        self.assertTrue(insights["hosts"])
        self.assertEqual(insights["traffic"]["days"], 1)

    def test_insights_endpoint_returns_seeded_daily_traffic_series(self):
        self.create_port(31098, "客户A")
        self.seed_daily_traffic(31098, 0, 1024, 2048, connections=3)
        self.seed_daily_traffic(31098, 1, 512, 512, connections=1)

        payload = self.client.get("/api/insights?days=7").get_json()["insights"]
        traffic = payload["traffic"]
        self.assertEqual(traffic["days"], 7)
        self.assertEqual(len(traffic["dates"]), 7)
        self.assertEqual(len(traffic["series"]["total_bytes"]), 7)
        port = traffic["ports"][0]
        self.assertEqual(port["listen_port"], 31098)
        self.assertEqual(port["totals"]["total_bytes"], 1024 + 2048 + 512 + 512)
        self.assertEqual(port["totals"]["connections"], 4)
        self.assertEqual(port["today"]["total_bytes"], 1024 + 2048)
        self.assertEqual(len(port["series"]["total_bytes"]), 7)
        self.assertEqual(traffic["totals"]["total_bytes"], 1024 + 2048 + 512 + 512)

    def test_insights_endpoint_clamps_the_requested_window(self):
        payload = self.client.get("/api/insights?days=9999").get_json()["insights"]
        self.assertEqual(payload["traffic"]["days"], 30)
        payload = self.client.get("/api/insights?days=abc").get_json()["insights"]
        self.assertEqual(payload["traffic"]["days"], 1)
        for requested in ("0", "-7"):
            payload = self.client.get(f"/api/insights?days={requested}").get_json()["insights"]
            self.assertEqual(payload["traffic"]["days"], 1)

    def test_calendar_windows_include_today_and_exclude_previous_and_future_dates(self):
        self.create_port(31098, "客户A")
        self.create_port(31099, "客户B")
        local_tz = timezone(timedelta(hours=8))
        today = datetime(2026, 10, 2, 0, 0, tzinfo=local_tz)
        traffic_module = importlib.import_module("app.state.traffic")
        with self.panel.state.connect() as conn:
            for offset in (-1, 0, 1, 6, 7, 29, 30):
                stat_date = (today.date() - timedelta(days=offset)).isoformat()
                for listen_port, multiplier in ((31098, 1), (31099, 2)):
                    conn.execute(
                        "INSERT INTO traffic_daily VALUES (?, ?, ?, ?, ?)",
                        (listen_port, stat_date, 3 * multiplier, 100 * multiplier, 200 * multiplier),
                    )
            conn.commit()

        with patch.object(traffic_module, "LOCAL_TZ", local_tz), patch.object(traffic_module, "datetime") as clock:
            clock.now.side_effect = lambda tz: today.astimezone(tz)
            for days, included_rows in ((1, 1), (7, 3), (30, 5)):
                with self.subTest(days=days):
                    traffic = self.client.get(f"/api/insights?days={days}").get_json()["insights"]["traffic"]
                    dates = [(today.date() - timedelta(days=offset)).isoformat() for offset in reversed(range(days))]
                    self.assertEqual(traffic["dates"], dates)
                    self.assertEqual(traffic["range_start"], dates[0])
                    self.assertEqual(traffic["range_end"], "2026-10-02")
                    self.assertEqual(traffic["totals"], {
                        "bytes_sent": 300 * included_rows,
                        "bytes_received": 600 * included_rows,
                        "connections": 9 * included_rows,
                        "total_bytes": 900 * included_rows,
                    })
                    for port in traffic["ports"]:
                        multiplier = 1 if port["listen_port"] == 31098 else 2
                        self.assertEqual(port["totals"]["total_bytes"], 300 * multiplier * included_rows)
                        self.assertEqual(port["today"]["total_bytes"], 300 * multiplier)
                    for key, total in traffic["totals"].items():
                        self.assertEqual(total, sum(port["totals"][key] for port in traffic["ports"]))
                        self.assertEqual(total, sum(traffic["series"][key]))

    def test_dashboard_reports_beijing_timezone(self):
        dashboard = self.client.get("/api/dashboard").get_json()["dashboard"]
        self.assertEqual(dashboard["meta"]["timezone_label"], "北京时间（UTC+08:00）")

    def test_today_changes_at_beijing_midnight(self):
        traffic_module = importlib.import_module("app.state.traffic")
        with patch.object(traffic_module, "datetime") as clock:
            for instant, expected_date in (
                (datetime(2026, 10, 1, 15, 59, 59, tzinfo=timezone.utc), "2026-10-01"),
                (datetime(2026, 10, 1, 16, 0, 0, tzinfo=timezone.utc), "2026-10-02"),
            ):
                clock.now.side_effect = lambda tz, instant=instant: instant.astimezone(tz)
                traffic = self.client.get("/api/insights").get_json()["insights"]["traffic"]
                self.assertEqual(traffic["dates"], [expected_date])

    def test_access_and_byte_accounting_use_the_local_calendar_date(self):
        self.create_port(31098, "客户A")
        traffic_module = importlib.import_module("app.state.traffic")
        service = traffic_module.TrafficService(
            stats_reader=SimpleNamespace(read_xray_traffic_stats=lambda: {
                31098: {"bytes_sent": 100, "bytes_received": 200},
            }),
        )
        for source_time, instant, expected_date in (
            ("2026/10/01 15:59:59", datetime(2026, 10, 1, 15, 59, 59, tzinfo=timezone.utc), "2026-10-01"),
            ("2026/10/01 16:00:00", datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc), "2026-10-02"),
        ):
            with self.subTest(source_time=source_time):
                listen_port, stat_date, seen_at = service.parse_xray_access_log_line(
                    f"{source_time} from 192.0.2.1:12345 accepted tcp:example.com:443 [panel-31098 >> direct]"
                )
                self.assertEqual(listen_port, 31098)
                self.assertEqual(stat_date, expected_date)
                self.assertEqual(seen_at, instant.isoformat(timespec="seconds"))
                with patch.object(traffic_module, "utc_now", return_value=instant), \
                        self.panel.state.connect() as conn:
                    conn.execute("DELETE FROM traffic_daily")
                    service.sync_xray_traffic_stats_in_tx(conn)
                    row = conn.execute("SELECT * FROM traffic_daily WHERE listen_port = 31098").fetchone()
                    self.assertEqual(row["stat_date"], expected_date)
                    self.assertEqual(row["total_bytes_sent"], 100)
                    self.assertEqual(row["total_bytes_received"], 200)
                    last_seen = conn.execute("SELECT last_seen FROM traffic_totals WHERE listen_port = 31098").fetchone()[0]
                    self.assertEqual(last_seen, instant.isoformat(timespec="seconds"))

    def test_insights_endpoint_summarizes_probe_history(self):
        self.create_port(31098, "客户A")
        self.seed_probe_history(31098, [True, True, False, True])

        probes = self.client.get("/api/insights").get_json()["insights"]["probes"]
        port = probes["ports"][0]
        self.assertEqual(port["listen_port"], 31098)
        self.assertEqual(port["total_checks"], 4)
        self.assertEqual(port["unhealthy_count"], 1)
        self.assertEqual(port["uptime_ratio"], "75.0")
        self.assertEqual(port["status"], "healthy")
        self.assertEqual(len(port["checks"]), 4)

    def test_insights_endpoint_returns_newest_failover_events_first(self):
        with self.panel.state.connect() as conn:
            conn.execute(
                """
                INSERT INTO dns_failover_history (event_type, event_status, target, detail, created_at)
                VALUES ('probe', 'error', 'primary', 'probe failed', ?)
                """,
                (utc_iso_now(),),
            )
            conn.execute(
                """
                INSERT INTO dns_failover_history (event_type, event_status, target, detail, created_at)
                VALUES ('switch', 'ok', 'backup', '自动切换完成。', ?)
                """,
                (utc_iso_now(),),
            )
            conn.commit()

        events = self.client.get("/api/insights").get_json()["insights"]["failover_events"]["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "switch")
        self.assertEqual(events[0]["status_label"], "成功")
        self.assertEqual(events[0]["event_type_label"], "切换")
        self.assertEqual(events[1]["event_type"], "probe")

    def test_insights_endpoint_does_not_mutate_dashboard_counters(self):
        self.create_port(31098, "客户A")
        self.seed_daily_traffic(31098, 0, 1024, 2048, connections=3)
        before = self.client.get("/api/dashboard").get_json()["dashboard"]["summary"]

        self.client.get("/api/insights")

        after = self.client.get("/api/dashboard").get_json()["dashboard"]["summary"]
        self.assertEqual(before["total_bytes_sent"], after["total_bytes_sent"])
        self.assertEqual(before["total_bytes_received"], after["total_bytes_received"])
        self.assertEqual(before["total_connections"], after["total_connections"])


class ProbeIncidentsApiTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root)
        self.client = self.panel.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_legacy_diagnosis_errors_are_chinese_without_rewriting_failed_records(self):
        incidents = self.panel.state.incidents
        for _ in range(5):
            incident_id = incidents.record("diagnostics", "legacy-target", {"ok": False, "error": "request failed"})
        incidents.finish(incident_id, error="Codex diagnosis unavailable or invalid")
        with incidents.database.connect() as conn:
            before = dict(conn.execute("SELECT * FROM probe_incidents WHERE id=?", (incident_id,)).fetchone())
        events = incidents.events(incident_id)
        for _ in range(2):
            response = self.client.get("/api/probe-incidents")
            self.assertEqual(response.status_code, 200)
            item = response.get_json()["incidents"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["diagnosis_error"], "Codex 暂不可用或返回结果无效")
            report = self.client.get(f"/api/probe-incidents/{incident_id}/report").get_json()["report"]
            self.assertIn("## 分析不可用\n\nCodex 暂不可用或返回结果无效", report)
            self.assertIn("未获得模型诊断。观测到的失败不足以确定根因。", report)
        with incidents.database.connect() as conn:
            after = dict(conn.execute("SELECT * FROM probe_incidents WHERE id=?", (incident_id,)).fetchone())
        self.assertEqual(before["diagnosis_error"], "Codex diagnosis unavailable or invalid")
        self.assertEqual(before["status"], "failed")
        self.assertEqual(before, after)
        self.assertEqual(events, incidents.events(incident_id))

    def test_incident_records_and_report_are_internal_safe_read_api(self):
        for _ in range(4):
            self.assertIsNone(self.panel.state.incidents.record("diagnostics", "target:443", {"ok": False, "probe_origin": "dedicated-probe", "error": "request failed"}))
            self.assertEqual(self.client.get("/api/probe-incidents").get_json()["incidents"], [])
        incident_id = self.panel.state.incidents.record("diagnostics", "target:443", {"ok": False, "probe_origin": "dedicated-probe", "error": "request failed"})
        response = self.client.get("/api/probe-incidents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        item = response.get_json()["incidents"][0]
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["occurrences"], 5)
        self.assertEqual(item["target"], "target:443")
        self.assertEqual(item["probe_origin"], "dedicated-probe")
        self.assertEqual(self.client.get(f"/api/probe-incidents/{incident_id}/report").status_code, 404)
        self.panel.state.incidents.finish(incident_id, {"diagnosis": '<script>alert("x")</script>', "uncertainty": "Insufficient evidence", "recommended_checks": ["Check target"]})
        response = self.client.get(f"/api/probe-incidents/{incident_id}/report")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script>alert("x")</script>', response.get_json()["report"])
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get("/api/probe-incidents/not-an-id/report").status_code, 404)
        self.assertEqual(self.client.get("/api/probe-incidents", environ_base={"REMOTE_ADDR": "203.0.113.10"}).status_code, 403)
        self.assertEqual(self.client.get(f"/api/probe-incidents/{incident_id}/report", environ_base={"REMOTE_ADDR": "203.0.113.10"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
