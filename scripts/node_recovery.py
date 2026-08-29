#!/usr/bin/env python3
"""Build and consume the node-level recovery contract.

The normal disaster bundle contains the control-plane database and source
files, but a node replacement needs a smaller, explicit contract: which files
are required, where they came from, and where they must be restored.  This
module keeps that contract independent from SSH collection and R2 upload.

No credentials are generated or printed here.  The encrypted disaster bundle
is the credential carrier; prepared files are always written with restrictive
permissions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


NODE_RECOVERY_MANIFEST_NAME = "node-recovery-manifest.json"
DEFAULT_XRAY_IMAGE = "ghcr.io/xtls/xray-core:26.5.3"
RECOVERABLE_ROLES = ("normal-data-plane", "ai-data-plane")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative(value: str, label: str = "path") -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe {label}: {value!r}")
    return path.as_posix()


def _safe_archive_name(value: str) -> str:
    return _safe_relative(value, "archive path")


def _file_entry_index(file_entries: list[dict]) -> dict[str, dict]:
    if not isinstance(file_entries, list):
        raise ValueError("backup manifest files must be a list")
    indexed: dict[str, dict] = {}
    for entry in file_entries:
        if not isinstance(entry, dict):
            raise ValueError("backup manifest file entries must be objects")
        archive_path = str(entry.get("archivePath", ""))
        if not archive_path:
            raise ValueError("backup manifest file entry is missing archivePath")
        archive_path = _safe_archive_name(archive_path)
        if archive_path in indexed:
            raise ValueError(f"duplicate archive path in backup manifest: {archive_path}")
        indexed[archive_path] = entry
    return indexed


def _artifact_name(remote_path: str, index: int) -> str:
    name = PurePosixPath(str(remote_path or "")).name
    names = {
        "config.json": "xray-config",
        ".env": "xray-env",
        "panel-ports.json": "panel-ports",
        "dynamic-routing.json": "dynamic-routing",
        "client-test.json": "client-test",
        "client-share.txt": "client-share",
        "latest.json": "ai-domain-report",
        "panel.db": "node-panel-database",
    }
    return names.get(name, f"node-file-{index + 1}")


def _fallback_restore_path(role: str, remote_path: str) -> str:
    """Map known Xray paths to the portable node layout.

    The collector normally supplies ``restorePath``.  This fallback keeps old
    collection manifests usable when they are re-bundled.
    """

    path = PurePosixPath(str(remote_path or ""))
    name = path.name
    if name == "config.json":
        return "app/xray/runtime/config.json"
    if name == ".env":
        return "app/xray/.env"
    if name in {"panel-ports.json", "dynamic-routing.json", "client-test.json", "client-share.txt"}:
        return f"app/xray/runtime/{name}"
    if "reports" in path.parts:
        marker = path.parts.index("reports")
        return _safe_relative("app/xray/" + "/".join(path.parts[marker:]), "restore path")
    safe_parts = [part for part in path.parts if part not in {"", "/", ".", ".."}]
    prefix = "remote/" + "/".join(safe_parts)
    return _safe_relative(prefix, "restore path")


def _artifact_from_collection(
    role: str,
    item: dict,
    file_entries: dict[str, dict],
    index: int,
    required_paths: set[str],
) -> dict:
    remote_path = str(item.get("path", ""))
    staged_path = str(item.get("stagedPath", ""))
    archive_path = f"nodes/{staged_path}" if staged_path else ""
    status = str(item.get("status", "missing")) or "missing"
    entry = file_entries.get(archive_path) if archive_path else None
    if status == "ok" and entry is None:
        status = "archive_missing"
    artifact = {
        "name": _artifact_name(remote_path, index),
        "remotePath": remote_path,
        "restorePath": str(item.get("restorePath", ""))
        or _fallback_restore_path(role, remote_path),
        "status": status,
        "required": remote_path in required_paths,
    }
    if archive_path:
        artifact["archivePath"] = _safe_archive_name(archive_path)
    if entry is not None:
        artifact["size"] = int(entry.get("size", 0))
        artifact["sha256"] = str(entry.get("sha256", ""))
    if item.get("error"):
        artifact["error"] = str(item["error"])
    return artifact


def _remote_node_manifest(
    role: str,
    collection_node: dict | None,
    file_entries: dict[str, dict],
) -> dict:
    collection_node = collection_node or {}
    requested_paths = [str(item) for item in collection_node.get("requestedPaths", []) if str(item)]
    required_paths = [
        str(item) for item in collection_node.get("requiredPaths", []) if str(item)
    ] or requested_paths[:1]
    required_set = set(required_paths)
    artifacts: list[dict] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(collection_node.get("files", [])):
        if not isinstance(item, dict):
            continue
        remote_path = str(item.get("path", ""))
        if not remote_path or remote_path in seen_paths:
            continue
        seen_paths.add(remote_path)
        artifacts.append(
            _artifact_from_collection(role, item, file_entries, index, required_set)
        )

    for remote_path in required_paths:
        if remote_path in seen_paths:
            continue
        artifacts.append(
            {
                "name": _artifact_name(remote_path, len(artifacts)),
                "remotePath": remote_path,
                "restorePath": _fallback_restore_path(role, remote_path),
                "status": "not_collected",
                "required": True,
            }
        )

    required = [item for item in artifacts if item.get("required")]
    optional = [item for item in artifacts if not item.get("required")]
    configured = bool(str(collection_node.get("target", "")).strip())
    recovery_ready = configured and bool(required) and all(
        item.get("status") == "ok" and item.get("archivePath") for item in required
    )
    return {
        "role": role,
        "source": "remote-ssh",
        "configured": configured,
        "target": str(collection_node.get("target", "")),
        "sshPort": str(collection_node.get("sshPort", "22")),
        "collectionStatus": str(collection_node.get("status", "not_collected")),
        "requiredPaths": required_paths,
        "requiredArtifacts": required,
        "optionalArtifacts": optional,
        "recoveryReady": recovery_ready,
    }


def _find_local_entry(file_entries: dict[str, dict], suffix: str) -> tuple[str, dict] | None:
    candidates = []
    for archive_path, entry in file_entries.items():
        if not archive_path.startswith("config/"):
            continue
        source_path = str(entry.get("sourcePath", "")).replace("\\", "/")
        if source_path.endswith(suffix) or archive_path.endswith(suffix):
            candidates.append((archive_path, entry))
    return sorted(candidates, key=lambda item: item[0])[0] if candidates else None


def _local_artifact(
    file_entries: dict[str, dict],
    name: str,
    suffix: str,
    restore_path: str,
    *,
    required: bool = True,
) -> dict:
    found = _find_local_entry(file_entries, suffix)
    artifact = {
        "name": name,
        "sourcePath": suffix,
        "restorePath": restore_path,
        "status": "missing",
        "required": required,
    }
    if found is None:
        return artifact
    archive_path, entry = found
    artifact.update(
        {
            "archivePath": archive_path,
            "status": "ok",
            "size": int(entry.get("size", 0)),
            "sha256": str(entry.get("sha256", "")),
        }
    )
    return artifact


def _local_runtime_node_manifest(
    role: str, file_entries: dict[str, dict], config_suffix: str
) -> dict:
    required = [
        _local_artifact(
            file_entries,
            "xray-config",
            config_suffix,
            "app/xray/runtime/config.json",
        ),
        _local_artifact(file_entries, "xray-env", "/app/xray/.env", "app/xray/.env"),
    ]
    optional_specs = (
        ("panel-ports", "/app/xray/runtime/panel-ports.json"),
        ("dynamic-routing", "/app/xray/runtime/dynamic-routing.json"),
        ("client-test", "/app/xray/runtime/client-test.json"),
        ("client-share", "/app/xray/runtime/client-share.txt"),
    )
    optional = [
        _local_artifact(
            file_entries,
            name,
            suffix,
            f"app/xray/runtime/{Path(suffix).name}",
            required=False,
        )
        for name, suffix in optional_specs
    ]
    configured = any(item.get("status") == "ok" for item in required)
    return {
        "role": role,
        "source": "local-runtime",
        "configured": configured,
        "target": "local Xray runtime" if configured else "",
        "requiredPaths": [config_suffix, "/app/xray/.env"],
        "requiredArtifacts": required,
        "optionalArtifacts": optional,
        "recoveryReady": configured and all(item.get("status") == "ok" for item in required),
    }


def _local_ai_node_manifest(file_entries: dict[str, dict], remote_node: dict | None) -> dict:
    if remote_node and str(remote_node.get("target", "")).strip():
        return _remote_node_manifest("ai-data-plane", remote_node, file_entries)

    result = _local_runtime_node_manifest(
        "ai-data-plane", file_entries, "/app/xray/runtime/config-ai-node.json"
    )
    result["source"] = "control-plane-local"
    result["target"] = "local Docker xray-ai-node" if result["configured"] else ""
    return result


def _database_artifact(file_entries: dict[str, dict]) -> dict:
    candidates = [
        (path, entry)
        for path, entry in file_entries.items()
        if path.startswith("database/") and Path(path).name != "ops.db"
    ]
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return {
            "name": "panel-database",
            "restorePath": "data/panel.db",
            "status": "missing",
            "required": True,
        }
    archive_path, entry = candidates[0]
    return {
        "name": "panel-database",
        "archivePath": archive_path,
        "restorePath": "data/panel.db",
        "status": "ok",
        "required": True,
        "size": int(entry.get("size", 0)),
        "sha256": str(entry.get("sha256", "")),
    }


def build_node_recovery_manifest(
    file_entries: list[dict], remote_collection: dict | None = None
) -> dict:
    """Build the explicit recovery contract for a just-created archive."""

    indexed = _file_entry_index(file_entries)
    remote_nodes = {
        str(item.get("role")): item
        for item in (remote_collection or {}).get("nodes", [])
        if isinstance(item, dict) and item.get("role")
    }
    remote_normal = remote_nodes.get("normal-data-plane")
    if remote_collection is None or not str((remote_normal or {}).get("target", "")).strip():
        normal = _local_runtime_node_manifest(
            "normal-data-plane", indexed, "/app/xray/runtime/config.json"
        )
    else:
        normal = _remote_node_manifest("normal-data-plane", remote_normal, indexed)
    ai = _local_ai_node_manifest(indexed, remote_nodes.get("ai-data-plane"))
    database = _database_artifact(indexed)
    shared_ready = database.get("status") == "ok"
    nodes = [normal, ai]
    configured_nodes = [node for node in nodes if node.get("configured")]
    optional_databases = {
        "name": "ops-database",
        "restorePath": "data/ops.db",
        "status": "ok" if "database/ops.db" in indexed else "missing",
        "required": False,
    }
    if "database/ops.db" in indexed:
        optional_databases["archivePath"] = "database/ops.db"
    recovery_ready = bool(configured_nodes) and shared_ready and all(
        node.get("recoveryReady") for node in configured_nodes
    )
    return {
        "version": 1,
        "purpose": "node-recovery",
        "generatedAt": _now(),
        "xrayImage": DEFAULT_XRAY_IMAGE,
        "sharedState": {
            "recoveryReady": shared_ready,
            "requiredArtifacts": [database],
            "optionalArtifacts": [optional_databases],
        },
        "nodes": nodes,
        "configuredRoles": [node["role"] for node in configured_nodes],
        "recoveryReady": recovery_ready,
    }


def readiness_summary(node_manifest: dict) -> dict:
    nodes = []
    for node in node_manifest.get("nodes", []):
        required = node.get("requiredArtifacts", [])
        missing = [
            item.get("name", item.get("remotePath", "artifact"))
            for item in required
            if item.get("status") != "ok" or not item.get("archivePath")
        ]
        nodes.append(
            {
                "role": node.get("role", ""),
                "configured": bool(node.get("configured")),
                "source": node.get("source", ""),
                "collectionStatus": node.get("collectionStatus", ""),
                "recoveryReady": bool(node.get("recoveryReady")),
                "missingRequiredArtifacts": missing,
            }
        )
    return {
        "recoveryReady": bool(node_manifest.get("recoveryReady")),
        "sharedReady": bool(node_manifest.get("sharedState", {}).get("recoveryReady")),
        "configuredRoles": list(node_manifest.get("configuredRoles", [])),
        "nodes": nodes,
    }


def _validate_tar_names(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        name = _safe_archive_name(member.name.rstrip("/"))
        if name in members:
            raise ValueError(f"duplicate archive member: {name}")
        members[name] = member
    return members


def _read_json_member(archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str) -> dict:
    member = members.get(name)
    if member is None or not member.isfile():
        raise ValueError(f"backup archive is missing {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError(f"cannot read {name} from backup archive")
    try:
        payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return payload


def _validate_node_artifacts(node_manifest: dict, file_index: dict[str, dict]) -> None:
    shared_state = node_manifest.get("sharedState")
    nodes = node_manifest.get("nodes")
    if not isinstance(shared_state, dict):
        raise ValueError("node recovery manifest sharedState must be an object")
    if not isinstance(nodes, list):
        raise ValueError("node recovery manifest nodes must be a list")
    for group in (shared_state, *nodes):
        if not isinstance(group, dict):
            raise ValueError("node recovery manifest node entries must be objects")
        for artifact_group in ("requiredArtifacts", "optionalArtifacts"):
            artifacts = group.get(artifact_group, [])
            if not isinstance(artifacts, list):
                raise ValueError(f"node recovery manifest {artifact_group} must be a list")
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    raise ValueError("node recovery manifest artifacts must be objects")
                archive_path = str(artifact.get("archivePath", ""))
                if not archive_path:
                    continue
                archive_path = _safe_archive_name(archive_path)
                if archive_path not in file_index:
                    raise ValueError(
                        f"node recovery artifact is not covered by backup manifest: {archive_path}"
                    )
                restore_path = str(artifact.get("restorePath", ""))
                if not restore_path:
                    raise ValueError(f"node recovery artifact has no restore path: {archive_path}")
                _safe_relative(restore_path, "restore path")


def validate_backup_bundle(bundle_path: str | Path) -> dict:
    """Verify every manifest hash and return both recovery manifests.

    Extraction is deliberately separate from validation.  A caller can run
    this function as a harmless readiness check before touching a replacement
    node.
    """

    bundle = Path(bundle_path)
    if not bundle.is_file():
        raise FileNotFoundError(f"backup bundle not found: {bundle}")
    with tarfile.open(bundle, mode="r:gz") as archive:
        members = _validate_tar_names(archive)
        backup_manifest = _read_json_member(archive, members, "backup-manifest.json")
        if backup_manifest.get("version") != 1 or not isinstance(
            backup_manifest.get("files"), list
        ):
            raise ValueError("unsupported or malformed backup-manifest.json")
        file_index = _file_entry_index(backup_manifest["files"])
        for archive_path, entry in file_index.items():
            member = members.get(archive_path)
            if member is None or not member.isfile():
                raise ValueError(f"backup manifest file is missing from archive: {archive_path}")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read archived file: {archive_path}")
            data = handle.read()
            try:
                expected_size = int(entry.get("size", -1))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid backup manifest size: {archive_path}") from exc
            expected_hash = str(entry.get("sha256", ""))
            actual_hash = hashlib.sha256(data).hexdigest()
            if expected_size != len(data) or expected_hash != actual_hash:
                raise ValueError(f"backup checksum mismatch: {archive_path}")

        node_manifest = _read_json_member(
            archive, members, NODE_RECOVERY_MANIFEST_NAME
        )
        if node_manifest.get("version") != 1 or node_manifest.get("purpose") != "node-recovery":
            raise ValueError("unsupported or malformed node-recovery-manifest.json")
        if NODE_RECOVERY_MANIFEST_NAME not in file_index:
            raise ValueError("backup manifest does not cover node-recovery-manifest.json")
        _validate_node_artifacts(node_manifest, file_index)
        return {
            "bundle": bundle.resolve(),
            "backupManifest": backup_manifest,
            "nodeManifest": node_manifest,
            "readiness": readiness_summary(node_manifest),
        }


def _node_for_role(node_manifest: dict, role: str) -> dict:
    for node in node_manifest.get("nodes", []):
        if node.get("role") == role:
            return node
    raise ValueError(f"node role is not present in recovery manifest: {role}")


def _write_file(path: Path, data: bytes, force: bool) -> None:
    if path.exists() or path.is_symlink():
        if not force:
            raise FileExistsError(f"restore target already exists: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"restore target is a directory: {path}")
    temporary = path.with_name(f".{path.name}.restore-tmp")
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_member_bytes(archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str) -> bytes:
    member = members.get(name)
    if member is None or not member.isfile():
        raise ValueError(f"backup archive is missing {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError(f"cannot read {name} from backup archive")
    return handle.read()


def _node_compose(role: str, image: str) -> str:
    service_name = "xray-recovered-normal" if role == "normal-data-plane" else "xray-recovered-ai"
    return f'''services:
  xray:
    image: {image}
    container_name: {service_name}
    restart: unless-stopped
    network_mode: host
    user: "0:0"
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
    command: ["run", "-c", "/etc/xray/config.json"]
    volumes:
      - ./app/xray/runtime/config.json:/etc/xray/config.json:ro
      - ./app/xray/logs:/var/log/xray
    healthcheck:
      test: ["CMD", "/usr/local/bin/xray", "run", "-test", "-config", "/etc/xray/config.json"]
      interval: 30s
      timeout: 10s
      retries: 3
'''


def prepare_node(
    bundle_path: str | Path,
    role: str,
    output_dir: str | Path,
    *,
    force: bool = False,
    allow_incomplete: bool = False,
) -> dict:
    """Validate a bundle and create an isolated, ready-to-start node folder."""

    if role not in RECOVERABLE_ROLES:
        raise ValueError(f"unsupported recoverable node role: {role}")
    validated = validate_backup_bundle(bundle_path)
    node = _node_for_role(validated["nodeManifest"], role)
    if not node.get("recoveryReady") and not allow_incomplete:
        missing = readiness_summary({"nodes": [node]}).get("nodes", [{}])[0].get(
            "missingRequiredArtifacts", []
        )
        raise ValueError(
            f"{role} recovery is not ready; missing required artifacts: {', '.join(missing) or 'unknown'}"
        )

    output = Path(output_dir)
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"restore output is not a directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()) and not force:
        raise FileExistsError(
            f"restore output is not empty: {output}; use a new directory or --force"
        )
    os.chmod(output, 0o700)

    artifacts = [*node.get("requiredArtifacts", []), *node.get("optionalArtifacts", [])]
    written = []
    with tarfile.open(Path(bundle_path), mode="r:gz") as archive:
        members = _validate_tar_names(archive)
        output_root = output.resolve()
        for artifact in artifacts:
            if artifact.get("status") != "ok" or not artifact.get("archivePath"):
                continue
            restore_path = _safe_relative(str(artifact["restorePath"]), "restore path")
            destination = (output / PurePosixPath(restore_path)).resolve()
            try:
                destination.relative_to(output_root)
            except ValueError as exc:
                raise ValueError(f"restore path escapes output directory: {restore_path}") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(destination.parent, 0o700)
            data = _read_member_bytes(archive, members, _safe_archive_name(artifact["archivePath"]))
            _write_file(destination, data, force)
            written.append(restore_path)

    logs_dir = output / "app" / "xray" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(logs_dir, 0o700)
    compose_path = output / "docker-compose.node.yml"
    _write_file(
        compose_path,
        _node_compose(role, str(validated["nodeManifest"].get("xrayImage") or DEFAULT_XRAY_IMAGE)).encode(
            "utf-8"
        ),
        force,
    )
    prepared = {
        "version": 1,
        "role": role,
        "preparedAt": _now(),
        "sourceBundle": Path(bundle_path).name,
        "recoveryReady": bool(node.get("recoveryReady")),
        "files": written,
        "startCommand": "docker compose -f docker-compose.node.yml up -d",
    }
    _write_file(
        output / "node-recovery.json",
        (json.dumps(prepared, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        force,
    )
    return {**prepared, "outputDir": str(output.resolve())}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or prepare an Xray node recovery bundle.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    validate = subparsers.add_parser("validate", help="verify archive hashes and readiness")
    validate.add_argument("--bundle", required=True)
    validate.add_argument("--require-ready", action="store_true")
    validate.add_argument("--json", action="store_true", dest="as_json")

    prepare = subparsers.add_parser("prepare", help="create an isolated replacement-node directory")
    prepare.add_argument("--bundle", required=True)
    prepare.add_argument("--node", choices=RECOVERABLE_ROLES, required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "validate":
            result = validate_backup_bundle(args.bundle)
            if args.as_json:
                print(json.dumps(result["readiness"], ensure_ascii=False, indent=2))
            else:
                readiness = result["readiness"]
                print(
                    f"recoveryReady={str(readiness['recoveryReady']).lower()} "
                    f"sharedReady={str(readiness['sharedReady']).lower()}"
                )
                for node in readiness["nodes"]:
                    print(
                        f"{node['role']}: configured={str(node['configured']).lower()} "
                        f"ready={str(node['recoveryReady']).lower()} "
                        f"missing={','.join(node['missingRequiredArtifacts']) or '-'}"
                    )
            if args.require_ready and not result["readiness"]["recoveryReady"]:
                return 2
            return 0

        result = prepare_node(
            args.bundle,
            args.node,
            args.output_dir,
            force=args.force,
            allow_incomplete=args.allow_incomplete,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        FileNotFoundError,
        NotADirectoryError,
        FileExistsError,
        IsADirectoryError,
        OSError,
        ValueError,
        tarfile.TarError,
    ) as exc:
        print(f"[node-recovery] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
