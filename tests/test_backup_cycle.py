import importlib.util
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
RUN_CYCLE_SCRIPT = ROOT / "scripts" / "run_db_backup_cycle.py"


def load_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BackupCycleTest(unittest.TestCase):
    def create_source_db(self, root):
        db_path = root / "panel.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sample (value) VALUES ('ok')")
        return db_path

    def test_backup_db_writes_latest_path_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = self.create_source_db(root)
            backup_dir = root / "backups"
            latest_path_file = root / "latest.txt"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(BACKUP_SCRIPT),
                    "--db-path",
                    str(db_path),
                    "--backup-dir",
                    str(backup_dir),
                    "--keep-days",
                    "7",
                    "--prefix",
                    "panel-test",
                    "--latest-path-file",
                    str(latest_path_file),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            latest_backup = Path(latest_path_file.read_text(encoding="utf-8").strip())
            self.assertTrue(latest_backup.exists())
            self.assertEqual(latest_backup.parent, backup_dir)
            self.assertTrue(latest_backup.name.startswith("panel-test-"))
            self.assertEqual(latest_backup.stat().st_mode & 0o777, 0o600)

    def test_run_db_backup_cycle_keeps_backup_only_when_uploader_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = self.create_source_db(root)
            backup_dir = root / "backups"
            env = os.environ.copy()
            env.update(
                {
                    "DB_PATH": str(db_path),
                    "DB_BACKUP_DIR": str(backup_dir),
                    "DB_BACKUP_KEEP_DAYS": "7",
                    "DB_BACKUP_PREFIX": "panel-test",
                    "DB_BACKUP_UPLOADER_ENABLED": "0",
                }
            )

            completed = subprocess.run(
                [sys.executable, str(RUN_CYCLE_SCRIPT)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertIn("DB_BACKUP_UPLOADER_ENABLED is disabled", completed.stdout)
            backups = list(backup_dir.glob("panel-test-*.db"))
            self.assertEqual(len(backups), 1)

    def test_run_db_backup_cycle_skips_cleanly_when_database_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = os.environ.copy()
            env.update(
                {
                    "DB_PATH": str(root / "missing.db"),
                    "DB_BACKUP_DIR": str(root / "backups"),
                    "DB_BACKUP_UPLOADER_ENABLED": "0",
                }
            )
            completed = subprocess.run(
                [sys.executable, str(RUN_CYCLE_SCRIPT)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertIn("source database was not found", completed.stdout)

    def test_run_db_backup_cycle_builds_project_defaults(self):
        module = load_module("run_db_backup_cycle", RUN_CYCLE_SCRIPT)
        original_environ = os.environ.copy()
        try:
            os.environ["DB_BACKUP_PREFIX"] = "Xray Routing Panel"
            artifact_name = module.build_artifact_name()
            version = module.build_package_version(Path("/tmp/panel-test-20260619T030000Z.db"))
        finally:
            os.environ.clear()
            os.environ.update(original_environ)

        self.assertEqual(artifact_name, "xray-routing-panel-db-backup")
        self.assertEqual(version, "20260619.30000.0")

    def test_disaster_bundle_contains_database_and_extra_configuration(self):
        module = load_module("build_backup_bundle", ROOT / "scripts" / "build_backup_bundle.py")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = root / "panel-20260808T030000Z.db"
            database.write_bytes(b"sqlite snapshot")
            runtime = root / "app" / "xray" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "config.json").write_text('{"inbounds": []}\n', encoding="utf-8")
            bundle = module.create_backup_bundle(
                database,
                [str(runtime), str(root / "missing.env")],
                root / "backups",
                "panel-test",
            )
            self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)

            with tarfile.open(bundle, "r:gz") as archive:
                self.assertIn("database/panel-20260808T030000Z.db", archive.getnames())
                self.assertIn("config/" + str(runtime).lstrip("/") + "/config.json", archive.getnames())
                manifest = json.load(archive.extractfile("backup-manifest.json"))
                for entry in manifest["files"]:
                    archived = archive.extractfile(entry["archivePath"])
                    self.assertIsNotNone(archived)
                    data = archived.read()
                    self.assertEqual(entry["size"], len(data))
                    self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

            self.assertEqual(manifest["purpose"], "disaster-recovery")
            self.assertEqual(manifest["recoveryMode"], "offline")
            self.assertEqual(manifest["skippedExtraPaths"], [str(root / "missing.env")])

    def test_snapshot_optional_database_creates_consistent_copy(self):
        module = load_module("run_db_backup_cycle_ops", RUN_CYCLE_SCRIPT)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = self.create_source_db(root)
            result = module.snapshot_optional_database(source, root / "staging", "ops.db")
            self.assertIsNotNone(result)
            snapshot, archive_path = result
            self.assertEqual(archive_path.as_posix(), "database/ops.db")
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(str(snapshot)) as conn:
                self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_bundle_artifact_uses_disaster_name_and_preserves_remote_versions(self):
        module = load_module("run_db_backup_cycle_bundle", RUN_CYCLE_SCRIPT)
        original_environ = os.environ.copy()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.environ["DB_BACKUP_PREFIX"] = "Xray Routing Panel"
                self.assertEqual(
                    module.build_artifact_name(Path("/tmp/panel-disaster-20260808T030000Z.tar.gz")),
                    "xray-routing-panel-disaster-backup",
                )
                os.environ.pop("DB_BACKUP_UPLOADER_PRUNE_REMOTE", None)
                os.environ["DB_BACKUP_UPLOADER_DATA_DIR"] = str(Path(tmpdir) / "uploader")
                upload_env = module.build_upload_env("test-password")
                self.assertEqual(upload_env["PRUNE_REMOTE_UPLOADS"], "0")
            finally:
                os.environ.clear()
                os.environ.update(original_environ)


if __name__ == "__main__":
    unittest.main()
