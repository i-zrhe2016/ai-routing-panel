#!/usr/bin/env python3
"""用中文安全配置和检查灾备加密密码、R2 凭据。"""

from __future__ import annotations

import argparse
import errno
import getpass
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

MIN_ENCRYPTION_PASSWORD_LENGTH = 32
R2_KEYS = (
    "DB_BACKUP_R2_ENDPOINT",
    "DB_BACKUP_R2_BUCKET",
    "DB_BACKUP_R2_ACCESS_KEY_ID",
    "DB_BACKUP_R2_SECRET_ACCESS_KEY",
)
MANAGED_KEYS = (
    "DB_BACKUP_R2_ENABLED",
    "DB_BACKUP_BUNDLE_ENABLED",
    "DB_BACKUP_ENCRYPTION_PASSWORD",
    *R2_KEYS,
)
STATUS_KEYS = MANAGED_KEYS
INTERPOLATION_PATTERN = re.compile(
    r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"
)
ENV_ASSIGNMENT = re.compile(
    r"^(?P<indent>\s*)(?:(?P<export>export)\s+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*="
)


class DurabilityError(RuntimeError):
    """配置已替换，但目录同步未能确认持久化。"""


def _quoted_end(value: str, quote: str) -> int:
    escaped = False
    for index, character in enumerate(value[1:], start=1):
        if escaped:
            escaped = False
        elif quote == '"' and character == "\\":
            escaped = True
        elif character == quote:
            return index
    raise ValueError("dotenv 值的引号未闭合")


def _decode_double_quoted(value: str) -> str:
    decoded: list[str] = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "$": "$"}
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            next_character = value[index + 1]
            decoded.append(escapes.get(next_character, f"\\{next_character}"))
            index += 2
            continue
        if character == "$" and index + 1 < len(value) and value[index + 1] == "$":
            decoded.append("$")
            index += 2
            continue
        decoded.append(character)
        index += 1
    return "".join(decoded)


def _has_unescaped_interpolation(value: str) -> bool:
    for match in INTERPOLATION_PATTERN.finditer(value):
        index = match.start()
        if index and value[index - 1] == "$":
            continue
        backslashes = 0
        index -= 1
        while index >= 0 and value[index] == "\\":
            backslashes += 1
            index -= 1
        if backslashes % 2 == 0:
            return True
    return False


def _inline_comment_start(value: str) -> int | None:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#" and index > 0 and value[index - 1].isspace():
            return index - 1
    return None


def _parse_env_value(raw: str, reject_interpolation: bool = True) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        end = _quoted_end(value, value[0])
        trailing = value[end + 1 :].strip()
        if trailing and not trailing.startswith("#"):
            raise ValueError("dotenv 引号值后存在无法解析的内容")
        quoted = value[1:end]
        if value[0] == '"' and reject_interpolation and _has_unescaped_interpolation(quoted):
            raise ValueError("dotenv 值不能使用未转义的变量引用")
        return _decode_double_quoted(quoted) if value[0] == '"' else quoted

    comment_start = _inline_comment_start(value)
    if comment_start is not None:
        value = value[:comment_start].rstrip()
    if reject_interpolation and _has_unescaped_interpolation(value):
        raise ValueError("dotenv 值不能使用未转义的变量引用")
    return value


def _parse_env_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = ENV_ASSIGNMENT.match(line.rstrip("\r\n"))
        if match:
            key = match.group("key")
            raw_value = line.rstrip("\r\n")[match.end() :]
            stripped_value = raw_value.lstrip()
            if stripped_value and stripped_value[0] in {"'", '"'}:
                try:
                    _quoted_end(stripped_value, stripped_value[0])
                except ValueError as exc:
                    raise ValueError("dotenv 不支持跨行引号值") from exc
            try:
                values[key] = _parse_env_value(raw_value, reject_interpolation=key in MANAGED_KEYS)
            except ValueError:
                if key in MANAGED_KEYS:
                    raise
                values[key] = raw_value.strip()
    return values


