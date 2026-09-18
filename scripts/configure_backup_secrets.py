#!/usr/bin/env python3
"""用中文安全配置和检查灾备加密密码、R2 凭据。"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shlex
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

MIN_ENCRYPTION_PASSWORD_LENGTH = 32
R2_KEYS = (
    "DB_BACKUP_R2_ENDPOINT",
    "DB_BACKUP_R2_BUCKET",
    "DB_BACKUP_R2_ACCESS_KEY_ID",
    "DB_BACKUP_R2_SECRET_ACCESS_KEY",
)
STATUS_KEYS = ("DB_BACKUP_R2_ENABLED", "DB_BACKUP_BUNDLE_ENABLED", "DB_BACKUP_ENCRYPTION_PASSWORD", *R2_KEYS)
ENV_ASSIGNMENT = re.compile(
    r"^(?P<indent>\s*)(?:(?P<export>export)\s+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*="
)


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    try:
        parts = shlex.split(value, comments=True, posix=True)
    except ValueError:
        return value.strip("\"'")
    return " ".join(parts)


def read_env_file(path: str | Path) -> tuple[list[str], dict[str, str]]:
    """读取 dotenv 文本；不会打印或记录任何值。"""

    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError(f"配置文件不能是符号链接: {target}")
    if not target.exists():
        return [], {}
    if not target.is_file():
        raise ValueError(f"配置路径不是普通文件: {target}")

    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    values: dict[str, str] = {}
    for line in lines:
        match = ENV_ASSIGNMENT.match(line.rstrip("\r\n"))
        if match:
            values[match.group("key")] = _parse_env_value(
                line.rstrip("\r\n")[match.end() :]
            )
    return lines, values


def _quote_env_value(value: str) -> str:
    return shlex.quote(str(value))


def _render_env(lines: list[str], updates: dict[str, str]) -> str:
    rendered: list[str] = []
    replaced: set[str] = set()
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
        rendered.append(f"{prefix}{key}={_quote_env_value(updates[key])}\n")
        replaced.add(key)

    if rendered and not rendered[-1].endswith(("\n", "\r")):
        rendered[-1] += "\n"
    for key, value in updates.items():
        if key not in replaced:
            rendered.append(f"{key}={_quote_env_value(value)}\n")
    return "".join(rendered)


def write_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """原子更新 dotenv，并让最终文件权限保持为 0600。"""

    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError(f"配置文件不能是符号链接: {target}")
    if target.exists() and not target.is_file():
        raise ValueError(f"配置路径不是普通文件: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = read_env_file(target)
    content = _render_env(lines, updates)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def generate_encryption_password() -> str:
    """生成不会在终端回显的高熵归档密码。"""

    return secrets.token_urlsafe(48)


def _enabled(value: str | None, default: bool = True) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _valid_endpoint(endpoint: str, bucket: str) -> bool:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
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
    r2_enabled = _enabled(values.get("DB_BACKUP_R2_ENABLED"))
    encryption_password = values.get("DB_BACKUP_ENCRYPTION_PASSWORD", "")
    if bundle_enabled and not encryption_password:
        issues.append("缺少 DB_BACKUP_ENCRYPTION_PASSWORD")
    elif encryption_password and len(encryption_password) < MIN_ENCRYPTION_PASSWORD_LENGTH:
        issues.append(
            f"DB_BACKUP_ENCRYPTION_PASSWORD 至少需要 {MIN_ENCRYPTION_PASSWORD_LENGTH} 个字符"
        )
    if "\n" in encryption_password or "\r" in encryption_password:
        issues.append("DB_BACKUP_ENCRYPTION_PASSWORD 不能包含换行")

    if not r2_enabled:
        return issues

    for key in R2_KEYS:
        if not values.get(key, "").strip():
            issues.append(f"缺少 {key}")
    endpoint = values.get("DB_BACKUP_R2_ENDPOINT", "").strip()
    bucket = values.get("DB_BACKUP_R2_BUCKET", "").strip()
    if endpoint and bucket and not _valid_endpoint(endpoint, bucket):
        issues.append("DB_BACKUP_R2_ENDPOINT 必须是 HTTPS 基础地址，不能带 query 或 fragment")
    if bucket and (any(char.isspace() for char in bucket) or "/" in bucket):
        issues.append("DB_BACKUP_R2_BUCKET 不能包含空白或斜杠")
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
    r2_enabled = _ask_yes_no(
        "是否启用 Cloudflare R2 灾备上传？",
        _enabled(values.get("DB_BACKUP_R2_ENABLED")),
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
    updates["DB_BACKUP_ENCRYPTION_PASSWORD"] = _prompt_encryption_password(
        values.get("DB_BACKUP_ENCRYPTION_PASSWORD", "")
    )
    return updates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="中文配置和检查灾备加密密码、R2 凭据。")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="要更新或检查的 dotenv 文件，默认是项目根目录 .env",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查配置，不写入文件，不显示任何密钥值",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    target = Path(args.env_file).expanduser()
    try:
        _, values = read_env_file(target)
        if args.check:
            print(f"检查文件: {target}")
            _print_status(values)
            issues = validate_values(values)
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
    except (EOFError, KeyboardInterrupt):
        print("已取消，未写入配置。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
