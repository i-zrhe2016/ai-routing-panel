import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_state_module(temp_root, extra_env=None):
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "config.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "panel-ports.json").write_text("{\"ports\": []}\n", encoding="utf-8")
    (xray_dir / ".env").write_text("XRAY_PUBLIC_HOST=panel.example.com\nXRAY_CLIENT_UUID=test\n", encoding="utf-8")

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["XRAY_ENV_FILE_PATH"] = str(xray_dir / ".env")
    os.environ["XRAY_CONFIG_PATH"] = str(runtime_dir / "config.json")
    os.environ["XRAY_PANEL_PORTS_PATH"] = str(runtime_dir / "panel-ports.json")
    os.environ["XRAY_ACCESS_LOG_PATH"] = str(logs_dir / "access.log")
    os.environ["SEED_LISTEN_PORT"] = ""
    os.environ["DATAPLANE_LOCAL_BIN"] = ""
    os.environ["DATAPLANE_CONTAINER_NAME"] = ""
    os.environ["DNS_FAILOVER_ENABLED"] = "1"
    os.environ["DNS_FAILOVER_INTERVAL"] = "15"
    os.environ["DNS_FAILOVER_TIMEOUT"] = "1"
    os.environ["DNS_FAILOVER_FAILURE_THRESHOLD"] = "2"
    os.environ["DNS_FAILOVER_RECOVERY_THRESHOLD"] = "2"
    os.environ["DNS_FAILOVER_PROBE_HOST"] = "edge.example.com"
    os.environ["DNS_FAILOVER_PROBE_PORT"] = "443"
    os.environ["CF_API_TOKEN"] = "test-token"
    os.environ["CF_ZONE_ID"] = "zone-id"
    os.environ["CF_DNS_RECORD_ID"] = "record-id"
    os.environ["CF_DNS_RECORD_TYPE"] = "A"
    os.environ["CF_DNS_RECORD_NAME"] = "edge.example.com"
    os.environ["CF_DNS_RECORD_PROXIED"] = "0"
    os.environ["CF_DNS_RECORD_TTL"] = "60"
    os.environ["DNS_FAILOVER_PRIMARY_CONTENT"] = "1.1.1.1"
    os.environ["DNS_FAILOVER_BACKUP_CONTENT"] = "2.2.2.2"
    os.environ["DNS_FAILOVER_BACKUP_LABEL"] = "控制面备用节点"
    if extra_env:
        for key, value in extra_env.items():
            os.environ[key] = value

    flask_stub = ModuleType("flask")
    flask_stub.request = SimpleNamespace(host="127.0.0.1")
    flask_stub.url_for = lambda *args, **kwargs: "/"
    sys.modules["flask"] = flask_stub

    for module_name in ["app.config", "app.dns_failover", "app.state"]:
        sys.modules.pop(module_name, None)
    state_module = importlib.import_module("app.state")
    return importlib.reload(state_module)


