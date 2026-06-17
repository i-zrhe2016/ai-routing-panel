import importlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_state_module(temp_root, api_server):
    data_dir = temp_root / "data"
    xray_dir = temp_root / "xray"
    runtime_dir = xray_dir / "runtime"
    logs_dir = xray_dir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "config.json").write_text("{}", encoding="utf-8")

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "panel.db")
    os.environ["XRAY_ENV_FILE_PATH"] = str(xray_dir / ".env")
    os.environ["XRAY_CONFIG_PATH"] = str(runtime_dir / "config.json")
    os.environ["XRAY_PANEL_PORTS_PATH"] = str(runtime_dir / "panel-ports.json")
    os.environ["XRAY_ACCESS_LOG_PATH"] = str(logs_dir / "access.log")
    os.environ["XRAY_API_SERVER"] = api_server
    os.environ["XRAY_LOCAL_BIN"] = shutil.which("true") or "/bin/true"
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


class LocalXrayModeTest(unittest.TestCase):
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

    def test_xray_running_uses_local_api_socket(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()
        state_module = load_state_module(self.root, f"{host}:{port}")
        accepted = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True)
        accepted.start()
        try:
            self.assertTrue(state_module.PanelState().xray_running())
        finally:
            server.close()
            accepted.join(timeout=1)

    def test_xray_config_test_uses_local_binary(self):
        state_module = load_state_module(self.root, "127.0.0.1:10085")
        state = state_module.PanelState()
        calls = []

        def fake_run_command(command, error_message):
            calls.append((command, error_message))
            return SimpleNamespace(stdout="")

        state.run_command = fake_run_command
        state.xray_config_test()

        self.assertEqual(len(calls), 1)
        command, error_message = calls[0]
        self.assertEqual(command[0], os.environ["XRAY_LOCAL_BIN"])
        self.assertEqual(command[1:], ["run", "-test", "-config", os.environ["XRAY_CONFIG_PATH"]])
        self.assertEqual(error_message, "Xray 配置校验失败")

    def test_read_xray_traffic_stats_uses_local_binary(self):
        state_module = load_state_module(self.root, "127.0.0.1:10085")
        state = state_module.PanelState()
        commands = []
        state.xray_running = lambda: True

        def fake_run_command(command, error_message):
            commands.append((command, error_message))
            return SimpleNamespace(stdout=json.dumps({"stat": []}))

        state.run_command = fake_run_command
        self.assertEqual(state.read_xray_traffic_stats(), {})
        self.assertEqual(len(commands), 1)
        command, error_message = commands[0]
        self.assertEqual(command[0], os.environ["XRAY_LOCAL_BIN"])
        self.assertEqual(command[1], "api")
        self.assertIn("--server=127.0.0.1:10085", command)
        self.assertEqual(error_message, "Xray 流量查询失败")