def read_env_file(path: str | Path) -> tuple[list[str], dict[str, str]]:
    """读取 dotenv 文本；不会打印或记录任何值。"""

    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError(f"配置文件不能是符号链接: {target}")
    if any(parent.is_symlink() for parent in target.parents):
        raise ValueError(f"配置路径的父目录不能是符号链接: {target}")
    if not target.exists():
        return [], {}
    if not target.is_file():
        raise ValueError(f"配置路径不是普通文件: {target}")

    with target.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines(keepends=True)
    return lines, _parse_env_lines(lines)


def validate_env_file_security(path: str | Path) -> list[str]:
    """检查现有 dotenv 的属主和权限，不读取或返回文件内容。"""

    target = Path(path).expanduser()
    if target.is_symlink():
        return ["配置文件不能是符号链接"]
    if not target.exists():
        return []
    if not target.is_file():
        return ["配置路径不是普通文件"]
    file_stat = target.stat()
    issues: list[str] = []
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else file_stat.st_uid
    if file_stat.st_uid not in {0, effective_uid}:
        issues.append("配置文件属主不受信任")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        issues.append("配置文件权限必须禁止 group/other 访问（建议 0600）")
    if os.name == "posix":
        try:
            directory_fd = _open_trusted_directory(target.parent, create_missing=False)
        except (OSError, ValueError):
            issues.append("配置文件父目录不满足安全属主或写权限要求")
        else:
            os.close(directory_fd)
    return issues


