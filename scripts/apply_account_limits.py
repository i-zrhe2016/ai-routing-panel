#!/usr/bin/env python3
"""Reconcile owned aggregate account queues; never shape listeners/client IPs.

Install with account_limits.py from app/xray beside this script. Commands use
argument lists. All interface roots, existing nft tables and foreign tc state
are preserved. Applying requires root and a verified, bound Xray daemon.
"""
import argparse
import fcntl
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from account_limits import MARK_MASK, account_mark, manifest_hash, validate_manifest
except ModuleNotFoundError:
    from app.xray.account_limits import MARK_MASK, account_mark, manifest_hash, validate_manifest

OWNER = "xray-account-limits:v1"
TABLE = "xray_account_limits"
UP = "xral-up"
DOWN = "xral-down"
PREF = "49150"
PREF_IPV6 = "49151"
QUEUE_PREF = "49152"
CHAIN = "49150"
RATE = "5000000bit"


class Limiter:
    def __init__(self, interfaces, state_path, config_path, container, run=None):
        if (
            not interfaces
            or len(set(interfaces)) != len(interfaces)
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", i) or i in {UP, DOWN, "lo"} for i in interfaces)
        ):
            raise ValueError("Explicit safe data-plane interfaces are required")
        self.interfaces = interfaces
        self.state_path = Path(state_path)
        self.config_path = Path(config_path)
        self.container = container
        self.run = run or self._run
        self.created_clsact = []

    @staticmethod
    def _run(args, input_text=None, allow_failure=False):
        r = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=15, check=False)
        if r.returncode and not allow_failure:
            # Do not emit source/config/credential contents.
            raise RuntimeError(f"{args[0]} command failed ({r.returncode}): {r.stderr.strip()[:240]}")
        return r.stdout if not r.returncode else ""

    def _json(self, args):
        return json.loads(self.run(args))

    def _state(self):
        if not self.state_path.exists():
            return {}
        state = json.loads(self.state_path.read_text())
        if state.get("owner") != OWNER:
            raise RuntimeError("Limiter state file belongs to another owner")
        return state

    def _save(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        candidate = self.state_path.with_suffix(".candidate")
        candidate.write_text(json.dumps(state, sort_keys=True) + "\n")
        candidate.chmod(0o600)
        candidate.replace(self.state_path)

    def daemon_binding(self, manifest):
        """Inspect actual daemon capabilities, executable and mounted config binding."""
        config = json.loads(self.config_path.read_text())
        for account in manifest["accounts"]:
            port, mark = account["port"], account["mark"]
            outbounds = [o for o in config.get("outbounds", []) if o.get("tag", "").startswith(f"account-{port}-")]
            if not outbounds or any(
                o.get("streamSettings", {}).get("sockopt", {}).get("mark") != mark for o in outbounds
            ):
                raise RuntimeError("Xray authenticated account marks are not configured")
        if not self.container:
            raise RuntimeError("A bound Xray container is required for privilege verification")
        data = self._json(["docker", "inspect", self.container])[0]
        pid = int(data.get("State", {}).get("Pid", 0))
        if pid <= 0 or not data["State"].get("Running") or data.get("HostConfig", {}).get("NetworkMode") != "host":
            raise RuntimeError("Xray must be running in the host network namespace")
        command = data.get("Config", {}).get("Cmd", [])
        paths = [command[n + 1] for n, arg in enumerate(command[:-1]) if arg in {"-c", "-config"}]
        if len(paths) != 1:
            raise RuntimeError("Cannot identify Xray daemon config argument")
        bound = False
        for mount in data.get("Mounts", []):
            source, dest = Path(mount["Source"]), Path(mount["Destination"])
            if paths[0] == str(dest) and source.resolve() == self.config_path.resolve():
                bound = True
            elif str(paths[0]).startswith(str(dest) + "/"):
                bound |= (source / Path(paths[0]).relative_to(dest)).resolve() == self.config_path.resolve()
        if not bound:
            raise RuntimeError("Limiter config is not the Xray daemon mounted config")
        status = Path(f"/proc/{pid}/status").read_text()
        caps = int(re.search(r"^CapEff:\s*([0-9a-f]+)", status, re.MULTILINE)[1], 16)
        kernel = tuple(int(n) for n in platform.release().split("-")[0].split(".")[:2])
        if not (caps & (1 << 12) or kernel >= (5, 17) and caps & (1 << 13)):
            raise RuntimeError("Actual Xray daemon lacks SO_MARK capability")
        executable = os.readlink(f"/proc/{pid}/exe")
        if Path(executable).name != "xray":
            raise RuntimeError("Bound process is not the Xray executable")
        config_hash = __import__("hashlib").sha256(self.config_path.read_bytes()).hexdigest()
        mounted_config = Path(f"/proc/{pid}/root") / paths[0].lstrip("/")
        if __import__("hashlib").sha256(mounted_config.read_bytes()).hexdigest() != config_hash:
            raise RuntimeError("Xray running container still mounts a previous config")
        started = datetime.fromisoformat(data["State"]["StartedAt"].replace("Z", "+00:00")).timestamp()
        binding = {"pid": pid, "startedAt": data["State"]["StartedAt"], "configHash": config_hash}
        known = self._state().get("loadedDaemon")
        if known != binding and mounted_config.stat().st_mtime > started:
            raise RuntimeError("Xray config was changed after daemon start; reload is required")
        return binding

    def _owned_link(self, name):
        raw = self.run(["ip", "-j", "link", "show", "dev", name], allow_failure=True)
        if not raw:
            return False
        link = json.loads(raw)[0]
        if link.get("ifalias") != OWNER:
            raise RuntimeError("Limiter IFB name belongs to another owner")
        return True

    def _owned_table(self):
        raw = self.run(["nft", "-j", "list", "table", "inet", TABLE], allow_failure=True)
        if not raw:
            return False
        entries = json.loads(raw).get("nftables", [])
        if not any(e.get("table", {}).get("comment") == OWNER for e in entries):
            raise RuntimeError("Limiter nft table belongs to another owner")
        return True

    def _preflight(self, state):
        for dev in self.interfaces:
            self._json(["ip", "-j", "link", "show", "dev", dev])
            qdiscs = self._json(["tc", "-j", "qdisc", "show", "dev", dev])
            if any(q["kind"] == "ingress" for q in qdiscs):
                raise RuntimeError("Existing ingress qdisc requires separate integration")
            for direction in ["ingress", "egress"]:
                filters = self.run(["tc", "filter", "show", "dev", dev, direction])
                if any(f"pref {pref} " in filters for pref in [PREF, PREF_IPV6]) and not state:
                    raise RuntimeError("Reserved tc filter priority is already in use")
                if f"chain {CHAIN} " in filters and not state:
                    raise RuntimeError("Reserved tc chain is already in use")
        self._owned_table()
        for dev in [UP, DOWN]:
            self._owned_link(dev)

    def _filters_remove(self):
        for dev in self.interfaces:
            for direction in ["ingress", "egress"]:
                for pref in [PREF, PREF_IPV6]:
                    self.run(["tc", "filter", "del", "dev", dev, direction, "pref", pref], allow_failure=True)
            self.run(["tc", "filter", "del", "dev", dev, "ingress", "chain", CHAIN], allow_failure=True)

    def _reconcile(self, manifest, state):
        self._preflight(state)
        if not state:
            state = {"owner": OWNER, "createdClsact": [], "policy": None}
        for dev in self.interfaces:
            qdiscs = self._json(["tc", "-j", "qdisc", "show", "dev", dev])
            if not any(q["kind"] == "clsact" for q in qdiscs):
                self.run(["tc", "qdisc", "add", "dev", dev, "clsact"])
                state["createdClsact"].append(dev)
                self._save(state)
        self._save(state)
        for dev in [UP, DOWN]:
            if not self._owned_link(dev):
                self.run(["ip", "link", "add", dev, "type", "ifb"])
                self.run(["ip", "link", "set", dev, "alias", OWNER])
            self.run(["ip", "link", "set", dev, "up"])
            qdiscs = self._json(["tc", "-j", "qdisc", "show", "dev", dev])
            if not any(q["kind"] == "htb" and q.get("handle") == "1:" for q in qdiscs):
                self.run(["tc", "qdisc", "replace", "dev", dev, "root", "handle", "1:", "htb", "default", "1"])
        self._filters_remove()
        if self._owned_table():
            prefix = f"delete table inet {TABLE}\n"
        else:
            prefix = ""
        rules = (
            prefix
            + f"""table inet {TABLE} {{
 comment "{OWNER}"
 chain save {{
  type filter hook output priority -149; policy accept;
  meta mark & 0xff000000 == 0x50000000 ct mark set (ct mark & 0x00ff0000) | (meta mark & 0xff00ffff);
 }}
}}
"""
        )
        self.run(["nft", "-f", "-"], input_text=rules)
        limited = [a for a in manifest["accounts"] if a["throttled"]]
        previous = {a["port"] for a in (state.get("policy") or {}).get("accounts", []) if a["throttled"]}
        for dev in [UP, DOWN]:
            for port in previous - {a["port"] for a in limited}:
                self.run(
                    [
                        "tc",
                        "filter",
                        "del",
                        "dev",
                        dev,
                        "parent",
                        "1:",
                        "pref",
                        QUEUE_PREF,
                        "protocol",
                        "all",
                        "handle",
                        f"{account_mark(port):#x}/{MARK_MASK:#x}",
                        "fw",
                    ],
                )
                self.run(["tc", "class", "del", "dev", dev, "classid", f"1:{port:x}"])
            for account in limited:
                port = account["port"]
                cid = f"1:{port:x}"
                handle = f'{account["mark"]:#x}/{MARK_MASK:#x}'
                self.run(
                    [
                        "tc",
                        "class",
                        "replace",
                        "dev",
                        dev,
                        "parent",
                        "1:",
                        "classid",
                        cid,
                        "htb",
                        "rate",
                        RATE,
                        "ceil",
                        RATE,
                        "burst",
                        "16k",
                        "cburst",
                        "16k",
                        "quantum",
                        "1514",
                    ]
                )
                qdiscs = self._json(["tc", "-j", "qdisc", "show", "dev", dev])
                if not any(q.get("parent") == cid for q in qdiscs):
                    self.run(
                        [
                            "tc",
                            "qdisc",
                            "add",
                            "dev",
                            dev,
                            "parent",
                            cid,
                            "handle",
                            f"{port:x}:",
                            "fq",
                            "limit",
                            "256",
                            "flow_limit",
                            "32",
                        ]
                    )
                self.run(
                    [
                        "tc",
                        "filter",
                        "replace",
                        "dev",
                        dev,
                        "parent",
                        "1:",
                        "pref",
                        QUEUE_PREF,
                        "protocol",
                        "all",
                        "handle",
                        handle,
                        "fw",
                        "classid",
                        cid,
                    ]
                )
        for dev in self.interfaces:
            if limited:
                for proto in ["ip", "ipv6"]:
                    self.run(
                        [
                            "tc",
                            "filter",
                            "add",
                            "dev",
                            dev,
                            "ingress",
                            "protocol",
                            proto,
                            "pref",
                            PREF if proto == "ip" else PREF_IPV6,
                            "flower",
                            "action",
                            "ct",
                            "zone",
                            "0",
                            "pipe",
                            "action",
                            "goto",
                            "chain",
                            CHAIN,
                        ]
                    )
            for account in limited:
                handle = f'{account["mark"]:#x}/{MARK_MASK:#x}'
                self.run(
                    [
                        "tc",
                        "filter",
                        "add",
                        "dev",
                        dev,
                        "egress",
                        "protocol",
                        "all",
                        "pref",
                        PREF,
                        "handle",
                        handle,
                        "fw",
                        "action",
                        "mirred",
                        "egress",
                        "redirect",
                        "dev",
                        UP,
                    ]
                )
                for proto in ["ip", "ipv6"]:
                    self.run(
                        [
                            "tc",
                            "filter",
                            "add",
                            "dev",
                            dev,
                            "ingress",
                            "chain",
                            CHAIN,
                            "protocol",
                            proto,
                            "pref",
                            PREF if proto == "ip" else PREF_IPV6,
                            "flower",
                            "ct_state",
                            "+trk",
                            "ct_mark",
                            handle,
                            "action",
                            "skbedit",
                            "mark",
                            handle,
                            "pipe",
                            "action",
                            "mirred",
                            "egress",
                            "redirect",
                            "dev",
                            DOWN,
                        ]
                    )
        state.update(owner=OWNER, policy=manifest)
        self._save(state)

    def inspect(self, manifest, daemon):
        observed = []
        limited = [a for a in manifest["accounts"] if a["throttled"]]
        if not self._owned_table():
            raise RuntimeError("Account conntrack rules are missing")
        table = self.run(["nft", "list", "table", "inet", TABLE])
        expected = "meta mark & 0xff000000 == 0x50000000 ct mark set ct mark & 0x00ff0000 | meta mark & 0xff00ffff"
        if expected not in table:
            raise RuntimeError("Account conntrack mark operation verification failed")
        stats = {}
        for dev in [UP, DOWN]:
            if not self._owned_link(dev):
                raise RuntimeError("Account queue is missing")
            rows = self._json(["tc", "-j", "-s", "class", "show", "dev", dev])
            by_id = {r["handle"]: r for r in rows}
            for a in limited:
                row = by_id.get(f'1:{a["port"]:x}', {})
                options = row.get("options", row)
                if (
                    row.get("class", row.get("kind")) != "htb"
                    or options.get("rate") != 625000
                    or options.get("ceil") != 625000
                ):
                    raise RuntimeError("Account rate/ceiling verification failed")
                stats[(dev, a["port"])] = int(row.get("stats", row).get("bytes", 0))
        for dev in self.interfaces:
            egress = self.run(["tc", "filter", "show", "dev", dev, "egress"])
            ingress = self.run(["tc", "filter", "show", "dev", dev, "ingress"])
            if limited and ("ct zone 0 pipe" not in ingress or f"goto chain {CHAIN}" not in ingress):
                raise RuntimeError("Account return conntrack lookup verification failed")
            for a in limited:
                if (
                    f'handle {a["mark"]:#x}/0xff00ffff' not in egress
                    or f'ct_mark {a["mark"]}/0xff00ffff' not in ingress
                ):
                    raise RuntimeError("Account interface filter verification failed")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        # Counters from an old daemon/config/boot are not fresh mark evidence.
        state = self._state()
        proof_key = json.dumps([boot, daemon, manifest_hash(manifest)], sort_keys=True)
        proof = state.get("observation", {})
        if proof.get("key") != proof_key:
            proof = {
                "key": proof_key,
                "baseline": {str(a["port"]): [stats[(UP, a["port"])], stats[(DOWN, a["port"])]] for a in limited},
                "verified": [],
            }
        for a in limited:
            port = str(a["port"])
            baseline = proof["baseline"].setdefault(port, [0, 0])
            current = [stats[(UP, a["port"])], stats[(DOWN, a["port"])]]
            if any(n < old for n, old in zip(current, baseline)):
                proof["baseline"][port] = current
                proof["verified"] = [p for p in proof["verified"] if p != port]
            elif all(n > old for n, old in zip(current, baseline)) and port not in proof["verified"]:
                proof["verified"].append(port)
            if port in proof["verified"]:
                observed.append(port)
        state["observation"] = proof
        if state.get("owner") == OWNER:
            self._save(state)
        return {
            "desiredHash": manifest_hash(manifest),
            "bootId": boot,
            "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kernelApplied": True,
            "observedAccounts": observed,
            "daemon": daemon,
        }

    def apply(self, manifest):
        manifest = validate_manifest(manifest)
        daemon = self.daemon_binding(manifest)
        state = self._state()
        previous = state.get("policy")
        if previous == manifest:
            try:
                return self.inspect(manifest, daemon)
            except RuntimeError:
                pass  # A reboot/rule loss needs reconciliation, not an applied claim.
        try:
            self._reconcile(manifest, state)
            state = self._state()
            state["loadedDaemon"] = daemon
            self._save(state)
            return self.inspect(manifest, daemon)
        except Exception:
            if previous is not None:
                try:
                    self._reconcile(previous, self._state())
                except Exception as rollback_error:  # noqa: BLE001 - retain the original apply failure
                    print("Account queue rollback failed: " + str(rollback_error)[:160], file=sys.stderr)
            else:
                try:
                    self.remove()
                except Exception as rollback_error:  # noqa: BLE001 - retain the original apply failure
                    print("Account queue cleanup failed: " + str(rollback_error)[:160], file=sys.stderr)
            raise

    def remove(self):
        state = self._state()
        if not state:
            raise RuntimeError("No owned state; refusing to remove arbitrary tc resources")
        self._preflight(state)
        self._filters_remove()
        if self._owned_table():
            self.run(["nft", "delete", "table", "inet", TABLE])
        for dev in [UP, DOWN]:
            if self._owned_link(dev):
                self.run(["ip", "link", "delete", dev])
        for dev in state.get("createdClsact", []):
            if not self.run(["tc", "filter", "show", "dev", dev, "ingress"]) and not self.run(
                ["tc", "filter", "show", "dev", dev, "egress"]
            ):
                self.run(["tc", "qdisc", "del", "dev", dev, "clsact"], allow_failure=True)
        self.state_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for name in ["apply", "check", "remove"]:
        group.add_argument("--" + name, action="store_true")
    parser.add_argument("--policy", default="-", help="panel-ports.json path or stdin")
    parser.add_argument("--interfaces", required=True, nargs="+")
    parser.add_argument("--state", default="/var/lib/xray-account-limits/state.json")
    parser.add_argument("--config", required=True)
    parser.add_argument("--xray-container", default="xray-reality-local")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Account limiter requires root")
    engine = Limiter(args.interfaces, args.state, args.config, args.xray_container)
    engine.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with engine.state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.remove:
            engine.remove()
            print(json.dumps({"removed": True}))
            return
        raw = sys.stdin.read() if args.policy == "-" else Path(args.policy).read_text()
        policy = json.loads(raw)
        manifest = validate_manifest(policy.get("accountLimits", policy))
        result = engine.apply(manifest) if args.apply else engine.inspect(manifest, engine.daemon_binding(manifest))
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - root CLI must report bounded errors
        print(str(error)[:300], file=sys.stderr)
        sys.exit(1)
