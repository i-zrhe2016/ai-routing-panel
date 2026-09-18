import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class RestoreBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle_module = load_module(
            "build_backup_bundle_for_restore", ROOT / "scripts" / "build_backup_bundle.py"
        )
        cls.uploader = load_module(
            "upload_backup_r2_for_restore", ROOT / "scripts" / "upload_backup_r2.py"
        )
        cls.restore = load_module("restore_backup_under_test", ROOT / "scripts" / "restore_backup.py")

    def _create_bundle(self, root, include_ai_config=True):
        root.mkdir(parents=True, exist_ok=True)
        database = root / "panel-20260917T030000Z.db"
        with sqlite3.connect(str(database)) as conn:
            conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO customers (name) VALUES ('customer')")

        project = root / "project"
        runtime = project / "app" / "xray" / "runtime"
        runtime.mkdir(parents=True)
        (project / ".env").write_text("PANEL_CONFIG_MARKER=control\n", encoding="utf-8")
        (project / "app" / "xray" / ".env").write_text(
            "XRAY_LISTEN_PORT=443\n", encoding="utf-8"
        )
        (runtime / "config.json").write_text('{"inbounds": []}\n', encoding="utf-8")
        if include_ai_config:
            (runtime / "config-ai-node.json").write_text(
                '{"inbounds": [{"port": 27166}]}\n', encoding="utf-8"
            )
        (runtime / "panel-ports.json").write_text("{}\n", encoding="utf-8")

        uploads = root / "data" / "uploads" / "payment-proofs"
        uploads.mkdir(parents=True)
        (uploads / "proof.txt").write_bytes(b"attachment")

        ops_db = root / "ops.db"
        with sqlite3.connect(str(ops_db)) as conn:
            conn.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO reports (value) VALUES ('report')")

        return self.bundle_module.create_backup_bundle(
            database,
            [str(project), str(root / "data" / "uploads")],
            root / "backups",
            "panel-test",
            named_paths=[(ops_db, "database/ops.db")],
        )

    def _add_archive_member(self, bundle, destination, member):
        with tarfile.open(bundle, "r:gz") as source, tarfile.open(destination, "w:gz") as target:
            for existing in source.getmembers():
                if existing.isfile():
                    target.addfile(existing, source.extractfile(existing))
                else:
                    target.addfile(existing)
            if member.isfile():
                target.addfile(member, io.BytesIO(b"unsafe"))
            else:
                target.addfile(member)

    def test_prepare_restores_shared_data_user_files_control_files_and_separate_nodes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            output = root / "restore"

            result = self.restore.prepare_restore(bundle, output)

            self.assertTrue(result["recoveryReady"])
            self.assertEqual(
                (output / "data" / "panel.db").read_bytes(),
                (root / "panel-20260917T030000Z.db").read_bytes(),
            )
            self.assertTrue((output / "data" / "xray-ops" / "ops.db").is_file())
            self.assertEqual(
                (output / "data" / "uploads" / "payment-proofs" / "proof.txt").read_bytes(),
                b"attachment",
            )
            self.assertEqual(
                (output / ".env").read_text(encoding="utf-8"),
                "PANEL_CONFIG_MARKER=control\n",
            )
            self.assertEqual(
                (output / "app" / "xray" / ".env").read_text(encoding="utf-8"),
                "XRAY_LISTEN_PORT=443\n",
            )
            self.assertEqual(
                (output / "app" / "xray" / "runtime" / "config.json").read_text(
                    encoding="utf-8"
                ),
                '{"inbounds": []}\n',
            )
            self.assertEqual(
                (
                    output
                    / "nodes"
                    / "normal-data-plane"
                    / "app"
                    / "xray"
                    / "runtime"
                    / "config.json"
                ).read_text(encoding="utf-8"),
                '{"inbounds": []}\n',
            )
            self.assertEqual(
                (
                    output
                    / "nodes"
                    / "ai-data-plane"
                    / "app"
                    / "xray"
                    / "runtime"
                    / "config.json"
                ).read_text(encoding="utf-8"),
                '{"inbounds": [{"port": 27166}]}\n',
            )
            self.assertEqual((output / "data" / "panel.db").stat().st_mode & 0o777, 0o600)
            self.assertEqual((output / "data").stat().st_mode & 0o777, 0o700)
            self.assertTrue((output / "restore-report.json").is_file())

    def test_control_source_without_project_data_or_app_keeps_archive_relative_path(self):
        self.assertEqual(
            self.restore._control_restore_path(
                "config/etc/xray/config.json",
                {"sourcePath": "/etc/xray/config.json"},
            ),
            "etc/xray/config.json",
        )

    def test_readiness_is_recomputed_from_required_artifact_status(self):
        readiness = self.restore._recompute_readiness(
            {
                "sharedState": {
                    "requiredArtifacts": [
                        {"name": "panel-database", "archivePath": "database/panel.db", "status": "ok"}
                    ]
                },
                "nodes": [
                    {
                        "role": "ai-data-plane",
                        "configured": True,
                        "requiredArtifacts": [
                            {"name": "xray-config", "archivePath": "", "status": "missing"}
                        ],
                    }
                ],
                "recoveryReady": True,
            }
        )

        self.assertFalse(readiness["recoveryReady"])
        self.assertFalse(readiness["nodes"][0]["recoveryReady"])
        self.assertEqual(readiness["nodes"][0]["missingRequiredArtifacts"], ["xray-config"])

    def test_encrypted_bundle_can_be_validated_and_prepared_from_password_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            encrypted = root / "bundle.tar.gz.enc"
            password_file = root / "restore-password"
            password_file.write_text("test-password\n", encoding="utf-8")
            self.uploader.encrypt_bundle(bundle, encrypted, "test-password")

            summary = self.restore.validate_restore_bundle(encrypted, password_file=password_file)
            output = root / "restore"
            result = self.restore.prepare_restore(encrypted, output, password_file=password_file)

            self.assertTrue(summary["recoveryReady"])
            self.assertTrue(result["recoveryReady"])
            self.assertTrue((output / "data" / "panel.db").is_file())

    def test_wrong_password_fails_before_prepare_directory_is_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            encrypted = root / "bundle.enc"
            self.uploader.encrypt_bundle(bundle, encrypted, "correct")

            output = root / "restore"
            with self.assertRaisesRegex(ValueError, "authentication") as context:
                self.restore.prepare_restore(encrypted, output, passphrase="wrong")
            self.assertNotIn("wrong", str(context.exception))
            self.assertFalse(output.exists())

    def test_failed_materialization_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            output = root / "restore"
            original_write_file = self.restore._write_file
            calls = 0

            def fail_on_second_write(path, data, force):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic materialization failure")
                return original_write_file(path, data, force)

            with (
                mock.patch.object(self.restore, "_write_file", side_effect=fail_on_second_write),
                self.assertRaisesRegex(OSError, "synthetic materialization failure"),
            ):
                self.restore.prepare_restore(bundle, output)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".restore.restore-*")), [])

    def test_archive_path_safety_rejects_traversal_and_links(self):
        cases = (
            ("parent", tarfile.TarInfo("../outside.txt")),
            ("absolute", tarfile.TarInfo("/outside.txt")),
            ("symlink", tarfile.TarInfo("unsafe-link")),
            ("hardlink", tarfile.TarInfo("unsafe-hardlink")),
        )
        cases[2][1].type = tarfile.SYMTYPE
        cases[2][1].linkname = "/outside.txt"
        cases[3][1].type = tarfile.LNKTYPE
        cases[3][1].linkname = "backup-manifest.json"

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            for label, member in cases:
                with self.subTest(label=label):
                    malicious = root / f"{label}.tar.gz"
                    output = root / f"restore-{label}"
                    self._add_archive_member(bundle, malicious, member)

                    with self.assertRaisesRegex(ValueError, "unsafe|links"):
                        self.restore.prepare_restore(malicious, output)
                    self.assertFalse(output.exists())
                    self.assertFalse((root / "outside.txt").exists())

    def test_cli_validate_prepare_and_require_ready_exit_code(self):
        script = ROOT / "scripts" / "restore_backup.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ready_bundle = self._create_bundle(root / "ready")
            ready_output = root / "ready-restore"

            validate = subprocess.run(
                [sys.executable, str(script), "validate", "--bundle", str(ready_bundle)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertTrue(json.loads(validate.stdout)["recoveryReady"])

            prepare = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "prepare",
                    "--bundle",
                    str(ready_bundle),
                    "--output-dir",
                    str(ready_output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(prepare.returncode, 0, prepare.stderr)
            self.assertTrue((ready_output / "restore-report.json").is_file())

            incomplete_bundle = self._create_bundle(root / "incomplete", include_ai_config=False)
            require_ready = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "validate",
                    "--bundle",
                    str(incomplete_bundle),
                    "--require-ready",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(require_ready.returncode, 2)
            self.assertFalse(json.loads(require_ready.stdout)["recoveryReady"])

    def test_incomplete_node_is_rejected_unless_explicitly_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root, include_ai_config=False)
            output = root / "restore"

            with self.assertRaisesRegex(ValueError, "recovery is not ready"):
                self.restore.prepare_restore(bundle, output)
            self.assertFalse(output.exists())

            result = self.restore.prepare_restore(bundle, output, allow_incomplete=True)
            self.assertFalse(result["recoveryReady"])
            self.assertTrue((output / "data" / "panel.db").is_file())

    def test_non_empty_output_is_rejected_without_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            output = root / "restore"
            output.mkdir()
            (output / "existing.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "not empty"):
                self.restore.prepare_restore(bundle, output)
            self.assertEqual((output / "existing.txt").read_text(encoding="utf-8"), "keep")

    def test_password_is_read_from_named_environment_variable_without_cli_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = self._create_bundle(root)
            encrypted = root / "bundle.enc"
            self.uploader.encrypt_bundle(bundle, encrypted, "env-password")

            original = os.environ.get("TEST_RESTORE_PASSWORD")
            os.environ["TEST_RESTORE_PASSWORD"] = "env-password"
            try:
                result = self.restore.validate_restore_bundle(
                    encrypted, password_env="TEST_RESTORE_PASSWORD"
                )
            finally:
                if original is None:
                    os.environ.pop("TEST_RESTORE_PASSWORD", None)
                else:
                    os.environ["TEST_RESTORE_PASSWORD"] = original

            self.assertTrue(result["recoveryReady"])


if __name__ == "__main__":
    unittest.main()
