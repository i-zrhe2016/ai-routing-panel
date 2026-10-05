"""Routing snapshots must follow applied policy, never management-health guesses."""

import json
from types import SimpleNamespace

import pytest

STAMP = "2026-10-02T12:00:00+00:00"


def candidate(host="ai.example.com", selected=True, reachable=True, index=0):
    return {
        "upstream_host": host,
        "upstream_port": 443,
        "index": index,
        "label": f"AI {index}",
        "selected": selected,
        "is_reachable": reachable,
    }


def routing(status="applied", apply="direct", candidates=None, dns=None, **changes):
    from app.web.core import build_traffic_routing

    ai = {
        "status": status,
        "config_apply_status": apply,
        "report_generated_at": STAMP,
        "ai_candidates": [candidate()] if candidates is None else candidates,
    }
    ai.update(changes)
    return build_traffic_routing({"configured": True, "reachable": True}, {"reachable": True}, ai, dns or {})


def test_applied_normal_snapshot_retains_both_traffic_classes():
    result = routing(candidates=[candidate(selected=False, index=8), candidate(index=3)])
    assert result["path"] == "normal_ai"
    assert result["ordinary_direct_state"] == "active"
    assert result["ai_branch_state"] == "active"
    assert result["traffic_scope"] == "split_domains"
    assert result["transit_nodes"] == ["AI 3"]


@pytest.mark.parametrize("status", ["disabled", "idle", "pending_proxy_template"])
def test_confirmed_no_ai_policy_is_direct(status):
    result = routing(status=status)
    assert result["path"] == "normal_direct"
    assert result["ordinary_direct_state"] == "active"
    assert result["ai_branch_state"] == "standby"


@pytest.mark.parametrize("status", ["waiting_report", "unknown", "sync_error", "failed"])
def test_unconfirmed_status_keeps_ai_unknown(status):
    result = routing(status=status)
    assert result["path"] == "normal_ai_pending"
    assert result["ordinary_direct_state"] == "active"
    assert result["ai_branch_state"] == "unknown"


@pytest.mark.parametrize("apply", ["delegated", "unmanaged", "failed", "not_needed", "unknown", ""])
def test_unconfirmed_application_never_claims_ai_or_fallback(apply):
    for status in ("applied", "manual_fallback", "pending_proxy_template"):
        result = routing(status=status, apply=apply)
        assert result["path"] == "normal_ai_pending"
        assert result["ai_branch_state"] == "unknown"


@pytest.mark.parametrize("probe,state", [(False, "blocked"), (None, "unknown"), (True, "active")])
def test_applied_selected_business_probe_does_not_invent_a_fallback(probe, state):
    result = routing(candidates=[candidate(reachable=probe)])
    assert result["path"] == "normal_ai"
    assert result["ai_branch_state"] == state
    assert result["ordinary_direct_state"] == "active"


def test_management_health_cannot_supply_missing_selection_or_probe():
    for candidates in ([], [candidate(selected=False)], [candidate(), candidate(index=1)]):
        result = routing(candidates=candidates)
        assert result["path"] == "normal_ai_pending"
        assert result["ai_branch_state"] == "unknown"


@pytest.mark.parametrize(
    "status", ["manual_fallback", "manual_target_unreachable", "fallback_to_primary", "probe_error"]
)
def test_only_confirmed_fallback_uses_direct_for_ai(status):
    result = routing(status=status, apply="unchanged")
    assert result["path"] == "normal_fallback"
    assert result["ordinary_direct_state"] == "active"
    assert result["ai_branch_state"] == "standby"


@pytest.mark.parametrize("stamp", [None, "", "invalid", "2026-10-02T12:00:00"])
def test_missing_invalid_or_timezone_free_report_timestamp_is_unconfirmed(stamp):
    assert routing(report_generated_at=stamp)["path"] == "normal_ai_pending"


def backup(mode, target=None):
    return {
        "enabled": True,
        "configured": True,
        "current_target": "backup",
        "control_plane_backup_xray_enabled": True,
        "backup_xray_mode": mode,
        "backup_relay_target": target,
    }


def test_backup_direct_stays_direct_with_healthy_ai_candidate():
    result = routing(dns=backup("direct"))
    assert result["path"] == "dns_backup_direct"
    assert result["traffic_scope"] == "all_traffic"
    assert result["ai_branch_state"] == "standby"
    assert result["ordinary_direct_state"] == "active"


