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


def test_remote_ai_node_metrics_uses_managed_node(monkeypatch):
    from app.web import metrics

    class RemoteNode:
        is_remote = True

        def read_metrics_payload(self, metrics_url, timeout_seconds):
            assert metrics_url.endswith("/debug/vars")
            assert timeout_seconds >= 1
            return {
                "stats": {
                    "inbound": {"panel-27166": {"uplink": 7, "downlink": 11}},
                    "outbound": {"direct": {"uplink": 13, "downlink": 17}},
                }
            }

    monkeypatch.setattr(metrics, "_ai_node_controller", lambda: RemoteNode())

    assert metrics._read_ai_node_metrics() == {
        "available": 1,
        "received": 7,
        "sent": 11,
        "egress_received": 17,
        "egress_sent": 13,
    }


def test_remote_ai_destination_metrics_reads_managed_log(monkeypatch):
    from app.web import metrics

    class RemoteNode:
        is_remote = True

        def display_target(self):
            return "root@example.com"

        def read_access_log_delta(self, inode, offset, since_epoch=None):
            assert inode is None
            assert offset == 0
            assert since_epoch is not None
            return {
                "exists": True,
                "inode": "42",
                "offset": 120,
                "data": "accepted tcp:api.openai.com:443 [direct]\n",
            }

    monkeypatch.setattr(metrics, "_ai_node_controller", lambda: RemoteNode())
    monkeypatch.setattr(metrics, "AI_NODE_ACCESS_LOG_PATH", "/var/log/xray/ai-access.log")
    monkeypatch.setattr(metrics, "_FALLBACK_METRICS_STATE", metrics._new_metrics_state())

    result = metrics._read_ai_destination_metrics()

    assert result["available"] == 1
    assert result["requests"][0]["domain"] == "api.openai.com"
    assert result["requests"][0]["port"] == "443"


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
