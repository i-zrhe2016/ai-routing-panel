"""Authenticated account identity and the versioned kernel limiter contract.

Marks use the high octet and low 16 bits; Tailscale's 0x00ff0000 is preserved.
No client IP, shared listener, password or usage counter is part of the policy.
"""

import copy
import hashlib
import json
from datetime import datetime, timezone

RATE_BITS_PER_SECOND = 5_000_000
MARK_PREFIX = 0x50000000
MARK_MASK = 0xFF00FFFF


def account_mark(port):
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Account port must be in 1..65535")
    return MARK_PREFIX | port


def limits_manifest(accounts):
    seen = set()
    normalized = []
    for account in accounts:
        port = int(account["port"])
        if port in seen:
            raise ValueError("Duplicate account port")
        seen.add(port)
        normalized.append({"port": port, "mark": account_mark(port), "throttled": bool(account["throttled"])})
    return {
        "version": 1,
        "rateBitsPerSecond": RATE_BITS_PER_SECOND,
        "accounts": sorted(normalized, key=lambda a: a["port"]),
    }


def validate_manifest(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported account limiter policy version")
    if payload.get("rateBitsPerSecond") != RATE_BITS_PER_SECOND:
        raise ValueError("Account limiter rate must be 5,000,000 bits/sec")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or any(
        not isinstance(a, dict)
        or type(a.get("port")) is not int
        or type(a.get("throttled")) is not bool
        or a.get("mark") != account_mark(a["port"])
        for a in accounts
    ):
        raise ValueError("Invalid account limiter identities")
    return limits_manifest(accounts)


def manifest_hash(manifest):
    normalized = validate_manifest(manifest)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mark_account_routes(config, ports):
    """Clone transport outbounds after route selection, preserving first-match order.

    Legacy identity is its private inbound; shared REALITY identity is the
    authenticated email. Keeping legacy clients without email preserves the
    existing inbound+unified-user accounting without double counting.
    """
    ports = sorted({int(p) for p in ports})
    for port in ports:
        account_mark(port)
    original = {o["tag"]: o for o in config.get("outbounds", []) if o.get("tag")}
    routable = {tag: o for tag, o in original.items() if o.get("protocol") not in {"blackhole", "dns"}}
    clones = {}
    for port in ports:
        for tag, outbound in routable.items():
            if outbound.get("proxySettings") or outbound.get("streamSettings", {}).get("sockopt", {}).get(
                "dialerProxy"
            ):
                raise ValueError("Account limiter does not support chained outbounds")
            clone = copy.deepcopy(outbound)
            clone["tag"] = f"account-{port}-{tag}"
            clone.setdefault("streamSettings", {}).setdefault("sockopt", {})["mark"] = account_mark(port)
            # Each account gets an independent dialer; never pool across accounts.
            if clone.get("mux", {}).get("enabled"):
                clone["mux"]["enabled"] = False
            clones[(port, tag)] = clone
    rules = []
    for rule in config.get("routing", {}).get("rules", []):
        tag = rule.get("outboundTag")
        if tag not in routable:
            if rule.get("balancerTag"):
                raise ValueError("Account limiter does not support outbound balancers")
            rules.append(rule)
            continue
        for port in ports:
            for identity in [{"inboundTag": [f"panel-{port}"]}, {"user": [f"panel-user-{port}"]}]:
                clone = copy.deepcopy(rule)
                compatible = True
                for key, requested in identity.items():
                    existing = clone.get(key)
                    if existing is not None and requested[0] not in existing:
                        compatible = False
                    clone[key] = requested
                if compatible:
                    clone["outboundTag"] = clones[(port, tag)]["tag"]
                    rules.append(clone)
        rules.append(rule)
    default_tag = config["outbounds"][0]["tag"]
    if default_tag not in routable:
        raise ValueError("Account limiter requires a transport default outbound")
    for port in ports:
        for identity in [{"inboundTag": [f"panel-{port}"]}, {"user": [f"panel-user-{port}"]}]:
            rules.append({"type": "field", **identity, "outboundTag": clones[(port, default_tag)]["tag"]})
    config["outbounds"].extend(clones.values())
    config.setdefault("routing", {})["rules"] = rules
    return config


def receipt_state(port, manifest, receipt, *, now=None, max_age_seconds=60):
    """A configured queue is distinct from observed authenticated account traffic."""
    pending = {"state": "pending", "label": "超额 5 Mbps，待配置", "kernelApplied": False, "markVerified": False}
    if not isinstance(receipt, dict) or receipt.get("desiredHash") != manifest_hash(manifest):
        return pending
    if receipt.get("error"):
        return {**pending, "state": "error", "label": "超额限速应用失败", "error": receipt["error"]}
    try:
        checked = datetime.fromisoformat(receipt["checkedAt"])
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
        if not 0 <= age <= max_age_seconds or not receipt.get("bootId"):
            return pending
    except (KeyError, ValueError, TypeError):
        return pending
    if not receipt.get("kernelApplied"):
        return pending
    verified = str(port) in receipt.get("observedAccounts", [])
    return {
        "state": "applied" if verified else "pending",
        "label": "已超额，限速 5 Mbps" if verified else "超额 5 Mbps，已配置，待连接验证",
        "kernelApplied": True,
        "markVerified": verified,
    }
