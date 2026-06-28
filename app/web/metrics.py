"""Prometheus text-format `/metrics` endpoint.

Exposes the panel's already-collected state (traffic, probes, DNS failover, AI
routing, data-plane status) for scraping by Prometheus. Host CPU/mem/disk come
from node_exporter, not this endpoint.

The handler is strictly read-only and SSH-free on the scrape path: it reads the
tables the background maintenance loop keeps fresh and never calls
``sync_traffic_state``/``dns_failover_status``/probes (any of which may do I/O).
The single SSH call (``data_plane_running``) is wrapped in a TTL cache.

The format is hand-rolled (no ``prometheus_client`` dependency) to keep the
panel on Flask + stdlib.
"""

import hmac
import time
from datetime import datetime

from flask import Response, request

from ..config import METRICS_DP_TTL, METRICS_TOKEN
from .core import route, state

# Cache for the data-plane running check (the one SSH call on this path).
_DP_CACHE = {"val": 0, "ts": 0.0}


def _esc(value):
    """Escape a Prometheus label value (\\, ", newline)."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _iso_to_epoch(value):
    """Parse a stored ISO timestamp to a Unix epoch float, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return None


def _data_plane_running_cached():
    now = time.monotonic()
    if now - _DP_CACHE["ts"] >= METRICS_DP_TTL:
        try:
            _DP_CACHE["val"] = 1 if state.data_plane_running() else 0
        except Exception:
            _DP_CACHE["val"] = 0
        _DP_CACHE["ts"] = now
    return _DP_CACHE["val"]


class _Renderer:
    """Accumulates metric families into Prometheus 0.0.4 text format."""

    def __init__(self):
        self._lines = []

    def family(self, name, mtype, help_text):
        self._lines.append(f"# HELP {name} {help_text}")
        self._lines.append(f"# TYPE {name} {mtype}")

    def sample(self, name, value, labels=None):
        if labels:
            rendered = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            self._lines.append(f"{name}{{{rendered}}} {value}")
        else:
            self._lines.append(f"{name} {value}")

    def text(self):
        return "\n".join(self._lines) + "\n"


