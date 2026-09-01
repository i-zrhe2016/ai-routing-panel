"""Versioned JSON report assembly, validation, rendering, and atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ai_domains import (
    AI_ANALYSIS_STATUSES,
    CLASSIFICATIONS,
    CODEX_STATUSES,
    SOURCE_BUCKETS,
    TRAFFIC_DIRECTIONS,
    empty_ai_domain_analysis,
)
from .codex_runner import MODEL_OUTPUT_SCHEMA_VERSION, PROMPT_VERSION, validate_model_analysis
from .models import parse_timestamp, utc_now
from .redaction import redact_value

REPORT_SCHEMA_VERSION = "1.1"
REPORT_SCHEMA_FILENAME = "daily-report-v1.schema.json"
STATUSES = {"normal", "suspected", "fault", "unknown"}
GENERATION_MODES = {"codex", "rules_only"}
REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
ROOT_FIELDS = {
    "schema_version",
    "rules_version",
    "rule_parameters",
    "prompt_version",
    "model_output_schema_version",
    "report_date",
    "timezone",
    "window_start",
    "window_end",
    "generated_at",
    "generation_mode",
    "overall_status",
    "nodes",
    "incidents",
    "evidence",
    "collection_health",
    "ai_domain_analysis",
    "model_analysis",
    "generation_health",
}


def report_schema_path() -> Path:
    return Path(__file__).with_name("schemas") / REPORT_SCHEMA_FILENAME


def build_report(
    *,
    report_date: str,
    window_start: datetime,
    window_end: datetime,
    classification: dict[str, Any],
    collection_health: dict[str, Any],
    generation_mode: str,
    model_analysis: dict[str, Any] | None,
    generation_health: dict[str, Any],
    ai_domain_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if ai_domain_analysis is None:
        ai_domain_analysis = empty_ai_domain_analysis(
            window_start.astimezone(ZoneInfo("UTC")),
            window_end.astimezone(ZoneInfo("UTC")),
        )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "rules_version": classification["rules_version"],
        "rule_parameters": classification["rule_parameters"],
        "prompt_version": PROMPT_VERSION if generation_mode == "codex" else None,
        "model_output_schema_version": MODEL_OUTPUT_SCHEMA_VERSION if generation_mode == "codex" else None,
        "report_date": report_date,
        "timezone": "Asia/Shanghai",
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "generated_at": utc_now().astimezone(REPORT_TIMEZONE).isoformat(timespec="seconds"),
        "generation_mode": generation_mode,
        "overall_status": classification["overall_status"],
        "nodes": classification["nodes"],
        "incidents": classification["incidents"],
        "evidence": classification["evidence"],
        "collection_health": collection_health,
        "ai_domain_analysis": ai_domain_analysis,
        "model_analysis": model_analysis,
        "generation_health": generation_health,
    }
    report = redact_value(report)
    validate_report(report)
    return report


def validate_report(report: Any) -> None:
    if not isinstance(report, dict) or set(report) != ROOT_FIELDS:
        raise ValueError("report root fields do not match schema version 1.1")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("unsupported report schema version")
    if report.get("generation_mode") not in GENERATION_MODES:
        raise ValueError("invalid generation_mode")
    if report.get("overall_status") not in STATUSES:
        raise ValueError("invalid overall_status")
    if report.get("timezone") != "Asia/Shanghai":
        raise ValueError("report timezone must be Asia/Shanghai")
    try:
        parsed_date = date.fromisoformat(str(report["report_date"]))
        start = parse_timestamp(report["window_start"])
        end = parse_timestamp(report["window_end"])
        generated = datetime.fromisoformat(str(report["generated_at"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("report contains invalid dates") from exc
    expected_start = datetime.combine(parsed_date, datetime.min.time(), tzinfo=REPORT_TIMEZONE)
    expected_end = expected_start + timedelta(days=1)
    if start != expected_start.astimezone(ZoneInfo("UTC")) or end != expected_end.astimezone(ZoneInfo("UTC")):
        raise ValueError("report window must be the Beijing natural day for report_date")
    for field in ("window_start", "window_end"):
        rendered = datetime.fromisoformat(str(report[field]))
        if rendered.tzinfo is None or rendered.utcoffset() != timedelta(hours=8):
            raise ValueError("report window timestamps must be rendered in Asia/Shanghai")
    if generated.tzinfo is None or generated.utcoffset() != timedelta(hours=8):
        raise ValueError("generated_at must be rendered in Asia/Shanghai")
    if not isinstance(report.get("rules_version"), str) or not report["rules_version"].strip():
        raise ValueError("report rules_version must be a non-empty string")
    if not isinstance(report.get("rule_parameters"), dict) or not report["rule_parameters"]:
        raise ValueError("report rule_parameters must be a non-empty object")
    if report["generation_mode"] == "codex":
        if report.get("prompt_version") != PROMPT_VERSION:
            raise ValueError("codex report has an invalid prompt_version")
        if report.get("model_output_schema_version") != MODEL_OUTPUT_SCHEMA_VERSION:
            raise ValueError("codex report has an invalid model_output_schema_version")
    elif report.get("prompt_version") is not None or report.get("model_output_schema_version") is not None:
        raise ValueError("rules_only report must not declare model versions")
    nodes = report.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 2 or {
        item.get("node_role") for item in nodes if isinstance(item, dict)
    } != {
        "normal_data_plane",
        "ai_data_plane",
    }:
        raise ValueError("report must contain both logical node roles")
    for node in nodes:
        if not isinstance(node, dict) or node.get("status") not in STATUSES:
            raise ValueError("report node has an invalid status")
    for field in ("incidents", "evidence"):
        if not isinstance(report.get(field), list):
            raise ValueError(f"report {field} must be an array")
    evidence_ids: set[str] = set()
    evidence_roles: dict[str, str] = {}
    for item in report["evidence"]:
        if not isinstance(item, dict) or item.get("node_role") not in {"normal_data_plane", "ai_data_plane"}:
            raise ValueError("report evidence has an invalid shape")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in evidence_ids:
            raise ValueError("report evidence IDs must be unique non-empty strings")
        evidence_ids.add(evidence_id)
        evidence_roles[evidence_id] = item["node_role"]
    for incident in report["incidents"]:
        if not isinstance(incident, dict) or incident.get("status") not in STATUSES:
            raise ValueError("report incident has an invalid status")
        incident_evidence = incident.get("evidence_ids")
        if not isinstance(incident_evidence, list) or not all(isinstance(value, str) for value in incident_evidence):
            raise ValueError("report incident evidence_ids must be an array of strings")
        if not set(incident_evidence) <= evidence_ids:
            raise ValueError("report incident references unknown evidence")
    if report["generation_mode"] == "codex" and not isinstance(report.get("model_analysis"), dict):
        raise ValueError("codex report must contain model_analysis")
    if report["generation_mode"] == "rules_only" and report.get("model_analysis") is not None:
        raise ValueError("rules_only report must not contain model_analysis")
    if report["generation_mode"] == "codex":
        validate_model_analysis(report["model_analysis"], evidence_roles)
    if not isinstance(report.get("collection_health"), dict) or not isinstance(report.get("generation_health"), dict):
        raise ValueError("report health fields must be objects")
    _validate_ai_domain_analysis(report.get("ai_domain_analysis"))


def _validate_count_map(value: Any, keys: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{field_name} must contain the fixed classification buckets")
    for key in keys:
        count = value.get(key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{field_name}.{key} must be a non-negative integer")


def _validate_ai_domain_route(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "outbound_tag",
        "path",
        "target",
        "hits",
        "window_count",
        "statuses",
    }:
        raise ValueError("AI domain traffic route has an invalid shape")
    if value.get("outbound_tag") not in {"ai_proxy", "direct", "unknown"}:
        raise ValueError("AI domain traffic route has an invalid outbound tag")
    if value.get("path") not in {"ai_node", "normal_data_plane", "unknown"}:
        raise ValueError("AI domain traffic route has an invalid path")
    if value.get("target") is not None:
        target = value["target"]
        if not isinstance(target, dict) or set(target) - {"upstream_host", "upstream_port"}:
            raise ValueError("AI domain traffic route has an invalid target")
        if not isinstance(target.get("upstream_host"), str) or not target["upstream_host"].strip():
            raise ValueError("AI domain traffic route target host is invalid")
        if "upstream_port" in target and (
            not isinstance(target["upstream_port"], int)
            or isinstance(target["upstream_port"], bool)
            or not 1 <= target["upstream_port"] <= 65535
        ):
            raise ValueError("AI domain traffic route target port is invalid")
    if not isinstance(value.get("hits"), int) or value["hits"] < 0:
        raise ValueError("AI domain traffic route hits are invalid")
    if not isinstance(value.get("window_count"), int) or value["window_count"] < 1:
        raise ValueError("AI domain traffic route window_count is invalid")
    if not isinstance(value.get("statuses"), list) or not all(isinstance(item, str) for item in value["statuses"]):
        raise ValueError("AI domain traffic route statuses are invalid")


def _validate_ai_domain_item(value: Any) -> None:
    required = {
        "domain",
        "hits",
        "classification",
        "reason",
        "source",
        "model",
        "first_seen",
        "last_seen",
        "protocols",
        "traffic_direction",
        "traffic_routes",
        "is_new",
        "codex_classifications",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("AI domain item has an invalid shape")
    if not isinstance(value.get("domain"), str) or not value["domain"].strip():
        raise ValueError("AI domain item domain is invalid")
    if not isinstance(value.get("hits"), int) or value["hits"] < 0:
        raise ValueError("AI domain item hits are invalid")
    if value.get("classification") not in CLASSIFICATIONS:
        raise ValueError("AI domain item classification is invalid")
    for field in ("reason", "source", "model"):
        if not isinstance(value.get(field), str):
            raise ValueError(f"AI domain item {field} is invalid")
    for field in ("first_seen", "last_seen"):
        if value[field] is not None:
            try:
                parse_timestamp(value[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"AI domain item {field} is invalid") from exc
    if not isinstance(value.get("protocols"), list) or not all(isinstance(item, str) for item in value["protocols"]):
        raise ValueError("AI domain item protocols are invalid")
    if value.get("traffic_direction") not in TRAFFIC_DIRECTIONS:
        raise ValueError("AI domain item traffic direction is invalid")
    if not isinstance(value.get("traffic_routes"), list):
        raise ValueError("AI domain item traffic routes are invalid")
    for route in value["traffic_routes"]:
        _validate_ai_domain_route(route)
    if not isinstance(value.get("is_new"), bool):
        raise ValueError("AI domain item is_new is invalid")
    if not isinstance(value.get("codex_classifications"), list):
        raise ValueError("AI domain item Codex classifications are invalid")
    for item in value["codex_classifications"]:
        if not isinstance(item, dict) or set(item) != {"classification", "reason", "model", "observed_at"}:
            raise ValueError("AI domain item Codex classification has an invalid shape")
        if item["classification"] not in CLASSIFICATIONS or not all(
            isinstance(item.get(field), str) for field in ("reason", "model", "observed_at")
        ):
            raise ValueError("AI domain item Codex classification is invalid")


def _validate_ai_domain_analysis(value: Any) -> None:
    required = {
        "status",
        "window_start",
        "window_end",
        "domain_count",
        "observed_hits",
        "new_domain_count",
        "new_domain_hits",
        "classification_counts",
        "classification_hits",
        "source_counts",
        "traffic_direction_counts",
        "domains",
        "new_domains",
        "codex",
        "sources",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("AI domain analysis has an invalid shape")
    if value.get("status") not in AI_ANALYSIS_STATUSES:
        raise ValueError("AI domain analysis status is invalid")
    for field in ("window_start", "window_end"):
        try:
            parse_timestamp(value[field])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"AI domain analysis {field} is invalid") from exc
    for field in ("domain_count", "observed_hits", "new_domain_count", "new_domain_hits"):
        if not isinstance(value.get(field), int) or value[field] < 0:
            raise ValueError(f"AI domain analysis {field} is invalid")
    _validate_count_map(value["classification_counts"], CLASSIFICATIONS, "classification_counts")
    _validate_count_map(value["classification_hits"], CLASSIFICATIONS, "classification_hits")
    _validate_count_map(value["source_counts"], SOURCE_BUCKETS, "source_counts")
    _validate_count_map(value["traffic_direction_counts"], TRAFFIC_DIRECTIONS, "traffic_direction_counts")
    if not isinstance(value.get("domains"), list) or not isinstance(value.get("new_domains"), list):
        raise ValueError("AI domain analysis domain arrays are invalid")
    for item in value["domains"]:
        _validate_ai_domain_item(item)
    for item in value["new_domains"]:
        _validate_ai_domain_item(item)
        if not item["is_new"]:
            raise ValueError("AI domain analysis new_domains contains an old domain")
    if value["domain_count"] != len(value["domains"]):
        raise ValueError("AI domain analysis domain_count does not match domains")
    if value["new_domain_count"] != len(value["new_domains"]):
        raise ValueError("AI domain analysis new_domain_count does not match new_domains")
    codex = value.get("codex")
    if not isinstance(codex, dict) or set(codex) != {
        "status",
        "classified_count",
        "ai_count",
        "not_ai_count",
        "domains",
        "pending_domains",
        "error_class",
    }:
        raise ValueError("AI domain analysis Codex section has an invalid shape")
    if codex["status"] not in CODEX_STATUSES:
        raise ValueError("AI domain analysis Codex status is invalid")
    for field in ("classified_count", "ai_count", "not_ai_count"):
        if not isinstance(codex.get(field), int) or codex[field] < 0:
            raise ValueError(f"AI domain analysis Codex {field} is invalid")
    if not isinstance(codex.get("error_class"), str) or not isinstance(codex.get("pending_domains"), list):
        raise ValueError("AI domain analysis Codex status details are invalid")
    for item in codex["domains"]:
        if not isinstance(item, dict) or set(item) != {"domain", "classification", "reason", "model", "observed_at"}:
            raise ValueError("AI domain analysis Codex domain has an invalid shape")
        if item["classification"] not in CLASSIFICATIONS or not all(
            isinstance(item.get(field), str) for field in ("domain", "reason", "model", "observed_at")
        ):
            raise ValueError("AI domain analysis Codex domain is invalid")
    if codex["classified_count"] != len(codex["domains"]):
        raise ValueError("AI domain analysis Codex classified_count does not match domains")
    if not isinstance(value.get("sources"), list):
        raise ValueError("AI domain analysis sources are invalid")
    for source in value["sources"]:
        if not isinstance(source, dict) or not all(
            isinstance(source.get(field), expected)
            for field, expected in (
                ("source", str),
                ("configured", bool),
                ("success", bool),
                ("error_class", str),
            )
        ):
            raise ValueError("AI domain analysis source health is invalid")


def _escape(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "—"


def _status_label(status: str) -> str:
    return {
        "normal": "正常",
        "suspected": "可疑",
        "fault": "故障",
        "unknown": "未知",
    }.get(status, status)


def _node_label(role: str) -> str:
    return "普通数据面" if role == "normal_data_plane" else "AI 数据面"


def _format_bytes(value: Any) -> str:
    try:
        size = max(0, int(value))
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{size} B"
            return f"{amount:.2f} {unit}"
        amount /= 1024


def _render_attribution(attribution: list[dict[str, Any]]) -> list[str]:
    if not attribution:
        return []
    lines = [
        "### 流量归因",
        "",
        "user 和 inbound 是同一批 Xray counter 的两种视角，不应相加。",
        "",
        "| 维度 | 脱敏标识 | 上行 | 下行 | 合计 | 样本 | Reset |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in attribution:
        lines.append(
            f"| `{_escape(item.get('entity_type'))}` | `{_escape(item.get('entity_ref'))}` | "
            f"{_format_bytes(item.get('uplink_bytes'))} | {_format_bytes(item.get('downlink_bytes'))} | "
            f"{_format_bytes(item.get('total_bytes'))} | {int(item.get('sample_count', 0))} | "
            f"{int(item.get('counter_resets', 0))} |"
        )
    lines.append("")
    return lines


def _render_node(node: dict[str, Any]) -> list[str]:
    service = node.get("service") or {}
    traffic = node.get("traffic") or {}
    telemetry = node.get("telemetry") or {}
    rules = node.get("matched_rules") or []
    network_devices = traffic.get("network_devices") or []
    lines = [
        f"## {_node_label(node['node_role'])}",
        "",
        f"- 状态：`{node['status']}`（{_status_label(node['status'])}）",
        f"- 服务遥测覆盖率：{float(service.get('coverage_ratio', 0)):.1%}",
        f"- 流量状态：`{traffic.get('status', 'unknown')}`",
        f"- 流量指标覆盖率：{float(traffic.get('coverage_ratio', 0)):.1%}",
        f"- 观测到的需求增量：{int(traffic.get('demand_increases', 0))}",
        f"- 数据面总流量：{_format_bytes(traffic.get('network_total_bytes'))}"
        f"（入站 {_format_bytes(traffic.get('network_received_bytes'))} / "
        f"出站 {_format_bytes(traffic.get('network_transmitted_bytes'))}）",
        f"- 数据面流量覆盖率：{float(traffic.get('network_coverage_ratio', 0)):.1%}",
        f"- 计入网络接口：{', '.join(network_devices) or '无'}",
        f"- 数据缺口来源：{', '.join(telemetry.get('missing_sources', [])) or '无'}",
        "",
    ]
    lines.extend(_render_attribution(traffic.get("attribution") or []))
    lines.extend(["### 命中规则", ""])
    if rules:
        lines.extend(["| 规则 | 开始 | 结束 | 证据 |", "| --- | --- | --- | --- |"])
        for rule in rules:
            lines.append(
                f"| `{_escape(rule.get('rule_id'))}` | {_escape(rule.get('started_at'))} | "
                f"{_escape(rule.get('ended_at'))} | {_escape(', '.join(rule.get('evidence_ids', [])))} |"
            )
    else:
        lines.append("无。")
    lines.append("")
    return lines


def _classification_label(value: str) -> str:
    return {"ai": "AI", "not_ai": "非 AI", "unknown": "未知"}.get(value, value)


def _traffic_direction_label(value: str) -> str:
    return {
        "ai_proxy": "AI 节点（ai_proxy）",
        "direct": "DMIT 直出（direct）",
        "mixed": "混合导向",
        "unknown": "未知",
    }.get(value, value)


def _format_ai_route(route: dict[str, Any]) -> str:
    target = route.get("target") or {}
    target_text = ""
    if target.get("upstream_host"):
        target_text = str(target["upstream_host"])
        if target.get("upstream_port"):
            target_text += f":{target['upstream_port']}"
    tag = route.get("outbound_tag", "unknown")
    if tag == "ai_proxy":
        path = f"AI 节点（{tag}）"
        if target_text:
            path += f" → `{_escape(target_text)}`"
    elif tag == "direct":
        path = "DMIT 直出（direct）"
    else:
        path = "未知"
    status = str(route.get("status", "") or "").strip()
    hits = int(route.get("hits", 0) or 0)
    suffix = f"，{hits} 次"
    if status:
        suffix += f"，{_escape(status)}"
    return path + suffix


def _format_ai_routes(item: dict[str, Any]) -> str:
    routes = item.get("traffic_routes") or []
    if not routes:
        return "未知"
    return "；".join(_format_ai_route(route) for route in routes)


def _render_ai_domain_analysis(analysis: dict[str, Any]) -> list[str]:
    counts = analysis["classification_counts"]
    hits = analysis["classification_hits"]
    sources = analysis["source_counts"]
    directions = analysis["traffic_direction_counts"]
    codex = analysis["codex"]
    lines = [
        "## AI 域名分类与流量导向",
        "",
        f"- 数据状态：`{_escape(analysis['status'])}`",
        f"- 日内观测域名：{int(analysis['domain_count'])} 个，合计 {int(analysis['observed_hits'])} 次请求",
        f"- 分类：AI {int(counts['ai'])} 个（{int(hits['ai'])} 次）、非 AI {int(counts['not_ai'])} 个（{int(hits['not_ai'])} 次）、"
        f"未知 {int(counts['unknown'])} 个（{int(hits['unknown'])} 次）",
        f"- 每日新增域名：{int(analysis['new_domain_count'])} 个（{int(analysis['new_domain_hits'])} 次请求）",
        f"- 流量导向：AI 节点 {int(directions['ai_proxy'])} 个、DMIT 直出 {int(directions['direct'])} 个、"
        f"混合 {int(directions['mixed'])} 个、未知 {int(directions['unknown'])} 个",
        f"- 分类来源：内建 {int(sources['builtin'])} 个、Codex {int(sources['codex'])} 个、"
        f"OpenAI 兼容接口 {int(sources['openai'])} 个、其他 {int(sources['other'])} 个、未知 {int(sources['unknown'])} 个",
        "",
        "### Codex 域名分类",
        "",
        f"- 状态：`{_escape(codex['status'])}`；已分类 {int(codex['classified_count'])} 个，"
        f"AI {int(codex['ai_count'])} 个，非 AI {int(codex['not_ai_count'])} 个",
    ]
    if codex.get("error_class"):
        lines.append(f"- 未完成域名：{int(len(codex.get('pending_domains') or []))} 个；错误分类：`{_escape(codex['error_class'])}`")
    if codex.get("pending_domains"):
        lines.append(f"- 待分类：{_escape(', '.join(codex['pending_domains']))}")
    if codex.get("domains"):
        lines.extend(["", "| 域名 | 分类 | 原因 | 模型 | 时间 |", "| --- | --- | --- | --- | --- |"])
        for item in codex["domains"]:
            lines.append(
                f"| `{_escape(item['domain'])}` | {_classification_label(item['classification'])} | "
                f"{_escape(item['reason'])} | `{_escape(item['model'])}` | {_escape(item['observed_at'])} |"
            )
    else:
        lines.append("无逐项 Codex 分类记录。")

    lines.extend(["", "### 每日新增域名", ""])
    new_domains = analysis.get("new_domains") or []
    if new_domains:
        lines.extend(
            [
                "| 域名 | 分类 | 命中 | 流量导向 | 分类来源/模型 | 原因 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in new_domains:
            source = item["source"] + (f" / {item['model']}" if item["model"] else "")
            lines.append(
                f"| `{_escape(item['domain'])}` | {_classification_label(item['classification'])} | {int(item['hits'])} | "
                f"{_format_ai_routes(item)} | `{_escape(source)}` | {_escape(item['reason'])} |"
            )
    else:
        lines.append("无。")

    lines.extend(["", "### 每个域名的流量导向", ""])
    domains = analysis.get("domains") or []
    if domains:
        lines.extend(
            [
                "| 域名 | 分类 | 命中 | 导向 | 分类来源/模型 | 原因 | 首次/最后观测 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in domains:
            source = item["source"] + (f" / {item['model']}" if item["model"] else "")
            observed = f"{_escape(item['first_seen'])} / {_escape(item['last_seen'])}"
            lines.append(
                f"| `{_escape(item['domain'])}` | {_classification_label(item['classification'])} | {int(item['hits'])} | "
                f"{_format_ai_routes(item)}（{_traffic_direction_label(item['traffic_direction'])}） | "
                f"`{_escape(source)}` | {_escape(item['reason'])} | {observed} |"
            )
    else:
        lines.append("报告窗口内无可用域名观测。")
    lines.extend(["", "### AI 域名数据源", "", "| 数据源 | 已配置 | 成功 | 记录 | 错误分类 |", "| --- | --- | --- | --- | --- |"])
    for source in analysis.get("sources", []):
        lines.append(
            f"| `{_escape(source.get('source'))}` | {_escape(source.get('configured'))} | "
            f"{_escape(source.get('success'))} | {int(source.get('records', 0) or 0)} | "
            f"{_escape(source.get('error_class'))} |"
        )
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    validate_report(report)
    collection = report["collection_health"]
    parameters = report["rule_parameters"]
    lines = [
        f"# Xray 节点运维日报：{report['report_date']}",
        "",
        f"- 总体状态：`{report['overall_status']}`（{_status_label(report['overall_status'])}）",
        f"- 生成模式：`{report['generation_mode']}`",
        f"- 规则版本：`{report['rules_version']}`",
        f"- Prometheus 抓取间隔：{int(parameters.get('scrape_step_seconds', 0))} 秒",
        f"- 关键遥测覆盖率阈值：{float(parameters.get('telemetry_min_coverage_ratio', 0)):.1%}",
        f"- 报告窗口：{report['window_start']} 至 {report['window_end']}",
        f"- 生成时间：{report['generated_at']}",
        f"- 总体遥测覆盖率：{float(collection.get('overall_coverage_ratio', 0)):.1%}",
        "",
        "## 执行摘要",
        "",
    ]
    if report["generation_mode"] == "codex":
        lines.append(_escape(report["model_analysis"].get("executive_summary")))
    else:
        lines.append("Codex 不可用或本次强制规则模式；本报告仅包含确定性规则结果。")
    lines.append("")

    for node in report["nodes"]:
        lines.extend(_render_node(node))

    lines.extend(_render_ai_domain_analysis(report["ai_domain_analysis"]))
    lines.extend(["## 流量中断", ""])
    traffic_incidents = [item for item in report["incidents"] if item.get("kind") == "traffic_gap"]
    if traffic_incidents:
        lines.extend(["| 节点 | 状态 | 开始 | 结束 | 规则 |", "| --- | --- | --- | --- | --- |"])
        for incident in traffic_incidents:
            lines.append(
                f"| {_node_label(incident['node_role'])} | `{incident['status']}` | "
                f"{_escape(incident['started_at'])} | {_escape(incident['ended_at'])} | "
                f"{_escape(', '.join(incident['rule_ids']))} |"
            )
    else:
        lines.append("无。")
    lines.extend(["", "## 故障时间线", ""])
    if report["incidents"]:
        lines.extend(["| 节点 | 类型 | 状态 | 开始 | 结束 | 规则 |", "| --- | --- | --- | --- | --- | --- |"])
        for incident in report["incidents"]:
            lines.append(
                f"| {_node_label(incident['node_role'])} | {_escape(incident['kind'])} | "
                f"`{incident['status']}` | {_escape(incident['started_at'])} | "
                f"{_escape(incident['ended_at'])} | {_escape(', '.join(incident['rule_ids']))} |"
            )
    else:
        lines.append("无。")

    lines.extend(["", "## 资源风险", ""])
    resource_breaches = [
        (node["node_role"], breach)
        for node in report["nodes"]
        for breach in (node.get("resources") or {}).get("threshold_breaches", [])
    ]
    if resource_breaches:
        lines.extend(["| 节点 | 资源 | 开始 | 结束 | 阈值 |", "| --- | --- | --- | --- | --- |"])
        for node_role, breach in resource_breaches:
            lines.append(
                f"| {_node_label(node_role)} | {_escape(breach.get('resource'))} | "
                f"{_escape(breach.get('started_at'))} | {_escape(breach.get('ended_at'))} | "
                f"{_escape(breach.get('threshold'))} |"
            )
    else:
        lines.append("无。")

    lines.extend(["", "## 采集完整性", ""])
    lines.extend(["| 数据源 | 已配置 | 成功 | 错误分类 |", "| --- | --- | --- | --- |"])
    for source in collection.get("sources", []):
        lines.append(
            f"| {_escape(source.get('source'))} | {_escape(source.get('configured', True))} | "
            f"{_escape(source.get('success'))} | {_escape(source.get('error_class'))} |"
        )

    lines.extend(["", "## 原因分析和建议", ""])
    analysis = report.get("model_analysis")
    if analysis:
        lines.extend(["### 原因候选", ""])
        if analysis.get("probable_causes"):
            for item in analysis["probable_causes"]:
                lines.append(
                    f"- **{_node_label(item['node_role'])} / {item['confidence']}**："
                    f"{_escape(item['cause'])}（证据：{_escape(', '.join(item['evidence_ids']))}）"
                )
        else:
            lines.append("无。")
        lines.extend(["", "### 建议", ""])
        if analysis.get("recommended_actions"):
            for item in analysis["recommended_actions"]:
                lines.append(
                    f"- **{_node_label(item['node_role'])} / {item['priority']}**："
                    f"{_escape(item['action'])}（证据：{_escape(', '.join(item['evidence_ids']))}）"
                )
        else:
            lines.append("无。")
        lines.extend(["", "### 不确定项", ""])
        if analysis.get("uncertainties"):
            for item in analysis["uncertainties"]:
                lines.append(
                    f"- **{_node_label(item['node_role'])}**：{_escape(item['description'])}"
                    f"（证据：{_escape(', '.join(item['evidence_ids']))}）"
                )
        else:
            lines.append("无。")
    else:
        lines.append("Codex 不可用或本次强制规则模式；未生成模型原因分析和建议。")

    lines.extend(["", "## Codex 用量", ""])
    usage = (report.get("generation_health") or {}).get("codex_usage")
    if usage:
        tokens = usage.get("tokens") or {}
        lines.extend(
            [
                f"- Provider：`{_escape(usage.get('provider'))}`",
                f"- 模型：`{_escape(usage.get('model'))}`",
                f"- 尝试次数：{int(usage.get('attempts', 0))}",
                f"- 输入 Token：{_escape(tokens.get('input_tokens'))}",
                f"- 缓存输入 Token：{_escape(tokens.get('cached_input_tokens'))}",
                f"- 缓存写入 Token：{_escape(tokens.get('cache_write_input_tokens'))}",
                f"- 输出 Token：{_escape(tokens.get('output_tokens'))}",
                f"- 推理输出 Token：{_escape(tokens.get('reasoning_output_tokens'))}",
                f"- 总 Token：{_escape(tokens.get('total_tokens'))}",
                "- 预估价格：未估算（第三方 API 单价未配置，币种预留为 USD）",
            ]
        )
    else:
        lines.append("无可用 Codex Token 用量；预估价格未计算。")

    lines.extend(["", "## 证据附录", ""])
    if report["evidence"]:
        lines.extend(["| ID | 节点 | 时间 | 来源 | 类型 | 摘要 |", "| --- | --- | --- | --- | --- | --- |"])
        for item in report["evidence"]:
            lines.append(
                f"| `{_escape(item['evidence_id'])}` | {_node_label(item['node_role'])} | "
                f"{_escape(item['observed_at'])} | {_escape(item['source'])} | "
                f"{_escape(item['evidence_type'])} | {_escape(item['summary'])} |"
            )
    else:
        lines.append("无。")
    return "\n".join(lines).rstrip() + "\n"


def _write_temp(directory: Path, suffix: str, data: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".ops-report-", suffix=suffix, dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _fsync_directory(directory: Path) -> None:
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _symlink_temp(directory: Path, target: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".ops-report-link-", suffix=".tmp", dir=directory)
    os.close(descriptor)
    temporary = Path(raw_path)
    try:
        temporary.unlink(missing_ok=True)
        os.symlink(target, temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _ensure_stable_report_link(path: Path, target: str) -> bool:
    if path.is_symlink():
        if os.readlink(path) != target:
            raise ValueError(f"existing report link has an unexpected target: {path.name}")
        return False
    if path.exists():
        raise ValueError(f"existing report path is not managed atomically: {path.name}")
    temporary = _symlink_temp(path.parent, target)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _remove_generation(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass


def write_report_atomic(report_dir: str | Path, report: dict[str, Any]) -> dict[str, str]:
    validate_report(report)
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    report_date = report["report_date"]
    json_path = directory / f"{report_date}.json"
    markdown_path = directory / f"{report_date}.md"
    json_bytes = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    markdown_bytes = render_markdown(report).encode("utf-8")
    digest = hashlib.sha256(json_bytes).hexdigest()
    generation_root = directory / f".{report_date}.generations"
    generation_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(generation_root, 0o700)
    generation_dir = Path(tempfile.mkdtemp(prefix="gen-", dir=generation_root))
    os.chmod(generation_dir, 0o700)
    generation_json = generation_dir / "report.json"
    generation_markdown = generation_dir / "report.md"
    current_link = directory / f".{report_date}.current"
    previous_target = os.readlink(current_link) if current_link.is_symlink() else None
    if current_link.exists() and not current_link.is_symlink():
        _remove_generation(generation_dir)
        raise ValueError(f"existing report generation pointer is not a symlink: {current_link.name}")
    json_temp: Path | None = None
    markdown_temp: Path | None = None
    current_temp: Path | None = None
    committed = False
    try:
        json_temp = _write_temp(generation_dir, ".json.tmp", json_bytes)
        markdown_temp = _write_temp(generation_dir, ".md.tmp", markdown_bytes)
        os.replace(json_temp, generation_json)
        os.replace(markdown_temp, generation_markdown)
        _fsync_directory(generation_dir)

        _ensure_stable_report_link(json_path, f".{report_date}.current/report.json")
        _ensure_stable_report_link(markdown_path, f".{report_date}.current/report.md")
        next_target = str(generation_dir.relative_to(directory))
        current_temp = _symlink_temp(directory, next_target)
        os.replace(current_temp, current_link)
        committed = True
        _fsync_directory(directory)
    except Exception:
        if committed:
            try:
                if previous_target is None:
                    current_link.unlink(missing_ok=True)
                else:
                    restore_temp = _symlink_temp(directory, previous_target)
                    try:
                        os.replace(restore_temp, current_link)
                    finally:
                        restore_temp.unlink(missing_ok=True)
                _fsync_directory(directory)
            except OSError:
                pass
        if previous_target is None:
            json_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
        _remove_generation(generation_dir)
        raise
    finally:
        if json_temp is not None:
            json_temp.unlink(missing_ok=True)
        if markdown_temp is not None:
            markdown_temp.unlink(missing_ok=True)
        if current_temp is not None:
            current_temp.unlink(missing_ok=True)

    for old_generation in generation_root.iterdir():
        if old_generation != generation_dir and old_generation.is_dir():
            try:
                _remove_generation(old_generation)
            except OSError:
                pass
    return {"json_path": str(json_path), "markdown_path": str(markdown_path), "payload_digest": digest}


def cleanup_reports(report_dir: str | Path, retention_days: int, today: date) -> int:
    directory = Path(report_dir)
    if not directory.is_dir():
        return 0
    cutoff = today - timedelta(days=retention_days)
    deleted = 0
    expired_dates: set[date] = set()
    for path in directory.iterdir():
        if path.suffix not in {".json", ".md"}:
            continue
        try:
            report_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if report_date < cutoff:
            expired_dates.add(report_date)
    for report_date in expired_dates:
        (directory / f".{report_date.isoformat()}.current").unlink(missing_ok=True)
        for suffix in (".json", ".md"):
            path = directory / f"{report_date.isoformat()}{suffix}"
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
                deleted += 1
        _remove_generation(directory / f".{report_date.isoformat()}.generations")
    stale_temp_cutoff = datetime.now().timestamp() - 24 * 60 * 60
    for path in directory.glob(".ops-report-*.tmp"):
        try:
            if path.stat().st_mtime < stale_temp_cutoff:
                path.unlink()
        except FileNotFoundError:
            continue
    for generation_root in directory.glob(".*.generations"):
        try:
            report_date = date.fromisoformat(generation_root.name[1:].removesuffix(".generations"))
        except ValueError:
            continue
        current_link = directory / f".{report_date.isoformat()}.current"
        current_target = os.readlink(current_link) if current_link.is_symlink() else ""
        for generation in generation_root.iterdir():
            if not generation.is_dir():
                continue
            relative = str(generation.relative_to(directory))
            try:
                stale = generation.stat().st_mtime < stale_temp_cutoff
            except FileNotFoundError:
                continue
            if relative != current_target and stale:
                _remove_generation(generation)
    return deleted
