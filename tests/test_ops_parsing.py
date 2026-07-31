import json
from datetime import datetime, timezone

from components.xray_ops.parsing import parse_log_bytes


NOW = datetime(2030, 1, 3, 0, 0, tzinfo=timezone.utc)


def test_plain_xray_log_parses_accepted_event_and_keeps_incomplete_tail():
    first = "2030/01/02 12:34:56.123 [Info] proxy accepted tcp:api.example.com:443\n"
    second = "2030/01/02 12:35:00 [Error] failed to start password=do-not-store\n"
    incomplete = "2030/01/02 12:36:00 [Info] partial"
    data = (first + second + incomplete).encode()

    result = parse_log_bytes(
        node_role="normal_data_plane",
        source_kind="file",
        stream="access",
        data=data,
        source_identity="1:2",
        base_offset=100,
        collected_at=NOW,
    )

    assert len(result.events) == 2
    assert result.consumed_bytes == len((first + second).encode())
    assert result.incomplete_bytes == len(incomplete.encode())
    accepted = result.events[0]
    assert accepted.event_type == "accepted"
    assert accepted.attributes == {
        "protocol": "tcp",
        "target_host": "api.example.com",
        "target_port": 443,
    }
    assert accepted.observed_at.startswith("2030-01-02T12:34:56.123")
    assert result.events[1].event_type == "xray_error"
    assert "do-not-store" not in result.events[1].message


def test_docker_json_uses_container_timestamp_and_stream():
    record = {
        "log": "[Warning] proxy accepted udp:[2001:db8::1]:53\n",
        "stream": "stderr",
        "time": "2030-01-02T12:34:56.500000000Z",
    }
    data = (json.dumps(record) + "\n").encode()

    result = parse_log_bytes(
        node_role="ai_data_plane",
        source_kind="docker_json",
        stream="container",
        data=data,
        source_identity="3:4",
        base_offset=0,
        collected_at=NOW,
        docker_json=True,
    )

    assert len(result.events) == 1
    event = result.events[0]
    assert event.stream == "error"
    assert event.timestamp_quality == "container"
    assert event.event_type == "accepted"
    assert event.attributes["target_host"] == "2001:db8::1"
    assert event.attributes["target_port"] == 53


def test_event_id_is_stable_across_source_offsets():
    line = b"2030/01/02 12:34:56 [Info] proxy accepted tcp:example.com:443\n"
    common = {
        "node_role": "normal_data_plane",
        "source_kind": "file",
        "stream": "access",
        "data": line,
        "source_identity": "1:2",
        "collected_at": NOW,
    }

    first = parse_log_bytes(base_offset=0, **common)
    second = parse_log_bytes(base_offset=4096, **common)

    assert first.events[0].event_id == second.events[0].event_id
