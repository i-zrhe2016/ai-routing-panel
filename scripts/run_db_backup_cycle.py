#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from collect_remote_backup import collect_remote_configs
    from upload_backup_r2 import encrypt_bundle, upload_bundle
except ModuleNotFoundError:
    from scripts.build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from scripts.collect_remote_backup import collect_remote_configs
    from scripts.upload_backup_r2 import encrypt_bundle, upload_bundle


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
TRUE_VALUES = {"1", "true", "yes", "on"}


def env_enabled(name, default="0"):
    raw = str(os.environ.get(name, default)).strip().lower()
    return raw in TRUE_VALUES


def run_backup():
    with tempfile.NamedTemporaryFile(prefix="panel-backup-path-", suffix=".txt", delete=False) as handle:
        path_file = Path(handle.name)

    command = [
        sys.executable,
        str(BACKUP_SCRIPT),
        "--db-path",
        os.environ.get("DB_PATH", "/data/panel.db"),
        "--backup-dir",
        os.environ.get("DB_BACKUP_DIR", "/backups"),
        "--keep-days",
        str(os.environ.get("DB_BACKUP_KEEP_DAYS", "7")),
        "--prefix",
        os.environ.get("DB_BACKUP_PREFIX", "xray-routing-panel"),
        "--latest-path-file",
        str(path_file),
    ]

    try:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return completed.returncode, None
        raw_path = path_file.read_text(encoding="utf-8").strip()
        if not raw_path:
            print("[backup] skipped: source database was not found.", flush=True)
            return 0, None
        backup_path = Path(raw_path)
        if not backup_path.is_file():
            raise RuntimeError(f"backup path file did not point to a file backup: {backup_path}")
        return 0, backup_path
    finally:
        path_file.unlink(missing_ok=True)


def bundle_enabled():
    return env_enabled("DB_BACKUP_BUNDLE_ENABLED", "1")


def collect_remote_backup(staging_dir):
    required = env_enabled("DB_BACKUP_SSH_COLLECTION_REQUIRED", "0")
    result = collect_remote_configs(Path(staging_dir), required=required)
    for node in result["manifest"]["nodes"]:
        print(
            f"[backup:ssh] role={node['role']} status={node['status']}",
            flush=True,
        )
    return Path(result["output_dir"])


def snapshot_optional_database(source_path, destination_dir, archive_name):
    source = Path(source_path)
    if not source.is_file():
        print(f"[backup] optional database missing: {source}", flush=True)
        return None

    destination = Path(destination_dir) / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(str(destination)) as backup_db:
            source_db.backup(backup_db)
    os.chmod(destination, 0o600)
    return destination, Path("database") / archive_name


def build_bundle(backup_path, named_paths=()):
    bundle_dir = Path(
        os.environ.get("DB_BACKUP_BUNDLE_DIR", os.environ.get("DB_BACKUP_DIR", "/backups"))
    )
    keep_days_raw = str(
        os.environ.get("DB_BACKUP_BUNDLE_KEEP_DAYS", os.environ.get("DB_BACKUP_KEEP_DAYS", "7"))
    ).strip()
    try:
        keep_days = int(keep_days_raw or "7")
    except ValueError as exc:
        raise ValueError(
            "DB_BACKUP_BUNDLE_KEEP_DAYS must be a non-negative integer"
        ) from exc
    if keep_days < 0:
        raise ValueError("DB_BACKUP_BUNDLE_KEEP_DAYS must be a non-negative integer")
    prefix = os.environ.get("DB_BACKUP_BUNDLE_PREFIX", os.environ.get("DB_BACKUP_PREFIX", "xray-routing-panel"))
    extra_paths = [
        *parse_extra_paths(os.environ.get("DB_BACKUP_EXTRA_PATHS", "")),
    ]
    bundle_path = create_backup_bundle(
        backup_path,
        extra_paths,
        bundle_dir,
        prefix,
        named_paths=named_paths,
    )
    removed = prune_bundles(bundle_dir, prefix, keep_days)
    if removed:
        print(f"[backup] pruned {removed} old disaster bundle(s)", flush=True)
    return bundle_path


def main():
    backup_code, backup_path = run_backup()
    if backup_code != 0:
        return backup_code
    if backup_path is None:
        return 0

    with tempfile.TemporaryDirectory(prefix="xray-remote-backup-") as staging_dir:
        named_paths = []
        bundle_is_enabled = bundle_enabled()
        ops_db_path = str(os.environ.get("DB_BACKUP_OPS_DB_PATH", "")).strip()
        if bundle_is_enabled and ops_db_path:
            ops_snapshot = snapshot_optional_database(ops_db_path, staging_dir, "ops.db")
            if ops_snapshot:
                named_paths.append(ops_snapshot)
        if bundle_is_enabled and env_enabled("DB_BACKUP_SSH_COLLECTION_ENABLED", "0"):
            named_paths.append((collect_remote_backup(staging_dir), "nodes"))
        elif bundle_is_enabled:
            print(
                "[backup:ssh] skipped: DB_BACKUP_SSH_COLLECTION_ENABLED is disabled.",
                flush=True,
            )

        upload_path = backup_path
        if bundle_is_enabled:
            upload_path = build_bundle(backup_path, named_paths=named_paths)
        else:
            print(
                "[backup] disaster bundle disabled: DB_BACKUP_BUNDLE_ENABLED is disabled.",
                flush=True,
            )

        if not env_enabled("DB_BACKUP_R2_ENABLED"):
            print("[backup:r2] skipped: DB_BACKUP_R2_ENABLED is disabled.", flush=True)
            return 0

        record_path = os.environ.get(
            "DB_BACKUP_R2_RECORD_PATH", "/backups/r2-upload-record.json"
        )
        encrypted_path = encrypt_bundle(upload_path)
        try:
            upload_bundle(encrypted_path, record_path=record_path)
        finally:
            encrypted_path.unlink(missing_ok=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
