import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.xray.node_control import DataPlaneConfig, DataPlaneController


def load_state_module(temp_root):
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    reports_dir = xray_dir / "reports" / "hourly-domains"
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "config.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "dynamic-routing.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "panel-ports.json").write_text("{\"ports\": []}\n", encoding="utf-8")

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["XRAY_ENV_FILE_PATH"] = str(xray_dir / ".env")
    os.environ["XRAY_CONFIG_PATH"] = str(runtime_dir / "config.json")
    os.environ["XRAY_PANEL_PORTS_PATH"] = str(runtime_dir / "panel-ports.json")
    os.environ["XRAY_ACCESS_LOG_PATH"] = str(logs_dir / "access.log")
    os.environ["DATAPLANE_LOCAL_BIN"] = ""
    os.environ["DATAPLANE_CONTAINER_NAME"] = ""

    if "flask" not in sys.modules:
        flask_stub = ModuleType("flask")
        flask_stub.request = SimpleNamespace(host="127.0.0.1")
        flask_stub.url_for = lambda *args, **kwargs: "/"
        sys.modules["flask"] = flask_stub

    for module_name in ["app.config", "app.state"]:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
    return importlib.reload(importlib.import_module("app.state"))


class NodeControlTest(unittest.TestCase):
    def setUp(self):
        self.original_environ = os.environ.copy()
        self.original_flask_module = sys.modules.get("flask")
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_environ)
        if self.original_flask_module is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = self.original_flask_module
        self.tempdir.cleanup()

    def test_remote_data_plane_syncs_before_validation(self):
        os.environ["DATAPLANE_SSH_TARGET"] = "root@default-node"
        os.environ["DATAPLANE_CONFIG_PATH"] = "/etc/xray/config.json"
        os.environ["DATAPLANE_PANEL_PORTS_PATH"] = "/etc/xray/panel-ports.json"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        calls = []

        def fake_sync(validate_config=False):
            calls.append(validate_config)
            return ["/etc/xray/config.json"]

        state.data_plane.sync_generated_files = fake_sync
        state.xray_config_test()

        self.assertEqual(calls, [True])

    def test_restart_data_plane_returns_summary(self):
        os.environ["DATAPLANE_SSH_TARGET"] = "root@data-plane"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        state.data_plane.is_configured = lambda: True
        state.data_plane.supports_restart = lambda: True
        state.data_plane.restart = lambda: True
        state.data_plane.status_summary = lambda: {"role": "data_plane", "label": "数据面", "configured": True}

        summary = state.restart_data_plane_or_raise()

        self.assertEqual(summary["role"], "data_plane")
        self.assertEqual(summary["label"], "数据面")

    def test_remote_sync_keeps_json_suffix_for_temp_config(self):
        source_config = self.root / "config.json"
        source_config.write_text("{}", encoding="utf-8")
        controller = DataPlaneController(
            DataPlaneConfig(
                role="data_plane",
                label="数据面",
                ssh_target="root@example.com",
                config_path="/etc/xray/config.json",
                source_config_path=source_config,
            )
        )
        remote_calls = []
        tested_paths = []

        def fake_run_remote(args, error_prefix, timeout=None, input_text=None):
            remote_calls.append((args, error_prefix, input_text))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        controller._run_remote = fake_run_remote
        controller.test_config = lambda config_path=None: tested_paths.append(config_path)

        uploaded = controller.sync_generated_files(validate_config=True)

        self.assertEqual(uploaded, ["/etc/xray/config.json"])
        self.assertEqual(tested_paths, ["/etc/xray/config.codex-tmp.json"])
        self.assertEqual(remote_calls[0][0][-1], "/etc/xray/config.codex-tmp.json")

    def test_remote_dynamic_routing_sync_updates_local_copy(self):
        local_path = self.root / "dynamic-routing.json"
        controller = DataPlaneController(
            DataPlaneConfig(
                role="data_plane",
                label="数据面",
                ssh_target="root@example.com",
                dynamic_routing_path="/etc/xray/dynamic-routing.json",
                source_dynamic_routing_path=local_path,
            )
        )

        controller._run_remote = lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"exists": true, "data": "{\\"routing\\": {\\"rules\\": []}}"}',
            stderr="",
        )

        changed = controller.sync_dynamic_routing_from_remote()

        self.assertTrue(changed)
        self.assertEqual(local_path.read_text(encoding="utf-8"), '{"routing": {"rules": []}}')

    def test_remote_ai_report_sync_updates_local_copy(self):
        local_path = self.root / "reports" / "hourly-domains" / "latest.json"
        controller = DataPlaneController(
            DataPlaneConfig(
                role="data_plane",
                label="数据面",
                ssh_target="root@example.com",
                ai_report_path="/srv/xray/reports/hourly-domains/latest.json",
                source_ai_report_path=local_path,
            )
        )

        controller._run_remote = lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"exists": true, "data": "{\\"generated_at\\": \\"2026-06-18T00:00:00+00:00\\"}"}',
            stderr="",
        )

        changed = controller.sync_ai_report_from_remote()

        self.assertTrue(changed)
        self.assertEqual(
            local_path.read_text(encoding="utf-8"),
            '{"generated_at": "2026-06-18T00:00:00+00:00"}',
        )

    def test_sync_data_plane_ai_state_replaces_local_snapshot(self):
        os.environ["DATAPLANE_SSH_TARGET"] = "root@default-node"
        os.environ["DATAPLANE_AI_REPORT_PATH"] = "/srv/xray/reports/hourly-domains/latest.json"
        os.environ["DATAPLANE_PANEL_DB_PATH"] = "/srv/xray/data/panel.db"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        report_path = state.data_plane.config.source_ai_report_path

        report = {
            "generated_at": "2026-06-18T00:00:00+00:00",
            "window_start": "2026-06-17T23:00:00+00:00",
            "window_end": "2026-06-18T00:00:00+00:00",
            "unique_domains": 2,
            "domains": [
                {
                    "domain": "openai.com",
                    "hits": 4,
                    "first_seen": "2026-06-17T23:10:00+00:00",
                    "last_seen": "2026-06-17T23:58:00+00:00",
                    "protocols": ["tcp"],
                    "classification": "ai",
                    "reason": "known ai",
                }
            ],
            "protocols": [{"protocol": "tcp", "hits": 4}],
            "ai_target": {"upstream_host": "ai.example.com", "upstream_port": 443},
            "panel_target": {"listen_port": 31001, "upstream_host": "panel.example.com", "upstream_port": 443},
            "route_status": {"status": "applied", "reason": ""},
        }

        def fake_sync_report():
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return True

        state.data_plane.sync_ai_report_from_remote = fake_sync_report
        state.data_plane.read_ai_domains_snapshot_from_remote = lambda: {
            "exists": True,
            "ai_domains": [
                {
                    "domain": "openai.com",
                    "classification": "ai",
                    "reason": "known ai",
                    "source": "codex",
                    "model": "gpt-5.5",
                    "first_seen": "2026-06-17T23:10:00+00:00",
                    "last_seen": "2026-06-17T23:58:00+00:00",
                    "total_hits": 9,
                    "last_protocols": "[\"tcp\"]",
                    "last_report_window_start": "2026-06-17T23:00:00+00:00",
                    "last_report_window_end": "2026-06-18T00:00:00+00:00",
                    "updated_at": "2026-06-18T00:00:00+00:00",
                }
            ],
        }

        result = state.sync_data_plane_ai_state()

        self.assertTrue(result["report_synced"])
        self.assertTrue(result["snapshot_synced"])
        self.assertEqual(state.read_ai_domain_report()["ai_domain_count"], 1)
        with state.connect() as conn:
            row = conn.execute(
                "SELECT domain, total_hits, source FROM ai_domains WHERE domain = ?",
                ("openai.com",),
            ).fetchone()
            observations = conn.execute("SELECT COUNT(*) FROM ai_domain_observations").fetchone()[0]
        self.assertEqual(dict(row), {"domain": "openai.com", "total_hits": 9, "source": "codex"})
        self.assertEqual(observations, 0)

    def test_read_ai_domain_report_accepts_pending_domain_list(self):
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        report_path = state.data_plane.config.source_ai_report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-06-19T06:00:00+00:00",
                    "window_start": "2026-06-19T05:00:00+00:00",
                    "window_end": "2026-06-19T06:00:00+00:00",
                    "unique_domains": 1,
                    "domains": [],
                    "protocols": [],
                    "route_status": {
                        "status": "pending",
                        "reason": "classifier_disabled",
                        "pending_domains_without_classifier": [
                            "api.example.com",
                            "cdn.example.com",
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        report = state.read_ai_domain_report()

        self.assertEqual(report["route_status"], "pending")
        self.assertEqual(report["pending_domains_without_classifier"], 2)

    def test_render_xray_config_pulls_remote_dynamic_routing_first(self):
        os.environ["DATAPLANE_SSH_TARGET"] = "root@default-node"
        os.environ["DATAPLANE_CONFIG_PATH"] = "/etc/xray/config.json"
        os.environ["DATAPLANE_DYNAMIC_ROUTING_PATH"] = "/etc/xray/dynamic-routing.json"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        calls = []

        state.data_plane.sync_dynamic_routing_from_remote = lambda: calls.append("pull")
        state.run_command = lambda command, error_prefix, timeout=None: calls.append("render")

        state.render_xray_config()

        self.assertEqual(calls, ["pull", "render"])


if __name__ == "__main__":
    unittest.main()
