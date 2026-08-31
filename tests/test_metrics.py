def test_parse_ai_node_metrics_keeps_only_ai_inbound_and_direct_egress_bytes():
    from app.web.metrics import _parse_ai_node_metrics_payload

    result = _parse_ai_node_metrics_payload(
        {
            "stats": {
                "inbound": {
                    "panel-27166": {"uplink": 120, "downlink": 340},
                    "other": {"uplink": 999, "downlink": 999},
                },
                "outbound": {
                    "direct": {"uplink": 500, "downlink": 700},
                    "ignored": {"uplink": 900, "downlink": 900},
                },
            }
        }
    )

    assert result == {
        "available": 1,
        "received": 120,
        "sent": 340,
        "egress_received": 700,
        "egress_sent": 500,
    }


def test_parse_ai_node_metrics_rejects_missing_stats():
    from app.web.metrics import _parse_ai_node_metrics_payload

    assert _parse_ai_node_metrics_payload({}) is None
