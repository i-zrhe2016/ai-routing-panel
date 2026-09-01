"""Daily aggregation of AI-domain classifications and routing decisions.

The AI domain manager writes one JSON report per hourly window and mirrors
classified AI domains into panel.db.  This module reads both sources without
modifying either one, then produces the report-shaped summary used by the
daily reporter.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import format_timestamp, parse_timestamp

CLASSIFICATIONS = ("ai", "not_ai", "unknown")
SOURCE_BUCKETS = ("builtin", "codex", "openai", "other", "unknown")
TRAFFIC_DIRECTIONS = ("ai_proxy", "direct", "mixed", "unknown")
AI_ANALYSIS_STATUSES = ("available", "empty", "partial", "unknown", "not_configured")
CODEX_STATUSES = ("success", "partial", "unavailable", "not_used", "unknown")


def normalize_classification(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"ai", "yes", "true", "related", "ai_related"}:
        return "ai"
    if raw in {"not_ai", "no", "false", "unrelated", "non_ai"}:
        return "not_ai"
    return "unknown"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, result)


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_timestamp(str(value))
    except (TypeError, ValueError):
        return None


def _timestamp_text(value: Any) -> str | None:
    parsed = _timestamp(value)
    return format_timestamp(parsed) if parsed else None


def _domain(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or len(candidate) > 253 or " " in candidate:
        return ""
    return candidate


def _target(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    host = str(value.get("upstream_host", value.get("host", "")) or "").strip()
    port = value.get("upstream_port", value.get("port"))
    if not host:
        return None
    try:
        port_value = int(port)
    except (TypeError, ValueError):
        return {"upstream_host": host}
    if port_value <= 0 or port_value > 65535:
        return {"upstream_host": host}
    return {"upstream_host": host, "upstream_port": port_value}


def _route_from_report(
    raw_route: Any,
    classification: str,
    route_status: dict[str, Any],
    ai_target: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(raw_route, dict):
        tag = str(raw_route.get("outbound_tag", raw_route.get("outboundTag", "")) or "").strip()
        if tag not in {"ai_proxy", "direct", "unknown"}:
            tag = "unknown"
        target = _target(raw_route.get("target"))
        status = str(raw_route.get("status", route_status.get("status", "unknown")) or "unknown").strip()
        reason = str(raw_route.get("reason", route_status.get("reason", "")) or "").strip()
        path = str(raw_route.get("path", "") or "").strip()
        if tag == "ai_proxy" and not target:
            target = _target(ai_target)
        if not path:
            path = "ai_node" if tag == "ai_proxy" else "normal_data_plane" if tag == "direct" else "unknown"
        return {
            "outbound_tag": tag,
            "path": path,
            "target": target,
            "status": status,
            "reason": reason,
        }

    status = str(route_status.get("status", "unknown") or "unknown").strip()
    reason = str(route_status.get("reason", "") or "").strip()
    if classification != "ai":
        return {
            "outbound_tag": "direct",
            "path": "normal_data_plane",
            "target": None,
            "status": status,
            "reason": "classification_not_ai",
        }
    if status == "applied":
        return {
            "outbound_tag": "ai_proxy",
            "path": "ai_node",
            "target": _target(ai_target),
            "status": status,
            "reason": reason,
        }
    if status in {
        "disabled",
        "idle",
        "fallback_to_primary",
        "manual_fallback",
        "manual_target_unreachable",
        "pending_proxy_template",
    }:
        return {
            "outbound_tag": "direct",
            "path": "normal_data_plane",
            "target": None,
            "status": status,
            "reason": reason or "ai_route_not_applied",
        }
    return {
        "outbound_tag": "unknown",
        "path": "unknown",
        "target": None,
        "status": status,
        "reason": reason or "route_status_unavailable",
    }


def _source_bucket(value: Any) -> str:
    source = str(value or "").strip().lower()
    if source in {"builtin", "codex", "openai"}:
        return source
    if not source:
        return "unknown"
    return "other"


def _source_health(
    source: str,
    *,
    configured: bool,
    success: bool,
    error_class: str = "",
    records: int = 0,
    files: int = 0,
    invalid_files: int = 0,
) -> dict[str, Any]:
    return {
        "source": source,
        "configured": configured,
        "success": success,
        "error_class": error_class,
        "records": max(0, records),
        "files": max(0, files),
        "invalid_files": max(0, invalid_files),
    }


def _empty_codex(status: str = "unknown") -> dict[str, Any]:
    return {
        "status": status,
        "classified_count": 0,
        "ai_count": 0,
        "not_ai_count": 0,
        "domains": [],
        "pending_domains": [],
        "error_class": "",
    }


def empty_ai_domain_analysis(
    window_start: datetime,
    window_end: datetime,
    *,
    reason: str = "ai_domain_sources_not_configured",
) -> dict[str, Any]:
    """Return a valid empty analysis for tests and unavailable installations."""

    configured = reason not in {"ai_domain_sources_not_configured", "ai_domain_history_not_configured"}
    return {
        "status": "not_configured" if not configured else "unknown",
        "window_start": format_timestamp(window_start),
        "window_end": format_timestamp(window_end),
        "domain_count": 0,
        "observed_hits": 0,
        "new_domain_count": 0,
        "new_domain_hits": 0,
        "classification_counts": {key: 0 for key in CLASSIFICATIONS},
        "classification_hits": {key: 0 for key in CLASSIFICATIONS},
        "source_counts": {key: 0 for key in SOURCE_BUCKETS},
        "traffic_direction_counts": {key: 0 for key in TRAFFIC_DIRECTIONS},
        "domains": [],
        "new_domains": [],
        "codex": _empty_codex("unknown"),
        "sources": [
            _source_health(
                "ai_routing_history",
                configured=False,
                success=False,
                error_class=reason,
            ),
            _source_health(
                "panel_db_ai_domains",
                configured=False,
                success=False,
                error_class=reason,
            ),
        ],
    }


def _load_hourly_history(
    report_dir: str | Path,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    directory = Path(report_dir)
    if not directory.is_dir():
        return {
            "records": [],
            "first_seen": {},
            "pending_domains": set(),
            "health": _source_health(
                "ai_routing_history",
                configured=False,
                success=False,
                error_class="ai_domain_history_not_configured",
            ),
        }

    candidates: dict[tuple[datetime, datetime], tuple[datetime, Path, dict[str, Any]]] = {}
    first_seen: dict[str, datetime] = {}
    invalid_files = 0
    inspected_files = 0
    try:
        paths = []
        latest_path = directory / "latest.json"
        if latest_path.is_file():
            paths.append(latest_path)
        history_directory = directory / "history"
        if history_directory.is_dir():
            paths.extend(sorted(history_directory.rglob("*.json")))
    except OSError:
        paths = []
        invalid_files = 1

    for path in paths:
        inspected_files += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            invalid_files += 1
            continue
        if not isinstance(payload, dict):
            invalid_files += 1
            continue
        start = _timestamp(payload.get("window_start"))
        end = _timestamp(payload.get("window_end"))
        if not start or not end or end <= start:
            invalid_files += 1
            continue
        generated = _timestamp(payload.get("generated_at")) or end
        key = (start, end)
        previous = candidates.get(key)
        # latest.json and its history copy describe the same window.  Prefer
        # the latest copy when timestamps are equal so the pair is counted once.
        preference = (generated, path.name == "latest.json", str(path))
        previous_preference = (
            (previous[0], previous[1].name == "latest.json", str(previous[1])) if previous else None
        )
        if previous is None or preference > previous_preference:
            candidates[key] = (generated, path, payload)

        raw_domains = payload.get("domains", [])
        if not isinstance(raw_domains, list):
            continue
        for raw_item in raw_domains:
            if not isinstance(raw_item, dict):
                continue
            domain = _domain(raw_item.get("domain"))
            observed = _timestamp(raw_item.get("first_seen"))
            if domain and observed and (domain not in first_seen or observed < first_seen[domain]):
                first_seen[domain] = observed

    records: list[dict[str, Any]] = []
    pending_domains: set[str] = set()
    for (start, end), (generated, path, payload) in sorted(candidates.items()):
        if end <= window_start or start >= window_end:
            continue
        route_status = payload.get("route_status")
        if not isinstance(route_status, dict):
            route_status = {}
        ai_target = payload.get("ai_target")
        if not isinstance(ai_target, dict):
            ai_target = None
        pending = route_status.get("pending_domains_without_classifier", [])
        if isinstance(pending, list):
            pending_domains.update(domain for item in pending if (domain := _domain(item)))
        raw_domains = payload.get("domains", [])
        if not isinstance(raw_domains, list):
            continue
        for raw_item in raw_domains:
            if not isinstance(raw_item, dict):
                continue
            domain = _domain(raw_item.get("domain"))
            if not domain:
                continue
            classification = normalize_classification(raw_item.get("classification"))
            records.append(
                {
                    "key": (start, end, domain),
                    "window_start": start,
                    "window_end": end,
                    "generated_at": generated,
                    "domain": domain,
                    "hits": _safe_int(raw_item.get("hits")),
                    "classification": classification,
                    "reason": str(raw_item.get("reason", "") or "").strip(),
                    "source": str(raw_item.get("source", "") or "").strip(),
                    "model": str(raw_item.get("model", "") or "").strip(),
                    "protocols": sorted(
                        {
                            str(protocol).strip().lower()
                            for protocol in (raw_item.get("protocols") or [])
                            if str(protocol).strip()
                        }
                    ),
                    "first_seen": _timestamp(raw_item.get("first_seen")) or start,
                    "last_seen": _timestamp(raw_item.get("last_seen")) or end,
                    "traffic_route": _route_from_report(
                        raw_item.get("traffic_route"), classification, route_status, ai_target
                    ),
                    "source_file": path.name,
                }
            )

    return {
        "records": records,
        "first_seen": first_seen,
        "pending_domains": pending_domains,
        "health": _source_health(
            "ai_routing_history",
            configured=True,
            success=invalid_files == 0,
            error_class="" if invalid_files == 0 else "ai_domain_history_partial",
            records=len(records),
            files=inspected_files,
            invalid_files=invalid_files,
        ),
    }


def _load_panel_db(path: str | Path, window_start: datetime, window_end: datetime) -> dict[str, Any]:
    db_path = Path(path) if str(path or "").strip() else None
    if db_path is None or not db_path.is_file():
        return {
            "catalog": {},
            "observations": [],
            "health": _source_health(
                "panel_db_ai_domains",
                configured=bool(db_path),
                success=False,
                error_class="panel_db_missing" if db_path else "panel_db_not_configured",
            ),
        }

    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error):
        return {
            "catalog": {},
            "observations": [],
            "health": _source_health(
                "panel_db_ai_domains",
                configured=True,
                success=False,
                error_class="panel_db_unreadable",
            ),
        }

    catalog: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    try:
        try:
            rows = connection.execute(
                """
                SELECT domain, classification, reason, source, model, first_seen,
                       last_seen, total_hits, last_protocols, last_report_window_start,
                       last_report_window_end, updated_at
                FROM ai_domains
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return {
                "catalog": {},
                "observations": [],
                "health": _source_health(
                    "panel_db_ai_domains",
                    configured=True,
                    success=False,
                    error_class="panel_db_schema_missing",
                ),
            }
        for row in rows:
            domain = _domain(row["domain"])
            if not domain:
                continue
            catalog[domain] = {
                "domain": domain,
                "classification": normalize_classification(row["classification"]),
                "reason": str(row["reason"] or "").strip(),
                "source": str(row["source"] or "").strip(),
                "model": str(row["model"] or "").strip(),
                "first_seen": _timestamp(row["first_seen"]),
                "last_seen": _timestamp(row["last_seen"]),
                "total_hits": _safe_int(row["total_hits"]),
                "updated_at": _timestamp(row["updated_at"]),
            }

        try:
            observation_rows = connection.execute(
                """
                SELECT domain, window_start, window_end, hits, classification, reason,
                       source, model, protocols, first_seen, last_seen, created_at
                FROM ai_domain_observations
                """
            ).fetchall()
        except sqlite3.OperationalError:
            observation_rows = []
        for row in observation_rows:
            domain = _domain(row["domain"])
            start = _timestamp(row["window_start"])
            end = _timestamp(row["window_end"])
            if not domain or not start or not end or end <= window_start or start >= window_end:
                continue
            protocols: list[str] = []
            try:
                raw_protocols = json.loads(row["protocols"] or "[]")
            except (TypeError, json.JSONDecodeError):
                raw_protocols = []
            if isinstance(raw_protocols, list):
                protocols = sorted({str(item).strip().lower() for item in raw_protocols if str(item).strip()})
            observations.append(
                {
                    "key": (start, end, domain),
                    "window_start": start,
                    "window_end": end,
                    "generated_at": _timestamp(row["created_at"]) or end,
                    "domain": domain,
                    "hits": _safe_int(row["hits"]),
                    "classification": normalize_classification(row["classification"]),
                    "reason": str(row["reason"] or "").strip(),
                    "source": str(row["source"] or "").strip(),
                    "model": str(row["model"] or "").strip(),
                    "protocols": protocols,
                    "first_seen": _timestamp(row["first_seen"]) or start,
                    "last_seen": _timestamp(row["last_seen"]) or end,
                    "traffic_route": {
                        "outbound_tag": "unknown",
                        "path": "unknown",
                        "target": None,
                        "status": "unknown",
                        "reason": "route_history_unavailable",
                    },
                    "source_file": "panel.db",
                }
            )
    finally:
        connection.close()

    return {
        "catalog": catalog,
        "observations": observations,
        "health": _source_health(
            "panel_db_ai_domains",
            configured=True,
            success=True,
            records=len(catalog) + len(observations),
        ),
    }