def _collect():
    r = _Renderer()

    ports = state.query_ports()
    summary = state.query_summary(ports)

    # --- Liveness ---------------------------------------------------------
    r.family("xray_panel_up", "gauge", "Panel control plane is responding (always 1).")
    r.sample("xray_panel_up", 1)

    # --- Business: per-port traffic & connections -------------------------
    r.family(
        "xray_panel_port_traffic_bytes_total",
        "counter",
        "Cumulative bytes per listen port and direction (resets on quota/traffic reset).",
    )
    for p in ports:
        labels = {"port": p["listen_port"], "note": p.get("note") or ""}
        r.sample(
            "xray_panel_port_traffic_bytes_total",
            int(p["total_bytes_sent"]),
            {**labels, "direction": "sent"},
        )
        r.sample(
            "xray_panel_port_traffic_bytes_total",
            int(p["total_bytes_received"]),
            {**labels, "direction": "received"},
        )

    r.family(
        "xray_panel_port_connections_total",
        "counter",
        "Cumulative connections per listen port (resets on quota/traffic reset).",
    )
    for p in ports:
        r.sample(
            "xray_panel_port_connections_total",
            int(p["total_connections"]),
            {"port": p["listen_port"], "note": p.get("note") or ""},
        )

    # --- Business: port counts -------------------------------------------
    r.family("xray_panel_ports_total", "gauge", "Total configured ports.")
    r.sample("xray_panel_ports_total", summary["total_ports"])
    r.family("xray_panel_ports_enabled", "gauge", "Ports with enabled=1.")
    r.sample("xray_panel_ports_enabled", sum(1 for p in ports if p["enabled"]))
    r.family("xray_panel_ports_active", "gauge", "Ports in 'active' status.")
    r.sample("xray_panel_ports_active", summary["active_ports"])
    r.family("xray_panel_ports_expired", "gauge", "Ports in 'expired' status.")
    r.sample("xray_panel_ports_expired", summary["expired_ports"])
    r.family("xray_panel_ports_quota", "gauge", "Ports in 'quota' (traffic-exhausted) status.")
    r.sample("xray_panel_ports_quota", summary["quota_ports"])
    r.family("xray_panel_ports_disabled", "gauge", "Ports in 'disabled' status.")
    r.sample("xray_panel_ports_disabled", summary["disabled_ports"])

    # --- Availability: per-port probe ------------------------------------
    r.family(
        "xray_panel_port_reachable",
        "gauge",
        "Latest upstream probe result per port (1=reachable, 0=not). Omitted if never probed.",
    )
    for p in ports:
        reachable = p.get("probe_is_reachable")
        if reachable is None:
            continue
        r.sample(
            "xray_panel_port_reachable",
            1 if int(reachable) else 0,
            {"port": p["listen_port"], "note": p.get("note") or ""},
        )

    r.family(
        "xray_panel_port_probe_timestamp_seconds",
        "gauge",
        "Unix time of the latest probe per port (use time()-metric for probe age).",
    )
    for p in ports:
        epoch = _iso_to_epoch(p.get("probe_checked_at"))
        if epoch is not None:
            r.sample(
                "xray_panel_port_probe_timestamp_seconds",
                int(epoch),
                {"port": p["listen_port"]},
            )

    # --- Xray process / data plane ---------------------------------------
    mode = state.data_plane.mode
    r.family("xray_panel_data_plane_configured", "gauge", "Data plane is configured (1/0).")
    try:
        configured = 1 if state.data_plane_configured() else 0
    except Exception:
        configured = 0
    r.sample("xray_panel_data_plane_configured", configured, {"mode": mode})

    r.family("xray_panel_data_plane_running", "gauge", "Data plane (xray) is running (1/0). TTL-cached.")
    r.sample("xray_panel_data_plane_running", _data_plane_running_cached(), {"mode": mode})

    # --- DNS failover (read the state table directly; no resolve_public_ip) -
    config = state.dns_failover_manager.config
    r.family("xray_panel_dns_failover_enabled", "gauge", "DNS failover is enabled (1/0).")
    r.sample("xray_panel_dns_failover_enabled", 1 if config.enabled else 0)

    try:
        with state.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dns_failover_state WHERE singleton_id = 1"
            ).fetchone()
        dns = dict(row) if row is not None else {}
    except Exception:
        dns = {}

    current_target = dns.get("current_target") or "primary"
    r.family(
        "xray_panel_dns_failover_target_info",
        "gauge",
        "Current DNS failover target (value=1 on the active target label).",
    )
    r.sample("xray_panel_dns_failover_target_info", 1, {"target": current_target})

    r.family(
        "xray_panel_dns_failover_last_probe_healthy",
        "gauge",
        "Last DNS failover probe was healthy (1/0).",
    )
    r.sample(
        "xray_panel_dns_failover_last_probe_healthy",
        1 if dns.get("last_probe_status") == "healthy" else 0,
    )
    r.family(
        "xray_panel_dns_failover_consecutive_failures",
        "gauge",
        "Consecutive failed DNS failover probes.",
    )
    r.sample(
        "xray_panel_dns_failover_consecutive_failures",
        int(dns.get("consecutive_failures") or 0),
    )
    r.family(
        "xray_panel_dns_failover_consecutive_successes",
        "gauge",
        "Consecutive successful DNS failover probes.",
    )
    r.sample(
        "xray_panel_dns_failover_consecutive_successes",
        int(dns.get("consecutive_successes") or 0),
    )

    peak = state.dns_failover_peak_window_status()
    r.family(
        "xray_panel_dns_failover_peak_window_active",
        "gauge",
        "Peak-window preference is currently active (1/0).",
    )
    r.sample(
        "xray_panel_dns_failover_peak_window_active",
        1 if peak.get("active") else 0,
    )

    # --- AI routing -------------------------------------------------------
    ai = state.query_ai_domain_aggregate()
    r.family("xray_panel_ai_domains_total", "gauge", "Number of classified AI domains.")
    r.sample("xray_panel_ai_domains_total", int(ai["total_ai_domains"]))
    r.family("xray_panel_ai_domain_hits_total", "counter", "Cumulative AI domain hits.")
    r.sample("xray_panel_ai_domain_hits_total", int(ai["total_hits"]))
    r.family(
        "xray_panel_ai_domains_last_update_timestamp_seconds",
        "gauge",
        "Unix time of the latest AI domain update (use time()-metric for report age).",
    )
    epoch = _iso_to_epoch(ai.get("updated_at"))
    if epoch is not None:
        r.sample("xray_panel_ai_domains_last_update_timestamp_seconds", int(epoch))

    return r.text()


@route("/metrics", methods=["GET"])
def metrics():
    # Token gate before any work. Empty token => endpoint disabled.
    if not METRICS_TOKEN:
        return Response("metrics disabled\n", status=404, mimetype="text/plain")
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {METRICS_TOKEN}"
    if not hmac.compare_digest(header, expected):
        return Response(
            "unauthorized\n",
            status=401,
            mimetype="text/plain",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(_collect(), content_type="text/plain; version=0.0.4; charset=utf-8")
