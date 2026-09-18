from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.configure_backup_secrets import (
    MIN_ENCRYPTION_PASSWORD_LENGTH,
    generate_encryption_password,
    read_env_file,
    validate_values,
    write_env_file,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure_backup_secrets.py"


def valid_values() -> dict[str, str]:
    return {
        "DB_BACKUP_R2_ENABLED": "1",
        "DB_BACKUP_BUNDLE_ENABLED": "1",
        "DB_BACKUP_R2_ENDPOINT": "https://r2.example.invalid",
        "DB_BACKUP_R2_BUCKET": "backup-bucket",
        "DB_BACKUP_R2_ACCESS_KEY_ID": "access-id-for-test",
        "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret-for-test",
        "DB_BACKUP_ENCRYPTION_PASSWORD": "p" * MIN_ENCRYPTION_PASSWORD_LENGTH,
    }


def test_validation_lists_missing_names_without_exposing_values() -> None:
    issues = validate_values(
        {
            "DB_BACKUP_R2_ENABLED": "1",
            "DB_BACKUP_BUNDLE_ENABLED": "1",
        }
    )

    joined = "\n".join(issues)
    assert "DB_BACKUP_ENCRYPTION_PASSWORD" in joined
    assert "DB_BACKUP_R2_ENDPOINT" in joined
    assert "DB_BACKUP_R2_SECRET_ACCESS_KEY" in joined
    assert "access-id-for-test" not in joined


def test_atomic_write_preserves_unrelated_lines_and_uses_private_mode(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 不相关配置\nAPP_ENV=production\nDB_BACKUP_R2_ENABLED=0\n",
        encoding="utf-8",
    )

    values = valid_values()
    write_env_file(env_file, values)
    lines, parsed = read_env_file(env_file)

    assert "# 不相关配置\n" in lines
    assert {key: parsed[key] for key in values} == values
    assert parsed["APP_ENV"] == "production"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".env.*.tmp"))


def test_invalid_endpoint_and_short_password_are_rejected() -> None:
    values = valid_values()
    values["DB_BACKUP_R2_ENDPOINT"] = "http://r2.example.invalid"
    values["DB_BACKUP_ENCRYPTION_PASSWORD"] = "short"

    issues = validate_values(values)

    assert any("DB_BACKUP_R2_ENDPOINT" in issue for issue in issues)
    assert any("DB_BACKUP_ENCRYPTION_PASSWORD" in issue for issue in issues)
    assert all("r2.example.invalid" not in issue for issue in issues)


def test_symlink_target_is_rejected_without_changing_target(tmp_path: Path) -> None:
    real_file = tmp_path / "real.env"
    real_file.write_text("APP_ENV=production\n", encoding="utf-8")
    symlink = tmp_path / ".env"
    symlink.symlink_to(real_file)

    with pytest.raises(ValueError, match="符号链接"):
        write_env_file(symlink, valid_values())

    assert real_file.read_text(encoding="utf-8") == "APP_ENV=production\n"


def test_check_command_reports_status_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret_marker = "secret-for-test"
    values = valid_values()
    values["DB_BACKUP_R2_SECRET_ACCESS_KEY"] = secret_marker
    write_env_file(env_file, values)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "检查通过" in completed.stdout
    assert secret_marker not in completed.stdout
    assert secret_marker not in completed.stderr


def test_check_command_returns_nonzero_for_incomplete_config(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    write_env_file(
        env_file,
        {
            "DB_BACKUP_R2_ENABLED": "1",
            "DB_BACKUP_BUNDLE_ENABLED": "1",
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "DB_BACKUP_R2_ENDPOINT" in completed.stdout
    assert "检查失败" in completed.stdout


def test_generated_password_is_long_enough_without_being_logged() -> None:
    generated = generate_encryption_password()

    assert len(generated) >= MIN_ENCRYPTION_PASSWORD_LENGTH
