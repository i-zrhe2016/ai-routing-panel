"""Deterministic classification based only on Prometheus and local AI route history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import AI_DATA_PLANE, NODE_ROLES, NORMAL_DATA_PLANE, format_timestamp, parse_timestamp, stable_id
from .redaction import redact_value

RULES_VERSION = "2.0"
STATUS_ORDER = {"normal": 0, "unknown": 1, "suspected": 2, "fault": 3}
SCRAPE_STEP_SECONDS = 30
TELEMETRY_MIN_COVERAGE = 0.90
SERVICE_DOWN_MIN_SAMPLES = 2
CORRELATION_WINDOW_SECONDS = 300
CPU_PRESSURE_PERCENT = 95.0
MEMORY_AVAILABLE_PRESSURE_RATIO = 0.10
ROOT_DISK_PRESSURE_RATIO = 0.90
RESOURCE_DURATION_SECONDS = 900
RULE_PARAMETERS = {
    "scrape_step_seconds": SCRAPE_STEP_SECONDS,
    "service_down_min_samples": SERVICE_DOWN_MIN_SAMPLES,
    "telemetry_min_coverage_ratio": TELEMETRY_MIN_COVERAGE,
    "resource_duration_seconds": RESOURCE_DURATION_SECONDS,
    "cpu_pressure_percent": CPU_PRESSURE_PERCENT,
    "memory_available_pressure_ratio": MEMORY_AVAILABLE_PRESSURE_RATIO,
    "root_disk_pressure_ratio": ROOT_DISK_PRESSURE_RATIO,
}


@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime
    samples: int = 1

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))

    def overlaps(self, other: "Interval", padding_seconds: int = 0) -> bool:
        p = timedelta(seconds=padding_seconds)
        return self.start <= other.end + p and self.end >= other.start - p

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": format_timestamp(self.start),
            "ended_at": format_timestamp(self.end),
            "duration_seconds": self.duration_seconds,
            "samples": self.samples,
        }


class EvidenceCatalog:
    def __init__(self):
        self._items = {}

    def add(
        self,
        *,
        node_role: str,
        source: str,
        observed_at: datetime | str,
        evidence_type: str,
        summary: str,
        attributes=None,
        source_quality="complete",
    ) -> str:
        timestamp = format_timestamp(parse_timestamp(observed_at))
        attrs = redact_value(attributes or {})
        summary = str(redact_value(summary))
        eid = stable_id("ev", node_role, source, timestamp, evidence_type, summary, attrs)
        self._items[eid] = {
            "evidence_id": eid,
            "node_role": node_role,
            "source": source,
            "observed_at": timestamp,
            "evidence_type": evidence_type,
            "summary": summary,
            "attributes": attrs,
            "source_quality": source_quality,
        }
        return eid

    def values(self):
        return sorted(self._items.values(), key=lambda x: (x["observed_at"], x["evidence_id"]))


def _points(prom: dict[str, Any], name: str, role: str | None = None):
    out = []
    for series in prom.get(name, []) or []:
        labels = {str(k): str(v) for k, v in (series.get("metric") or {}).items()}
        labelled_role = labels.get("node_role") or labels.get("role")
        if role and labelled_role and labelled_role != role:
            continue
        for raw in series.get("values", []) or []:
            try:
                out.append(
                    (
                        datetime.fromtimestamp(float(raw[0]), tz=parse_timestamp("1970-01-01T00:00:00Z").tzinfo),
                        float(raw[1]),
                        labels,
                    )
                )
            except (TypeError, ValueError, OSError, IndexError):
                pass
    return sorted(out, key=lambda x: x[0])


def _coverage(points, start, end):
    stamps = {int(t.timestamp()) for t, _, _ in points if start <= t < end}
    expected = max(1, int((end - start).total_seconds() / SCRAPE_STEP_SECONDS))
    return min(1.0, len(stamps) / expected)


def _intervals(points, predicate, min_samples=2):
    result = []
    current = []
    for t, v, _ in points:
        if predicate(v) and (not current or (t - current[-1]).total_seconds() <= SCRAPE_STEP_SECONDS * 1.5):
            current.append(t)
        else:
            if len(current) >= min_samples:
                result.append(Interval(current[0], current[-1] + timedelta(seconds=SCRAPE_STEP_SECONDS), len(current)))
            current = []
            if predicate(v):
                current = [t]
    if len(current) >= min_samples:
        result.append(Interval(current[0], current[-1] + timedelta(seconds=SCRAPE_STEP_SECONDS), len(current)))
    return result


def _incident(role, kind, status, interval, rule, eids, facts, missing=None):
    return {
        "incident_id": stable_id("inc", role, kind, interval.start.isoformat(), interval.end.isoformat(), rule),
        "node_role": role,
        "kind": kind,
        "status": status,
        "started_at": format_timestamp(interval.start),
        "ended_at": format_timestamp(interval.end),
        "rule_ids": [rule],
        "evidence_ids": sorted(set(eids)),
        "missing_sources": sorted(set(missing or [])),
        "facts": facts,
    }


def _route_fallback(events, start, end):
    out = []
    for event in events:
        try:
            t = parse_timestamp(event["observed_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= t < end and (
            event.get("fallback")
            or str(event.get("status", "")).lower() in {"fallback", "fallback_to_primary", "failed", "unreachable"}
        ):
            out.append((t, event))
    return out


def _classify(role, start, end, prom, route_events, evidence):
    running_name = "xray_panel_data_plane_running" if role == NORMAL_DATA_PLANE else "xray_panel_ai_node_running"
    running = _points(prom, running_name)
    target = _points(prom, "up", role)
    # A missing target, failed scrapes, or sparse running series is unknown rather than healthy/faulty.
    target_coverage = _coverage(target, start, end)
    running_coverage = _coverage(running, start, end)
    target_healthy = bool(target) and all(v >= 0.5 for t, v, _ in target if start <= t < end)
    coverage = min(target_coverage, running_coverage) if target else 0.0
    missing = []
    if target_coverage < TELEMETRY_MIN_COVERAGE or not target_healthy:
        missing.append("prometheus:target_scrape")
    if running_coverage < TELEMETRY_MIN_COVERAGE:
        missing.append(f"prometheus:{running_name}")
    incidents = []
    rules = []
    fault = False
    suspected = False
    down = _intervals(
        [(timestamp, value, labels) for timestamp, value, labels in running if start <= timestamp < end],
        lambda v: v < 0.5,
        SERVICE_DOWN_MIN_SAMPLES,
    )
    for interval in down:
        eid = evidence.add(
            node_role=role,
            source=f"prometheus:{running_name}",
            observed_at=interval.start,
            evidence_type="service_state",
            summary="Prometheus 节点运行指标连续显示停止",
            attributes=interval.as_dict(),
        )
        incidents.append(
            _incident(
                role,
                "service_down",
                "fault",
                interval,
                "OPS-F001",
                [eid],
                ["面板 Prometheus 运行指标连续至少两个采样显示停止。"],
            )
        )
        rules.append({"rule_id": "OPS-F001", "evidence_ids": [eid], **interval.as_dict()})
        fault = True
    if role == NORMAL_DATA_PLANE:
        dns = _points(prom, "xray_panel_dns_failover_target_info")
        health = _points(prom, "xray_panel_dns_failover_last_probe_healthy")
        bad_health = {int(t.timestamp()) for t, v, _ in health if v < 0.5}
        for timestamp, value, labels in dns:
            if (
                start <= timestamp < end
                and value >= 0.5
                and labels.get("target", "primary") != "primary"
                and any(
                    abs(int(timestamp.timestamp()) - health_timestamp) <= CORRELATION_WINDOW_SECONDS
                    for health_timestamp in bad_health
                )
            ):
                interval = Interval(timestamp, timestamp + timedelta(seconds=SCRAPE_STEP_SECONDS))
                eid = evidence.add(
                    node_role=role,
                    source="prometheus:xray_panel_dns_failover_target_info",
                    observed_at=timestamp,
                    evidence_type="dns_failover",
                    summary="健康检查失败期间 DNS 目标为非 primary",
                    attributes=interval.as_dict(),
                )
                incidents.append(
                    _incident(
                        role,
                        "dns_failover",
                        "fault",
                        interval,
                        "OPS-F003",
                        [eid],
                        ["Prometheus 面板指标确认故障转移。"],
                    )
                )
                rules.append({"rule_id": "OPS-F003", "evidence_ids": [eid], **interval.as_dict()})
                fault = True
                break
    if role == AI_DATA_PLANE:
        for t, event in _route_fallback(route_events, start, end):
            interval = Interval(t, t + timedelta(seconds=60))
            eid = evidence.add(
                node_role=role,
                source="ai_routing_history",
                observed_at=t,
                evidence_type="ai_route_fallback",
                summary="本地 AI 路由历史记录了故障回退",
                attributes={"status": event.get("status"), "reason": event.get("reason", "")},
            )
            incidents.append(
                _incident(
                    role,
                    "ai_route_fallback",
                    "fault",
                    interval,
                    "OPS-F004",
                    [eid],
                    ["本地 AI 路由历史记录了明确的故障回退。"],
                )
            )
            rules.append({"rule_id": "OPS-F004", "evidence_ids": [eid], **interval.as_dict()})
            fault = True
    # Counter increases represent observed demand; a flat counter means no demand, not a fault.
    traffic = []
    if role == NORMAL_DATA_PLANE:
        traffic = _points(prom, "xray_panel_port_traffic_bytes_total") + _points(
            prom, "xray_panel_port_connections_total"
        )
    traffic_coverage = _coverage(traffic, start, end)
    increases = 0
    by_series = {}
    for _timestamp, value, labels in traffic:
        by_series.setdefault(tuple(sorted(labels.items())), []).append(value)
    for values in by_series.values():
        increases += sum(1 for a, b in zip(values, values[1:]) if b > a)
    if traffic_coverage < TELEMETRY_MIN_COVERAGE:
        traffic_status = "unknown"
    elif increases == 0:
        traffic_status = "no_observed_demand"
    else:
        traffic_status = "demand_observed"
    if not fault and missing:
        status = "unknown"
        eid = evidence.add(
            node_role=role,
            source="prometheus",
            observed_at=start,
            evidence_type="telemetry_insufficient",
            summary="Prometheus 目标或抓取覆盖不足，无法判断",
            attributes={"coverage_ratio": round(coverage, 6), "missing_sources": missing},
            source_quality="partial",
        )
        rules.append({"rule_id": "OPS-U001", "evidence_ids": [eid]})
        incidents.append(
            _incident(
                role,
                "telemetry_gap",
                "unknown",
                Interval(start, end),
                "OPS-U001",
                [eid],
                ["目标缺失或抓取覆盖不足。"],
                missing,
            )
        )
    elif fault:
        status = "fault"
    elif suspected:
        status = "suspected"
    else:
        status = "normal"
    return {
        "node_role": role,
        "status": status,
        "service": {
            "coverage_ratio": round(coverage, 6),
            "prometheus_coverage_ratio": round(running_coverage, 6),
            "target_coverage_ratio": round(target_coverage, 6),
            "downtime_intervals": [x.as_dict() for x in down],
            "restart_events": 0,
        },
        "traffic": {
            "status": traffic_status,
            "coverage_ratio": round(traffic_coverage, 6),
            "demand_increases": increases,
        },
        "telemetry": {"coverage_ratio": round(coverage, 6), "missing_sources": missing, "recorded_gaps": 0},
        "resources": {"threshold_breaches": []},
        "matched_rules": sorted(rules, key=lambda x: (x["rule_id"], x.get("started_at", ""))),
    }, incidents


def classify_report(
    *,
    window_start,
    window_end,
    prometheus=None,
    route_events=None,
    events=None,
    samples=None,
    gaps=None,
    collection_runs=None,
    rollups=None,
):
    """Classify Prometheus evidence. Legacy SQLite arguments are accepted but deliberately ignored."""
    start = parse_timestamp(window_start)
    end = parse_timestamp(window_end)
    if end <= start:
        raise ValueError("window_end must be after window_start")
    evidence = EvidenceCatalog()
    nodes = []
    incidents = []
    for role in NODE_ROLES:
        node, found = _classify(role, start, end, prometheus or {}, route_events or [], evidence)
        nodes.append(node)
        incidents.extend(found)
    return {
        "rules_version": RULES_VERSION,
        "rule_parameters": RULE_PARAMETERS,
        "overall_status": max((x["status"] for x in nodes), key=lambda s: STATUS_ORDER[s]),
        "nodes": nodes,
        "incidents": sorted(incidents, key=lambda x: (x["started_at"], x["node_role"], x["incident_id"])),
        "evidence": evidence.values(),
    }