def _route_signature(route: dict[str, Any]) -> tuple[str, str, int | None]:
    target = route.get("target") if isinstance(route.get("target"), dict) else {}
    host = str(target.get("upstream_host", "") or "")
    port = target.get("upstream_port")
    try:
        port_value = int(port) if port is not None else None
    except (TypeError, ValueError):
        port_value = None
    return str(route.get("outbound_tag", "unknown")), host, port_value


def _aggregate_domain_records(
    records: list[dict[str, Any]],
    first_seen: dict[str, datetime],
    pending_domains: set[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["domain"]].append(record)

    domains: list[dict[str, Any]] = []
    for domain, items in grouped.items():
        items.sort(key=lambda item: (item["window_end"], item["generated_at"], item["source_file"]))
        latest = items[-1]
        classification = latest["classification"]
        domain_first_seen = min((item["first_seen"] for item in items), default=None)
        domain_last_seen = max((item["last_seen"] for item in items), default=None)
        global_first_seen = first_seen.get(domain, domain_first_seen)
        is_new = bool(global_first_seen and window_start <= global_first_seen < window_end)

        route_groups: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        protocols: set[str] = set()
        codex_results: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in items:
            protocols.update(item["protocols"])
            route = item["traffic_route"]
            signature = _route_signature(route)
            route_item = route_groups.setdefault(
                signature,
                {
                    "outbound_tag": signature[0],
                    "path": route.get("path", "unknown"),
                    "target": route.get("target"),
                    "hits": 0,
                    "window_count": 0,
                    "statuses": set(),
                },
            )
            route_item["hits"] += item["hits"]
            route_item["window_count"] += 1
            route_item["statuses"].add(str(route.get("status", "unknown")))
            if _source_bucket(item["source"]) == "codex":
                codex_key = (item["classification"], item["reason"], item["model"])
                codex_results[codex_key] = {
                    "classification": item["classification"],
                    "reason": item["reason"],
                    "model": item["model"],
                    "observed_at": format_timestamp(item["generated_at"]),
                }

        route_list = []
        for route_item in route_groups.values():
            route_list.append(
                {
                    "outbound_tag": route_item["outbound_tag"],
                    "path": route_item["path"],
                    "target": route_item["target"],
                    "hits": route_item["hits"],
                    "window_count": route_item["window_count"],
                    "statuses": sorted(route_item["statuses"]),
                }
            )
        route_list.sort(key=lambda item: (-item["hits"], item["outbound_tag"], str(item.get("target"))))
        direction_tags = {item["outbound_tag"] for item in route_list}
        if direction_tags == {"ai_proxy"}:
            direction = "ai_proxy"
        elif direction_tags == {"direct"}:
            direction = "direct"
        elif direction_tags:
            direction = "mixed" if len(direction_tags) > 1 else "unknown"
        else:
            direction = "unknown"

        source = next(
            (str(item["source"]).strip() for item in reversed(items) if str(item["source"] or "").strip()),
            "unknown",
        )
        model = next(
            (str(item["model"]).strip() for item in reversed(items) if str(item["model"] or "").strip()),
            "",
        )
        reason = next(
            (str(item["reason"]).strip() for item in reversed(items) if str(item["reason"] or "").strip()),
            "",
        )
        domains.append(
            {
                "domain": domain,
                "hits": sum(item["hits"] for item in items),
                "classification": classification,
                "reason": reason,
                "source": source,
                "model": model,
                "first_seen": format_timestamp(domain_first_seen) if domain_first_seen else None,
                "last_seen": format_timestamp(domain_last_seen) if domain_last_seen else None,
                "protocols": sorted(protocols),
                "traffic_direction": direction,
                "traffic_routes": route_list,
                "is_new": is_new,
                "codex_classifications": sorted(
                    codex_results.values(), key=lambda item: (item["observed_at"], item["classification"])
                ),
            }
        )

    domains.sort(key=lambda item: (-item["hits"], item["domain"]))
    new_domains = [item for item in domains if item["is_new"]]
    classification_counts = {key: 0 for key in CLASSIFICATIONS}
    classification_hits = {key: 0 for key in CLASSIFICATIONS}
    source_counts = {key: 0 for key in SOURCE_BUCKETS}
    direction_counts = {key: 0 for key in TRAFFIC_DIRECTIONS}
    for item in domains:
        classification_counts[item["classification"]] += 1
        classification_hits[item["classification"]] += item["hits"]
        source_counts[_source_bucket(item["source"])] += 1
        direction_counts[item["traffic_direction"]] += 1

    codex_domains: list[dict[str, Any]] = []
    for item in domains:
        for result in item["codex_classifications"]:
            codex_domains.append(
                {
                    "domain": item["domain"],
                    "classification": result["classification"],
                    "reason": result["reason"],
                    "model": result["model"],
                    "observed_at": result["observed_at"],
                }
            )
    codex_domains.sort(key=lambda item: (item["domain"], item["observed_at"]))
    pending = sorted(pending_domains)
    if codex_domains:
        codex_status = "partial" if pending else "success"
    elif pending:
        codex_status = "unavailable"
    elif domains:
        codex_status = "not_used"
    else:
        codex_status = "unknown"
    codex = {
        "status": codex_status,
        "classified_count": len(codex_domains),
        "ai_count": sum(item["classification"] == "ai" for item in codex_domains),
        "not_ai_count": sum(item["classification"] == "not_ai" for item in codex_domains),
        "domains": codex_domains,
        "pending_domains": pending,
        "error_class": "codex_classifier_unavailable" if pending else "",
    }

    return {
        "domain_count": len(domains),
        "observed_hits": sum(item["hits"] for item in domains),
        "new_domain_count": len(new_domains),
        "new_domain_hits": sum(item["hits"] for item in new_domains),
        "classification_counts": classification_counts,
        "classification_hits": classification_hits,
        "source_counts": source_counts,
        "traffic_direction_counts": direction_counts,
        "domains": domains,
        "new_domains": new_domains,
        "codex": codex,
    }


def build_ai_domain_analysis(
    report_dir: str | Path,
    panel_db_path: str | Path,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Build the AI-domain section for one Beijing-day report window."""

    history = _load_hourly_history(report_dir, window_start, window_end)
    database = _load_panel_db(panel_db_path, window_start, window_end)
    records = list(history["records"])
    indexed_keys = {item["key"] for item in records}
    first_seen = dict(history["first_seen"])
    for domain, item in database["catalog"].items():
        observed = item.get("first_seen")
        if observed and (domain not in first_seen or observed < first_seen[domain]):
            first_seen[domain] = observed
    for observation in database["observations"]:
        domain = observation["domain"]
        observed = observation["first_seen"]
        if observed and (domain not in first_seen or observed < first_seen[domain]):
            first_seen[domain] = observed
        if observation["key"] not in indexed_keys:
            records.append(observation)
            indexed_keys.add(observation["key"])
            continue
        # The route history supplies hit counts and egress; the DB supplies
        # the exact classifier source/model for the same hourly window.
        for record in records:
            if record["key"] != observation["key"]:
                continue
            if not record["reason"]:
                record["reason"] = observation["reason"]
            if not record["source"]:
                record["source"] = observation["source"]
            if not record["model"]:
                record["model"] = observation["model"]
            if record["classification"] == "unknown":
                record["classification"] = observation["classification"]
            if not record["protocols"]:
                record["protocols"] = observation["protocols"]
            break

    for record in records:
        catalog = database["catalog"].get(record["domain"])
        if not catalog:
            continue
        if record["classification"] == "unknown":
            record["classification"] = catalog["classification"]
        if not record["reason"]:
            record["reason"] = catalog["reason"]
        if not record["source"]:
            record["source"] = catalog["source"]
        if not record["model"]:
            record["model"] = catalog["model"]

    aggregate = _aggregate_domain_records(
        records,
        first_seen,
        set(history["pending_domains"]),
        window_start,
        window_end,
    )
    source_health = [history["health"], database["health"]]
    if aggregate["domain_count"]:
        status = "partial" if any(not item["success"] for item in source_health if item["configured"]) else "available"
    elif any(item["configured"] for item in source_health):
        status = "empty" if all(item["success"] for item in source_health if item["configured"]) else "unknown"
    else:
        status = "not_configured"
    return {
        "status": status,
        "window_start": format_timestamp(window_start),
        "window_end": format_timestamp(window_end),
        "sources": source_health,
        **aggregate,
    }


def codex_context(analysis: dict[str, Any], *, max_domains: int = 100) -> dict[str, Any]:
    """Keep the daily operational Codex prompt bounded while exposing AI data."""

    domains = analysis.get("domains") if isinstance(analysis.get("domains"), list) else []
    compact_domains = [
        {
            "domain": item.get("domain"),
            "hits": item.get("hits", 0),
            "classification": item.get("classification", "unknown"),
            "source": item.get("source", "unknown"),
            "model": item.get("model", ""),
            "traffic_direction": item.get("traffic_direction", "unknown"),
            "reason": item.get("reason", ""),
        }
        for item in domains[:max_domains]
        if isinstance(item, dict)
    ]
    return {
        "status": analysis.get("status", "unknown"),
        "domain_count": analysis.get("domain_count", 0),
        "observed_hits": analysis.get("observed_hits", 0),
        "new_domain_count": analysis.get("new_domain_count", 0),
        "classification_counts": analysis.get("classification_counts", {}),
        "classification_hits": analysis.get("classification_hits", {}),
        "source_counts": analysis.get("source_counts", {}),
        "traffic_direction_counts": analysis.get("traffic_direction_counts", {}),
        "new_domains": [
            item.get("domain")
            for item in (analysis.get("new_domains") or [])[:max_domains]
            if isinstance(item, dict)
        ],
        "codex": {
            key: value
            for key, value in (analysis.get("codex") or {}).items()
            if key != "domains"
        },
        "domains": compact_domains,
        "truncated": len(domains) > max_domains,
    }
