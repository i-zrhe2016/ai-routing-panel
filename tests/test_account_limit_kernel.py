"""Kernel ownership and honest checks; actual transport evidence is isolated on DO."""

import json

import pytest

from app.xray.account_limits import account_mark, limits_manifest
from scripts.apply_account_limits import OWNER, QUEUE_PREF, Limiter


def test_cannot_remove_foreign_state_or_interface_roots(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"owner":"other"}')
    calls = []
    engine = Limiter(["eth0"], state, tmp_path / "config.json", "xray", run=lambda args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="another owner"):
        engine.remove()
    assert calls == []
    state.unlink()
    with pytest.raises(RuntimeError, match="No owned state"):
        engine.remove()
    assert calls == []


def test_kernel_check_requires_both_directions_before_observed_and_missing_rules_fail(tmp_path):
    policy = limits_manifest([{"port": 31000, "throttled": True}])
    mark = account_mark(31000)
    upbytes = 10
    downbytes = 0

    def run(args, **kwargs):
        if args[:4] == ["nft", "-j", "list", "table"]:
            return json.dumps({"nftables": [{"table": {"comment": OWNER}}]})
        if args[:3] == ["nft", "list", "table"]:
            return "meta mark & 0xff000000 == 0x50000000 ct mark set ct mark & 0x00ff0000 | meta mark & 0xff00ffff"
        if args[:4] == ["ip", "-j", "link", "show"]:
            return json.dumps([{"ifalias": OWNER}])
        if args[:4] == ["tc", "-j", "-s", "class"]:
            return json.dumps(
                [
                    {
                        "class": "htb",
                        "handle": "1:7918",
                        "rate": 625000,
                        "ceil": 625000,
                        "stats": {"bytes": upbytes if args[-1] == "xral-up" else downbytes},
                    }
                ]
            )
        if args[-1] == "egress":
            return f"handle {mark:#x}/0xff00ffff mirred Egress Redirect to device xral-up"
        if args[-1] == "ingress":
            return f"ct zone 0 pipe goto chain 49150 ct_mark {mark}/0xff00ffff Egress Redirect to device xral-down"
        raise AssertionError(args)

    engine = Limiter(["eth0", "tailscale0"], tmp_path / "state.json", tmp_path / "config.json", "xray", run=run)
    engine._save({"owner":OWNER})
    assert engine.inspect(policy, {})["observedAccounts"] == []
    downbytes = 11
    upbytes = 20
    assert engine.inspect(policy, {})["observedAccounts"] == ["31000"]
    assert engine.inspect(policy, {"pid":2})["observedAccounts"] == []
    engine.run = lambda args, **kwargs: "" if args[:3] == ["nft", "-j", "list"] else run(args, **kwargs)
    with pytest.raises(RuntimeError, match="missing"):
        engine.inspect(policy, {})


def test_malformed_interface_and_wrong_daemon_config_fail_before_mutation(tmp_path):
    with pytest.raises(ValueError):
        Limiter(["eth0;reboot"], tmp_path / "s", tmp_path / "c", "xray")
    config = tmp_path / "c"
    config.write_text(json.dumps({"outbounds": [{"tag": "direct", "protocol": "freedom"}]}))
    calls = []
    engine = Limiter(["eth0"], tmp_path / "s", config, "xray", run=lambda args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="not configured"):
        engine.apply(limits_manifest([{"port": 31000, "throttled": True}]))
    assert calls == []


@pytest.mark.parametrize("reset", [False, True])
def test_account_queue_classifier_has_explicit_priority_and_reset_errors_propagate(tmp_path, reset):
    calls = []
    fail_delete = False

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ["tc", "class", "del"] and fail_delete:
            raise RuntimeError("HTB class in use")
        return ""

    engine = Limiter(["eth0"], tmp_path / "state", tmp_path / "config", "xray", run=run)
    engine._preflight = lambda state: None
    engine._owned_link = lambda dev: True
    engine._owned_table = lambda: False
    engine._json = lambda args: [{"kind": "clsact"}, {"kind": "htb", "handle": "1:"}]
    policy = limits_manifest([{"port": 31000, "throttled": not reset}])
    state = {"owner": OWNER, "createdClsact": [], "policy": limits_manifest([{"port": 31000, "throttled": reset}])}
    engine._reconcile(policy, state)
    operation = "del" if reset else "replace"
    queues = [(args, kwargs) for args, kwargs in calls if args[:3] == ["tc", "filter", operation] and "1:" in args]
    assert len(queues) == 2
    assert all(args[args.index("pref") + 1] == QUEUE_PREF for args, _ in queues)
    assert all(not kwargs.get("allow_failure") for _, kwargs in queues)
    if reset:
        fail_delete = True
        state["policy"] = limits_manifest([{"port": 31000, "throttled": True}])
        with pytest.raises(RuntimeError, match="HTB class in use"):
            engine._reconcile(policy, state)

@pytest.mark.parametrize('changed', ['mounted_hash', 'newer_config', 'unchanged'])
def test_daemon_binding_requires_running_mounted_and_loaded_config(tmp_path, monkeypatch, changed):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    from scripts import apply_account_limits as kernel

    policy = limits_manifest([{'port': 31000, 'throttled': True}])
    config = tmp_path / 'config.json'
    content = json.dumps({'outbounds': [{'tag': 'account-31000-direct', 'streamSettings': {'sockopt': {'mark': account_mark(31000)}}}]})
    config.write_text(content)
    mounted = Path('/proc/999999/root/etc/xray/config.json')
    original_read = Path.read_text
    original_bytes = Path.read_bytes
    original_stat = Path.stat
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: 'CapEff:\t0000000000002000\n' if str(p) == '/proc/999999/status' else original_read(p, *a, **kw))
    monkeypatch.setattr(Path, 'read_bytes', lambda p: (b'old' if changed == 'mounted_hash' else content.encode()) if p == mounted else original_bytes(p))
    monkeypatch.setattr(Path, 'stat', lambda p, *a, **kw: SimpleNamespace(st_mtime=200 if changed == 'newer_config' else 50) if p == mounted else original_stat(p, *a, **kw))
    monkeypatch.setattr(kernel.os, 'readlink', lambda p: '/usr/local/bin/xray')
    monkeypatch.setattr(kernel.platform, 'release', lambda: '7.0.0')
    data = {'State': {'Pid': 999999, 'Running': True, 'StartedAt': '1970-01-01T00:01:40Z'}, 'HostConfig': {'NetworkMode': 'host'}, 'Config': {'Cmd': ['run', '-c', '/etc/xray/config.json']}, 'Mounts': [{'Source': str(config), 'Destination': '/etc/xray/config.json'}]}
    engine = Limiter(['eth0'], tmp_path / 'state', config, 'xray')
    engine._json = lambda args: [data]
    if changed == 'unchanged':
        assert engine.daemon_binding(policy)['configHash'] == hashlib.sha256(content.encode()).hexdigest()
    else:
        with pytest.raises(RuntimeError, match='previous config|reload is required'):
            engine.daemon_binding(policy)