def _quote_env_value(value: str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("dotenv 值不能包含换行")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'"{escaped}"'


def _open_trusted_directory(parent: Path, create_missing: bool = True) -> int:
    if os.name != "posix":
        raise OSError("安全原子更新需要 POSIX 目录 fd 支持")
    absolute_parent = Path(os.path.abspath(parent))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(os.sep, flags)
    try:
        _verify_directory_fd(directory_fd)
        for component in absolute_parent.parts[1:]:
            if component in {"", "."}:
                continue
            try:
                next_fd = os.open(component, flags, dir_fd=directory_fd)
                created = False
            except FileNotFoundError:
                if not create_missing:
                    raise
                os.mkdir(component, 0o700, dir_fd=directory_fd)
                next_fd = os.open(component, flags, dir_fd=directory_fd)
                created = True
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("配置路径的父目录不能是符号链接或普通文件") from exc
                raise
            try:
                if created:
                    os.fchmod(next_fd, 0o700)
                _verify_directory_fd(next_fd)
            except Exception:
                os.close(next_fd)
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _verify_directory_fd(directory_fd: int) -> None:
    directory_stat = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ValueError("配置路径父级不是目录")
    if directory_stat.st_uid not in {0, os.geteuid()}:
        raise ValueError("配置路径父目录属主不受信任")
    mode = stat.S_IMODE(directory_stat.st_mode)
    if mode & 0o022 and not mode & stat.S_ISVTX:
        raise ValueError("配置路径父目录可被其他用户写入")


def _open_existing_file(directory_fd: int, name: str) -> int | None:
    try:
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(entry_stat.st_mode):
        raise ValueError("配置文件不能是符号链接")
    if not stat.S_ISREG(entry_stat.st_mode):
        raise ValueError("配置路径不是普通文件")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError("配置路径不是普通文件")
        return source_fd
    except Exception:
        os.close(source_fd)
        raise


def _verify_existing_file_security(file_fd: int) -> None:
    file_stat = os.fstat(file_fd)
    if file_stat.st_uid not in {0, os.geteuid()}:
        raise ValueError("配置文件属主不受信任")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise ValueError("配置文件权限必须禁止 group/other 访问（建议 0600）")


def _open_private_temp(directory_fd: int, basename: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(32):
        name = f".{basename}.{secrets.token_hex(12)}.tmp"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
    raise OSError("无法创建唯一的临时配置文件")


def _preserve_file_metadata(source_fd: int, destination_fd: int) -> None:
    source_stat = os.fstat(source_fd)
    destination_stat = os.fstat(destination_fd)
    if (source_stat.st_uid, source_stat.st_gid) != (
        destination_stat.st_uid,
        destination_stat.st_gid,
    ):
        os.fchown(destination_fd, source_stat.st_uid, source_stat.st_gid)
    os.utime(
        destination_fd,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )
    listxattr = getattr(os, "listxattr", None)
    getxattr = getattr(os, "getxattr", None)
    setxattr = getattr(os, "setxattr", None)
    if listxattr:
        names = listxattr(source_fd)
        if names and (not getxattr or not setxattr):
            raise ValueError("当前平台无法安全复制配置文件扩展属性")
        unsupported = [name for name in names if not name.startswith("user.")]
        if unsupported:
            raise ValueError("配置文件含不能安全复制的访问控制或安全扩展属性")
        for name in names:
            value = getxattr(source_fd, name)
            setxattr(destination_fd, name, value)


def _render_env(lines: list[str], updates: dict[str, str]) -> str:
    rendered: list[str] = []
    replaced: set[str] = set()
    default_newline = next(
        (
            "\r\n"
            if line.endswith("\r\n")
            else "\r"
            if line.endswith("\r")
            else "\n"
            for line in lines
            if line.endswith(("\r\n", "\r", "\n"))
        ),
        "\n",
    )
    for line in lines:
        line_body = line.rstrip("\r\n")
        match = ENV_ASSIGNMENT.match(line_body)
        if not match or match.group("key") not in updates:
            rendered.append(line)
            continue
        key = match.group("key")
        prefix = match.group("indent")
        if match.group("export"):
            prefix += "export "
        suffix = line_body[match.end() :]
        if _parse_env_value(suffix) == str(updates[key]):
            rendered.append(line)
            replaced.add(key)
            continue
        comment_start = _inline_comment_start(suffix)
        comment = suffix[comment_start:].rstrip() if comment_start is not None else ""
        newline = "\r\n" if line.endswith("\r\n") else "\r" if line.endswith("\r") else "\n"
        rendered.append(f"{prefix}{key}={_quote_env_value(updates[key])}{comment}{newline}")
        replaced.add(key)

    if rendered and not rendered[-1].endswith(("\n", "\r")):
        rendered[-1] += default_newline
    for key, value in updates.items():
        if key not in replaced:
            rendered.append(f"{key}={_quote_env_value(value)}{default_newline}")
    return "".join(rendered)


def write_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """原子更新 dotenv，并让最终文件权限保持为 0600。"""

    target = Path(os.path.abspath(Path(path).expanduser()))
    directory_fd = _open_trusted_directory(target.parent)
    source_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None
    replaced = False
    try:
        source_fd = _open_existing_file(directory_fd, target.name)
        if source_fd is None:
            lines = []
            source_stat = None
        else:
            _verify_existing_file_security(source_fd)
            with os.fdopen(os.dup(source_fd), "r", encoding="utf-8", newline="") as handle:
                lines = handle.read().splitlines(keepends=True)
            _parse_env_lines(lines)
            source_stat = os.fstat(source_fd)
        content = _render_env(lines, updates)

        temporary_fd, temporary_name = _open_private_temp(directory_fd, target.name)
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="") as handle:
            temporary_fd = None
            handle.write(content)
            handle.flush()
            if source_fd is not None:
                _preserve_file_metadata(source_fd, handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
            handle.flush()
            os.fsync(handle.fileno())
        current_stat = None
        try:
            current_stat = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        if source_stat is None:
            if current_stat is not None:
                raise OSError("配置文件在更新期间被创建")
        elif (
            current_stat is None
            or current_stat.st_dev != source_stat.st_dev
            or current_stat.st_ino != source_stat.st_ino
        ):
            raise OSError("配置文件在更新期间发生变化")
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replaced = True
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise DurabilityError("配置已替换，但未能确认目录持久化同步") from exc
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if not replaced and temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if source_fd is not None:
            os.close(source_fd)
        os.close(directory_fd)


def generate_encryption_password() -> str:
    """生成不会在终端回显的高熵归档密码。"""

    return secrets.token_urlsafe(48)


def _enabled(value: str | None, default: bool = True) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _valid_endpoint(endpoint: str, bucket: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
        or any(character.isspace() for character in endpoint)
    ):
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    path = parsed.path.rstrip("/")
    return path in {"", f"/{bucket}"}


def validate_values(values: dict[str, str]) -> list[str]:
    """返回不含配置值的中文诊断。"""

    issues: list[str] = []
    for key in ("DB_BACKUP_R2_ENABLED", "DB_BACKUP_BUNDLE_ENABLED"):
        raw = values.get(key, "").strip().lower()
        if raw and raw not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
            issues.append(f"{key} 必须是 0/1 或布尔值")

    bundle_enabled = _enabled(values.get("DB_BACKUP_BUNDLE_ENABLED"))
    r2_enabled = _enabled(values.get("DB_BACKUP_R2_ENABLED"), default=False)
    encryption_password = values.get("DB_BACKUP_ENCRYPTION_PASSWORD", "")
    if (bundle_enabled or r2_enabled) and not encryption_password:
        issues.append("缺少 DB_BACKUP_ENCRYPTION_PASSWORD")
    elif encryption_password and len(encryption_password) < MIN_ENCRYPTION_PASSWORD_LENGTH:
        issues.append(
            f"DB_BACKUP_ENCRYPTION_PASSWORD 至少需要 {MIN_ENCRYPTION_PASSWORD_LENGTH} 个字符"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in encryption_password):
        issues.append("DB_BACKUP_ENCRYPTION_PASSWORD 不能包含换行")

    if not r2_enabled:
        return issues

    for key in R2_KEYS:
        if not values.get(key, "").strip():
            issues.append(f"缺少 {key}")
    for key in ("DB_BACKUP_R2_ACCESS_KEY_ID", "DB_BACKUP_R2_SECRET_ACCESS_KEY"):
        value = values.get(key, "")
        if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
            issues.append(f"{key} 不能包含空白或控制字符")
    endpoint = values.get("DB_BACKUP_R2_ENDPOINT", "").strip()
    bucket = values.get("DB_BACKUP_R2_BUCKET", "").strip()
    if (
        endpoint
        and bucket
        and not _valid_endpoint(endpoint, bucket)
    ):
        issues.append("DB_BACKUP_R2_ENDPOINT 必须是 HTTPS 基础地址，不能带 query 或 fragment")
    if bucket and (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        or ".." in bucket
        or re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", bucket)
    ):
        issues.append("DB_BACKUP_R2_BUCKET 不是有效的 R2/S3 bucket 名称")
    return issues


def _print_status(values: dict[str, str]) -> None:
    for key in STATUS_KEYS:
        status = "已设置" if values.get(key, "").strip() else "缺失"
        print(f"{key}: {status}")


def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "是", "1", "true"}


def _prompt_text(key: str, current: str, required: bool = True) -> str:
    if current:
        answer = input(f"{key} 已设置，回车保留；输入新值覆盖：").strip()
        return current if not answer else answer
    while True:
        answer = input(f"请输入 {key}：").strip()
        if answer or not required:
            return answer
        print(f"{key} 不能为空。")


def _prompt_secret(key: str, current: str) -> str:
    if current and _ask_yes_no(f"{key} 已设置，是否保留现有值？", True):
        return current
    while True:
        answer = getpass.getpass(f"请输入 {key}（输入不回显）：").strip()
        if answer:
            return answer
        print(f"{key} 不能为空。")


def _prompt_encryption_password(current: str) -> str:
    if current and _ask_yes_no("DB_BACKUP_ENCRYPTION_PASSWORD 已设置，是否保留现有值？", True):
        return current
    if _ask_yes_no("是否自动生成新的灾备归档密码？", True):
        print("已生成新密码；密码值不会显示，请从受保护的 .env/密码管理器保存。")
        return generate_encryption_password()
    while True:
        first = getpass.getpass("请输入灾备归档密码（至少 32 个字符，输入不回显）：").strip()
        second = getpass.getpass("请再次输入灾备归档密码：").strip()
        if first != second:
            print("两次输入不一致。")
        elif first:
            return first
        else:
            print("密码不能为空。")


def interactive_updates(values: dict[str, str]) -> dict[str, str]:
    updates: dict[str, str] = {}
    bundle_enabled = _ask_yes_no(
        "是否生成灾备归档？",
        _enabled(values.get("DB_BACKUP_BUNDLE_ENABLED")),
    )
    updates["DB_BACKUP_BUNDLE_ENABLED"] = "1" if bundle_enabled else "0"
    r2_enabled = _ask_yes_no(
        "是否启用 Cloudflare R2 灾备上传？",
        _enabled(values.get("DB_BACKUP_R2_ENABLED"), default=False),
    )
    updates["DB_BACKUP_R2_ENABLED"] = "1" if r2_enabled else "0"
    if r2_enabled:
        updates["DB_BACKUP_R2_ENDPOINT"] = _prompt_text(
            "DB_BACKUP_R2_ENDPOINT", values.get("DB_BACKUP_R2_ENDPOINT", "")
        )
        updates["DB_BACKUP_R2_BUCKET"] = _prompt_text(
            "DB_BACKUP_R2_BUCKET", values.get("DB_BACKUP_R2_BUCKET", "")
        )
        updates["DB_BACKUP_R2_ACCESS_KEY_ID"] = _prompt_secret(
            "DB_BACKUP_R2_ACCESS_KEY_ID", values.get("DB_BACKUP_R2_ACCESS_KEY_ID", "")
        )
        updates["DB_BACKUP_R2_SECRET_ACCESS_KEY"] = _prompt_secret(
            "DB_BACKUP_R2_SECRET_ACCESS_KEY",
            values.get("DB_BACKUP_R2_SECRET_ACCESS_KEY", ""),
        )
    if bundle_enabled or r2_enabled:
        updates["DB_BACKUP_ENCRYPTION_PASSWORD"] = _prompt_encryption_password(
            values.get("DB_BACKUP_ENCRYPTION_PASSWORD", "")
        )
    return updates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="中文配置和检查灾备加密密码、R2 凭据。")
    parser.add_argument(
        "--env-file",
        default=None,
        help="要更新或检查的 dotenv 文件，默认是项目根目录 .env",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查配置，不写入文件，不显示任何密钥值",
    )
    return parser.parse_args()


