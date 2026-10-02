import importlib
import json
import os
import re
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
UPDATE_GOLDENS = os.environ.get("CONTROL_PLANE_UPDATE_GOLDENS") == "1"

SUBSCRIPTION_PROFILE = {
    "server": "panel.example.com",
    "uuid": "11111111-1111-1111-1111-111111111111",
    "flow": "xtls-rprx-vision",
    "server_name": "www.microsoft.com",
    "public_key": "synthetic-public-key",
    "short_id": "0123456789abcdef",
    "fingerprint": "chrome",
}

XRAY_VALUES = {
    "XRAY_LISTEN_HOST": "0.0.0.0",
    "XRAY_LISTEN_PORT": "443",
    "XRAY_PUBLIC_HOST": "panel.example.com",
    "XRAY_PUBLIC_PORT": "31098",
    "XRAY_CLIENT_UUID": "11111111-1111-1111-1111-111111111111",
    "XRAY_FLOW": "xtls-rprx-vision",
    "XRAY_REALITY_PRIVATE_KEY": "synthetic-normal-private-key",
    "XRAY_REALITY_PUBLIC_KEY": "synthetic-normal-public-key",
    "XRAY_REALITY_SHORT_ID": "0123456789abcdef",
    "XRAY_SERVER_NAME": "www.microsoft.com",
    "XRAY_DEST": "www.microsoft.com:443",
    "XRAY_FINGERPRINT": "chrome",
    "XRAY_LOGLEVEL": "warning",
    "AI_NODE_CLIENT_UUID": "22222222-2222-2222-2222-222222222222",
    "AI_NODE_FLOW": "xtls-rprx-vision",
    "AI_NODE_REALITY_PRIVATE_KEY": "synthetic-ai-private-key",
    "AI_NODE_REALITY_PUBLIC_KEY": "synthetic-ai-public-key",
    "AI_NODE_REALITY_SHORT_ID": "fedcba9876543210",
    "AI_NODE_SERVER_NAME": "www.amazon.com",
    "AI_NODE_DEST": "www.amazon.com:443",
    "AI_NODE_FINGERPRINT": "chrome",
}

DYNAMIC_ROUTING = {
    "routing": {
        "domainStrategy": "AsIs",
        "rules": [
            {
                "type": "field",
                "domain": ["domain:chatgpt.com", "domain:openai.com"],
                "outboundTag": "ai_proxy",
            }
        ],
    },
    "outbounds": [
        {
            "tag": "ai_proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": "ai.example.net",
                        "port": 443,
                        "users": [
                            {
                                "id": "22222222-2222-2222-2222-222222222222",
                                "encryption": "none",
                                "flow": "xtls-rprx-vision",
                            }
                        ],
                    }
                ]
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": "www.amazon.com",
                    "fingerprint": "chrome",
                    "publicKey": "synthetic-ai-public-key",
                    "shortId": "fedcba9876543210",
                },
            },
        }
    ],
}


