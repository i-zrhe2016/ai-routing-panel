from datetime import datetime, timedelta, timezone

from components.xray_ops.rules import classify_report

START = datetime(2030, 1, 2, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _series(values, labels=None):
    return [
        {"metric": labels or {}, "values": [[START.timestamp() + i * 30, str(value)] for i, value in enumerate(values)]}
    ]


def _network_series(role, direction, values, device="eth0"):
    metric = "node_network_receive_bytes_total" if direction == "receive" else "node_network_transmit_bytes_total"
    return metric, _series(
        values,
        {
            "node_role": role,
            "job": "data-plane-node",
            "environment": "production",
            "device": device,
        },
    )


def _metrics(running=1, traffic=0, *, scrape=True):
    values = [running] * 20
    normal_rx_metric, normal_rx = _network_series("normal_data_plane", "receive", [0] * 20)
    normal_tx_metric, normal_tx = _network_series("normal_data_plane", "transmit", [0] * 20)
    _ai_rx_metric, ai_rx = _network_series("ai_data_plane", "receive", [0] * 20)
    _ai_tx_metric, ai_tx = _network_series("ai_data_plane", "transmit", [0] * 20)
    metrics = {
        "xray_panel_data_plane_running": _series(values, {"role": "control_plane"}),
        "xray_panel_ai_node_running": _series(values, {"role": "control_plane"}),
        "xray_panel_port_traffic_bytes_total": _series([traffic] * 20),
        "xray_panel_port_connections_total": _series([0] * 20),
        normal_rx_metric: [*normal_rx, *ai_rx],
        normal_tx_metric: [*normal_tx, *ai_tx],
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
        "ai_data_plane": "no_observed_demand",
    }


def test_network_traffic_totals_are_reported_per_data_plane():
    metrics = _metrics()
    normal_rx_metric, normal_rx = _network_series("normal_data_plane", "receive", [100, 150, 180] + [180] * 17)
    normal_tx_metric, normal_tx = _network_series("normal_data_plane", "transmit", [10, 30, 70] + [70] * 17)
    _ai_rx_metric, ai_rx = _network_series("ai_data_plane", "receive", [1000, 1100, 1200] + [1200] * 17)
    _ai_tx_metric, ai_tx = _network_series("ai_data_plane", "transmit", [2000, 2300, 2600] + [2600] * 17)
    _ignored_metric, ignored_loopback = _network_series("ai_data_plane", "receive", [0, 999, 999] + [999] * 17, "lo")
    metrics[normal_rx_metric] = [*normal_rx, *ai_rx, *ignored_loopback]
    metrics[normal_tx_metric] = [*normal_tx, *ai_tx]

    result = classify_report(window_start=START, window_end=END, prometheus=metrics)
    by_role = {node["node_role"]: node["traffic"] for node in result["nodes"]}

    assert by_role["normal_data_plane"]["network_received_bytes"] == 80
    assert by_role["normal_data_plane"]["network_transmitted_bytes"] == 60
    assert by_role["normal_data_plane"]["network_total_bytes"] == 140
    assert by_role["normal_data_plane"]["network_devices"] == ["eth0"]
    assert by_role["ai_data_plane"]["network_received_bytes"] == 200
    assert by_role["ai_data_plane"]["network_transmitted_bytes"] == 600
    assert by_role["ai_data_plane"]["network_total_bytes"] == 800
    assert by_role["ai_data_plane"]["network_devices"] == ["eth0"]
    assert by_role["ai_data_plane"]["status"] == "demand_observed"


def test_xray_stats_samples_add_sanitized_user_and_inbound_attribution():
    samples = []
    for observed_at, user_uplink, user_downlink, inbound_uplink, inbound_downlink in (
        (START, 100, 300, 100, 300),
        (START + timedelta(minutes=5), 700, 1300, 700, 1300),
    ):
        for entity_type, entity_ref, uplink, downlink in (
            ("user", "usr-0123456789abcdef", user_uplink, user_downlink),
            ("inbound", "inb-fedcba9876543210", inbound_uplink, inbound_downlink),
        ):
            for direction, value in (("uplink", uplink), ("downlink", downlink)):
                samples.append(
                    {
                        "sample_id": f"{entity_type}-{direction}-{observed_at.timestamp()}",
                        "node_role": "ai_data_plane",
                        "source_id": "ai-xray",
                        "observed_at": observed_at.isoformat(),
                        "collected_at": observed_at.isoformat(),
                        "entity_type": entity_type,
                        "entity_ref": entity_ref,
                        "direction": direction,
                        "value": value,
                    }
                )

    result = classify_report(window_start=START, window_end=END, prometheus=_metrics(), xray_traffic_samples=samples)
    ai = next(node for node in result["nodes"] if node["node_role"] == "ai_data_plane")

    assert ai["traffic"]["attribution"] == [
        {
            "source_id": "ai-xray",
            "entity_type": "user",
            "entity_ref": "usr-0123456789abcdef",
            "uplink_bytes": 600,
            "downlink_bytes": 1000,
            "total_bytes": 1600,
            "sample_count": 2,
            "first_sample_at": "2030-01-02T00:00:00.000000Z",
            "last_sample_at": "2030-01-02T00:05:00.000000Z",
            "counter_resets": 0,
        },
        {
            "source_id": "ai-xray",
            "entity_type": "inbound",
            "entity_ref": "inb-fedcba9876543210",
            "uplink_bytes": 600,
            "downlink_bytes": 1000,
            "total_bytes": 1600,
            "sample_count": 2,
            "first_sample_at": "2030-01-02T00:00:00.000000Z",
            "last_sample_at": "2030-01-02T00:05:00.000000Z",
            "counter_resets": 0,
        },
    ]


def test_xray_attribution_handles_counter_resets():
    samples = []
    for index, value in enumerate((1000, 200, 450)):
        observed_at = START + timedelta(minutes=index)
        samples.append(
            {
                "sample_id": f"reset-{index}",
                "node_role": "ai_data_plane",
                "source_id": "ai-xray",
                "observed_at": observed_at.isoformat(),
                "collected_at": observed_at.isoformat(),
                "entity_type": "user",
                "entity_ref": "usr-0123456789abcdef",
                "direction": "uplink",
                "value": value,
            }
        )

    result = classify_report(window_start=START, window_end=END, prometheus=_metrics(), xray_traffic_samples=samples)
    ai = next(node for node in result["nodes"] if node["node_role"] == "ai_data_plane")

    assert ai["traffic"]["attribution"][0]["uplink_bytes"] == 450
    assert ai["traffic"]["attribution"][0]["counter_resets"] == 1


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