def test_backup_relay_matches_actual_target_without_health_based_mode_switch():
    target = {"upstream_host": "relay.example.com", "upstream_port": 443}
    result = routing(
        candidates=[candidate("relay.example.com", selected=False, reachable=False)], dns=backup("relay", target)
    )
    assert result["path"] == "dns_backup_relay_ai"
    assert result["traffic_scope"] == "all_traffic"
    assert result["ordinary_direct_state"] == "standby"
    assert result["ai_branch_state"] == "blocked"
    assert result["transit_nodes"] == ["relay.example.com:443"]


def test_different_or_missing_relay_target_probe_stays_unknown():
    for target in (None, {"upstream_host": "relay.example.com", "upstream_port": 443}):
        result = routing(dns=backup("relay", target))
        assert result["path"] == "dns_backup_relay_ai"
        assert result["ai_branch_state"] == "unknown"


@pytest.mark.parametrize("mode", [None, "", "disabled", "invalid"])
def test_unknown_backup_configuration_does_not_invent_direct(mode):
    result = routing(dns=backup(mode))
    assert result["path"] == "dns_backup_unknown"
    assert result["ordinary_direct_state"] == result["ai_branch_state"] == "unknown"


@pytest.mark.parametrize("target", [None, "", "invalid"])
def test_enabled_dns_with_unknown_target_keeps_entry_unknown(target):
    assert routing(dns={"enabled": True, "configured": True, "current_target": target})["path"] == "unknown"


def test_backup_relay_target_exposes_only_validated_default_outbound_identity(tmp_path, monkeypatch):
    from app.xray import apply

    monkeypatch.setattr(apply, "XRAY_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(apply, "CONTROL_PLANE_BACKUP_XRAY_ENABLED", True)
    config = tmp_path / "config-backup.json"
    payload = {
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "vless",
                "settings": {
                    "vnext": [{"address": "relay.example.com", "port": 443, "users": [{"id": "dummy-user-credential"}]}]
                },
                "streamSettings": {"realitySettings": {"publicKey": "dummy-key-material"}},
            }
        ]
    }
    config.write_text(json.dumps(payload))
    assert apply.XrayApplyService().backup_config_mode() == "relay"
    result = apply.XrayApplyService().backup_relay_target()
    assert result == {"upstream_host": "relay.example.com", "upstream_port": 443}
    assert "dummy" not in json.dumps(result)
    # Do not accept an auxiliary relay as the default exit, or arbitrary host text.
    for invalid in (
        {},
        {"outbounds": [dict(payload["outbounds"][0], tag="auxiliary")]},
        {"outbounds": [{"tag": "direct", "protocol": "freedom"}]},
        {
            "outbounds": [
                {
                    "tag": "direct",
                    "protocol": "vless",
                    "settings": {"vnext": [{"address": "credential@example.com", "port": 443}]},
                }
            ]
        },
        {
            "outbounds": [
                {
                    "tag": "direct",
                    "protocol": "vless",
                    "settings": {"vnext": [{"address": "relay.example.com", "port": True}]},
                }
            ]
        },
    ):
        config.write_text(json.dumps(invalid))
        assert apply.XrayApplyService().backup_relay_target() is None
    config.write_text("invalid json")
    assert apply.XrayApplyService().backup_relay_target() is None
    config.unlink()
    assert apply.XrayApplyService().backup_relay_target() is None


def test_ai_status_preserves_report_timestamp_and_application_metadata(tmp_path, monkeypatch):
    from app.state import ai_routing

    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"generated_at": STAMP, "route_status": {"status": "applied", "config_apply_status": "delegated"}})
    )
    service = ai_routing.AiRoutingService(
        node_controller=SimpleNamespace(mode="local", config=SimpleNamespace(source_ai_report_path=str(path)))
    )
    monkeypatch.setattr(ai_routing, "AI_ROUTING_ENABLED", True)
    monkeypatch.setattr(service, "query_ai_domain_aggregate", lambda: {"total_ai_domains": 0})
    monkeypatch.setattr(service, "ai_routing_traffic_scope", lambda: "classified")
    monkeypatch.setattr(service, "ai_routing_port_policy", list)
    # These tests isolate report/application metadata from the classification store.
    monkeypatch.setattr(service, "query_classification_cache_summary", lambda: {
        "status": "available", "total_domains": 0, "ai_domains": 0, "non_ai_domains": 0,
    })
    monkeypatch.setattr(
        service,
        "ai_routing_manual_state",
        lambda: {
            "mode": "auto",
            "mode_label": "auto",
            "updated_at": "",
            "updated_at_display": "",
            "candidates": [],
            "candidate_count": 0,
        },
    )
    result = service.ai_routing_status()
    assert result["report_generated_at"] == STAMP
    assert result["config_apply_status"] == "delegated"
    path.write_text(json.dumps({"generated_at": "invalid", "route_status": {"status": "applied"}}))
    assert service.ai_routing_status()["report_generated_at"] is None
    assert service.ai_routing_status()["config_apply_status"] == "unknown"


