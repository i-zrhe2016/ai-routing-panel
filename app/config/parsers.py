"""Pure environment-variable parsing helpers.

Separated from the configuration values themselves so the parsing/validation
logic can be unit-tested and reused without importing the whole config surface.
Error messages are kept verbatim (Chinese) because callers surface them to the
admin UI and tests assert on them.
"""

import re
import shlex
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TIME_OF_DAY_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})$")
UTC_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hour>\d{2}):(?P<minute>\d{2})$")


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


def parse_time_of_day_env(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = TIME_OF_DAY_RE.fullmatch(raw)
    if match is None:
        raise ValueError(f"{field_name} 必须使用 HH:MM 格式。")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ValueError(f"{field_name} 必须使用有效的 HH:MM 时间。")
    return f"{hour:02d}:{minute:02d}"


def parse_timezone_env(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return ""

    offset_match = UTC_OFFSET_RE.fullmatch(raw)
    if offset_match is not None:
        hour = int(offset_match.group("hour"))
        minute = int(offset_match.group("minute"))
        if hour > 23 or minute > 59:
            raise ValueError(f"{field_name} UTC 偏移必须有效，例如 +08:00。")
        sign = -1 if offset_match.group("sign") == "-" else 1
        timezone(sign * timedelta(hours=hour, minutes=minute))
        return raw

    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"{field_name} 必须是有效的时区名或 UTC 偏移，例如 Asia/Shanghai 或 +08:00。") from exc
    return raw
