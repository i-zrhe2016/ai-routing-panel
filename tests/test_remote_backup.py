import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_SCRIPT = ROOT / "scripts" / "collect_remote_backup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_remote_backup_test", COLLECTOR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RemoteBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_read_remote_uses_direct_password_ssh_and_strict_host_checking(self):
        node = self.module.RemoteNode(
            role="normal-data-plane",
            target="root@data-plane",
            paths=("/etc/xray/config.json",),
            known_hosts="/tmp/known_hosts",
        )
        response = {"version": 1, "role": node.role, "files": []}
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")
        with patch.object(self.module.subprocess, "run", return_value=completed) as run:
            result = self.module.read_remote(node, 12, 2048)

        self.assertEqual(result, response)
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=no", command)
        self.assertIn("PubkeyAuthentication=no", command)
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", command)
        self.assertIn("PasswordAuthentication=yes", command)
        self.assertIn("KbdInteractiveAuthentication=yes", command)
        self.assertIn("ChallengeResponseAuthentication=yes", command)
        self.assertIn("UserKnownHostsFile=/tmp/known_hosts", command)
        self.assertNotIn("-i", command)
        self.assertIn("XRAY_BACKUP_REMOTE_MAX_FILE_BYTES=2048", command[-1])

    def test_ssh_options_cannot_override_identity_or_known_hosts(self):
        with self.assertRaisesRegex(ValueError, "UserKnownHostsFile"):
            self.module.validate_options(("-o", "UserKnownHostsFile=/tmp/other"))
        with self.assertRaisesRegex(ValueError, "IdentityFile"):
            self.module.validate_options(("-o", "IdentityFile=/tmp/other"))
        self.module.validate_options(("-4", "-o", "ConnectTimeout=5"))

    def test_read_remote_rejects_malformed_protocol_payload(self):
        node = self.module.RemoteNode(
            role="normal-data-plane",
            target="root@data-plane",
            paths=("/etc/xray/config.json",),
            known_hosts="/tmp/known_hosts",
        )
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"version": 1, "role": "wrong", "files": []}),
            stderr="",
        )
        with patch.object(self.module.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "invalid SSH collection response"):
                self.module.read_remote(node, 12, 2048)

    def test_write_collection_verifies_checksum_and_preserves_node_path(self):
        data = b'{"inbounds": []}\n'
        payload = {
            "files": [
                {
                    "path": "/etc/xray/config.json",
                    "status": "ok",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data_base64": base64.b64encode(data).decode("ascii"),
                },
                {"path": "/etc/xray/.env", "status": "missing", "exists": False},
            ]
        }
        node = self.module.RemoteNode(
            role="ai-data-plane",
            target="root@ai-node",
            paths=("/etc/xray/config.json", "/etc/xray/.env"),
            known_hosts="/tmp/known_hosts_ai",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir)
            result = self.module.write_collection(output, node, payload)
            target = output / "ai-data-plane" / "etc" / "xray" / "config.json"
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["files"][0]["stagedPath"], "ai-data-plane/etc/xray/config.json")
        self.assertNotIn("data_base64", result["files"][0])

    def test_strict_mode_rejects_missing_target(self):
        node = self.module.RemoteNode(
            role="normal-data-plane",
            target="",
            paths=("/etc/xray/config.json",),
            known_hosts="/tmp/known_hosts",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(RuntimeError, "missing SSH target"):
                self.module.collect_nodes(Path(tmpdir), (node,), required=True)

    def test_strict_mode_skips_unconfigured_remote_ai_target(self):
        node = self.module.RemoteNode(
            role="ai-data-plane",
            target="",
            paths=("/etc/xray/config.json", "/etc/xray/.env"),
            known_hosts="/tmp/known_hosts_ai",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.module.collect_nodes(Path(tmpdir), (node,), required=True)

        self.assertEqual(result[0]["status"], "skipped_no_target")
        self.assertFalse(result[0]["recoveryReady"])

    def test_strict_mode_requires_primary_config_not_only_optional_env(self):
        payload = {
            "files": [
                {
                    "path": "/etc/xray/.env",
                    "status": "ok",
                    "size": 6,
                    "sha256": hashlib.sha256(b"KEY=1\n").hexdigest(),
                    "data_base64": base64.b64encode(b"KEY=1\n").decode("ascii"),
                },
                {"path": "/etc/xray/config.json", "status": "missing", "exists": False},
            ]
        }
        node = self.module.RemoteNode(
            role="ai-data-plane",
            target="root@ai-node",
            paths=("/etc/xray/config.json", "/etc/xray/.env"),
            known_hosts="/tmp/known_hosts_ai",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.module.write_collection(Path(tmpdir), node, payload)
            self.assertFalse(result["configCollected"])
            self.assertEqual(result["status"], "partial")

    def test_collection_rejects_non_empty_staging_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir)
            (output / "stale.txt").write_text("old", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "not empty"):
                self.module.collect_remote_configs(output)

    def test_build_nodes_uses_separate_ports_and_paths(self):
        original = os.environ.copy()
        try:
            os.environ.update(
                {
                    "DB_BACKUP_DATAPLANE_SSH_TARGET": "root@override-data-plane",
                    "DB_BACKUP_DATAPLANE_REMOTE_PATHS": "/srv/xray/config.json",
                    "DB_BACKUP_AI_NODE_SSH_TARGET": "root@override-ai-node",
                    "DB_BACKUP_AI_NODE_SSH_PORT": "2222",
                    "DB_BACKUP_AI_NODE_REMOTE_PATHS": "/etc/xray/config.json,/opt/xray/.env",
                }
            )
            normal, ai = self.module.build_nodes()
        finally:
            os.environ.clear()
            os.environ.update(original)

        self.assertEqual(normal.target, "root@override-data-plane")
        self.assertIn("/srv/xray/config.json", normal.paths)
        self.assertEqual(ai.target, "root@override-ai-node")
        self.assertEqual(ai.ssh_port, "2222")
        self.assertIn("/opt/xray/.env", ai.paths)

    def test_build_nodes_defaults_to_private_network_normal_target_and_no_remote_ai_target(self):
        original = os.environ.copy()
        try:
            for name in (
                "DB_BACKUP_DATAPLANE_SSH_TARGET",
                "DATAPLANE_SSH_TARGET",
                "DB_BACKUP_AI_NODE_SSH_TARGET",
                "AI_NODE_SSH_TARGET",
                "DB_BACKUP_AI_NODE_SSH_PORT",
            ):
                os.environ.pop(name, None)
            normal, ai = self.module.build_nodes()
        finally:
            os.environ.clear()
            os.environ.update(original)

        self.assertEqual(normal.target, "root@100.116.187.106")
        self.assertEqual(normal.ssh_port, "22")
        self.assertEqual(ai.target, "")
        self.assertEqual(ai.ssh_port, "22")
        self.assertIn("/root/xray-routing-panel/app/xray/runtime/dynamic-routing.json", normal.paths)
        self.assertEqual(
            normal.required_paths,
            (
                "/root/xray-routing-panel/app/xray/runtime/config.json",
                "/root/xray-routing-panel/app/xray/.env",
            ),
        )

    def test_explicit_remote_paths_still_require_sibling_env_file(self):
        original = os.environ.copy()
        try:
            os.environ["DB_BACKUP_DATAPLANE_REMOTE_PATHS"] = "/srv/xray/config.json"
            os.environ.pop("DB_BACKUP_DATAPLANE_CONFIG_PATH", None)
            normal, _ = self.module.build_nodes()
        finally:
            os.environ.clear()
            os.environ.update(original)

        self.assertEqual(normal.paths, ("/srv/xray/config.json",))
        self.assertEqual(normal.required_paths, ("/srv/xray/config.json", "/srv/xray/.env"))

    def test_config_path_override_derives_sibling_env_in_default_path_set(self):
        original = os.environ.copy()
        try:
            os.environ["DB_BACKUP_DATAPLANE_CONFIG_PATH"] = "/srv/xray/runtime/config.json"
            os.environ.pop("DB_BACKUP_DATAPLANE_REMOTE_PATHS", None)
            normal, _ = self.module.build_nodes()
        finally:
            os.environ.clear()
            os.environ.update(original)

        self.assertEqual(normal.paths[0], "/srv/xray/runtime/config.json")
        self.assertEqual(normal.paths[1], "/srv/xray/runtime/.env")


if __name__ == "__main__":
    unittest.main()
