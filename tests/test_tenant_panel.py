import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


def load_panel_module(temp_root):
    data_dir = temp_root / "data"
    logs_dir = temp_root / "logs"
    streams_dir = temp_root / "streams"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    streams_dir.mkdir(parents=True, exist_ok=True)

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

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["STREAMS_DIR"] = str(streams_dir)
    os.environ["STREAM_ACCESS_LOG"] = str(logs_dir / "stream-access.log")
    os.environ["NGINX_CONFIG_PATH"] = str(temp_root / "nginx.conf")
    os.environ["NGINX_PID_PATH"] = str(temp_root / "nginx.pid")
    os.environ["XRAY_CLIENT_CONFIG_PATH"] = str(client_config_path)
    os.environ["PANEL_PUBLIC_URL"] = "http://panel.example.com"
    os.environ["SEED_LISTEN_PORT"] = ""
    os.environ["PANEL_USERNAME"] = ""
    os.environ["PANEL_PASSWORD"] = ""
    os.environ["PANEL_SECRET_KEY"] = "test-secret-key"

    sys.modules.pop("app.panel", None)
    module = importlib.import_module("app.panel")
    module = importlib.reload(module)
    module.state.nginx_config_test = lambda: None
    module.state.nginx_running = lambda: False
    module.state.start_nginx = lambda: None
    module.state.nginx_reload = lambda: None
    module.state.nginx_stop = lambda: None
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

    def tenant_login(self, tenant_token, username, password, follow_redirects=False):
        return self.client.post(
            f"/tenant/{tenant_token}/login",
            data={"username": username, "password": password},
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
        self.assertIn(f"/tenant/{port_a['tenant_token']}/login", response.headers["Location"])

        login_page = self.client.get(f"/tenant/{port_a['tenant_token']}/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("登录租户面板", login_page.get_data(as_text=True))

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
        self.assertIn(f"/tenant/{port_b['tenant_token']}/login", other_tenant.headers["Location"])

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

        changed = self.panel.state.disable_auto_stopped_ports(reload_nginx=False)
        self.assertGreaterEqual(changed, 1)

        remaining_ports = self.panel.state.query_ports()
        self.assertFalse(any(item["id"] == port["id"] for item in remaining_ports))

        with self.panel.state.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM traffic_totals WHERE listen_port = ?",
                (port["listen_port"],),
            ).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
