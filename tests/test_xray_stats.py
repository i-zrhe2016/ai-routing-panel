import json
import re
import stat
from datetime import datetime, timezone

from components.xray_ops.storage import OpsStore
from components.xray_ops.xray_stats import (
    XrayStatsRedactor,
    XrayStatsSamplerConfig,
    _load_or_create_salt,
    parse_xray_debug_vars,
)


def test_xray_debug_vars_parser_never_persists_raw_user_or_inbound(tmp_path):
    payload = {
        "stats": {
            "user": {
                "alice@example.com": {"uplink": 100, "downlink": 900},
            },
            "inbound": {
                "vless-reality-inbound": {"uplink": 100, "downlink": 900},
            },
        }
    }
    redactor = XrayStatsRedactor(b"test-salt")

    samples = parse_xray_debug_vars(
        payload,
        source_id="ai-xray",
        node_role="ai_data_plane",
        observed_at=datetime(2030, 1, 2, tzinfo=timezone.utc),
        redactor=redactor,
    )
    rows = [sample.as_dict() for sample in samples]
    serialized = json.dumps(rows, sort_keys=True)

    assert len(rows) == 4
    assert "alice@example.com" not in serialized
    assert "vless-reality-inbound" not in serialized
    assert {row["entity_type"] for row in rows} == {"user", "inbound"}
    assert all(re.match(r"^(usr|inb)-[0-9a-f]{16}$", row["entity_ref"]) for row in rows)

    store = OpsStore(tmp_path / "ops.db")
    store.initialize()
    assert store.insert_xray_traffic_samples(rows) == 4
    persisted = json.dumps(store.query_xray_traffic_samples("2030-01-01T00:00:00Z", "2030-01-03T00:00:00Z"))
    assert "alice@example.com" not in persisted
    assert "vless-reality-inbound" not in persisted


def test_xray_statsquery_payload_is_normalized_for_the_same_redacted_parser():
    payload = {
        "stat": [
            {"name": "user>>>alice@example.com>>>traffic>>>uplink", "value": 100},
            {"name": "user>>>alice@example.com>>>traffic>>>downlink"},
            {"name": "inbound>>>vless-reality-inbound>>>traffic>>>uplink", "value": 50},
            {"name": "outbound>>>direct>>>traffic>>>uplink", "value": 999},
        ]
    }
    redactor = XrayStatsRedactor(b"test-salt")

    samples = parse_xray_debug_vars(
        payload,
        source_id="normal-xray",
        node_role="normal_data_plane",
        observed_at=datetime(2030, 1, 2, tzinfo=timezone.utc),
        redactor=redactor,
    )
    rows = [sample.as_dict() for sample in samples]

    assert len(rows) == 3
    assert {row["entity_type"] for row in rows} == {"user", "inbound"}
    assert {row["value"] for row in rows} == {0, 50, 100}
    serialized = json.dumps(rows, sort_keys=True)
    assert "alice@example.com" not in serialized
    assert "vless-reality-inbound" not in serialized


def test_xray_redaction_salt_file_is_created_private(tmp_path):
    salt_file = tmp_path / "xray-stats-salt"

    salt = _load_or_create_salt(str(salt_file))

    assert salt
    assert stat.S_IMODE(salt_file.stat().st_mode) == 0o600
    assert _load_or_create_salt(str(salt_file)) == salt


def test_sampler_config_defaults_stats_sources_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("OPS_XRAY_STATS_ENABLED", raising=False)
    monkeypatch.setenv("OPS_DB_PATH", str(tmp_path / "ops.db"))

    config = XrayStatsSamplerConfig.from_env()

    assert all(not source.enabled for source in config.sources)
