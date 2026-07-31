from datetime import datetime, timedelta, timezone

from components.xray_ops.rules import classify_report

START = datetime(2030, 1, 2, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _series(values, labels=None):
    return [
        {"metric": labels or {}, "values": [[START.timestamp() + i * 30, str(value)] for i, value in enumerate(values)]}
    ]


def _metrics(running=1, traffic=0, *, scrape=True):
    values = [running] * 20
    metrics = {
        "xray_panel_data_plane_running": _series(values, {"role": "control_plane"}),
        "xray_panel_ai_node_running": _series(values, {"role": "control_plane"}),
        "xray_panel_port_traffic_bytes_total": _series([traffic] * 20),
        "xray_panel_port_connections_total": _series([0] * 20),
    }
    if scrape:
        metrics["up"] = [
            *_series([1] * 20, {"node_role": "normal_data_plane"}),
            *_series([1] * 20, {"node_role": "ai_data_plane"}),
        ]
    return metrics


def test_scrape_gap_is_unknown_even_if_last_values_are_healthy():
    result = classify_report(window_start=START, window_end=END, prometheus=_metrics(scrape=False))
    assert {node["status"] for node in result["nodes"]} == {"unknown"}
    assert all("prometheus:target_scrape" in node["telemetry"]["missing_sources"] for node in result["nodes"])


def test_no_demand_is_not_a_fault():
    result = classify_report(window_start=START, window_end=END, prometheus=_metrics())
    assert result["overall_status"] == "normal"
    traffic_by_role = {node["node_role"]: node["traffic"]["status"] for node in result["nodes"]}
    assert traffic_by_role == {
        "normal_data_plane": "no_observed_demand",
        "ai_data_plane": "unknown",
    }


def test_prometheus_down_samples_confirm_fault_without_sqlite_inputs():
    metrics = _metrics()
    metrics["xray_panel_data_plane_running"] = _series([1, 0, 0] + [1] * 17, {"role": "control_plane"})
    result = classify_report(
        window_start=START,
        window_end=END,
        prometheus=metrics,
        events=[{"event_type": "accepted"}],
        samples=[{"service_running": True}],
        gaps=[{"source": "log:access"}],
        collection_runs=[],
        rollups=[{"accepted_count": 99}],
    )
    normal = next(node for node in result["nodes"] if node["node_role"] == "normal_data_plane")
    assert normal["status"] == "fault"
    assert any(rule["rule_id"] == "OPS-F001" for rule in normal["matched_rules"])
    assert all("log" not in item["source"] for item in result["evidence"])


def test_local_ai_route_history_is_preserved():
    result = classify_report(
        window_start=START,
        window_end=END,
        prometheus=_metrics(),
        route_events=[{"observed_at": START.isoformat(), "status": "fallback", "fallback": True}],
    )
    ai = next(node for node in result["nodes"] if node["node_role"] == "ai_data_plane")
    assert ai["status"] == "fault"
    assert any(rule["rule_id"] == "OPS-F004" for rule in ai["matched_rules"])
