import base64
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NodeRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle_module = load_module(
            "build_backup_bundle_for_node_recovery", ROOT / "scripts" / "build_backup_bundle.py"
        )
        cls.collector = load_module(
            "collect_remote_backup_for_node_recovery", ROOT / "scripts" / "collect_remote_backup.py"
        )
        cls.recovery = load_module(
            "node_recovery_for_tests", ROOT / "scripts" / "node_recovery.py"
        )

    def _make_bundle(self, root, include_normal_env=True):
        database = root / "panel-20260828T030000Z.db"
        database.write_bytes(b"sqlite snapshot")

        staging = root / "remote-staging"
        normal_paths = [
            "/root/xray-routing-panel/app/xray/runtime/config.json",
        ]
        if include_normal_env:
            normal_paths.append("/root/xray-routing-panel/app/xray/.env")
        normal_files = []
        for path in normal_paths:
            data = b'{"inbounds": []}\n' if path.endswith("config.json") else b"XRAY_LISTEN_PORT=443\n"
            normal_files.append(
                {
                    "path": path,
                    "status": "ok",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data_base64": base64.b64encode(data).decode("ascii"),
                }
            )
        normal_node = self.collector.RemoteNode(
            role="normal-data-plane",
            target="root@normal.example",
            paths=tuple(normal_paths),
            known_hosts="/tmp/known_hosts",
            required_paths=(
                "/root/xray-routing-panel/app/xray/runtime/config.json",
                "/root/xray-routing-panel/app/xray/.env",
            ),
            restore_root="/root/xray-routing-panel",
        )
        normal_result = self.collector.write_collection(
            staging, normal_node, {"files": normal_files}
        )
        (staging / "remote-node-collection.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "purpose": "remote-node-config-collection",
                    "nodes": [normal_result],
                }
            ),
            encoding="utf-8",
        )

        control_xray = root / "control" / "app" / "xray"
        (control_xray / "runtime").mkdir(parents=True)
        (control_xray / "runtime" / "config-ai-node.json").write_text(
            '{"inbounds": [{"port": 27166}]}\n', encoding="utf-8"
        )
        (control_xray / ".env").write_text(
            "AI_NODE_CLIENT_UUID=00000000-0000-4000-8000-000000000000\n",
            encoding="utf-8",
        )

        return self.bundle_module.create_backup_bundle(
            database,
            [str(control_xray)],
            root / "backups",
            "panel-test",
            named_paths=[(staging, "nodes")],
        )

    def test_bundle_contains_recovery_contract_and_is_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = self._make_bundle(Path(tmpdir))
            validated = self.recovery.validate_backup_bundle(bundle)

            self.assertTrue(validated["readiness"]["recoveryReady"])
            self.assertTrue(
                all(item["recoveryReady"] for item in validated["readiness"]["nodes"])
            )
            self.assertEqual(
                validated["nodeManifest"]["sharedState"]["requiredArtifacts"][0]["name"],
                "panel-database",
            )

    def test_local_runtime_files_make_both_local_nodes_recovery_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = root / "panel-20260828T030000Z.db"
            database.write_bytes(b"sqlite snapshot")
            xray = root / "app" / "xray"
            runtime = xray / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "config.json").write_text('{"inbounds": []}\n', encoding="utf-8")
            (runtime / "config-ai-node.json").write_text(
                '{"inbounds": [{"port": 27166}]}\n', encoding="utf-8"
            )
            (xray / ".env").write_text("XRAY_LISTEN_PORT=443\n", encoding="utf-8")

            bundle = self.bundle_module.create_backup_bundle(
                database,
                [str(xray)],
                root / "backups",
                "panel-local-test",
            )
            validated = self.recovery.validate_backup_bundle(bundle)

            self.assertTrue(validated["readiness"]["recoveryReady"])
            sources = {node["role"]: node["source"] for node in validated["nodeManifest"]["nodes"]}
            self.assertEqual(sources["normal-data-plane"], "local-runtime")
            self.assertEqual(sources["ai-data-plane"], "control-plane-local")

    def test_prepare_node_creates_ready_standalone_compose_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._make_bundle(root)
            output = root / "replacement"

            result = self.recovery.prepare_node(bundle, "normal-data-plane", output)

            self.assertTrue(result["recoveryReady"])
            self.assertEqual(
                (output / "app/xray/runtime/config.json").read_text(encoding="utf-8"),
                '{"inbounds": []}\n',
            )
            self.assertTrue((output / "app/xray/.env").is_file())
            self.assertTrue((output / "docker-compose.node.yml").is_file())
            self.assertEqual((output / "app/xray/.env").stat().st_mode & 0o777, 0o600)
            self.assertEqual((output / "docker-compose.node.yml").stat().st_mode & 0o777, 0o600)

    def test_incomplete_remote_node_is_not_prepared_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._make_bundle(root, include_normal_env=False)
            validated = self.recovery.validate_backup_bundle(bundle)
            normal = next(
                item for item in validated["readiness"]["nodes"] if item["role"] == "normal-data-plane"
            )
            self.assertFalse(normal["recoveryReady"])
            self.assertIn("xray-env", normal["missingRequiredArtifacts"])
            with self.assertRaisesRegex(ValueError, "recovery is not ready"):
                self.recovery.prepare_node(bundle, "normal-data-plane", root / "replacement")

    def test_validate_rejects_tampered_archived_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._make_bundle(root)
            tampered = root / "tampered.tar.gz"

            with tarfile.open(bundle, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
                for member in source.getmembers():
                    if not member.isfile():
                        target.addfile(member)
                        continue
                    data = source.extractfile(member).read()
                    if member.name.endswith("config.json"):
                        data = data + b"tampered"
                    member.size = len(data)
                    target.addfile(member, io.BytesIO(data))

            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.recovery.validate_backup_bundle(tampered)


if __name__ == "__main__":
    unittest.main()
