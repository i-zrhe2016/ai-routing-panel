"""Limit identity, route selection, isolation and honest receipt contracts."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.xray.account_limits import (
    account_mark,
    limits_manifest,
    manifest_hash,
    mark_account_routes,
    receipt_state,
    validate_manifest,
)
from app.xray.limit_client import reconcile_limits
from app.xray.render_config import build_server_config
from tests.test_unified_entry import values


def chosen(config, *, port, unified, ai=False, udp=False, other=False):
    for rule in config["routing"]["rules"]:
        if "network" in rule and rule["network"] != ("udp" if udp else "tcp"):
            continue
        if "port" in rule and rule["port"] != 443:
            continue
        if "domain" in rule and not ai:
            continue
        if "user" in rule and (not unified or f"panel-user-{port}" not in rule["user"]):
            continue
        inbound = "unified-443" if unified else f"panel-{port}"
        if other:
            inbound = "api"
        if "inboundTag" in rule and inbound not in rule["inboundTag"]:
            continue
        return rule.get("outboundTag")
    return config["outbounds"][0]["tag"]


@pytest.mark.parametrize("scope", ["classified", "all"])
def test_account_routes_follow_existing_ai_scope_and_static_blocks(scope):
    env = values()
    env["XRAY_ACCOUNT_LIMITS_ENABLED"] = "1"
    ai_rule = {"type": "field", "outboundTag": "ai_proxy"}
    if scope == "classified":
        ai_rule["domain"] = ["domain:ai.example"]
    config = build_server_config(
        env,
        {
            "outbounds": [
                {
                    "tag": "ai_proxy",
                    "protocol": "vless",
                    "settings": {"vnext": []},
                    "mux": {"enabled": True},
                    "streamSettings": {"sockopt": {"domainStrategy": "UseIPv4"}},
                }
            ],
            "routing": {"rules": [ai_rule]},
        },
        [31000, 31001],
    )
    for port in [31000, 31001]:
        for unified in [True, False]:
            assert chosen(config, port=port, unified=unified, ai=True) == f"account-{port}-ai_proxy"
            assert chosen(config, port=port, unified=unified) == f"account-{port}-" + (
                "ai_proxy" if scope == "all" else "direct"
            )
            assert chosen(config, port=port, unified=unified, ai=True, udp=True) == "block"
        clones = [o for o in config["outbounds"] if o["tag"].startswith(f"account-{port}-")]
        assert len(clones) == 2
        assert {o["streamSettings"]["sockopt"]["mark"] for o in clones} == {account_mark(port)}
        assert not next(o for o in clones if o["protocol"] == "vless")["mux"]["enabled"]
    # The original direct, AI and blackhole paths remain unmarked.
    assert all(not o.get("streamSettings", {}).get("sockopt", {}).get("mark") for o in config["outbounds"][:3])
    assert chosen(config, port=999, unified=False, other=True, ai=False) == ("ai_proxy" if scope == "all" else "direct")
    assert account_mark(31000) & 0x00FF0000 == 0
    assert config["inbounds"][0]["settings"]["clients"][0].get("email") is None
    assert len(config["inbounds"][-1]["settings"]["clients"]) == 2


def test_route_wrapper_does_not_mutate_dynamic_payload_and_rejects_unsupported_hops():
    config = {"outbounds": [{"tag": "direct", "protocol": "freedom"}], "routing": {"rules": []}}
    snapshot = copy.deepcopy(config)
    marked = mark_account_routes(copy.deepcopy(config), [1, 65535])
    assert len(marked["outbounds"]) == 3
    assert config == snapshot
    assert account_mark(1) != account_mark(65535)
    config["outbounds"][0]["proxySettings"] = {"tag": "other"}
    with pytest.raises(ValueError, match="chained"):
        mark_account_routes(config, [31000])
    with pytest.raises(ValueError):
        account_mark(0)


def test_receipts_require_policy_freshness_boot_and_observed_account():
    policy = limits_manifest([{"port": 31000, "throttled": True}])
    now = datetime.now(timezone.utc)
    receipt = {
        "desiredHash": manifest_hash(policy),
        "bootId": "test-boot",
        "checkedAt": now.isoformat(),
        "kernelApplied": True,
        "observedAccounts": [],
    }
    assert receipt_state(31000, policy, receipt, now=now)["state"] == "pending"
    receipt["observedAccounts"] = ["31000"]
    assert receipt_state(31000, policy, receipt, now=now)["state"] == "applied"
    for bad in [
        {**receipt, "desiredHash": "old"},
        {**receipt, "bootId": ""},
        {**receipt, "checkedAt": (now - timedelta(seconds=61)).isoformat()},
    ]:
        assert receipt_state(31000, policy, bad, now=now)["state"] == "pending"
    receipt["error"] = "missing capability"
    assert receipt_state(31000, policy, receipt, now=now)["state"] == "error"
    with pytest.raises(ValueError):
        validate_manifest({**policy, "rateBitsPerSecond": 5_000_001})
    with pytest.raises(ValueError):
        limits_manifest([{"port": 1, "throttled": True}, {"port": 1, "throttled": False}])


def test_transport_command_and_failure_never_fabricate_applied(monkeypatch):
    policy = limits_manifest([{"port": 31000, "throttled": True}])
    node = SimpleNamespace(is_remote=True, is_configured=lambda: True)
    monkeypatch.delenv("DATAPLANE_ACCOUNT_LIMITS_COMMAND", raising=False)
    with pytest.raises(RuntimeError, match="未配置"):
        reconcile_limits(node, policy)
    monkeypatch.setenv(
        "DATAPLANE_ACCOUNT_LIMITS_COMMAND",
        "python3 /owned/apply.py --apply --interfaces eth0 tailscale0 --config /owned/config.json",
    )
    calls = []
    node.run_remote = lambda command, *args, **kwargs: (
        calls.append((command, kwargs)) or SimpleNamespace(stdout='{"kernelApplied":true}')
    )
    with pytest.raises(RuntimeError, match="不一致"):
        reconcile_limits(node, policy)
    assert calls[0][0][0] == "python3"
    assert "rateBitsPerSecond" in calls[0][1]["input_text"]


def test_port_scope_stale_inventory_rebinds_identity_and_marked_routes_preserve_isolation():
    from app.xray.render_config import bind_port_scope_accounts
    payload={'traffic_scope':'mixed','all_ports':[31001], 'port_scopes':[{'id':1,'listen_port':31001,'traffic_scope':'all','active':True}],
             'routing':{'rules':[{'type':'field','network':'tcp,udp','inboundTag':['panel-31001'],'outboundTag':'ai_proxy'},
                                {'type':'field','domain':['domain:ai.example'],'outboundTag':'ai_proxy'}]},
             'outbounds':[{'tag':'ai_proxy','protocol':'vless'}]}
    rebound=bind_port_scope_accounts(payload,[{'id':1,'listen_port':31003},{'id':3,'listen_port':31001}])
    assert rebound['all_ports']==[31003]
    assert rebound['routing']['rules'][:2]==[
        {'type':'field','outboundTag':'ai_proxy','network':'tcp,udp','inboundTag':['panel-31003']},
        {'type':'field','outboundTag':'ai_proxy','network':'tcp,udp','user':['panel-user-31003']}]
    assert payload['all_ports']==[31001]
    deleted=bind_port_scope_accounts(payload,[{'id':3,'listen_port':31001}])
    assert deleted['routing']['rules']==[{'type':'field','domain':['domain:ai.example'],'outboundTag':'ai_proxy'}]
    config={'outbounds':[{'tag':'direct','protocol':'freedom'}, {'tag':'ai_proxy','protocol':'vless'}],
            'routing':rebound['routing']}
    mark_account_routes(config,[31003,31001])
    catch=[r for r in config['routing']['rules'] if r.get('network')=='tcp,udp']
    assert not any(r.get('inboundTag') == ['panel-31001'] and not r.get('user') for r in catch)
    assert not any(r.get('user') == ['panel-user-31001'] and not r.get('inboundTag') for r in catch)
    assert any(r.get('outboundTag')=='account-31003-ai_proxy' and r.get('user')==['panel-user-31003'] for r in catch)
