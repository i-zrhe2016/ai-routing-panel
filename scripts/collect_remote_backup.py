#!/usr/bin/env python3
"""Collect node configuration files over strict, read-only SSH.

This is intentionally separate from the archive builder and remote uploader.  It
only reads regular files from the normal and AI data planes and writes a local
staging directory plus a collection manifest.  The staging directory is then
included in the encrypted disaster bundle by ``run_db_backup_cycle.py``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REMOTE_READ_SCRIPT = r"""
import base64
import hashlib
import json
import os
import stat
import sys

role = sys.argv[1]
paths = sys.argv[2:]
max_bytes = max(1, int(os.environ.get("XRAY_BACKUP_REMOTE_MAX_FILE_BYTES", "5242880")))
files = []

for raw_path in paths:
    path = os.path.expanduser(raw_path)
    item = {"path": path, "exists": False, "status": "missing"}
    try:
        # lstat deliberately rejects symlinks.  A backup path must point to a
        # regular file on the remote node, not an attacker-controlled target.
        metadata = os.lstat(path)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        item["status"] = "unreadable" if isinstance(exc, PermissionError) else "missing"
        files.append(item)
        continue

    item.update(
        {
            "exists": True,
            "mode": stat.S_IMODE(metadata.st_mode),
            "mtime": float(metadata.st_mtime),
            "size": int(metadata.st_size),
        }
    )
    if not stat.S_ISREG(metadata.st_mode):
        item["status"] = "not_regular_file"
        files.append(item)
        continue
    if metadata.st_size > max_bytes:
        item["status"] = "too_large"
        files.append(item)
        continue

    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
        ):
            item["status"] = "changed"
            files.append(item)
            continue
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            item["status"] = "too_large"
            files.append(item)
            continue
        item.update(
            {
                "status": "ok",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "data_base64": base64.b64encode(data).decode("ascii"),
            }
        )
    except (PermissionError, OSError) as exc:
        item["status"] = "unreadable"
        item["error"] = type(exc).__name__
    finally:
        if fd is not None:
            os.close(fd)
    files.append(item)