def test_backup_rendered_mode_is_independent_of_desired_mode_and_missing_config(tmp_path, monkeypatch):
    from app.state.dns_failover import DnsFailoverService
    from app.xray import apply

    monkeypatch.setattr(apply, "XRAY_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(apply, "CONTROL_PLANE_BACKUP_XRAY_ENABLED", True)
    service = apply.XrayApplyService()
    monkeypatch.setattr(service, "backup_xray_mode", lambda: "relay")
    dns = DnsFailoverService(backup_xray=service)
    path = tmp_path / "config-backup.json"
    assert dns._backup_xray_mode() == "unknown"
    path.write_text(json.dumps({"outbounds": [{"protocol": "freedom", "tag": "direct"}]}))
    assert dns._backup_xray_mode() == "direct"
    assert service.backup_relay_target() is None
    monkeypatch.setattr(apply, "CONTROL_PLANE_BACKUP_XRAY_ENABLED", False)
    assert dns._backup_xray_mode() == "disabled"


def test_management_failure_only_suggests_pending_dns_switch():
    from app.web.core import build_traffic_routing

    dns = {"enabled": True, "configured": True, "current_target": "primary", "last_probe_status": "unknown"}
    result = build_traffic_routing({"reachable": False}, {"reachable": True}, {}, dns)
    assert result["path"] == "dns_backup_pending"
    assert result["ordinary_direct_state"] == result["ai_branch_state"] == "unknown"
    assert "未确认" in result["scenario"]
    assert "故障" not in result["scenario"]


@pytest.mark.parametrize("sync_error", ["report copy failed", "report unavailable"])
def test_report_sync_failure_prevents_applied_claim(sync_error):
    assert routing(sync_error=sync_error)["path"] == "normal_ai_pending"


def test_ai_status_does_not_reuse_applied_evidence_for_a_different_manual_or_disabled_status(tmp_path, monkeypatch):
    from app.state import ai_routing

    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"generated_at": STAMP, "route_status": {"status": "applied", "config_apply_status": "direct"}})
    )
    service = ai_routing.AiRoutingService(
        node_controller=SimpleNamespace(mode="local", config=SimpleNamespace(source_ai_report_path=str(path)))
    )
    monkeypatch.setattr(service, "query_ai_domain_aggregate", lambda: {"total_ai_domains": 0})
    monkeypatch.setattr(service, "ai_routing_traffic_scope", lambda: "classified")
    monkeypatch.setattr(service, "ai_routing_port_policy", list)
    # These tests isolate report/application metadata from the classification store.
    monkeypatch.setattr(service, "query_classification_cache_summary", lambda: {
        "status": "available", "total_domains": 0, "ai_domains": 0, "non_ai_domains": 0,
    })
    monkeypatch.setattr(
        service,
        "ai_routing_manual_state",
        lambda: {
            "mode": "forced_fallback",
            "mode_label": "fallback",
            "updated_at": "",
            "updated_at_display": "",
            "candidates": [],
            "candidate_count": 0,
        },
    )
    monkeypatch.setattr(ai_routing, "AI_ROUTING_ENABLED", True)
    status = service.ai_routing_status()
    assert status["status"] == "manual_fallback"
    assert status["config_apply_status"] == "unknown"
    assert status["report_generated_at"] == STAMP
    monkeypatch.setattr(ai_routing, "AI_ROUTING_ENABLED", False)
    status = service.ai_routing_status()
    assert status["status"] == "disabled"
    assert status["config_apply_status"] == "unknown"
    assert status["report_generated_at"] == STAMP


