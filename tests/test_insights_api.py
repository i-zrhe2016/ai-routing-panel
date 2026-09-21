import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

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
        self.assertEqual(payload["traffic"]["days"], 14)

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


if __name__ == "__main__":
    unittest.main()
