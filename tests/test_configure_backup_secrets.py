from __future__ import annotations

import os
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
posix_only = pytest.mark.skipif(os.name != "posix", reason="requires POSIX file permissions and symlinks")


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


@posix_only
def test_atomic_write_preserves_unrelated_lines_and_uses_private_mode(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 不相关配置\nAPP_ENV=production\nOTHER_SETTING=${SHARED_VALUE}\n"
        "DB_BACKUP_R2_ENABLED=0 # 保持此说明\n",
        encoding="utf-8",
    )
    original_stat = env_file.stat()
    env_file.chmod(0o640)

    values = valid_values()
    write_env_file(env_file, values)
    lines, parsed = read_env_file(env_file)

    assert "# 不相关配置\n" in lines
    assert parsed["OTHER_SETTING"] == "${SHARED_VALUE}"
    assert "DB_BACKUP_R2_ENABLED=\"1\" # 保持此说明\n" in lines
    assert {key: parsed[key] for key in values} == values
    assert parsed["APP_ENV"] == "production"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert env_file.stat().st_uid == original_stat.st_uid
    assert env_file.stat().st_gid == original_stat.st_gid
    assert not list(tmp_path.glob(".env.*.tmp"))


@posix_only
def test_failed_replace_keeps_original_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = "APP_ENV=production\nDB_BACKUP_R2_ENABLED=0\n"
    env_file.write_text(original, encoding="utf-8")

    with (
        mock.patch.object(os, "replace", side_effect=OSError("injected replace failure")),
        pytest.raises(OSError, match="injected replace failure"),
    ):
        write_env_file(env_file, valid_values())

    assert env_file.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".env.*.tmp"))


@posix_only
def test_temporary_file_is_private_before_replace(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    observed: dict[str, object] = {}

    def inspect_replace(source: str, target: str, **_kwargs: object) -> None:
        temporary = tmp_path / source
        observed["mode"] = stat.S_IMODE(temporary.stat().st_mode)
        observed["content"] = temporary.read_text(encoding="utf-8")
        raise OSError("injected replace failure")

    real_open_private_temp = configure_backup_secrets._open_private_temp

    def permissive_open_private_temp(directory_fd: int, basename: str) -> tuple[int, str]:
        descriptor, temporary_name = real_open_private_temp(directory_fd, basename)
        os.fchmod(descriptor, 0o666)
        return descriptor, temporary_name

    with (
        mock.patch.object(
            configure_backup_secrets,
            "_open_private_temp",
            side_effect=permissive_open_private_temp,
        ),
        mock.patch.object(os, "replace", side_effect=inspect_replace),
        pytest.raises(OSError, match="injected replace failure"),
    ):
        write_env_file(env_file, valid_values())

    assert observed["mode"] == 0o600
    assert "secret-for-test" in observed["content"]
    assert not list(tmp_path.glob(".env.*.tmp"))


@posix_only
def test_crlf_line_endings_and_default_env_path_are_preserved(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"APP_ENV=production\r\nDB_BACKUP_R2_ENABLED=0\r\n")

    write_env_file(env_file, {"DB_BACKUP_R2_ENABLED": "1", "NEW_KEY": "value"})

    assert env_file.read_bytes() == (
        b'APP_ENV=production\r\nDB_BACKUP_R2_ENABLED="1"\r\nNEW_KEY="value"\r\n'
    )
    assert configure_backup_secrets.default_env_file() == ROOT / ".env"


@posix_only
def test_main_uses_project_default_path_when_env_file_is_omitted(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    write_env_file(env_file, valid_values())

    with (
        mock.patch.object(configure_backup_secrets, "default_env_file", return_value=env_file),
        mock.patch.object(sys, "argv", [str(SCRIPT), "--check"]),
    ):
        assert configure_backup_secrets.main() == 0


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
        "https://[r2.example.invalid",
    ],
)
def test_endpoint_rejects_credentials_and_malformed_authority(endpoint: str) -> None:
    values = valid_values()
    values["DB_BACKUP_R2_ENDPOINT"] = endpoint

    issues = validate_values(values)

    assert any("DB_BACKUP_R2_ENDPOINT" in issue for issue in issues)