@pytest.mark.parametrize("metadata", [[], {}, True, 12])
def test_malformed_report_application_metadata_stays_unknown(tmp_path, metadata):
    from app.state.ai_routing import AiRoutingService

    path = tmp_path / "report.json"
    path.write_text(json.dumps({
        "generated_at": STAMP,
        "route_status": {"status": "applied", "config_apply_status": metadata},
    }))
    service = AiRoutingService(
        node_controller=SimpleNamespace(config=SimpleNamespace(source_ai_report_path=str(path)))
    )
    assert service.read_ai_domain_report()["config_apply_status"] == "unknown"
    assert routing(apply=metadata)["path"] == "normal_ai_pending"


@pytest.mark.parametrize("target", [[], {}, True, 12])
def test_malformed_dns_target_keeps_entry_unknown(target):
    result = routing(dns={"enabled": True, "configured": True, "current_target": target})
    assert result["path"] == "unknown"
    assert result["ordinary_direct_state"] == result["ai_branch_state"] == "unknown"


def test_all_traffic_snapshot_has_only_ai_active_with_applied_scope():
    result = routing(applied_traffic_scope="all", traffic_scope="all")
    assert result["path"] == "normal_ai"
    assert result["traffic_scope"] == "all_traffic"
    assert result["ordinary_direct_state"] == "standby"
    assert result["ai_branch_state"] == "active"
    pending = routing(apply="delegated", applied_traffic_scope="unknown", traffic_scope="all")
    assert pending["path"] == "normal_ai_pending"
    assert pending["ai_branch_state"] == "unknown"


def test_preserved_all_route_management_error_never_claims_direct_fallback():
    result = routing(status="probe_error", route_preserved=True, applied_traffic_scope="all")
    assert result["path"] == "normal_ai"
    assert result["ordinary_direct_state"] == "standby"


@pytest.mark.parametrize('applied,expected', [('mixed','all'), ('direct','direct'), ('unknown','unknown')])
def test_per_port_status_uses_account_specific_evidence_and_never_confirms_stale_policy(monkeypatch, applied, expected):
    from app.state.ai_routing import AiRoutingService
    service=AiRoutingService(node_controller=SimpleNamespace(is_configured=lambda:True,mode="local"))
    policy=[{'id':1,'listen_port':31001,'traffic_scope':'all','inherited':False,'active':True},
            {'id':2,'listen_port':31002,'traffic_scope':'classified','inherited':True,'active':True}]
    from app.xray.ai_routing.repository import scope_policy_signature
    monkeypatch.setattr(service,'ai_routing_port_policy',lambda:policy)
    monkeypatch.setattr(service,'ai_routing_traffic_scope',lambda:'classified')
    monkeypatch.setattr(service,'query_ai_domain_aggregate',lambda:{'total_ai_domains':0})
    monkeypatch.setattr(service,'query_classification_cache_summary',lambda:{'ai_domains':0})
    monkeypatch.setattr(service,'sync_data_plane_ai_state',dict)
    monkeypatch.setattr(service,'ai_routing_manual_state',lambda:{'mode':'auto','mode_label':'auto','updated_at':'','updated_at_display':'','candidates':[],'candidate_count':0})
    report={'generated_at':STAMP,'route_status':'applied','route_status_label':'applied','route_status_tone':'ok',
            'config_apply_status':'direct' if applied!='unknown' else 'delegated','traffic_scope':'classified','applied_traffic_scope':applied,
            'requested_port_scopes':scope_policy_signature(policy),'applied_port_scopes':scope_policy_signature(policy)}
    # Existing full report fixture supplies unrelated presentation fields.
    base={'generated_at_display':STAMP,'routing_checked_at':STAMP,'route_status_reason':'','ai_domain_count':0,'unique_domains':0,
          'window_start_display':'','window_end_display':'','total_events':0,'domain_hits':0,'domains':[],
          'pending_domains_without_classifier':0,'known_ai_domains':0,'config_changed':False,'config_retried':False}
    report={**base,**report}
    monkeypatch.setattr(service,'read_ai_domain_report',lambda:report)
    result=service.ai_routing_status()
    assert result['port_scopes'][0]['applied_traffic_scope']==expected
    if applied=='unknown': assert result['port_scopes'][0]['scope_apply_state']=='pending'
    policy[0]={**policy[0],'traffic_scope':'classified'}
    assert service.ai_routing_status()['port_scopes'][0]['scope_apply_state']=='pending'
