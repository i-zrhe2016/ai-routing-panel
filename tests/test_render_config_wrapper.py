import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "render_config.py"

ENV_CONTENT = """\
XRAY_LISTEN_HOST=0.0.0.0
XRAY_LISTEN_PORT=443
XRAY_PUBLIC_HOST=panel.example.com
XRAY_PUBLIC_PORT=31098
XRAY_CLIENT_UUID=11111111-1111-1111-1111-111111111111
XRAY_FLOW=xtls-rprx-vision
XRAY_REALITY_PRIVATE_KEY=private-key-example
XRAY_REALITY_PUBLIC_KEY=public-key-example
XRAY_REALITY_SHORT_ID=0123456789abcdef
XRAY_SERVER_NAME=www.microsoft.com
XRAY_DEST=www.microsoft.com:443
XRAY_FINGERPRINT=chrome
XRAY_LOGLEVEL=warning
XRAY_NODE_TAG=test-node
"""


class RenderConfigWrapperTest(unittest.TestCase):
    def test_wrapper_matches_module_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_file = root / "xray.env"
            env_file.write_text(ENV_CONTENT, encoding="utf-8")

            wrapper_dir = root / "wrapper"
            module_dir = root / "module"
            wrapper_dir.mkdir()
            module_dir.mkdir()

            wrapper_outputs = self.run_render_command(
                [sys.executable, str(WRAPPER)],
                env_file,
                wrapper_dir,
            )
            module_outputs = self.run_render_command(
                [sys.executable, "-m", "app.xray.render_config"],
                env_file,
                module_dir,
            )

            self.assertEqual(wrapper_outputs, module_outputs)
            client = json.loads(wrapper_outputs["client"])
            self.assertEqual(client["outbounds"][0]["settings"]["vnext"][0]["address"], "panel.example.com")
            self.assertEqual(client["outbounds"][0]["settings"]["vnext"][0]["port"], 31098)
            server = json.loads(wrapper_outputs["config"])
            self.assertEqual(server["api"]["listen"], "127.0.0.1:10085")
            self.assertTrue(server["policy"]["system"]["statsInboundUplink"])
            self.assertEqual(server["inbounds"][0]["tag"], "panel-443")

    def test_ai_domain_manager_wrapper_exposes_cli(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ai_domain_manager.py"), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("Classify Xray destination domains", completed.stdout)

    def test_panel_ports_file_overrides_server_inbounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_file = root / "xray.env"
            env_file.write_text(ENV_CONTENT, encoding="utf-8")

            outputs = self.run_render_command(
                [sys.executable, "-m", "app.xray.render_config"],
                env_file,
                root,
                panel_ports=[31098, 32001],
            )

            server = json.loads(outputs["config"])
            self.assertEqual([inbound["port"] for inbound in server["inbounds"]], [31098, 32001])
            self.assertEqual([inbound["tag"] for inbound in server["inbounds"]], ["panel-31098", "panel-32001"])

    def run_render_command(self, base_cmd, env_file, output_dir, panel_ports=None):
        config_out = output_dir / "config.json"
        client_out = output_dir / "client.json"
        share_out = output_dir / "share.txt"
        dynamic_out = output_dir / "dynamic.json"
        panel_ports_file = output_dir / "panel-ports.json"
        if panel_ports is not None:
            panel_ports_file.write_text(json.dumps({"ports": panel_ports}), encoding="utf-8")

        command = base_cmd + [
            "--env-file",
            str(env_file),
            "--config-out",
            str(config_out),
            "--client-out",
            str(client_out),
            "--share-out",
            str(share_out),
            "--dynamic-routing-file",
            str(dynamic_out),
        ]
        if panel_ports is not None:
            command.extend(["--panel-ports-file", str(panel_ports_file)])

        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return {
            "config": config_out.read_text(encoding="utf-8"),
            "client": client_out.read_text(encoding="utf-8"),
            "share": share_out.read_text(encoding="utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