class DnsFailoverTest(unittest.TestCase):
    def setUp(self):
        self.original_environ = os.environ.copy()
        self.original_flask = sys.modules.get("flask")
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_environ)
        if self.original_flask is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = self.original_flask
        self.tempdir.cleanup()

    def build_state(self, extra_env=None):
        state_module = load_state_module(self.root, extra_env=extra_env)
        state = state_module.PanelState()
        state.render_xray_config = lambda: None
        state.xray_config_test = lambda: None
        state.restart_data_plane = lambda: None
        state.init_db()
        return state, state_module

    def seed_record(self, state, content):
        state.dns_failover_manager.get_record = lambda: {
            "content": content,
            "ttl": 60,
            "proxied": False,
        }
        state.refresh_dns_failover_record_snapshot()

    def test_dns_failover_status_disabled(self):
        state, _state_module = self.build_state({"DNS_FAILOVER_ENABLED": "0"})

        status = state.dns_failover_status()

        self.assertFalse(status["enabled"])
        self.assertFalse(status["configured"])

    def test_dns_failover_incomplete_config_records_error_on_check(self):
        state, _state_module = self.build_state({"CF_API_TOKEN": ""})

        status = state.run_dns_failover_check()

        self.assertFalse(status["configured"])
        self.assertIn("CF_API_TOKEN", status["config_error"])
        self.assertIn("CF_API_TOKEN", status["last_error"])

    def test_dns_failover_auto_resolves_primary_and_backup_contents(self):
        state, _state_module = self.build_state(
            {
                "CONTROL_PLANE_BACKUP_XRAY_ENABLED": "1",
                "DNS_FAILOVER_PRIMARY_CONTENT": "",
                "DNS_FAILOVER_BACKUP_CONTENT": "",
            }
        )
        state.data_plane.resolve_public_ip = lambda timeout_seconds=5: "1.1.1.1"
        state.resolve_dns_failover_contents.__globals__["resolve_public_ip"] = lambda timeout=5.0: "2.2.2.2"

        status = state.dns_failover_status()

        self.assertTrue(status["configured"])
        self.assertEqual(status["primary_content"], "1.1.1.1")
        self.assertEqual(status["backup_content"], "2.2.2.2")

    def test_dns_failover_requires_explicit_backup_when_control_plane_backup_disabled(self):
        state, _state_module = self.build_state(
            {
                "CONTROL_PLANE_BACKUP_XRAY_ENABLED": "0",
                "DNS_FAILOVER_BACKUP_CONTENT": "",
            }
        )

        status = state.dns_failover_status()

        self.assertFalse(status["configured"])
        self.assertIn("CONTROL_PLANE_BACKUP_XRAY_ENABLED", status["config_error"])

    def test_dns_failover_switches_to_backup_after_threshold(self):
        state, _state_module = self.build_state()
        self.seed_record(state, "1.1.1.1")
        state.dns_failover_manager.probe_once = lambda: {"ok": False, "error": "timeout"}
        switch_calls = []
        state.dns_failover_manager.sync_target = lambda target, primary_content=None, backup_content=None: switch_calls.append(target) or {
            "content": "2.2.2.2",
            "ttl": 60,
            "proxied": False,
        }

        first = state.run_dns_failover_check()
        second = state.run_dns_failover_check()

        self.assertEqual(first["current_target"], "primary")
        self.assertEqual(second["current_target"], "backup")
        self.assertEqual(second["last_switch_reason"], "auto_failover")
        self.assertEqual(switch_calls, ["backup"])

    def test_dns_failover_does_not_repeat_backup_switch(self):
        state, _state_module = self.build_state()
        self.seed_record(state, "2.2.2.2")
        state.dns_failover_manager.probe_once = lambda: {"ok": False, "error": "timeout"}
        switch_calls = []
        state.dns_failover_manager.sync_target = lambda target, primary_content=None, backup_content=None: switch_calls.append(target) or {
            "content": "2.2.2.2",
            "ttl": 60,
            "proxied": False,
        }

        state.run_dns_failover_check()
        state.run_dns_failover_check()

        self.assertEqual(switch_calls, [])
        self.assertEqual(state.dns_failover_status()["current_target"], "backup")

    def test_dns_failover_recovers_to_primary_after_threshold(self):
        state, _state_module = self.build_state()
        self.seed_record(state, "2.2.2.2")
        state.dns_failover_manager.probe_once = lambda: {"ok": True, "error": ""}
        switch_calls = []
        state.dns_failover_manager.sync_target = lambda target, primary_content=None, backup_content=None: switch_calls.append(target) or {
            "content": "1.1.1.1",
            "ttl": 60,
            "proxied": False,
        }

        first = state.run_dns_failover_check()
        second = state.run_dns_failover_check()

        self.assertEqual(first["current_target"], "backup")
        self.assertEqual(second["current_target"], "primary")
        self.assertEqual(second["last_switch_reason"], "auto_recovery")
        self.assertEqual(switch_calls, ["primary"])

    def test_dns_failover_api_failure_records_error(self):
        state, state_module = self.build_state()
        self.seed_record(state, "1.1.1.1")
        state.dns_failover_manager.probe_once = lambda: {"ok": False, "error": "timeout"}

        def fail_switch(_target, primary_content=None, backup_content=None):
            raise state_module.CloudflareApiError("api denied")

        state.dns_failover_manager.sync_target = fail_switch

        state.run_dns_failover_check()
        with self.assertRaises(state_module.ValidationError):
            state.run_dns_failover_check()

        status = state.dns_failover_status()
        self.assertEqual(status["current_target"], "primary")
        self.assertEqual(status["last_error"], "api denied")

    def test_manual_switch_updates_state(self):
        state, _state_module = self.build_state()
        self.seed_record(state, "1.1.1.1")
        state.dns_failover_manager.sync_target = lambda _target, primary_content=None, backup_content=None: {
            "content": "2.2.2.2",
            "ttl": 60,
            "proxied": False,
        }

        status = state.switch_dns_target("backup")

        self.assertEqual(status["current_target"], "backup")
        self.assertEqual(status["last_switch_reason"], "manual_switch")
        self.assertEqual(status["record_content"], "2.2.2.2")


if __name__ == "__main__":
    unittest.main()
