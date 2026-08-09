#!/usr/bin/env python3
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from collect_remote_backup import collect_remote_configs
except ModuleNotFoundError:  # Imported by the repository test suite.
    from scripts.build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from scripts.collect_remote_backup import collect_remote_configs


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
UPLOAD_SCRIPT = ROOT / "components" / "db-backup-uploader" / "shard-upload.js"
DEFAULT_PASSWORD = "your-strong-password-here-123456"
TRUE_VALUES = {"1", "true", "yes", "on"}


def env_enabled(name, default="0"):
    raw = str(os.environ.get(name, default)).strip().lower()
    return raw in TRUE_VALUES


def sanitize_name(value):
    sanitized = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower())
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    return sanitized or "db-backup"


def build_artifact_name(file_path=None):
    configured = str(os.environ.get("DB_BACKUP_UPLOADER_ARTIFACT_NAME", "")).strip()
    if configured:
        return sanitize_name(configured)
    prefix = os.environ.get("DB_BACKUP_PREFIX", "xray-routing-panel")
    suffix = "db-backup"
    if file_path and Path(file_path).suffixes[-2:] == [".tar", ".gz"]:
        suffix = "disaster-backup"
    return f"{sanitize_name(prefix)}-{suffix}"


def build_package_version(backup_path):
    configured = str(os.environ.get("DB_BACKUP_UPLOADER_PACKAGE_VERSION", "")).strip()
    if configured:
        return configured

    match = re.search(r"(\d{8})T(\d{6})Z", backup_path.name)
    if match:
        date_part, time_part = match.groups()
    else:
        now = datetime.now(timezone.utc)
        date_part = now.strftime("%Y%m%d")
        time_part = now.strftime("%H%M%S")
    return f"{int(date_part)}.{int(time_part)}.0"


def ensure_upload_password():
    password = str(os.environ.get("DB_BACKUP_UPLOADER_PASSWORD", "")).strip()
    if not password:
        raise RuntimeError("DB_BACKUP_UPLOADER_PASSWORD is required when backup upload is enabled.")
    if password == DEFAULT_PASSWORD:
        raise RuntimeError("DB_BACKUP_UPLOADER_PASSWORD must not use the upstream placeholder password.")
    return password


def build_upload_env(password):
    data_dir = Path(os.environ.get("DB_BACKUP_UPLOADER_DATA_DIR", "/db-backup-uploader-data"))
    files_dir = Path(os.environ.get("DB_BACKUP_UPLOADER_FILES_DIR", str(data_dir / "files")))
    shards_dir = Path(os.environ.get("DB_BACKUP_UPLOADER_SHARDS_DIR", str(data_dir / "shards")))
    restored_dir = Path(
        os.environ.get("DB_BACKUP_UPLOADER_RESTORED_DIR", str(data_dir / "restored"))
    )
    record_path = Path(
        os.environ.get("DB_BACKUP_UPLOADER_RECORD_PATH", str(data_dir / "upload-records.json"))
    )

    for directory in [data_dir, files_dir, shards_dir, restored_dir, record_path.parent]:
        directory.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SHARD_PASSWORD"] = password
    env["FILES_DIR"] = str(files_dir)
    env["SHARDS_DIR"] = str(shards_dir)
    env["OUTPUT_DIR"] = str(restored_dir)
    env["UPLOAD_RECORD_PATH"] = str(record_path)
    env["UPLOAD_RECORD_HISTORY_LIMIT"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_RECORD_HISTORY_LIMIT", "20")
    )
    env["NPM_SCOPE"] = str(os.environ.get("DB_BACKUP_UPLOADER_SCOPE", "")).strip()
    env["NPM_ACCESS"] = str(os.environ.get("DB_BACKUP_UPLOADER_ACCESS", "public")).strip() or "public"
    env["NPM_PACKAGE_VERSION"] = str(os.environ.get("DB_BACKUP_UPLOADER_PACKAGE_VERSION", "")).strip()
    env["NPM_REGISTRY"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_REGISTRY", "https://registry.npmjs.org")
    ).strip()
    env["NPM_WEB_BASE"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_NPM_WEB_BASE", "https://www.npmjs.com/package")
    ).strip()
    env["NPM_TAG"] = str(os.environ.get("DB_BACKUP_UPLOADER_TAG", "latest")).strip() or "latest"
    env["NPM_PUBLISH_OTP"] = str(os.environ.get("DB_BACKUP_UPLOADER_OTP", "")).strip()
    env["PUBLISH_CONCURRENCY"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_PUBLISH_CONCURRENCY", "2")
    )
    env["NPM_PUBLISH_TIMEOUT_MS"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_NPM_PUBLISH_TIMEOUT_MS", "600000")
    )
    # npm is the long-term disaster-recovery channel.  Keep immutable package
    # versions by default; local retention is handled by DB_BACKUP_KEEP_DAYS.
    env["PRUNE_REMOTE_UPLOADS"] = "1" if env_enabled(
        "DB_BACKUP_UPLOADER_PRUNE_REMOTE", "0"
    ) else "0"
    env["SHARD_SIZE_BYTES"] = str(
        os.environ.get("DB_BACKUP_UPLOADER_SHARD_SIZE_BYTES", str(5 * 1024 * 1024))
    )
    env["DRY_RUN"] = "1" if env_enabled("DB_BACKUP_UPLOADER_DRY_RUN") else "0"

    npmrc_path = str(
        os.environ.get("DB_BACKUP_UPLOADER_NPMRC_PATH", str(data_dir / ".npmrc"))
    ).strip()
    if npmrc_path and Path(npmrc_path).exists():
        env["NPM_CONFIG_USERCONFIG"] = npmrc_path
    elif npmrc_path and not env_enabled("DB_BACKUP_UPLOADER_DRY_RUN"):
        print(
            f"[upload] npmrc not found at {npmrc_path}; relying on existing npm auth config.",
            file=sys.stderr,
            flush=True,
        )

    return env


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


def run_upload(backup_path):
    password = ensure_upload_password()
    artifact_name = build_artifact_name(backup_path)
    package_version = build_package_version(backup_path)
    env = build_upload_env(password)
    env["NPM_PACKAGE_VERSION"] = package_version

    command = [
        "node",
        str(UPLOAD_SCRIPT),
        "--file",
        str(backup_path),
        "--artifact",
        artifact_name,
        "--version",
        package_version,
    ]

    print(f"[upload] processing {backup_path}", flush=True)
    print(f"[upload] artifact={artifact_name} version={package_version}", flush=True)
    completed = subprocess.run(command, cwd=str(ROOT), env=env, check=False)
    return completed.returncode


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

        if not env_enabled("DB_BACKUP_UPLOADER_ENABLED"):
            print("[upload] skipped: DB_BACKUP_UPLOADER_ENABLED is disabled.", flush=True)
            return 0

        return run_upload(upload_path)


if __name__ == "__main__":
    raise SystemExit(main())
