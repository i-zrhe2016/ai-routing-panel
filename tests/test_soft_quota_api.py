"""API/subscription integration uses the existing isolated panel fixture."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture
def panel(tmp_path):
    # Fixture import stays inside the test to avoid collecting an import-time app
    # graph before the established reload/isolation mechanism.
    from tests.test_tenant_panel import load_panel_module

    with patch.dict(os.environ):
        yield load_panel_module(tmp_path)


def test_overquota_account_stays_subscribed_and_reset_preserves_manual_disable(panel):
    state = panel.state
    # The fixture replaces rendering with a no-op. Give mutation orchestration
    # one stable harmless config artifact to compare without any real restart.
    from app.config import XRAY_CONFIG_PATH

    XRAY_CONFIG_PATH.write_text("{}")
    payload = state.validate_port_payload({"listen_port": "31111", "traffic_limit": "100G", "note": "softquota"})
    state.create_port(payload)
    port = state.query_ports()[0]
    with state.connect() as conn:
        conn.execute(
            "INSERT INTO traffic_totals(listen_port,total_bytes_sent,total_bytes_received) VALUES(31111,?,?)",
            (50 * 1024**3, 50 * 1024**3),
        )
        conn.commit()
    state.disable_expired_ports()
    port = state.query_ports()[0]
    assert port["enabled"] == 1
    assert port["status"] == "throttled"
    assert port["rate_limit"]["state"] == "pending"
    summary = state.query_summary([port])
    assert summary["active_ports"] == 1 and summary["throttled_ports"] == 1
    client = panel.app.test_client()
    token = port["subscription_token"]
    assert client.get("/tenant-subscriptions/" + token + "/clash").status_code == 200
    assert client.get("/tenant-subscriptions/" + token + "/v2ray").status_code == 200
    assert client.get("/" + state.get_subscription_token() + "/31111/clash").status_code == 200
    state.toggle_port(port["id"])
    assert state.query_ports()[0]["status"] == "disabled"
    assert client.get("/tenant-subscriptions/" + token + "/clash").status_code == 404
    assert client.get("/" + state.get_subscription_token() + "/31111/clash").status_code == 404
    assert state.reset_port_traffic(port["id"]) is False
    assert state.query_ports()[0]["enabled"] == 0
    state.toggle_port(port["id"])
    restored = state.query_ports()[0]
    assert restored["status"] == "active" and restored["traffic_usage_bytes"] == 0