print(json.dumps({"version": 1, "role": role, "files": files}, ensure_ascii=True))
"""


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_KEY_PATH = "/run/secrets/fleet_ssh_key"
DEFAULT_KNOWN_HOSTS = "/root/.ssh/known_hosts"
DEFAULT_AI_KNOWN_HOSTS = "/root/.ssh/known_hosts_ai"
DEFAULT_DATAPLANE_CONFIG_PATH = "/root/xray-routing-panel/app/xray/runtime/config.json"
DEFAULT_DATAPLANE_ENV_PATH = "/root/xray-routing-panel/app/xray/.env"
DEFAULT_DATAPLANE_DEPLOY_ROOT = "/root/xray-routing-panel"
DEFAULT_AI_CONFIG_PATH = "/etc/xray/config.json"
DEFAULT_AI_ENV_PATH = "/etc/xray/.env"
DEFAULT_AI_DEPLOY_ROOT = "/root/xray-routing-panel"
DEFAULT_NORMAL_TARGET = "root@100.65.108.93"
DEFAULT_AI_TARGET = ""
DEFAULT_AI_SSH_PORT = "22"
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_NORMAL_REMOTE_PATHS = (
    DEFAULT_DATAPLANE_CONFIG_PATH,
    DEFAULT_DATAPLANE_ENV_PATH,
    "/root/xray-routing-panel/app/xray/runtime/panel-ports.json",
    "/root/xray-routing-panel/app/xray/runtime/dynamic-routing.json",
    "/root/xray-routing-panel/app/xray/runtime/client-test.json",
    "/root/xray-routing-panel/app/xray/runtime/client-share.txt",
    "/root/xray-routing-panel/app/xray/reports/hourly-domains/latest.json",
)
DEFAULT_AI_REMOTE_PATHS = (DEFAULT_AI_CONFIG_PATH, DEFAULT_AI_ENV_PATH)


def env_enabled(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in TRUE_VALUES


def parse_non_negative_int(value: str, default: int) -> int:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"invalid non-negative integer: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"invalid non-negative integer: {value!r}")
    return parsed


def parse_paths(value: str, defaults: tuple[str, ...] = ()) -> tuple[str, ...]:
    items = []
    seen = set()
    for raw in re.split(r"[,\n]+", str(value or "")):
        item = raw.strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    if not items:
        for item in defaults:
            if item and item not in seen:
                seen.add(item)
                items.append(item)
    return tuple(items)


def split_options(value: str) -> tuple[str, ...]:
    return tuple(shlex.split(str(value or "")))


def validate_options(options: tuple[str, ...]) -> None:
    # The collector owns identity, host-key and command-execution boundaries.
    # Do not let an environment value append a last-option-wins override such
    # as IdentityFile, UserKnownHostsFile, ProxyCommand or RemoteCommand.
    allowed_flags = {"-4", "-6", "-q", "-v", "-vv", "-vvv"}
    allowed_keys = {
        "compression",
        "connecttimeout",
        "ipqos",
        "loglevel",
        "serveralivecountmax",
        "serveraliveinterval",
    }
    dangerous_keys = {
        "controlmaster",
        "controlpath",
        "identityfile",
        "localcommand",
        "proxycommand",
        "proxyjump",
        "remotecommand",
        "userknownhostsfile",
    }
    index = 0
    while index < len(options):
        token = options[index]
        if token in allowed_flags:
            index += 1
            continue
        if token != "-o" or index + 1 >= len(options):
            raise ValueError(f"unsupported remote backup SSH option: {token}")
        key = options[index + 1].split("=", 1)[0].strip().lower()
        if key in dangerous_keys or key not in allowed_keys:
            raise ValueError(f"unsupported remote backup SSH option: {options[index + 1]}")
        index += 2


def safe_component(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "")).strip("-")
    return re.sub(r"-{2,}", "-", safe) or "node"


def safe_relative_path(remote_path: str) -> Path:
    parts = [part for part in Path(remote_path).as_posix().split("/") if part not in {"", ".", ".."}]
    return Path(*parts) if parts else Path("root")


def portable_restore_path(role: str, remote_path: str, deploy_root: str) -> str:
    """Map a remote file to the directory emitted by ``node_recovery.py``."""

    normalized = Path(remote_path).as_posix()
    root = Path(deploy_root).as_posix().rstrip("/")
    if root and (normalized == root or normalized.startswith(f"{root}/")):
        relative = normalized[len(root) :].lstrip("/")
        if relative:
            return relative

    name = Path(normalized).name
    if name == "config.json":
        return "app/xray/runtime/config.json"
    if name == ".env":
        return "app/xray/.env"
    if name in {"panel-ports.json", "dynamic-routing.json", "client-test.json", "client-share.txt"}:
        return f"app/xray/runtime/{name}"
    if "reports" in Path(normalized).parts:
        parts = Path(normalized).parts
        marker = parts.index("reports")
        return Path("app", "xray", *parts[marker:]).as_posix()
    return Path("remote", safe_relative_path(normalized)).as_posix()


def required_paths(paths: tuple[str, ...], config_hint: str, default_env: str) -> tuple[str, ...]:
    """Choose the config and env paths even when callers add custom paths first."""

    config = config_hint or next(
        (path for path in paths if Path(path).name == "config.json"),
        paths[0] if paths else "",
    )
    env = next((path for path in paths if Path(path).name == ".env" and path != config), "")
    if not env and default_env in paths:
        env = default_env
    if not env and config:
        env = str(Path(config).with_name(".env"))
    return tuple(path for path in (config, env) if path)


@dataclass(frozen=True)
class RemoteNode:
    role: str
    target: str
    paths: tuple[str, ...]
    known_hosts: str
    ssh_port: str = "22"
    options: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    restore_root: str = ""


def build_nodes() -> tuple[RemoteNode, ...]:
    normal_target = str(
        os.environ.get(
            "DB_BACKUP_DATAPLANE_SSH_TARGET",
            os.environ.get("DATAPLANE_SSH_TARGET", DEFAULT_NORMAL_TARGET),
        )
    ).strip()
    ai_target = str(
        os.environ.get(
            "DB_BACKUP_AI_NODE_SSH_TARGET",
            os.environ.get("AI_NODE_SSH_TARGET", DEFAULT_AI_TARGET),
        )
    ).strip()
    normal_config = str(
        os.environ.get("DB_BACKUP_DATAPLANE_CONFIG_PATH", os.environ.get("DATAPLANE_CONFIG_PATH", ""))
    ).strip()
    ai_config = str(
        os.environ.get("DB_BACKUP_AI_NODE_CONFIG_PATH", os.environ.get("AI_NODE_CONFIG_PATH", ""))
    ).strip()
    normal_path_value = os.environ.get("DB_BACKUP_DATAPLANE_REMOTE_PATHS", "")
    normal_defaults = list(DEFAULT_NORMAL_REMOTE_PATHS)
    if normal_config:
        normal_defaults[0] = normal_config
        normal_defaults[1] = str(Path(normal_config).with_name(".env"))
    normal_paths = parse_paths(normal_path_value, tuple(normal_defaults))
    ai_path_value = os.environ.get("DB_BACKUP_AI_NODE_REMOTE_PATHS", "")
    ai_defaults = list(DEFAULT_AI_REMOTE_PATHS)
    if ai_config:
        ai_defaults[0] = ai_config
        ai_defaults[1] = str(Path(ai_config).with_name(".env"))
    ai_paths = parse_paths(ai_path_value, tuple(ai_defaults))
    normal_restore_root = str(
        os.environ.get("DB_BACKUP_DATAPLANE_DEPLOY_ROOT", DEFAULT_DATAPLANE_DEPLOY_ROOT)
    ).strip() or DEFAULT_DATAPLANE_DEPLOY_ROOT
    ai_restore_root = str(
        os.environ.get("DB_BACKUP_AI_NODE_DEPLOY_ROOT", DEFAULT_AI_DEPLOY_ROOT)
    ).strip() or DEFAULT_AI_DEPLOY_ROOT
    common_options = split_options(os.environ.get("DB_BACKUP_SSH_OPTIONS", ""))
    normal_options = (
        *common_options,
        *split_options(
            os.environ.get(
                "DB_BACKUP_DATAPLANE_SSH_OPTIONS",
                os.environ.get("DATAPLANE_SSH_OPTIONS", ""),
            )
        ),
    )
    ai_options = (
        *common_options,
        *split_options(
            os.environ.get(
                "DB_BACKUP_AI_NODE_SSH_OPTIONS",
                os.environ.get("AI_NODE_SSH_OPTIONS", ""),
            )
        ),
    )
    validate_options(normal_options)
    validate_options(ai_options)
    default_known_hosts = str(
        os.environ.get("DB_BACKUP_SSH_KNOWN_HOSTS", DEFAULT_KNOWN_HOSTS)
    ).strip() or DEFAULT_KNOWN_HOSTS
    return (
        RemoteNode(
            role="normal-data-plane",
            target=normal_target,
            paths=normal_paths,
            known_hosts=str(
                os.environ.get("DB_BACKUP_DATAPLANE_KNOWN_HOSTS", default_known_hosts)
            ).strip()
            or default_known_hosts,
            ssh_port=str(os.environ.get("DB_BACKUP_DATAPLANE_SSH_PORT", "22")).strip() or "22",
            options=normal_options,
            required_paths=required_paths(
                normal_paths,
                normal_config,
                DEFAULT_DATAPLANE_ENV_PATH,
            ),
            restore_root=normal_restore_root,
        ),
        RemoteNode(
            role="ai-data-plane",
            target=ai_target,
            paths=ai_paths,
            known_hosts=str(
                os.environ.get("DB_BACKUP_AI_NODE_KNOWN_HOSTS", DEFAULT_AI_KNOWN_HOSTS)
            ).strip()
            or DEFAULT_AI_KNOWN_HOSTS,
            ssh_port=str(
                os.environ.get("DB_BACKUP_AI_NODE_SSH_PORT", DEFAULT_AI_SSH_PORT)
            ).strip()
            or DEFAULT_AI_SSH_PORT,
            options=ai_options,
            required_paths=required_paths(ai_paths, ai_config, DEFAULT_AI_ENV_PATH),
            restore_root=ai_restore_root,
        ),
    )


def resolve_key_path() -> Path:
    configured = str(os.environ.get("DB_BACKUP_SSH_KEY_PATH", DEFAULT_KEY_PATH)).strip()
    candidates = [Path(configured), Path("/root/ssh-keys/xray_fleet_ed25519_20260805")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"SSH identity file not found: {configured}")


def read_remote(node: RemoteNode, key_path: Path, timeout: int, max_bytes: int) -> dict:
    options = (
        *node.options,
        "-o",
        "BatchMode=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ChallengeResponseAuthentication=no",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={node.known_hosts}",
        "-o",
        f"ConnectTimeout={max(1, timeout)}",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "-i",
        str(key_path),
        "-p",
        node.ssh_port,
    )
    remote_command = " ".join(
        shlex.quote(value)
        for value in (
            "env",
            f"XRAY_BACKUP_REMOTE_MAX_FILE_BYTES={max_bytes}",
            "python3",
            "-c",
            REMOTE_READ_SCRIPT,
            node.role,
            *node.paths,
        )
    )
    try:
        completed = subprocess.run(
            [os.environ.get("DB_BACKUP_SSH_BIN", "ssh"), *options, node.target, remote_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, timeout + 5),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"SSH timeout for {node.role}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "remote command failed"
        raise RuntimeError(f"SSH collection failed for {node.role}: {detail[:500]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid SSH collection response for {node.role}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("role") != node.role
        or not isinstance(payload.get("files"), list)
    ):
        raise RuntimeError(f"invalid SSH collection response for {node.role}")
    return payload


def write_collection(output_dir: Path, node: RemoteNode, payload: dict) -> dict:
    node_dir = output_dir / safe_component(node.role)
    node_dir.mkdir(parents=True, exist_ok=True)
    required = tuple(node.required_paths or node.paths[:1])
    result = {
        "role": node.role,
        "target": node.target,
        "sshPort": node.ssh_port,
        "knownHosts": node.known_hosts,
        "requestedPaths": list(node.paths),
        "requiredPaths": list(required),
        "restoreRoot": node.restore_root,
        "status": "ok",
        "configCollected": False,
        "recoveryReady": False,
        "files": [],
    }
    collected_files = 0
    path_statuses = {}
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        entry = {key: value for key, value in item.items() if key != "data_base64"}
        remote_path = str(item.get("path", ""))
        if remote_path:
            entry["restorePath"] = portable_restore_path(
                node.role,
                remote_path,
                node.restore_root,
            )
        data_encoded = item.get("data_base64", "")
        if item.get("status") != "ok" or not data_encoded:
            if item.get("status") == "ok" and not data_encoded:
                entry["status"] = "missing_data"
            result["files"].append(entry)
            path_statuses[remote_path] = entry.get("status", item.get("status"))
            if entry.get("status") not in {"missing"} and remote_path not in required:
                result["status"] = "partial"
            elif remote_path in required:
                result["status"] = "partial"
            continue
        try:
            data = base64.b64decode(data_encoded, validate=True)
        except (ValueError, TypeError) as exc:
            entry["status"] = "invalid_base64"
            entry["error"] = str(exc)
            result["status"] = "partial"
            path_statuses[remote_path] = entry["status"]
            result["files"].append(entry)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest != item.get("sha256"):
            entry["status"] = "checksum_mismatch"
            result["status"] = "partial"
            path_statuses[remote_path] = entry["status"]
            result["files"].append(entry)
            continue
        destination = node_dir / safe_relative_path(str(item["path"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        os.chmod(destination, 0o600)
        entry["stagedPath"] = destination.relative_to(output_dir).as_posix()
        result["files"].append(entry)
        collected_files += 1
        path_statuses[remote_path] = "ok"
        if node.paths and remote_path == node.paths[0]:
            result["configCollected"] = True
    result["recoveryReady"] = bool(required) and all(
        path_statuses.get(path) == "ok" for path in required
    )
    if collected_files == 0 or not result["configCollected"] or not result["recoveryReady"]:
        result["status"] = "partial"
    return result


def collect_nodes(
    output_dir: Path,
    nodes: tuple[RemoteNode, ...],
    key_path: Path | None,
    required: bool,
    key_error: str = "",
) -> list[dict]:
    results = []
    timeout = parse_non_negative_int(os.environ.get("DB_BACKUP_SSH_TIMEOUT_SECONDS", "20"), 20)
    max_bytes = parse_non_negative_int(
        os.environ.get("DB_BACKUP_SSH_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES)),
        DEFAULT_MAX_FILE_BYTES,
    )
    for node in nodes:
        if not node.target:
            if required:
                raise RuntimeError(f"missing SSH target for {node.role}")
            results.append(
                {
                    "role": node.role,
                    "target": node.target,
                    "sshPort": node.ssh_port,
                    "knownHosts": node.known_hosts,
                    "status": "skipped_no_target",
                    "requestedPaths": list(node.paths),
                    "requiredPaths": list(node.required_paths or node.paths[:1]),
                    "restoreRoot": node.restore_root,
                    "recoveryReady": False,
                }
            )
            continue
        if key_path is None:
            results.append(
                {
                    "role": node.role,
                    "target": node.target,
                    "sshPort": node.ssh_port,
                    "knownHosts": node.known_hosts,
                    "requestedPaths": list(node.paths),
                    "status": "skipped_no_key",
                    "error": key_error,
                    "requiredPaths": list(node.required_paths or node.paths[:1]),
                    "restoreRoot": node.restore_root,
                    "recoveryReady": False,
                }
            )
            continue
        try:
            payload = read_remote(node, key_path, timeout, max_bytes)
            node_result = write_collection(output_dir, node, payload)
            results.append(node_result)
            if required and not node_result.get("recoveryReady", False):
                raise RuntimeError(f"remote recovery artifacts are incomplete for {node.role}")
        except Exception as exc:
            results.append(
                {
                    "role": node.role,
                    "target": node.target,
                    "sshPort": node.ssh_port,
                    "knownHosts": node.known_hosts,
                    "requestedPaths": list(node.paths),
                    "requiredPaths": list(node.required_paths or node.paths[:1]),
                    "restoreRoot": node.restore_root,
                    "status": "failed",
                    "recoveryReady": False,
                    "error": str(exc)[:500],
                }
            )
            if required:
                raise RuntimeError(str(exc)) from exc
    return results


def collect_remote_configs(output_dir: Path, required: bool = False) -> dict:
    output_dir = Path(output_dir)
    if output_dir.exists():
        if not output_dir.is_dir():
            raise NotADirectoryError(f"remote staging path is not a directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise FileExistsError(f"remote staging directory is not empty: {output_dir}")
    else:
        output_dir.mkdir(parents=True)
    nodes = build_nodes()
    manifest = {
        "version": 1,
        "purpose": "remote-node-config-collection",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "nodes": [],
    }
    key_temp = None
    try:
        source_key_path = resolve_key_path()
        key_temp = tempfile.TemporaryDirectory(prefix="xray-backup-ssh-key-")
        key_path = Path(key_temp.name) / "identity"
        shutil.copyfile(source_key_path, key_path)
        os.chmod(key_path, 0o600)
    except FileNotFoundError as exc:
        if required:
            raise
        key_path = None
        key_error = str(exc)
    try:
        manifest["nodes"] = collect_nodes(
            output_dir,
            nodes,
            key_path,
            required,
            key_error if key_path is None else "",
        )
        manifest_path = output_dir / "remote-node-collection.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        return {"output_dir": output_dir, "manifest_path": manifest_path, "manifest": manifest}
    finally:
        if key_temp is not None:
            key_temp.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Xray node configuration files over SSH.")
    parser.add_argument("--output-dir", default=os.environ.get("DB_BACKUP_REMOTE_STAGING_DIR", ""))
    parser.add_argument("--required", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(tempfile.mkdtemp(prefix="xray-remote-backup-"))
    )
    collect_remote_configs(output_dir, required=args.required)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
