"""Pure environment-variable parsing helpers.

Separated from the configuration values themselves so the parsing/validation
logic can be unit-tested and reused without importing the whole config surface.
Error messages are kept verbatim (Chinese) because callers surface them to the
admin UI and tests assert on them.
"""

import shlex


def parse_optional_env_port(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是数字。") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{field_name} 必须在 1-65535 之间。")
    return port


def parse_nonnegative_env_int(value, field_name):
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是非负整数。") from exc
    if number < 0:
        raise ValueError(f"{field_name} 必须是非负整数。")
    return number


def parse_positive_env_int(value, field_name):
    number = parse_nonnegative_env_int(value, field_name)
    if number <= 0:
        raise ValueError(f"{field_name} 必须大于 0。")
    return number


def parse_positive_env_float(value, field_name):
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是正数。") from exc
    if number <= 0:
        raise ValueError(f"{field_name} 必须是正数。")
    return number


def parse_bool_env(value, default=False):
    raw = str(value if value is not None else ("1" if default else "0")).strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def parse_shell_words_env(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw))
    except ValueError as exc:
        raise ValueError(f"{field_name} 配置格式无效。") from exc
