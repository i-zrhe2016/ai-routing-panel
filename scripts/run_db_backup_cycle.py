#!/usr/bin/env python3
import os
import json
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    from build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from collect_remote_backup import collect_remote_configs
    from node_recovery import validate_backup_bundle
    from upload_backup_r2 import encrypt_bundle, upload_bundle
except ModuleNotFoundError:
    from scripts.build_backup_bundle import create_backup_bundle, parse_extra_paths, prune_bundles
    from scripts.collect_remote_backup import collect_remote_configs
    from scripts.node_recovery import validate_backup_bundle
    from scripts.upload_backup_r2 import encrypt_bundle, upload_bundle


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
TRUE_VALUES = {"1", "true", "yes", "on"}


def emit_backup_event(event, result="success", message="", exc=None):
    payload = {
        "schema_version": "1",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": "error" if result == "failure" else "info",
        "service": "control-plane",
        "category": "background",
        "event": event,
        "result": result,
        "actor_type": "system",
        "actor_id": "",
        "resource_type": "backup",
        "resource_id": "",
        "request_id": "",
        "endpoint": "",
        "method": "",
        "status_code": None,
        "duration_ms": None,
        "error_code": "backup_failed" if result == "failure" else "",
        "message": str(message or "")[:2000],
        "metadata": {},
    }
    if exc is not None:
        payload["error_type"] = type(exc).__name__
        payload["stacktrace"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)


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
    # Keep the node collector's dedicated empty staging directory separate
    # from optional snapshots (for example ops.db) written by this cycle.
    node_staging_dir = Path(staging_dir) / "nodes"
    result = collect_remote_configs(node_staging_dir, required=required)
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


def write_recovery_status(bundle_path, validated):
    """Keep a small local readiness record for monitoring and operators."""

    configured_path = str(os.environ.get("DB_BACKUP_RECOVERY_STATUS_PATH", "")).strip()
    status_path = Path(configured_path) if configured_path else Path(bundle_path).parent / "node-recovery-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "bundle": str(Path(bundle_path).resolve()),
        **validated["readiness"],
    }
    temporary = status_path.with_name(f".{status_path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, status_path)
    finally:
        temporary.unlink(missing_ok=True)
    return status_path


def enforce_recovery_readiness(bundle_path):
    validated = validate_backup_bundle(bundle_path)
    readiness = validated["readiness"]
    status_path = write_recovery_status(bundle_path, validated)
    print(
        f"[backup:recovery] ready={str(readiness['recoveryReady']).lower()} "
        f"shared={str(readiness['sharedReady']).lower()} status={status_path}",
        flush=True,
    )
    for node in readiness["nodes"]:
        missing = ",".join(node["missingRequiredArtifacts"]) or "-"
        print(
            f"[backup:recovery] role={node['role']} "
            f"configured={str(node['configured']).lower()} "
            f"ready={str(node['recoveryReady']).lower()} missing={missing}",
            flush=True,
        )
    if env_enabled("DB_BACKUP_RECOVERY_REQUIRED", "0") and not readiness["recoveryReady"]:
        raise RuntimeError("node recovery artifacts are incomplete; see node-recovery-status.json")
    return validated


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
            enforce_recovery_readiness(upload_path)
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
    try:
        exit_code = main()
    except Exception as exc:
        emit_backup_event("backup.failed", result="failure", exc=exc)
        raise
    if exit_code == 0:
        emit_backup_event("backup.completed")
    else:
        emit_backup_event("backup.failed", result="failure", message=f"exit_code={exit_code}")
    raise SystemExit(exit_code)
