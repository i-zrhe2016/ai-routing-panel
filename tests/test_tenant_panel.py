import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def load_panel_module(temp_root, panel_username="", panel_password=""):
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    reports_dir = xray_dir / "reports" / "hourly-domains"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    client_config_path = temp_root / "client-test.json"
    client_config_path.write_text(
        json.dumps(
            {
                "outbounds": [
                    {
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": "example.com",
                                    "users": [
                                        {
                                            "id": "11111111-1111-1111-1111-111111111111",
                                            "flow": "xtls-rprx-vision",
                                        }
                                    ],
                                }
                            ]
                        },
                        "streamSettings": {
                            "realitySettings": {
                                "serverName": "www.example.com",
                                "publicKey": "pubkey-example",
                                "shortId": "0123456789abcdef",
                                "fingerprint": "chrome",
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

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
    os.environ["PANEL_USERNAME"] = panel_username
    os.environ["PANEL_PASSWORD"] = panel_password
    os.environ["PANEL_SECRET_KEY"] = "test-secret-key"

    sys.modules.pop("app.panel", None)
    module = importlib.import_module("app.panel")
    module = importlib.reload(module)
    module.state.render_xray_config = lambda: None
    module.state.xray_config_test = lambda: None
    module.state.restart_data_plane = lambda: None
    module.state.data_plane_configured = lambda: False
    module.state.data_plane_running = lambda: False
    module.state.init_db()
    return module


class TenantPanelTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root)
        self.client = self.panel.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def create_port(self, listen_port, note):
        payload = self.panel.state.validate_port_payload(
            {
                "listen_port": listen_port,
                "traffic_limit": "10G",
                "note": note,
            }
        )
        self.panel.state.create_port(payload)
        for port in self.panel.state.query_ports():
            if port["listen_port"] == listen_port:
                return port
        self.fail(f"port {listen_port} was not created")

    def seed_ai_domain_dashboard(self):
        report_path = self.panel.state.data_plane.config.source_ai_report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-06-18T00:00:00+00:00",
                    "window_start": "2026-06-17T23:00:00+00:00",
                    "window_end": "2026-06-18T00:00:00+00:00",
                    "unique_domains": 3,
                    "domains": [
                        {
                            "domain": "openai.com",
                            "hits": 6,
                            "first_seen": "2026-06-17T23:10:00+00:00",
                            "last_seen": "2026-06-17T23:58:00+00:00",
                            "protocols": ["tcp", "tls"],
                            "classification": "ai",
                            "reason": "known ai",
                        },
                        {
                            "domain": "example.com",
                            "hits": 2,
                            "first_seen": "2026-06-17T23:20:00+00:00",
                            "last_seen": "2026-06-17T23:30:00+00:00",
                            "protocols": ["tcp"],
                            "classification": "not_ai",
                            "reason": "normal site",
                        },
                    ],
                    "protocols": [{"protocol": "tcp", "hits": 8}],
                    "ai_target": {"upstream_host": "ai.example.com", "upstream_port": 443},
                    "panel_target": {
                        "listen_port": 31098,
                        "upstream_host": "panel.example.com",
                        "upstream_port": 443,
                    },
                    "route_status": {
                        "status": "applied",
                        "reason": "",
                        "config_changed": True,
                        "pending_domains_without_classifier": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.panel.state.replace_ai_domains_snapshot(
            [
                {
                    "domain": "openai.com",
                    "classification": "ai",
                    "reason": "known ai",
                    "source": "codex",
                    "model": "gpt-5.5",
                    "first_seen": "2026-06-17T23:10:00+00:00",
                    "last_seen": "2026-06-17T23:58:00+00:00",
                    "total_hits": 9,
                    "last_protocols": "[\"tcp\", \"tls\"]",
                    "last_report_window_start": "2026-06-17T23:00:00+00:00",
                    "last_report_window_end": "2026-06-18T00:00:00+00:00",
                    "updated_at": "2026-06-18T00:00:00+00:00",
                }
            ]
        )

    def assert_login_redirect_target(self, response, expected_next):
        location = response.headers["Location"]
        parsed = urlparse(location)
        self.assertEqual(parsed.path, "/login")
        self.assertEqual(parse_qs(parsed.query).get("next"), [expected_next])

    def tenant_login(self, tenant_token, username, password, follow_redirects=False):
        return self.client.post(
            "/login",
            data={"username": username, "password": password, "next": f"/tenant/{tenant_token}"},
            follow_redirects=follow_redirects,
        )

    def test_tenant_panel_login_is_isolated_per_port(self):
        port_a = self.create_port(31001, "Tenant A")
        port_b = self.create_port(31002, "Tenant B")

        self.assertTrue(port_a["tenant_token"])
        self.assertTrue(port_a["subscription_token"])
        self.assertTrue(port_a["tenant_username"])
        self.assertTrue(port_a["tenant_password"])
        self.assertNotEqual(port_a["tenant_token"], port_b["tenant_token"])
        self.assertNotEqual(port_a["subscription_token"], port_b["subscription_token"])
        self.assertNotEqual(port_a["tenant_username"], port_b["tenant_username"])

        response = self.client.get(f"/tenant/{port_a['tenant_token']}")
        self.assertEqual(response.status_code, 303)
        self.assert_login_redirect_target(response, f"/tenant/{port_a['tenant_token']}")

        legacy_login = self.client.get(f"/tenant/{port_a['tenant_token']}/login")
        self.assertEqual(legacy_login.status_code, 303)
        self.assert_login_redirect_target(legacy_login, f"/tenant/{port_a['tenant_token']}")

        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("统一登录入口", login_page.get_data(as_text=True))

        wrong_login = self.tenant_login(port_a["tenant_token"], port_a["tenant_username"], "wrong-password")
        self.assertEqual(wrong_login.status_code, 401)

        tenant_login = self.tenant_login(
            port_a["tenant_token"],
            port_a["tenant_username"],
            port_a["tenant_password"],
            follow_redirects=True,
        )
        self.assertEqual(tenant_login.status_code, 200)
        body = tenant_login.get_data(as_text=True)
        self.assertIn("Tenant A", body)
        self.assertIn("31001", body)
        self.assertNotIn("Tenant B", body)
        self.assertNotIn("31002", body)

        other_tenant = self.client.get(f"/tenant/{port_b['tenant_token']}")
        self.assertEqual(other_tenant.status_code, 303)
        self.assert_login_redirect_target(other_tenant, f"/tenant/{port_b['tenant_token']}")

        missing = self.client.get("/tenant/not-a-real-token")
        self.assertEqual(missing.status_code, 404)

    def test_rotating_tokens_invalidates_old_tenant_and_subscription_links(self):
        port = self.create_port(32001, "Tenant Rotate")

        old_tenant_token = port["tenant_token"]
        old_subscription_token = port["subscription_token"]

        rotate_tenant = self.client.post(f"/api/ports/{port['id']}/rotate-tenant-token")
        self.assertEqual(rotate_tenant.status_code, 200)
        rotate_subscription = self.client.post(f"/api/ports/{port['id']}/rotate-subscription-token")
        self.assertEqual(rotate_subscription.status_code, 200)

        updated_port = next(item for item in self.panel.state.query_ports() if item["id"] == port["id"])
        self.assertNotEqual(updated_port["tenant_token"], old_tenant_token)
        self.assertNotEqual(updated_port["subscription_token"], old_subscription_token)

        old_tenant_response = self.client.get(f"/tenant/{old_tenant_token}")
        self.assertEqual(old_tenant_response.status_code, 404)
        new_tenant_response = self.client.get(f"/tenant/{updated_port['tenant_token']}")
        self.assertEqual(new_tenant_response.status_code, 303)

        old_subscription_response = self.client.get(
            f"/tenant-subscriptions/{old_subscription_token}/clash"
        )
        self.assertEqual(old_subscription_response.status_code, 404)

        new_subscription_response = self.client.get(
            f"/tenant-subscriptions/{updated_port['subscription_token']}/clash"
        )
        self.assertEqual(new_subscription_response.status_code, 200)
        body = new_subscription_response.get_data(as_text=True)
        self.assertIn("port: 32001", body)

    def test_expired_ports_are_deleted_during_maintenance(self):
        port = self.create_port(33001, "Tenant Expired")

        with self.panel.state.connect() as conn:
            conn.execute(
                "UPDATE ports SET expires_at = ?, updated_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", port["id"]),
            )
            conn.execute(
                """
                INSERT INTO traffic_totals (
                    listen_port, total_connections, total_bytes_sent, total_bytes_received, last_seen
                ) VALUES (?, 3, 10, 20, ?)
                """,
                (port["listen_port"], "2000-01-01T00:00:00+00:00"),
            )
            conn.commit()

        changed = self.panel.state.disable_auto_stopped_ports(reload_xray=False)
        self.assertGreaterEqual(changed, 1)

        remaining_ports = self.panel.state.query_ports()
        self.assertFalse(any(item["id"] == port["id"] for item in remaining_ports))

        with self.panel.state.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM traffic_totals WHERE listen_port = ?",
                (port["listen_port"],),
            ).fetchone()
        self.assertIsNone(row)

    def test_api_dashboard_includes_ai_domain_summary(self):
        self.seed_ai_domain_dashboard()

        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        ai_stats = payload["dashboard"]["meta"]["ai_domain_stats"]
        self.assertTrue(ai_stats["available"])
        self.assertEqual(ai_stats["current_ai_domains"], 1)
        self.assertEqual(ai_stats["total_ai_domains"], 1)
        self.assertEqual(ai_stats["route_status"], "applied")
        self.assertEqual(payload["dashboard"]["meta"]["ai_domain_dashboard_url"], "/ai-domain-dashboard")

    def test_ai_domain_dashboard_renders_mirrored_report(self):
        self.seed_ai_domain_dashboard()

        response = self.client.get("/ai-domain-dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("AI 域名统计", body)
        self.assertIn("openai.com", body)
        self.assertIn("已应用 AI 路由", body)
        self.assertIn("2026-06-18 00:00:00", body)


class UnifiedAdminLoginTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root, panel_username="admin-user", panel_password="admin-pass-123")
        self.client = self.panel.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_admin_login_uses_unified_login_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        parsed = urlparse(location)
        self.assertEqual(parsed.path, "/login")
        self.assertEqual(parse_qs(parsed.query).get("next"), ["/"])

        login_page = self.client.get("/login?next=/")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("统一登录入口", login_page.get_data(as_text=True))

        failed = self.client.post(
            "/login",
            data={"username": "admin-user", "password": "wrong-password", "next": "/"},
        )
        self.assertEqual(failed.status_code, 401)

        logged_in = self.client.post(
            "/login",
            data={"username": "admin-user", "password": "admin-pass-123", "next": "/"},
            follow_redirects=True,
        )
        self.assertEqual(logged_in.status_code, 200)
        self.assertIn("xray-routing-panel", logged_in.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