@posix_only
def test_symlink_target_is_rejected_without_changing_target(tmp_path: Path) -> None:
    real_file = tmp_path / "real.env"
    real_file.write_text("APP_ENV=production\n", encoding="utf-8")
    symlink = tmp_path / ".env"
    symlink.symlink_to(real_file)

    with pytest.raises(ValueError):
        write_env_file(symlink, valid_values())

    assert real_file.read_text(encoding="utf-8") == "APP_ENV=production\n"


@posix_only
def test_symlinked_parent_is_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError):
        write_env_file(linked_parent / ".env", valid_values())


def test_check_command_reports_status_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret_marker = "secret-for-test"
    values = valid_values()
    values["DB_BACKUP_R2_SECRET_ACCESS_KEY"] = secret_marker
    env_file.write_text(
        "DB_BACKUP_R2_ENABLED=1\n"
        "DB_BACKUP_BUNDLE_ENABLED=1\n"
        'DB_BACKUP_R2_ENDPOINT="https://r2.example.invalid" # endpoint\n'
        "DB_BACKUP_R2_BUCKET='backup-bucket'\n"
        'DB_BACKUP_R2_ACCESS_KEY_ID="access-id-for-test"\n'
        "DB_BACKUP_R2_SECRET_ACCESS_KEY='secret-for-test'\n"
        f'DB_BACKUP_ENCRYPTION_PASSWORD="{values["DB_BACKUP_ENCRYPTION_PASSWORD"]}"\n',
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    for key in (
        "DB_BACKUP_R2_ENDPOINT",
        "DB_BACKUP_R2_BUCKET",
        "DB_BACKUP_R2_ACCESS_KEY_ID",
        "DB_BACKUP_R2_SECRET_ACCESS_KEY",
        "DB_BACKUP_ENCRYPTION_PASSWORD",
    ):
        assert values[key] not in completed.stdout
        assert values[key] not in completed.stderr
        assert values[key][:8] not in completed.stdout
        assert values[key][:8] not in completed.stderr
        assert values[key][2:10] not in completed.stdout
        assert values[key][2:10] not in completed.stderr
        assert values[key][-8:] not in completed.stdout
        assert values[key][-8:] not in completed.stderr


@posix_only
def test_check_rejects_insecure_env_file_permissions(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    values = valid_values()
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    env_file.chmod(0o640)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "权限" in completed.stdout


@posix_only
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


@posix_only
def test_failed_check_does_not_echo_invalid_secret_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    values = {
        "DB_BACKUP_R2_ENABLED": "1",
        "DB_BACKUP_BUNDLE_ENABLED": "1",
        "DB_BACKUP_R2_ENDPOINT": "http://endpoint-sentinel.example.invalid",
        "DB_BACKUP_R2_BUCKET": "Invalid_Bucket_Sentinel",
        "DB_BACKUP_R2_ACCESS_KEY_ID": "access-sentinel-value",
        "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret-sentinel-value",
        "DB_BACKUP_ENCRYPTION_PASSWORD": "password-sentinel-value-0123456789",
    }
    write_env_file(env_file, values)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    for key in (
        "DB_BACKUP_R2_ENDPOINT",
        "DB_BACKUP_R2_BUCKET",
        "DB_BACKUP_R2_ACCESS_KEY_ID",
        "DB_BACKUP_R2_SECRET_ACCESS_KEY",
        "DB_BACKUP_ENCRYPTION_PASSWORD",
    ):
        for fragment in (values[key], values[key][:8], values[key][2:10], values[key][-8:]):
            assert fragment not in completed.stdout
            assert fragment not in completed.stderr


@posix_only
def test_interactive_generation_is_persisted_without_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    generated = "generated-password-for-test-0123456789abcdef"
    prompts: list[str] = []

    def respond(prompt: str) -> str:
        prompts.append(prompt)
        if "是否生成灾备归档？" in prompt:
            return ""
        if "是否启用 Cloudflare R2 灾备上传？" in prompt:
            return "n"
        if "是否自动生成新的灾备归档密码？" in prompt:
            return ""
        raise AssertionError(f"unexpected prompt: {prompt}")

    with (
        mock.patch.object(configure_backup_secrets, "generate_encryption_password", return_value=generated),
        mock.patch("builtins.input", side_effect=respond),
        mock.patch.object(sys, "argv", [str(SCRIPT), "--env-file", str(env_file)]),
    ):
        assert configure_backup_secrets.main() == 0

    captured = capsys.readouterr()
    _, values = read_env_file(env_file)
    assert [prompt.split(" [", 1)[0] for prompt in prompts] == [
        "是否生成灾备归档？",
        "是否启用 Cloudflare R2 灾备上传？",
        "是否自动生成新的灾备归档密码？",
    ]
    assert values["DB_BACKUP_BUNDLE_ENABLED"] == "1"
    assert values["DB_BACKUP_R2_ENABLED"] == "0"
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

    temp.write_text("DB_BACKUP_R2_ACCESS_KEY_ID='ends-with-backslash\\'\n", encoding="utf-8")
    _, parsed = read_env_file(temp)
    assert parsed["DB_BACKUP_R2_ACCESS_KEY_ID"] == "ends-with-backslash\\"

    values = {
        "DB_BACKUP_R2_ENABLED": "1",
        "DB_BACKUP_R2_ENDPOINT": "https://r2.example.invalid",
        "DB_BACKUP_R2_BUCKET": "backup-bucket",
        "DB_BACKUP_R2_ACCESS_KEY_ID": 'access"quote$piece',
        "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret$piece\\suffix",
        "DB_BACKUP_ENCRYPTION_PASSWORD": "p" * MIN_ENCRYPTION_PASSWORD_LENGTH,
    }

    rendered = _render_env([], values)
    temp.write_text(rendered, encoding="utf-8")
    _, parsed = read_env_file(temp)
    assert parsed["DB_BACKUP_R2_ACCESS_KEY_ID"] == values["DB_BACKUP_R2_ACCESS_KEY_ID"]
    assert parsed["DB_BACKUP_R2_SECRET_ACCESS_KEY"] == values["DB_BACKUP_R2_SECRET_ACCESS_KEY"]
    assert not validate_values(parsed)

    with pytest.raises(ValueError, match="换行"):
        _render_env([], {"DB_BACKUP_R2_SECRET_ACCESS_KEY": "line\nbreak"})


def test_multiline_quoted_values_are_rejected_before_update(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER_SETTING='first\nDB_BACKUP_R2_ENABLED=1\n'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="跨行"):
        read_env_file(env_file)


def test_variable_references_are_rejected_in_managed_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DB_BACKUP_ENCRYPTION_PASSWORD=${ARCHIVE_PASSWORD}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="变量引用"):
        read_env_file(env_file)


def test_r2_requires_encryption_password_even_when_bundle_is_disabled() -> None:
    values = {
        "DB_BACKUP_BUNDLE_ENABLED": "0",
        "DB_BACKUP_R2_ENABLED": "1",
    }

    issues = validate_values(values)

    assert any("DB_BACKUP_ENCRYPTION_PASSWORD" in issue for issue in issues)


@pytest.mark.parametrize("bucket", ["A_B", "ab", "a" * 64, "-bucket", "bucket-", "a..b", "192.0.2.1"])
def test_r2_bucket_names_follow_s3_rules(bucket: str) -> None:
    values = valid_values()
    values["DB_BACKUP_R2_BUCKET"] = bucket

    issues = validate_values(values)

    assert any("DB_BACKUP_R2_BUCKET" in issue for issue in issues)


@posix_only
def test_new_parent_directory_is_private(tmp_path: Path) -> None:
    env_file = tmp_path / "private" / "nested" / ".env"

    write_env_file(
        env_file,
        {
            "DB_BACKUP_R2_ENABLED": "0",
            "DB_BACKUP_ENCRYPTION_PASSWORD": "p" * MIN_ENCRYPTION_PASSWORD_LENGTH,
        },
    )

    assert stat.S_IMODE(env_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(env_file.parent.parent.stat().st_mode) == 0o700
