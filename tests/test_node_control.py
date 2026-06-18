import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_state_module(temp_root):
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "config.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "panel-ports.json").write_text("{\"ports\": []}\n", encoding="utf-8")

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["XRAY_ENV_FILE_PATH"] = str(xray_dir / ".env")
    os.environ["XRAY_CONFIG_PATH"] = str(runtime_dir / "config.json")
    os.environ["XRAY_PANEL_PORTS_PATH"] = str(runtime_dir / "panel-ports.json")
    os.environ["XRAY_ACCESS_LOG_PATH"] = str(logs_dir / "access.log")
    os.environ["XRAY_LOCAL_BIN"] = ""
    os.environ["XRAY_CONTAINER_NAME"] = ""

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

    def test_remote_default_node_syncs_before_validation(self):
        os.environ["DEFAULT_NODE_SSH_TARGET"] = "root@default-node"
        os.environ["DEFAULT_NODE_CONFIG_PATH"] = "/etc/xray/config.json"
        os.environ["DEFAULT_NODE_PANEL_PORTS_PATH"] = "/etc/xray/panel-ports.json"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        calls = []

        def fake_sync(validate_config=False):
            calls.append(validate_config)
            return ["/etc/xray/config.json"]

        state.default_node.sync_generated_files = fake_sync
        state.xray_config_test()

        self.assertEqual(calls, [True])

    def test_restart_node_returns_ai_summary(self):
        os.environ["AI_NODE_HOST"] = "ai.example.com"
        os.environ["AI_NODE_PORT"] = "443"
        os.environ["AI_NODE_RESTART_COMMAND"] = "systemctl restart xray"
        state_module = load_state_module(self.root)
        state = state_module.PanelState()
        state.ai_node.restart = lambda: True
        state.ai_node.status_summary = lambda: {"role": "ai", "label": "AI 节点", "configured": True}

        summary = state.restart_node("ai")

        self.assertEqual(summary["role"], "ai")
        self.assertEqual(summary["label"], "AI 节点")


if __name__ == "__main__":
    unittest.main()
