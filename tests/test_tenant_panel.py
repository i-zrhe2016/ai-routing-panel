import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch


def load_panel_module(temp_root, probe_enabled=False, probe_test_listen_port=""):
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
                                    "port": 443,
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
    os.environ["PANEL_SECRET_KEY"] = "test-secret-key"
    os.environ["PROBE_ENABLED"] = "1" if probe_enabled else "0"
    os.environ["PROBE_TEST_LISTEN_PORT"] = str(probe_test_listen_port or "")

    # app.panel imports configuration, builds the application graph and injects
    # it into the Web factory at module import time. Remove the whole graph so
    # each test gets its own temporary database and runtime paths instead of
    # stale constants from the previous test case.
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

    def test_admin_shell_disables_cache_for_asset_version_rollout(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")
        body = response.get_data(as_text=True)
        self.assertIn("admin.js?v=20261003-main-integration", body)
        self.assertIn("admin.css?v=20261003-main-integration", body)

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

    def test_ai_routing_status_exposes_cache_probe_and_effective_domain_routes(self):
        self.seed_ai_domain_dashboard()
        report_path = self.panel.state.data_plane.config.source_ai_report_path
        report = json.loads(report_path.read_text())
        report["routing_checked_at"] = "2026-06-18T00:10:00+00:00"
        report["route_status"].update({"status": "fallback_to_primary", "reason": "ai_upstream_unreachable", "config_apply_status": "direct", "health_interval_seconds": 30, "classification_interval_seconds": 3600})
        report["domains"][0].update({"source": "codex", "traffic_route": {"outbound_tag": "direct", "status": "fallback_to_primary", "reason": "ai_upstream_unreachable"}})
        report["domains"][1]["traffic_route"] = {"outbound_tag": "direct", "status": "applied"}
        report_path.write_text(json.dumps(report))
        from app.xray.ai_routing.repository import save_classifications
        save_classifications(self.panel.state.database.path, {"domains": {"openai.com": {"classification": "ai"}, "example.com": {"classification": "not_ai"}}})
        with patch.object(self.panel.state.ai_routing, "ai_routing_manual_state", return_value={"mode": "auto", "mode_label": "自动探测", "updated_at": "", "updated_at_display": "暂无", "candidate_count": 1, "candidates": [{"checked_at": "invalid", "is_reachable": False}]}):
            status = self.panel.state.ai_routing_status()
        self.assertEqual(status["route_status"], "fallback_to_primary")
        self.assertEqual(status["config_apply_status"], "direct")
        self.assertEqual(status["last_probe_at_display"], "暂无")
        self.assertEqual(status["health_interval_seconds"], 30)
        self.assertEqual(status["classification_interval_seconds"], 3600)
        self.assertEqual(status["classification_cache"], {"status": "available", "total_domains": 2, "ai_domains": 1, "non_ai_domains": 1})
        self.assertEqual(status["recent_domains"][0]["traffic_route"]["mode"], "fallback")
        self.assertEqual(status["recent_domains"][0]["source"], "codex")
        self.assertEqual(status["recent_domains"][1]["traffic_route"]["mode"], "direct")
        self.assertNotEqual(status["routing_checked_at_display"], status["report_generated_at_display"])

    def tenant_login(self, tenant_token, username, password, follow_redirects=False):
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "next": f"/tenant/{tenant_token}",
                "csrf_token": self.csrf_token(),
            },
            follow_redirects=follow_redirects,
        )

    def csrf_token(self):
        with self.client.session_transaction() as session:
            csrf_value = session.get("csrf_token")
            if not csrf_value:
                csrf_value = "test-csrf-token"
                session["csrf_token"] = csrf_value
            return csrf_value

    def test_port_api_create_returns_snapshot_without_second_maintenance_pass(self):
        original_sync = self.panel.state.sync_traffic_state
        sync_calls = []

        def track_maintenance_pass():
            sync_calls.append(True)
            return original_sync()

        self.panel.state.sync_traffic_state = track_maintenance_pass
        response = self.client.post(
            "/api/ports",
            json={"listen_port": 31200, "traffic_limit": "10G", "note": "API create"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(sync_calls, [])
        self.assertIn(31200, [port["listen_port"] for port in body["dashboard"]["ports"]])
        self.assertEqual(
            [port["listen_port"] for port in self.panel.state.query_ports()].count(31200),
            1,
        )

    def test_port_api_duplicate_listen_port_is_idempotent(self):
        self.create_port(31201, "Existing")

        response = self.client.post(
            "/api/ports",
            json={"listen_port": 31201, "traffic_limit": "10G", "note": "Duplicate"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "监听端口已存在，已选中已有端口。")
        self.assertEqual(body["level"], "info")
        self.assertIn(31201, [port["listen_port"] for port in body["dashboard"]["ports"]])
        self.assertEqual(
            [port["listen_port"] for port in self.panel.state.query_ports()].count(31201),
            1,
        )

    def test_port_api_auto_allocates_for_missing_or_blank_port(self):
        with patch.multiple("app.state.ports", COMMERCE_AUTO_PORT_START=35000, COMMERCE_AUTO_PORT_END=35005):
            for offset, value in enumerate(({}, {"listen_port": ""}, {"listen_port": "  "}, {"listen_port": None})):
                response = self.client.post("/api/ports", json={**value, "note": f"Auto tenant {offset}"})
                self.assertEqual(response.status_code, 201)
                body = response.get_json()
                created = next(port for port in body["dashboard"]["ports"] if port["id"] == body["created_port_id"])
                self.assertEqual(created["listen_port"], 35000 + offset)
                self.assertEqual(created["note"], f"Auto tenant {offset}")
                self.assertTrue(created["tenant_username"])
                self.assertTrue(created["tenant_password"])
                self.assertTrue(created["access"]["tenant_subscription_v2ray_url"])

    def test_port_api_auto_allocation_skips_disabled_ports_and_reuses_deleted_gap(self):
        first = self.create_port(35000, "Disabled tenant")
        self.panel.state.toggle_port(first["id"])
        gap = self.create_port(35001, "Deleted tenant")
        self.create_port(35002, "Existing tenant")
        self.panel.state.delete_port(gap["id"])
        with patch.multiple("app.state.ports", COMMERCE_AUTO_PORT_START=35000, COMMERCE_AUTO_PORT_END=35002):
            response = self.client.post("/api/ports", json={"note": "Auto tenant"})
        self.assertEqual(response.status_code, 201)
        ports = response.get_json()["dashboard"]["ports"]
        created = next(port for port in ports if port["id"] == response.get_json()["created_port_id"])
        self.assertEqual(created["listen_port"], 35001)
        self.assertEqual(len(ports), 3)

    def test_port_api_auto_allocation_handles_range_boundary_and_exhaustion(self):
        with patch.multiple("app.state.ports", COMMERCE_AUTO_PORT_START=65535, COMMERCE_AUTO_PORT_END=65535):
            first = self.client.post("/api/ports", json={"note": "Last port"})
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.get_json()["dashboard"]["ports"][0]["listen_port"], 65535)
            second = self.client.post("/api/ports", json={"note": "No space"})
        self.assertEqual(second.status_code, 400)
        self.assertFalse(second.get_json()["ok"])
        self.assertIn("范围已耗尽", second.get_json()["message"])
        self.assertEqual(len(self.panel.state.query_ports()), 1)

    def test_port_api_auto_allocation_requires_configured_range(self):
        with patch.multiple("app.state.ports", COMMERCE_AUTO_PORT_START=None, COMMERCE_AUTO_PORT_END=None):
            response = self.client.post("/api/ports", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("范围未配置", response.get_json()["message"])
        self.assertEqual(self.panel.state.query_ports(), [])

    def test_port_api_auto_allocation_rolls_back_when_config_validation_fails(self):
        with patch.multiple("app.state.ports", COMMERCE_AUTO_PORT_START=35000, COMMERCE_AUTO_PORT_END=35000):
            with patch.object(self.panel.state, "xray_config_test", side_effect=RuntimeError("Invalid config")):
                response = self.client.post("/api/ports", json={"note": "Failed tenant"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.panel.state.query_ports(), [])
            retry = self.client.post("/api/ports", json={"note": "Valid tenant"})
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.get_json()["dashboard"]["ports"][0]["listen_port"], 35000)

    def test_port_api_update_still_requires_an_explicit_port(self):
        port = self.create_port(35000, "Existing tenant")
        response = self.client.put(f"/api/ports/{port['id']}", json={"note": "Update"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.panel.state.query_ports()[0]["listen_port"], 35000)

    def test_port_api_invalid_explicit_port_is_not_auto_allocated(self):
        for value in (0, 65536, "invalid"):
            response = self.client.post("/api/ports", json={"listen_port": value})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.panel.state.query_ports(), [])

    def test_port_api_delete_is_idempotent_for_missing_record(self):
        port = self.create_port(31202, "Delete once")

        first = self.client.delete(f"/api/ports/{port['id']}")
        self.assertEqual(first.status_code, 200)
        self.assertNotIn(31202, [item["listen_port"] for item in first.get_json()["dashboard"]["ports"]])

        second = self.client.delete(f"/api/ports/{port['id']}")
        self.assertEqual(second.status_code, 200)
        body = second.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "端口已不存在，列表已刷新。")
        self.assertNotIn(31202, [item["listen_port"] for item in body["dashboard"]["ports"]])

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

        # The /tenant/<token> page is now a public portal shell (token mode); the
        # SPA boots with the tenant_token and fetches the gated subscription API.
        shell = self.client.get(f"/tenant/{port_a['tenant_token']}")
        self.assertEqual(shell.status_code, 200)
        shell_body = shell.get_data(as_text=True)
        self.assertIn(port_a["tenant_token"], shell_body)
        self.assertIn("/static/portal/portal.js", shell_body)

        # Without a tenant session the subscription API returns a JSON 401.
        unauth = self.client.get(f"/api/tenant/{port_a['tenant_token']}/subscription")
        self.assertEqual(unauth.status_code, 401)
        self.assertFalse(unauth.get_json()["ok"])

        legacy_login = self.client.get(f"/tenant/{port_a['tenant_token']}/login")
        self.assertEqual(legacy_login.status_code, 303)
        self.assert_login_redirect_target(legacy_login, f"/tenant/{port_a['tenant_token']}")

        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("租户登录", login_page.get_data(as_text=True))

        # Wrong credentials via the per-port JSON login -> 401.
        wrong = self.client.post(
            f"/api/tenant/{port_a['tenant_token']}/login",
            json={"username": port_a["tenant_username"], "password": "wrong-password"},
            headers={"X-CSRF-Token": self.csrf_token()},
        )
        self.assertEqual(wrong.status_code, 401)

        # Correct credentials authenticate the per-port tenant session.
        ok = self.client.post(
            f"/api/tenant/{port_a['tenant_token']}/login",
            json={"username": port_a["tenant_username"], "password": port_a["tenant_password"]},
            headers={"X-CSRF-Token": self.csrf_token()},
        )
        self.assertEqual(ok.status_code, 200)

        sub_a = self.client.get(f"/api/tenant/{port_a['tenant_token']}/subscription")
        self.assertEqual(sub_a.status_code, 200)
        data_a = sub_a.get_json()["data"]["subscription"]
        self.assertEqual(data_a["listen_port"], 31001)
        self.assertIn("Tenant A", data_a.get("note") or "")

        with self.panel.app.test_request_context("/"):
            from app.web.core import build_tenant_dashboard_state
            tenant_dashboard = build_tenant_dashboard_state(port_a["tenant_token"])
            self.assertEqual(tenant_dashboard["meta"]["timezone_label"], "北京时间（UTC+08:00）")

        # Isolation: the port_a tenant session cannot read port_b's subscription.
        sub_b = self.client.get(f"/api/tenant/{port_b['tenant_token']}/subscription")
        self.assertEqual(sub_b.status_code, 401)

        # The other port's shell is still public, but its data stays gated (above).
        other_shell = self.client.get(f"/tenant/{port_b['tenant_token']}")
        self.assertEqual(other_shell.status_code, 200)

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
        # The rotated token now serves the public portal shell (token mode).
        new_tenant_response = self.client.get(f"/tenant/{updated_port['tenant_token']}")
        self.assertEqual(new_tenant_response.status_code, 200)

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

    def test_inactive_port_does_not_serve_a_subscription(self):
        disabled = self.create_port(32002, "Tenant Disabled")
        expired = self.create_port(32003, "Tenant Expired")

        with self.panel.state.connect() as conn:
            conn.execute(
                "UPDATE ports SET enabled = 0 WHERE id = ?",
                (disabled["id"],),
            )
            conn.execute(
                "UPDATE ports SET expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", expired["id"]),
            )
            conn.commit()

        for port in (disabled, expired):
            response = self.client.get(
                f"/tenant-subscriptions/{port['subscription_token']}/clash"
            )
            self.assertEqual(response.status_code, 404)

    def test_legacy_and_tenant_subscriptions_share_rules_and_unified_credentials(self):
        port = self.create_port(32007, 'Common services: "test"')
        from app.web import core

        profile, error = core.parse_xray_client_profile()
        self.assertEqual(error, "")
        user_uuid = "22222222-2222-2222-2222-222222222222"
        profile.update(unified_port=443, user_uuids={"32007": user_uuid})
        legacy_path = f"/{self.panel.state.get_subscription_token()}/32007"
        tenant_path = f"/tenant-subscriptions/{port['subscription_token']}"

        with patch.object(core, "parse_xray_client_profile", return_value=(profile, "")):
            responses = [self.client.get(path) for path in (
                legacy_path, legacy_path + "/clash", tenant_path, tenant_path + "/clash",
            )]
            self.assertTrue(all(response.status_code == 200 for response in responses))
            bodies = [response.get_data(as_text=True) for response in responses]
            self.assertTrue(all(body == bodies[0] for body in bodies))
            for rule in (
                "DOMAIN-SUFFIX,chatgpt.com,PROXY", "DOMAIN-SUFFIX,claude.ai,PROXY",
                "DOMAIN-SUFFIX,gemini.google.com,PROXY", "DOMAIN-SUFFIX,github.com,PROXY",
                "DOMAIN-SUFFIX,discord.com,PROXY", "DOMAIN-SUFFIX,whatsapp.com,PROXY",
            ):
                self.assertIn(rule, bodies[0])
            self.assertIn("    port: 443\n", bodies[0])
            self.assertIn(f'    uuid: "{user_uuid}"\n', bodies[0])
            self.assertNotIn(profile["uuid"], bodies[0])

            for path in (legacy_path, tenant_path):
                response = self.client.get(path + "/v2ray")
                self.assertEqual(response.status_code, 200)
                share_link = base64.b64decode(response.get_data()).decode()
                self.assertIn(f"vless://{user_uuid}@example.com:443?", share_link)
                self.assertNotIn(profile["uuid"], share_link)
                self.assertNotIn("DOMAIN-SUFFIX", share_link)
                query = parse_qs(urlparse(share_link.strip()).query)
                self.assertEqual(query["pbk"], [profile["public_key"]])
                self.assertEqual(query["sid"], [profile["short_id"]])
                self.assertEqual(query["sni"], [profile["server_name"]])

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
        self.assertIn("2026-06-18 08:00:00", body)
        self.assertIn("北京时间（UTC+08:00）", body)


class PanelAccessTest(unittest.TestCase):
    """The console has no login; the source address is the authorization."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root)
        self.client = self.panel.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def csrf_token(self):
        with self.client.session_transaction() as session:
            csrf_value = session.get("csrf_token")
            if not csrf_value:
                csrf_value = "test-csrf-token"
                session["csrf_token"] = csrf_value
            return csrf_value

    def create_port(self, listen_port, note):
        payload = self.panel.state.validate_port_payload(
            {"listen_port": listen_port, "traffic_limit": "10G", "note": note}
        )
        self.panel.state.create_port(payload)
        return next(item for item in self.panel.state.query_ports() if item["listen_port"] == listen_port)

    def test_admin_console_requires_no_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("xray-routing-panel", response.get_data(as_text=True))

        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200)

    def test_logout_route_is_gone(self):
        self.assertEqual(self.client.get("/logout").status_code, 404)

    def test_external_source_is_rejected(self):
        for path in ("/", "/api/dashboard", "/healthz", "/probe-dashboard"):
            response = self.client.get(path, environ_base={"REMOTE_ADDR": "203.0.113.9"})
            self.assertEqual(response.status_code, 403, path)
            self.assertNotIn("xray-routing-panel", response.get_data(as_text=True))

    def test_external_source_json_denial_is_json(self):
        response = self.client.get("/api/dashboard", environ_base={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "forbidden_source")

    def test_tailscale_source_reaches_admin(self):
        # Use a synthetic client in the configured public CGNAT allocation.
        from ipaddress import ip_network

        cgnat = ip_network("100.64.0.0/10")
        response = self.client.get("/api/dashboard", environ_base={"REMOTE_ADDR": str(cgnat.network_address + 1)})
        self.assertEqual(response.status_code, 200)

    def test_state_changing_api_still_requires_csrf_token(self):
        response = self.client.post("/api/client-errors", json={"message": "no token", "source": "test"})
        self.assertEqual(response.status_code, 400)

        accepted = self.client.post(
            "/api/client-errors",
            json={"message": "with token", "source": "test"},
            headers={"X-CSRF-Token": self.csrf_token()},
        )
        self.assertEqual(accepted.status_code, 202)

    def test_login_page_is_tenant_only_and_rejects_admin_credentials(self):
        page = self.client.get("/login?next=/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("租户登录", page.get_data(as_text=True))

        response = self.client.post(
            "/login",
            data={
                "username": "admin-user",
                "password": "admin-pass-123",
                "next": "/",
                "csrf_token": self.csrf_token(),
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_tenant_login_ignores_external_next_target(self):
        port = self.create_port(31011, "Tenant Next")
        response = self.client.post(
            "/login",
            data={
                "username": port["tenant_username"],
                "password": port["tenant_password"],
                "next": "https://attacker.example/phishing",
                "csrf_token": self.csrf_token(),
            },
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        self.assertNotIn("attacker.example", location)
        self.assertEqual(urlparse(location).path, f"/tenant/{port['tenant_token']}")


class ProbeDashboardRenderTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.panel = load_panel_module(self.root, probe_enabled=True, probe_test_listen_port="34001")
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
        return next(item for item in self.panel.state.query_ports() if item["listen_port"] == listen_port)

    def test_probe_dashboard_renders_recent_status_grid(self):
        port = self.create_port(34001, "Probe Tenant")
        # Timestamps are relative to "now" so the rows stay inside the default
        # 24h dashboard window regardless of the wall-clock date the suite runs on.
        now = datetime.now(timezone.utc)
        recent = [
            (port["listen_port"], 1, (now - timedelta(hours=3)).isoformat(), ""),
            (port["listen_port"], 0, (now - timedelta(hours=2)).isoformat(), "timeout"),
            (port["listen_port"], 1, (now - timedelta(hours=1)).isoformat(), ""),
        ]
        with self.panel.state.connect() as conn:
            conn.executemany(
                """
                INSERT INTO upstream_probe_history (listen_port, is_reachable, checked_at, failure_reason)
                VALUES (?, ?, ?, ?)
                """,
                recent,
            )
            conn.commit()

        response = self.client.get("/probe-dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("最近状态分布", body)
        self.assertIn("当前窗口共", body)
        self.assertIn("timeout", body)
        self.assertIn("北京时间（UTC+08:00）", body)


if __name__ == "__main__":
    unittest.main()