def _write_or_compare_text(path, actual):
    if UPDATE_GOLDENS:
        path.write_text(actual, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == actual


def _normalize_clash_subscription(content):
    return re.sub(
        r"(IP-CIDR,)(?:\d{1,3}\.){3}\d{1,3}(/\d+)",
        r"\1<private-network>\2",
        content,
    )


def _write_or_compare_json(path, actual):
    serialized = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if UPDATE_GOLDENS:
        path.write_text(serialized, encoding="utf-8")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


def test_subscription_and_xray_configuration_match_synthetic_goldens():
    from app.subscriptions import (
        build_clash_subscription_content,
        build_v2ray_subscription_content,
    )
    from app.xray.render_config import build_ai_node_config, build_server_config

    clash_content = build_clash_subscription_content(
        SUBSCRIPTION_PROFILE, 31098, "golden-plan"
    )
    _write_or_compare_text(
        GOLDEN_DIR / "subscription-clash.yaml",
        _normalize_clash_subscription(clash_content),
    )
    _write_or_compare_json(
        GOLDEN_DIR / "subscription-v2ray.json",
        {"content": build_v2ray_subscription_content(SUBSCRIPTION_PROFILE, 31098, "golden-plan")},
    )
    _write_or_compare_json(
        GOLDEN_DIR / "xray-data-plane-config.json",
        build_server_config(XRAY_VALUES, DYNAMIC_ROUTING, panel_ports=[31098]),
    )
    _write_or_compare_json(
        GOLDEN_DIR / "xray-ai-node-config.json",
        build_ai_node_config(XRAY_VALUES),
    )


def _configure_synthetic_dashboard_state(state):
    state.sync_traffic_state = lambda: None
    state.disable_auto_stopped_ports = lambda reload_xray=True: None
    state.sync_data_plane_ai_state = lambda: None
    state.nodes.data_plane_status = lambda: {
        "label": "normal-synthetic",
        "configured": True,
        "reachable": True,
        "xray_running": True,
        "management_target": "agent-normal",
        "supports_restart": True,
    }
    state.nodes.ai_nodes_status = lambda: []
    state.nodes.ai_node_status = lambda nodes=None: {
        "label": "ai-synthetic",
        "configured": True,
        "reachable": True,
        "xray_running": True,
        "management_target": "agent-ai",
        "supports_restart": True,
    }
    state.ai_routing.ai_routing_status = lambda sync_error="": {
        "status": "applied",
        "route_status": "applied",
        "report_generated_at": "2026-01-02T03:04:05+00:00",
        "config_apply_status": "direct",
        "ai_candidates": [
            {"selected": True, "is_reachable": True, "label": "ai-synthetic"}
        ],
    }
    state.ai_routing.query_ai_domain_overview = lambda sync_error="": {
        "available": True,
        "current_ai_domains": 1,
        "total_ai_domains": 1,
        "route_status": "applied",
    }
    state.dns_failover.dns_failover_status = lambda: {
        "enabled": False,
        "configured": False,
        "current_target": "primary",
    }


def _normalize_dashboard(payload):
    dashboard = payload["dashboard"]
    dashboard["meta"]["csrf_token"] = "<csrf-token>"
    dashboard["meta"]["timezone_label"] = "<timezone>"
    dashboard["subscription"]["client_config_path"] = "<client-config-path>"
    return payload


def _seed_synthetic_port(state):
    payload = state.validate_port_payload(
        {"listen_port": 31098, "traffic_limit": "10G", "note": "golden-plan"}
    )
    port_id = state.create_port(payload)
    with state.connect() as connection:
        connection.execute(
            """
            UPDATE ports
            SET tenant_token = ?, subscription_token = ?, tenant_username = ?,
                tenant_password = ?, created_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "tenant-golden",
                "subscription-golden",
                "tenant-user",
                "tenant-password",
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
                port_id,
            ),
        )
    return port_id


def test_dashboard_and_tenant_api_match_synthetic_goldens(tmp_path, monkeypatch, request):
    monkeypatch.setenv("PANEL_ALLOWED_NETWORKS", "127.0.0.1/32,::1/128")
    monkeypatch.setenv("DNS_FAILOVER_ENABLED", "0")
    monkeypatch.setenv("GRAFANA_PUBLIC_URL", "https://retired-grafana.example.com")
    monkeypatch.setenv("GRAFANA_OBSERVABILITY_UID", "retired-dashboard")

    helper_environment = (
        "DATA_DIR",
        "DB_PATH",
        "XRAY_ENV_FILE_PATH",
        "XRAY_CONFIG_PATH",
        "XRAY_PANEL_PORTS_PATH",
        "XRAY_ACCESS_LOG_PATH",
        "DATAPLANE_CONTAINER_NAME",
        "XRAY_CLIENT_CONFIG_PATH",
        "PANEL_PUBLIC_URL",
        "SEED_LISTEN_PORT",
        "PANEL_SECRET_KEY",
        "PROBE_ENABLED",
        "PROBE_TEST_LISTEN_PORT",
    )
    original_environment = {key: os.environ.get(key) for key in helper_environment}

    def restore_helper_environment():
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    request.addfinalizer(restore_helper_environment)

    from tests.test_tenant_panel import load_panel_module

    panel = load_panel_module(tmp_path)
    subscriptions = importlib.reload(importlib.import_module("app.subscriptions"))
    from app.web import core, customer_api

    core.parse_xray_client_profile = subscriptions.parse_xray_client_profile
    customer_api.parse_xray_client_profile = subscriptions.parse_xray_client_profile
    state = panel.state
    _configure_synthetic_dashboard_state(state)
    client = panel.app.test_client()

    empty_dashboard = client.get("/api/dashboard")
    assert empty_dashboard.status_code == 200
    assert "grafana_url" not in empty_dashboard.get_json()["dashboard"]["meta"]
    assert "grafana_observability_uid" not in empty_dashboard.get_json()["dashboard"]["meta"]
    _write_or_compare_json(
        GOLDEN_DIR / "dashboard-empty.json",
        _normalize_dashboard(empty_dashboard.get_json()),
    )

    _seed_synthetic_port(state)
    seeded_dashboard = client.get("/api/dashboard")
    assert seeded_dashboard.status_code == 200
    _write_or_compare_json(
        GOLDEN_DIR / "dashboard-seeded.json",
        _normalize_dashboard(seeded_dashboard.get_json()),
    )

    port = state.query_ports()[0]
    tenant_path = f"/api/tenant/{port['tenant_token']}/subscription"
    unauthenticated = panel.app.test_client().get(tenant_path)
    assert unauthenticated.status_code == 401
    _write_or_compare_json(
        GOLDEN_DIR / "tenant-subscription-unauthenticated.json",
        unauthenticated.get_json(),
    )

    from app.auth.sessions import tenant_session_marker
    from app.config import TENANT_SESSION_MARKER_KEY, TENANT_SESSION_TOKEN_KEY

    tenant_client = panel.app.test_client()
    with tenant_client.session_transaction() as session:
        session[TENANT_SESSION_TOKEN_KEY] = port["tenant_token"]
        session[TENANT_SESSION_MARKER_KEY] = tenant_session_marker(port)
    authenticated = tenant_client.get(tenant_path)
    assert authenticated.status_code == 200
    _write_or_compare_json(
        GOLDEN_DIR / "tenant-subscription-authenticated.json",
        authenticated.get_json(),
    )
