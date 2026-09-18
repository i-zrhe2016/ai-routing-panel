#!/usr/bin/env python3
"""Expose a narrowly scoped Tailscale SSH read broker to the backup container.

The broker is the only Compose service that sees the host Tailscale LocalAPI
socket.  Clients can request a configured recovery role only; the broker
chooses the target and paths and always calls the fixed read-only collector.
It never accepts an arbitrary remote command or target from the client.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import stat
import threading
from pathlib import Path

from collect_remote_backup import (
    DEFAULT_TAILSCALE_BROKER_SOCKET,
    build_nodes,
    parse_non_negative_int,
    read_remote,
)


MAX_REQUEST_BYTES = 4096


def configured_nodes() -> dict:
    nodes = {}
    for node in build_nodes():
        if node.role in nodes:
            raise RuntimeError(f"duplicate broker recovery role: {node.role}")
        if node.target:
            nodes[node.role] = node
    return nodes


class BrokerRequestHandler(socketserver.StreamRequestHandler):
    def _reply(self, payload: dict) -> None:
        self.wfile.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        role = "unknown"
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise ValueError("invalid broker request")
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict) or request.get("version") != 1:
                raise ValueError("invalid broker request")
            if set(request) - {"version", "role", "maxBytes"}:
                raise ValueError("invalid broker request")
            role = str(request.get("role", "")).strip()
            node = self.server.allowed_nodes.get(role)
            if node is None:
                raise ValueError("recovery role is not configured")
            requested_bytes = parse_non_negative_int(
                str(request.get("maxBytes", self.server.max_bytes)), self.server.max_bytes
            )
            max_bytes = min(max(1, requested_bytes), self.server.max_bytes)
            payload = read_remote(node, self.server.timeout, max_bytes)
            self._reply({"version": 1, "ok": True, "payload": payload})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._reply({"version": 1, "ok": False, "error": "invalid broker request"})
            print(f"[tailscale-broker] role={role} status=rejected", flush=True)
        except Exception:
            self._reply({"version": 1, "ok": False, "error": "remote collection failed"})
            print(f"[tailscale-broker] role={role} status=failed", flush=True)


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def socket_path() -> Path:
    return Path(
        str(
            os.environ.get(
                "DB_BACKUP_TAILSCALE_BROKER_SOCKET", DEFAULT_TAILSCALE_BROKER_SOCKET
            )
        ).strip()
        or DEFAULT_TAILSCALE_BROKER_SOCKET
    )


def prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"broker socket path is not a socket: {path}")
        path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=os.environ.get("DB_BACKUP_TAILSCALE_BROKER_SOCKET", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.socket.strip() if args.socket else socket_path())
    prepare_socket(path)
    server = BrokerServer(str(path), BrokerRequestHandler)
    server.allowed_nodes = configured_nodes()
    server.timeout = parse_non_negative_int(
        os.environ.get("DB_BACKUP_SSH_TIMEOUT_SECONDS", "20"), 20
    )
    server.max_bytes = parse_non_negative_int(
        os.environ.get("DB_BACKUP_SSH_MAX_FILE_BYTES", "5242880"), 5242880
    )
    os.chmod(path, 0o660)

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
