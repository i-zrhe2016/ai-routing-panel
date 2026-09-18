#!/usr/bin/env python3
"""Validate and prepare a disaster bundle without touching live services.

The backup pipeline deliberately produces an offline recovery artifact.  This
script is the corresponding safe restore entry point: it accepts the plain
bundle kept locally or the AES-256-GCM encrypted object uploaded to R2,
validates both manifests, and materializes a portable recovery tree.  It never
opens SSH, starts Docker, changes DNS, or replaces a live file automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

try:
    from node_recovery import RECOVERABLE_ROLES, validate_backup_bundle
    from upload_backup_r2 import MAGIC
except ModuleNotFoundError:
    from scripts.node_recovery import RECOVERABLE_ROLES, validate_backup_bundle
    from scripts.upload_backup_r2 import MAGIC


DEFAULT_PASSWORD_ENV = "DB_BACKUP_ENCRYPTION_PASSWORD"
RESTORE_REPORT_NAME = "restore-report.json"


@dataclass(frozen=True)
class PlannedFile:
    archive_path: str
    destination: str
    category: str


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


def _archive_member_index(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        name = member.name.rstrip("/")
        if not name:
            continue
        safe_name = _safe_relative(name, "archive path")
        if safe_name in members:
            raise ValueError(f"duplicate archive member: {safe_name}")
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not supported: {safe_name}")
        members[safe_name] = member
    return members


def _is_encrypted(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def _resolve_password(
    *,
    passphrase: str | None = None,
    password_file: str | Path | None = None,
    password_env: str = DEFAULT_PASSWORD_ENV,
) -> str:
    if passphrase is not None:
        value = passphrase
    elif password_file:
        value = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
    else:
        value = os.environ.get(password_env, "")
    if not value:
        source = str(password_file) if password_file else password_env
        raise ValueError(f"encryption password is missing; provide a protected password source: {source}")
    return value


def _decrypt_bundle(source: Path, destination: Path, passphrase: str) -> None:
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise RuntimeError("cryptography is required to decrypt a disaster bundle") from exc

    header_size = len(MAGIC)
    salt_size = 16
    nonce_size = 12
    tag_size = 16
    payload_start = header_size + salt_size + nonce_size
    source_size = source.stat().st_size
    if source_size < payload_start + tag_size:
        raise ValueError("invalid encrypted disaster bundle header")

    with source.open("rb") as handle:
        if handle.read(header_size) != MAGIC:
            raise ValueError("invalid encrypted disaster bundle header")
        salt = handle.read(salt_size)
        nonce = handle.read(nonce_size)
        if len(salt) != salt_size or len(nonce) != nonce_size:
            raise ValueError("invalid encrypted disaster bundle header")
        handle.seek(-tag_size, os.SEEK_END)
        tag = handle.read(tag_size)
        if len(tag) != tag_size:
            raise ValueError("invalid encrypted disaster bundle header")
        handle.seek(payload_start)

    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode())
    ciphertext_size = source_size - payload_start - tag_size
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(MAGIC)
    try:
        with source.open("rb") as handle, destination.open("wb") as output:
            handle.seek(payload_start)
            remaining = ciphertext_size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("encrypted disaster bundle ended unexpectedly")
                output.write(decryptor.update(chunk))
                remaining -= len(chunk)
            output.write(decryptor.finalize())
    except (InvalidTag, ValueError) as exc:
        destination.unlink(missing_ok=True)
        raise ValueError("encrypted disaster bundle authentication failed") from exc

    os.chmod(destination, 0o600)


@contextmanager
def _open_validated_bundle(
    bundle_path: str | Path,
    *,
    passphrase: str | None = None,
    password_file: str | Path | None = None,
    password_env: str = DEFAULT_PASSWORD_ENV,
) -> Iterator[tuple[Path, dict, bool]]:
    source = Path(bundle_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"disaster bundle not found: {source}")

    encrypted = _is_encrypted(source)
    with tempfile.TemporaryDirectory(prefix="xray-restore-bundle-") as temp_dir:
        plain_bundle = source
        if encrypted:
            plain_bundle = Path(temp_dir) / "bundle.tar.gz"
            _decrypt_bundle(
                source,
                plain_bundle,
                _resolve_password(
                    passphrase=passphrase,
                    password_file=password_file,
                    password_env=password_env,
                ),
            )
        with tarfile.open(plain_bundle, mode="r:gz") as archive:
            _archive_member_index(archive)
        validated = validate_backup_bundle(plain_bundle)
        yield plain_bundle, validated, encrypted


def validate_restore_bundle(
    bundle_path: str | Path,
    *,
    passphrase: str | None = None,
    password_file: str | Path | None = None,
    password_env: str = DEFAULT_PASSWORD_ENV,
) -> dict:
    """Validate a plain or encrypted bundle and return a non-secret summary."""

    with _open_validated_bundle(
        bundle_path,
        passphrase=passphrase,
        password_file=password_file,
        password_env=password_env,
    ) as (plain_bundle, validated, encrypted):
        readiness = validated["readiness"]
        return {
            "bundle": Path(bundle_path).expanduser().name,
            "validatedBundle": plain_bundle.name,
            "encrypted": encrypted,
            **readiness,
        }


def _control_restore_path(archive_path: str, entry: dict) -> str:
    safe_archive = _safe_relative(archive_path, "archive path")
    parts = list(PurePosixPath(safe_archive).parts)
    if not parts or parts[0] != "config" or len(parts) < 2:
        raise ValueError(f"control file is outside config/: {archive_path}")

    source = str(entry.get("sourcePath", "")).replace("\\", "/")
    source_parts = list(PurePosixPath(source).parts)
    for marker in ("project", "data", "app"):
        if marker in source_parts:
            index = len(source_parts) - 1 - source_parts[::-1].index(marker)
            relative = source_parts[index + 1 :] if marker == "project" else source_parts[index:]
            if relative:
                return _safe_relative("/".join(relative), "control restore path")

    relative_parts = parts[1:]
    for marker in ("project", "data", "app"):
        if marker in relative_parts:
            index = len(relative_parts) - 1 - relative_parts[::-1].index(marker)
            relative = relative_parts[index + 1 :] if marker == "project" else relative_parts[index:]
            if relative:
                return _safe_relative("/".join(relative), "control restore path")
    return _safe_relative("/".join(relative_parts), "control restore path")


def _panel_database_archive(node_manifest: dict) -> str:
    shared = node_manifest.get("sharedState", {})
    for artifact in shared.get("requiredArtifacts", []):
        if artifact.get("name") == "panel-database":
            return _safe_relative(str(artifact.get("archivePath", "")), "database archive path")
    return ""


def _database_restore_path(archive_path: str, panel_archive: str) -> str:
    safe_archive = _safe_relative(archive_path, "database archive path")
    if safe_archive == panel_archive:
        return "data/panel.db"
    if PurePosixPath(safe_archive).name == "ops.db":
        return "data/xray-ops/ops.db"
    return "data/recovered-databases/" + PurePosixPath(safe_archive).name


def _add_plan(plans: list[PlannedFile], seen_destinations: set[str], plan: PlannedFile) -> None:
    destination = _safe_relative(plan.destination, "restore path")
    if destination in seen_destinations:
        raise ValueError(f"multiple archive files target the same restore path: {destination}")
    seen_destinations.add(destination)
    plans.append(PlannedFile(plan.archive_path, destination, plan.category))


def _build_restore_plan(validated: dict) -> list[PlannedFile]:
    manifest = validated["backupManifest"]
    node_manifest = validated["nodeManifest"]
    file_entries = manifest.get("files", [])
    plans: list[PlannedFile] = []
    seen_destinations: set[str] = set()
    panel_archive = _panel_database_archive(node_manifest)
    if not panel_archive:
        raise ValueError("node recovery manifest does not identify the panel database")

    for entry in file_entries:
        archive_path = _safe_relative(str(entry.get("archivePath", "")), "archive path")
        if archive_path.startswith("database/"):
            _add_plan(
                plans,
                seen_destinations,
                PlannedFile(
                    archive_path,
                    _database_restore_path(archive_path, panel_archive),
                    "database",
                ),
            )
        elif archive_path.startswith("config/"):
            _add_plan(
                plans,
                seen_destinations,
                PlannedFile(
                    archive_path,
                    _control_restore_path(archive_path, entry),
                    "control-plane",
                ),
            )

    for node in node_manifest.get("nodes", []):
        role = str(node.get("role", ""))
        if role not in RECOVERABLE_ROLES:
            raise ValueError(f"unsupported node role in recovery manifest: {role}")
        for group_name in ("requiredArtifacts", "optionalArtifacts"):
            for artifact in node.get(group_name, []):
                if artifact.get("status") != "ok" or not artifact.get("archivePath"):
                    continue
                archive_path = _safe_relative(str(artifact["archivePath"]), "node archive path")
                restore_path = _safe_relative(str(artifact.get("restorePath", "")), "node restore path")
                _add_plan(
                    plans,
                    seen_destinations,
                    PlannedFile(
                        archive_path,
                        f"nodes/{role}/{restore_path}",
                        role,
                    ),
                )

    # Keep the verification records available in the prepared tree without
    # treating them as live configuration.
    for archive_path, destination in (
        ("backup-manifest.json", "recovery/backup-manifest.json"),
        ("node-recovery-manifest.json", "recovery/node-recovery-manifest.json"),
        ("nodes/remote-node-collection.json", "recovery/remote-node-collection.json"),
    ):
        if any(str(entry.get("archivePath", "")) == archive_path for entry in file_entries) or archive_path in {
            "backup-manifest.json",
            "node-recovery-manifest.json",
        }:
            _add_plan(plans, seen_destinations, PlannedFile(archive_path, destination, "metadata"))

    return plans


def _open_member(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    archive_path: str,
) -> BinaryIO:
    safe_archive = _safe_relative(archive_path, "archive path")
    member = members.get(safe_archive)
    if member is None or not member.isfile():
        raise ValueError(f"backup archive is missing {safe_archive}")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError(f"cannot read {safe_archive} from backup archive")
    return handle


def _write_file(path: Path, data: bytes | BinaryIO, force: bool) -> None:
    if path.exists() or path.is_symlink():
        if not force:
            raise FileExistsError(f"restore target already exists: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"restore target is a directory: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.restore-", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            if isinstance(data, bytes):
                handle.write(data)
            else:
                shutil.copyfileobj(data, handle, length=1024 * 1024)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_output(output_dir: Path, force: bool) -> None:
    if output_dir.is_symlink():
        raise ValueError(f"restore output must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(f"restore output is not a directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        raise FileExistsError(
            f"restore output is not empty: {output_dir}; use a new directory or --force"
        )


def _new_staging_directory(output_dir: Path) -> Path:
    prefix = f".{output_dir.name or 'restore'}.restore-"
    staging = Path(tempfile.mkdtemp(prefix=prefix, dir=output_dir.parent))
    os.chmod(staging, 0o700)
    return staging


def _restore_destination(root: Path, relative: str) -> Path:
    safe_relative = _safe_relative(relative, "restore path")
    destination = root / PurePosixPath(safe_relative)
    try:
        destination.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"restore path escapes output directory: {relative}") from exc

    parent = root
    for part in PurePosixPath(safe_relative).parts[:-1]:
        parent /= part
        if parent.is_symlink():
            raise ValueError(f"restore path traverses a symlink: {relative}")
    return destination


def _staged_files(staging: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(staging.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"staging tree contains an unexpected symlink: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError(f"staging tree contains an unexpected entry: {path}")
    return files


def _merge_staging(staging: Path, output: Path) -> None:
    """Publish into an existing forced output with file-level rollback."""

    output_root = output.resolve()
    rollback = Path(tempfile.mkdtemp(prefix=f".{output.name}.rollback-", dir=output.parent))
    staged_entries: list[tuple[Path, Path, Path | None]] = []
    applied: list[tuple[Path, Path | None]] = []
    created_directories: list[Path] = []
    try:
        for staged_file in _staged_files(staging):
            relative = _safe_relative(
                staged_file.relative_to(staging).as_posix(), "restore path"
            )
            target = _restore_destination(output_root, relative)
            backup: Path | None = None
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    raise IsADirectoryError(f"restore target is a directory: {target}")
                backup = rollback / PurePosixPath(relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    backup.symlink_to(os.readlink(target))
                else:
                    shutil.copy2(target, backup)
            staged_entries.append((staged_file, target, backup))

        for staged_file, target, backup in staged_entries:
            missing_directories: list[Path] = []
            parent = target.parent
            while not parent.exists():
                missing_directories.append(parent)
                parent = parent.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            for directory in reversed(missing_directories):
                os.chmod(directory, 0o700)
                created_directories.append(directory)
            os.replace(staged_file, target)
            applied.append((target, backup))
            os.chmod(target, 0o600)
        os.chmod(output, 0o700)
    except Exception:
        for target, backup in reversed(applied):
            target.unlink(missing_ok=True)
            if backup is not None:
                os.replace(backup, target)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(rollback, ignore_errors=True)


def _publish_staging(staging: Path, output: Path, force: bool) -> None:
    if output.is_symlink():
        raise ValueError(f"restore output must not be a symlink: {output}")
    if not output.exists():
        os.replace(staging, output)
        os.chmod(output, 0o700)
        return
    if not output.is_dir():
        raise NotADirectoryError(f"restore output is not a directory: {output}")
    if not any(output.iterdir()):
        output.rmdir()
        try:
            os.replace(staging, output)
        except Exception:
            output.mkdir(parents=True, exist_ok=True)
            os.chmod(output, 0o700)
            raise
        os.chmod(output, 0o700)
        return
    if not force:
        raise FileExistsError(
            f"restore output is not empty: {output}; use a new directory or --force"
        )
    _merge_staging(staging, output)


def prepare_restore(
    bundle_path: str | Path,
    output_dir: str | Path,
    *,
    passphrase: str | None = None,
    password_file: str | Path | None = None,
    password_env: str = DEFAULT_PASSWORD_ENV,
    allow_incomplete: bool = False,
    force: bool = False,
) -> dict:
    """Validate and materialize a complete recovery tree under ``output_dir``."""

    output = Path(output_dir).expanduser()
    with _open_validated_bundle(
        bundle_path,
        passphrase=passphrase,
        password_file=password_file,
        password_env=password_env,
    ) as (plain_bundle, validated, encrypted):
        readiness = validated["readiness"]
        if not readiness["sharedReady"]:
            raise ValueError("recovery is not ready; the panel database is missing or invalid")
        if not readiness["recoveryReady"] and not allow_incomplete:
            missing = [
                f"{node['role']}:{','.join(node['missingRequiredArtifacts']) or 'unknown'}"
                for node in readiness["nodes"]
                if not node["recoveryReady"]
            ]
            raise ValueError(
                "recovery is not ready; missing required node artifacts: "
                + "; ".join(missing)
            )

        plans = _build_restore_plan(validated)
        _validate_output(output, force)
        staging = _new_staging_directory(output)
        try:
            output_root = staging.resolve()
            restored_files: list[str] = []
            with tarfile.open(plain_bundle, mode="r:gz") as archive:
                members = _archive_member_index(archive)
                for plan in plans:
                    destination = _restore_destination(output_root, plan.destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(destination.parent, 0o700)
                    member = _open_member(archive, members, plan.archive_path)
                    try:
                        _write_file(destination, member, False)
                    finally:
                        member.close()
                    restored_files.append(plan.destination)

            report = {
                "version": 1,
                "purpose": "restore-preparation",
                "preparedAt": _now(),
                "sourceBundle": Path(bundle_path).expanduser().name,
                "encrypted": encrypted,
                "recoveryReady": bool(readiness["recoveryReady"]),
                "sharedReady": bool(readiness["sharedReady"]),
                "allowIncomplete": bool(allow_incomplete),
                "files": sorted(restored_files),
                "nodes": readiness["nodes"],
                "nextStep": "Review this tree, then perform service-specific recovery manually.",
            }
            _write_file(
                staging / RESTORE_REPORT_NAME,
                (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                False,
            )
            report["report"] = RESTORE_REPORT_NAME
            result = {**report, "outputDir": str(output.resolve())}
            _publish_staging(staging, output, force)
            return result
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


def _add_bundle_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle", required=True, help="plain .tar.gz or encrypted .enc bundle")
    parser.add_argument(
        "--password-file",
        help="protected file containing the encryption password; never pass the password as an argument",
    )
    parser.add_argument(
        "--password-env",
        default=DEFAULT_PASSWORD_ENV,
        help=f"environment variable for the encryption password (default: {DEFAULT_PASSWORD_ENV})",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or prepare an isolated recovery tree from a disaster bundle."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    validate = subparsers.add_parser("validate", help="validate hashes and recovery readiness")
    _add_bundle_options(validate)
    validate.add_argument("--require-ready", action="store_true")

    prepare = subparsers.add_parser("prepare", help="materialize an isolated recovery tree")
    _add_bundle_options(prepare)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--allow-incomplete", action="store_true")
    prepare.add_argument(
        "--force",
        action="store_true",
        help="allow replacement of files in an existing output directory; never deletes stale files",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "validate":
            result = validate_restore_bundle(
                args.bundle,
                password_file=args.password_file,
                password_env=args.password_env,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if not args.require_ready or result["recoveryReady"] else 2

        result = prepare_restore(
            args.bundle,
            args.output_dir,
            password_file=args.password_file,
            password_env=args.password_env,
            allow_incomplete=args.allow_incomplete,
            force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        FileNotFoundError,
        NotADirectoryError,
        FileExistsError,
        IsADirectoryError,
        OSError,
        RuntimeError,
        ValueError,
        tarfile.TarError,
    ) as exc:
        print(f"[restore] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
