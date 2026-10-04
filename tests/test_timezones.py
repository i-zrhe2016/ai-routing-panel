import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app import helpers


@pytest.mark.parametrize("server_timezone", ["UTC", "America/Los_Angeles"])
def test_display_and_expiry_use_beijing_time_independent_of_server(server_timezone):
    original_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = server_timezone
        time.tzset()
        stored = "2026-10-01T16:00:00+00:00"
        assert helpers.format_display_time(stored) == "2026-10-02 00:00:00"
        assert helpers.format_optional_display_time(stored) == "2026-10-02 00:00:00"
        assert helpers.format_input_time(stored) == "2026-10-02T00:00"
        assert helpers.localize_time(stored).isoformat() == "2026-10-02T00:00:00+08:00"
        with patch.object(helpers, "utc_now", return_value=datetime(2026, 10, 1, tzinfo=timezone.utc)):
            assert helpers.parse_expiry("2026-10-02T00:00") == stored
            assert helpers.parse_expiry("2026-10-02T00:00+08:00") == stored
            assert helpers.parse_expiry("2026-10-01T09:00-07:00") == stored
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()


def test_empty_display_and_expiry_values_keep_their_existing_meanings():
    assert helpers.parse_expiry("  ") is None
    assert helpers.parse_expiry(None) is None
    assert helpers.format_display_time(None) == "永久"
    assert helpers.format_input_time(None) == ""
    assert helpers.localize_time(None) is None
    assert helpers.format_optional_display_time(None) == "暂无"


def test_invalid_and_elapsed_beijing_expiry_inputs_are_rejected():
    with pytest.raises(helpers.ValidationError, match="到期时间格式不正确"):
        helpers.parse_expiry("2026-99-99T00:00")
    with patch.object(helpers, "utc_now", return_value=datetime(2026, 10, 1, 16, tzinfo=timezone.utc)):
        for value in ("2026-10-01T23:59", "2026-10-02T00:00", "2026-10-02T00:00+08:00"):
            with pytest.raises(helpers.ValidationError, match="到期时间必须晚于当前时间"):
                helpers.parse_expiry(value)
