from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scripts import configure_backup_secrets
from scripts.configure_backup_secrets import (
    MIN_ENCRYPTION_PASSWORD_LENGTH,
    _render_env,
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
        "# 不相关配置\nAPP_ENV=production\nDB_BACKUP_R2_ENABLED=0 # 保持此说明\n",
        encoding="utf-8",
    )
    original_stat = env_file.stat()
    env_file.chmod(0o640)

    values = valid_values()
    write_env_file(env_file, values)
    lines, parsed = read_env_file(env_file)

    assert "# 不相关配置\n" in lines
    assert "DB_BACKUP_R2_ENABLED=\"1\" # 保持此说明\n" in lines
    assert {key: parsed[key] for key in values} == values
    assert parsed["APP_ENV"] == "production"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert env_file.stat().st_uid == original_stat.st_uid
    assert env_file.stat().st_gid == original_stat.st_gid
    assert not list(tmp_path.glob(".env.*.tmp"))


def test_invalid_endpoint_and_short_password_are_rejected() -> None:
    values = valid_values()
    values["DB_BACKUP_R2_ENDPOINT"] = "http://r2.example.invalid"
    values["DB_BACKUP_ENCRYPTION_PASSWORD"] = "short"

    issues = validate_values(values)

    assert any("DB_BACKUP_R2_ENDPOINT" in issue for issue in issues)
    assert any("DB_BACKUP_ENCRYPTION_PASSWORD" in issue for issue in issues)
    assert all("r2.example.invalid" not in issue for issue in issues)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password" + "@" + "r2.example.invalid",
        "https://:443",
        "https://r2.example.invalid:bad",
        "https://r2.example.invalid:65536",
        "https://r2.example.invalid host",
        "https://r2.example.invalid#fragment",
    ],
)
def test_endpoint_rejects_credentials_and_malformed_authority(endpoint: str) -> None:
    values = valid_values()
    values["DB_BACKUP_R2_ENDPOINT"] = endpoint

    issues = validate_values(values)

    assert any("DB_BACKUP_R2_ENDPOINT" in issue for issue in issues)


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


def test_interactive_generation_is_persisted_without_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    answers = iter(["n", ""])
    generated = "generated-password-for-test-0123456789abcdef"

    with (
        mock.patch.object(configure_backup_secrets, "generate_encryption_password", return_value=generated),
        mock.patch("builtins.input", side_effect=lambda _prompt: next(answers)),
        mock.patch.object(sys, "argv", [str(SCRIPT), "--env-file", str(env_file)]),
    ):
        assert configure_backup_secrets.main() == 0

    captured = capsys.readouterr()
    _, values = read_env_file(env_file)
    assert values["DB_BACKUP_ENCRYPTION_PASSWORD"] == generated
    assert generated not in captured.out
    assert generated not in captured.err


def test_generated_password_is_long_enough_without_being_logged() -> None:
    generated = generate_encryption_password()

    assert len(generated) >= MIN_ENCRYPTION_PASSWORD_LENGTH


def test_dotenv_hash_and_backslash_values_are_not_truncated(tmp_path: Path) -> None:
    temp = tmp_path / ".env"
    temp.write_text(
        "DB_BACKUP_R2_ACCESS_KEY_ID=access#id\\suffix\n"
        "DB_BACKUP_R2_SECRET_ACCESS_KEY=secret#value\\suffix # operator note\n",
        encoding="utf-8",
    )
    _, parsed = read_env_file(temp)
    assert parsed["DB_BACKUP_R2_ACCESS_KEY_ID"] == "access#id\\suffix"
    assert parsed["DB_BACKUP_R2_SECRET_ACCESS_KEY"] == "secret#value\\suffix"

    values = {
        "DB_BACKUP_R2_ENABLED": "1",
        "DB_BACKUP_R2_ENDPOINT": "https://r2.example.invalid",
        "DB_BACKUP_R2_BUCKET": "backup-bucket",
        "DB_BACKUP_R2_ACCESS_KEY_ID": "access#id\\suffix",
        "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret#value\\suffix",
        "DB_BACKUP_ENCRYPTION_PASSWORD": "p" * MIN_ENCRYPTION_PASSWORD_LENGTH,
    }

    rendered = _render_env([], values)
    temp.write_text(rendered, encoding="utf-8")
    _, parsed = read_env_file(temp)
    assert parsed["DB_BACKUP_R2_ACCESS_KEY_ID"] == values["DB_BACKUP_R2_ACCESS_KEY_ID"]
    assert parsed["DB_BACKUP_R2_SECRET_ACCESS_KEY"] == values["DB_BACKUP_R2_SECRET_ACCESS_KEY"]
