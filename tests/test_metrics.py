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


def test_parse_ai_access_line_extracts_destination_and_ignores_non_access_lines():
    from app.web.metrics import _parse_ai_access_line

    event = _parse_ai_access_line(
        "2026/08/31 15:30:00.123456 from 127.0.0.1:1234 "
        "accepted tcp:API.OpenAI.COM:443 [direct] email:"
    )

    assert event["domain"] == "api.openai.com"
    assert event["port"] == "443"
    assert event["network"] == "tcp"
    assert event["timestamp"] > 0
    assert _parse_ai_access_line("received request for tcp:api.openai.com:443") is None
    assert _parse_ai_access_line("accepted tcp:missing-port [direct]") is None


def test_parse_ai_access_line_supports_udp_and_bracketed_ipv6():
    from app.web.metrics import _parse_ai_access_line

    event = _parse_ai_access_line("accepted udp:[2001:db8::1]:53", fallback_timestamp=12)

    assert event == {
        "timestamp": 12.0,
        "domain": "2001:db8::1",
        "port": "53",
        "network": "udp",
    }


def test_summarize_ai_destination_events_limits_labels_and_reports_rates():
    from app.web.metrics import _summarize_ai_destination_events

    events = [
        {"timestamp": 995, "domain": "a.example", "port": "443", "network": "tcp"},
        {"timestamp": 994, "domain": "a.example", "port": "443", "network": "tcp"},
        {"timestamp": 993, "domain": "a.example", "port": "443", "network": "tcp"},
        {"timestamp": 992, "domain": "b.example", "port": "443", "network": "tcp"},
        {"timestamp": 991, "domain": "b.example", "port": "443", "network": "tcp"},
        {"timestamp": 990, "domain": "c.example", "port": "80", "network": "tcp"},
        {"timestamp": 800, "domain": "old.example", "port": "443", "network": "tcp"},
    ]

    result = _summarize_ai_destination_events(events, 1000, 100, 2)

    assert result["available"] == 1
    assert result["other_requests"] == 1
    assert result["requests"] == [
        {
            "domain": "a.example",
            "port": "443",
            "network": "tcp",
            "requests": 3,
            "requests_per_second": 0.03,
            "last_seen": 995,
        },
        {
            "domain": "b.example",
            "port": "443",
            "network": "tcp",
            "requests": 2,
            "requests_per_second": 0.02,
            "last_seen": 992,
        },
    ]
