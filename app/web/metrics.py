"""Prometheus text-format `/metrics` endpoint.

Exposes the panel's already-collected state (traffic, probes, DNS failover, AI
routing, data-plane status) for scraping by Prometheus. Host CPU/mem/disk come
from node_exporter, not this endpoint.

The handler is strictly read-only on the scrape path: it reads the tables the
background maintenance loop keeps fresh, reads the AI node's loopback-only
expvar endpoint, and never calls ``sync_traffic_state``/``dns_failover_status``/
probes (any of which may do I/O). The data-plane status check and AI metrics
read are wrapped in TTL caches.

The format is hand-rolled (no ``prometheus_client`` dependency) to keep the
panel on Flask + stdlib.
"""

import hmac
import json
import time
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import Response, request

from ..config import AI_NODE_METRICS_URL, METRICS_DP_TTL, METRICS_TOKEN, XRAY_STATS_QUERY_TIMEOUT
from .core import route, state

# Cache for the data-plane running check (the one SSH call on this path).
_DP_CACHE = {"val": 0, "ts": 0.0}
_AI_METRICS_CACHE = {
    "ts": 0.0,
    "available": 0,
    "received": 0,
    "sent": 0,
    "egress_received": 0,
    "egress_sent": 0,
}


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


def _read_ai_node_metrics():
    """Read the loopback-only Xray expvar endpoint and keep only byte totals."""
    if not AI_NODE_METRICS_URL:
        return None
    parsed = urlsplit(AI_NODE_METRICS_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    request = Request(AI_NODE_METRICS_URL, headers={"Accept": "application/json"})
    with urlopen(request, timeout=max(1, int(XRAY_STATS_QUERY_TIMEOUT))) as response:
        payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    return _parse_ai_node_metrics_payload(payload)


def _parse_ai_node_metrics_payload(payload):
    """Reduce Xray expvar data to bounded, direction-only byte counters."""
    if not isinstance(payload, dict) or not isinstance(payload.get("stats"), dict):
        return None

    stats = payload["stats"]
    inbound = stats.get("inbound")
    outbound = stats.get("outbound")
    if not isinstance(inbound, dict) or not isinstance(outbound, dict):
        return None

    result = {
        "available": 1,
        "received": 0,
        "sent": 0,
        "egress_received": 0,
        "egress_sent": 0,
    }
    for tag, counters in inbound.items():
        if not str(tag).startswith("panel-") or not isinstance(counters, dict):
            continue
        result["received"] += max(0, int(counters.get("uplink", 0) or 0))
        result["sent"] += max(0, int(counters.get("downlink", 0) or 0))
    for tag, counters in outbound.items():
        if str(tag) != "direct" or not isinstance(counters, dict):
            continue
        result["egress_sent"] += max(0, int(counters.get("uplink", 0) or 0))
        result["egress_received"] += max(0, int(counters.get("downlink", 0) or 0))
    return result


def _ai_node_metrics_cached():
    now = time.monotonic()
    if now - _AI_METRICS_CACHE["ts"] >= METRICS_DP_TTL:
        try:
            result = _read_ai_node_metrics()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result = None
        if result is None:
            result = {
                "available": 0,
                "received": 0,
                "sent": 0,
                "egress_received": 0,
                "egress_sent": 0,
            }
        _AI_METRICS_CACHE.update(result)
        _AI_METRICS_CACHE["ts"] = now
    return _AI_METRICS_CACHE


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

    # --- AI node ---------------------------------------------------------
    ai_node_status = state.ai_node_status()
    r.family(
        "xray_panel_ai_node_configured",
        "gauge",
        "AI node is managed via SSH (1/0).",
    )
    r.sample(
        "xray_panel_ai_node_configured",
        1 if ai_node_status.get("configured") else 0,
    )
    r.family(
        "xray_panel_ai_node_running",
        "gauge",
        "AI node is reachable (1/0).",
    )
    r.sample(
        "xray_panel_ai_node_running",
        1 if ai_node_status.get("reachable") else 0,
    )

    ai_metrics = _ai_node_metrics_cached()
    r.family(
        "xray_panel_ai_node_metrics_available",
        "gauge",
        "AI node Xray metrics endpoint is available (1/0).",
    )
    r.sample("xray_panel_ai_node_metrics_available", ai_metrics["available"])
    r.family(
        "xray_panel_ai_node_traffic_bytes_total",
        "counter",
        "Cumulative AI node inbound traffic bytes by proxy direction.",
    )
    r.sample(
        "xray_panel_ai_node_traffic_bytes_total",
        ai_metrics["received"],
        {"direction": "received"},
    )
    r.sample(
        "xray_panel_ai_node_traffic_bytes_total",
        ai_metrics["sent"],
        {"direction": "sent"},
    )
    r.family(
        "xray_panel_ai_node_egress_bytes_total",
        "counter",
        "Cumulative AI node direct-egress traffic bytes by direction.",
    )
    r.sample(
        "xray_panel_ai_node_egress_bytes_total",
        ai_metrics["egress_received"],
        {"direction": "received"},
    )
    r.sample(
        "xray_panel_ai_node_egress_bytes_total",
        ai_metrics["egress_sent"],
        {"direction": "sent"},
    )

    # --- Backup Xray mode ------------------------------------------------
    backup_mode = state.backup_xray_mode() if hasattr(state, "backup_xray_mode") else "disabled"
    r.family(
        "xray_panel_backup_xray_mode_info",
        "gauge",
        "Control-plane backup Xray mode (value=1 on the active mode label).",
    )
    r.sample(
        "xray_panel_backup_xray_mode_info",
        1,
        {"mode": backup_mode},
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