def default_env_file() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def main() -> int:
    args = _parse_args()
    target = Path(args.env_file).expanduser() if args.env_file else default_env_file()
    try:
        _, values = read_env_file(target)
        if args.check:
            print(f"检查文件: {target}")
            _print_status(values)
            issues = [*validate_env_file_security(target), *validate_values(values)]
            if issues:
                print("检查失败：")
                for issue in issues:
                    print(f"- {issue}")
                return 2
            print("检查通过：备份加密和 R2 配置字段完整。")
            return 0

        print("开始配置灾备密钥。密钥只写入本地受保护文件，不会打印到终端。")
        updates = interactive_updates(values)
        candidate = {**values, **updates}
        issues = validate_values(candidate)
        if issues:
            print("配置未写入，发现以下问题：")
            for issue in issues:
                print(f"- {issue}")
            return 2
        write_env_file(target, updates)
        print(f"配置完成: {target}（权限已设置为 0600）")
        print("请在安全窗口重建/重启备份容器，然后使用 --check 复核；本脚本不会自动重启服务。")
        return 0
    except (OSError, ValueError) as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 2
    except DurabilityError as exc:
        print(f"{exc}，请使用 --check 复核当前文件。", file=sys.stderr)
        return 3
    except (EOFError, KeyboardInterrupt):
        print("已取消，未写入配置。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
