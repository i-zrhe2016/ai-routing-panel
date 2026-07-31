import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from components.xray_ops.prometheus import PrometheusClient, PrometheusError


START = datetime(2030, 1, 2, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_query_range_builds_read_only_request(monkeypatch):
    captured = {}
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"role": "normal"}, "values": [[START.timestamp(), "1"]]}],
        },
    }

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr("components.xray_ops.prometheus.urllib.request.urlopen", fake_urlopen)
    client = PrometheusClient("http://prometheus.internal:9090/", timeout_seconds=7, bearer_token="secret-token")

    result = client.query_range("up", START, END, step_seconds=30)

    assert result == payload["data"]["result"]
    assert captured["timeout"] == 7
    assert captured["request"].method == "GET"
    assert captured["request"].get_header("Authorization") == "Bearer secret-token"
    assert "/api/v1/query_range?" in captured["request"].full_url
    assert "query=up" in captured["request"].full_url
    assert "step=30" in captured["request"].full_url


def test_collect_isolates_metric_failures_and_populates_unconfigured_metrics(monkeypatch):
    def fake_query(self, query, _start, _end, step_seconds=30):
        assert step_seconds == 30
        if query == "metric_bad":
            raise PrometheusError("prometheus_timeout", "timed out")
        return [{"metric": {}, "values": [[START.timestamp(), "1"]]}]

    monkeypatch.setattr(PrometheusClient, "query_range", fake_query)
    configured = PrometheusClient("http://prometheus.internal:9090").collect(
        START,
        END,
        metrics=("metric_ok", "metric_bad"),
    )

    assert len(configured.metrics["metric_ok"]) == 1
    assert configured.metrics["metric_bad"] == []
    assert next(item for item in configured.sources if item["source"].endswith("metric_bad"))["error_class"] == (
        "prometheus_timeout"
    )

    unconfigured = PrometheusClient("").collect(START, END, metrics=("metric_ok", "metric_bad"))
    assert unconfigured.metrics == {"metric_ok": [], "metric_bad": []}
    assert all(not item["configured"] for item in unconfigured.sources)


def test_query_errors_are_classified_and_redacted(monkeypatch):
    error = urllib.error.HTTPError(
        "http://prometheus.internal:9090",
        500,
        "error",
        {},
        io.BytesIO(b"authorization=super-secret"),
    )

    def fake_urlopen(_request, timeout):
        assert timeout == 10
        raise error

    monkeypatch.setattr("components.xray_ops.prometheus.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(PrometheusError) as caught:
        PrometheusClient("http://prometheus.internal:9090").query_range("up", START, END)

    assert caught.value.error_class == "prometheus_http_error"
    assert "super-secret" not in caught.value.detail
    assert "[REDACTED]" in caught.value.detail


@pytest.mark.parametrize("url", ["prometheus.internal:9090", "file:///etc/passwd", ""])
def test_query_rejects_non_http_urls(url):
    with pytest.raises(PrometheusError, match="HTTP or HTTPS") as caught:
        PrometheusClient(url).query_range("up", START, END)

    assert caught.value.error_class == "prometheus_invalid_url"
