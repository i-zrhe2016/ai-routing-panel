"""Small read-only Prometheus HTTP API client for daily evidence queries."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .redaction import redact_text


DEFAULT_METRICS = (
    # Prometheus scrape health is required to distinguish healthy values from gaps.
    "up",
    "xray_panel_data_plane_running",
    "xray_panel_ai_node_running",
    "xray_panel_port_reachable",
    "xray_panel_port_probe_timestamp_seconds",
    "xray_panel_port_traffic_bytes_total",
    "xray_panel_port_connections_total",
    "xray_panel_dns_failover_target_info",
    "xray_panel_dns_failover_last_probe_healthy",
    "xray_panel_dns_failover_consecutive_failures",
    # node_exporter and cAdvisor resource evidence.
    "node_cpu_seconds_total",
    "node_memory_MemAvailable_bytes",
    "node_memory_MemTotal_bytes",
    "node_filesystem_avail_bytes",
    "node_filesystem_size_bytes",
    "node_network_receive_bytes_total",
    "node_network_transmit_bytes_total",
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "container_spec_memory_limit_bytes",
    "container_last_seen",
)


class PrometheusError(RuntimeError):
    def __init__(self, error_class: str, detail: str):
        super().__init__(detail)
        self.error_class = error_class
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PrometheusResult:
    metrics: dict[str, list[dict[str, Any]]]
    sources: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PrometheusClient:
    base_url: str
    timeout_seconds: int = 10
    bearer_token: str = ""

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        parsed_base = urllib.parse.urlsplit(self.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise PrometheusError("prometheus_invalid_url", "Prometheus URL must use HTTP or HTTPS")
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": f"{start.timestamp():.6f}",
                "end": f"{end.timestamp():.6f}",
                "step": str(step_seconds),
            }
        )
        endpoint = f"{self.base_url.rstrip('/')}/api/v1/query_range?{params}"
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = redact_text(exc.read().decode("utf-8", errors="replace")[:500]).text
            raise PrometheusError("prometheus_http_error", f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise PrometheusError("prometheus_unreachable", redact_text(str(exc.reason)).text) from exc
        except TimeoutError as exc:
            raise PrometheusError("prometheus_timeout", "Prometheus request timed out") from exc
        except OSError as exc:
            raise PrometheusError("prometheus_transport_error", redact_text(str(exc)).text[:500]) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PrometheusError("prometheus_invalid_json", "Prometheus returned invalid JSON") from exc

        if not isinstance(payload, dict) or payload.get("status") != "success":
            detail = redact_text(str(payload.get("error", "query failed")) if isinstance(payload, dict) else "query failed").text
            raise PrometheusError("prometheus_query_failed", detail[:500])
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise PrometheusError("prometheus_invalid_result", "Prometheus response data is not an object")
        if data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
            raise PrometheusError("prometheus_invalid_result", "Prometheus query_range did not return a matrix")
        return [item for item in data["result"] if isinstance(item, dict)]

    def collect(
        self,
        start: datetime,
        end: datetime,
        metrics: tuple[str, ...] = DEFAULT_METRICS,
    ) -> PrometheusResult:
        output: dict[str, list[dict[str, Any]]] = {}
        sources: list[dict[str, Any]] = []
        if not self.base_url:
            for metric in metrics:
                output[metric] = []
                sources.append(
                    {
                        "source": f"prometheus:{metric}",
                        "configured": False,
                        "success": False,
                        "error_class": "prometheus_not_configured",
                        "series": 0,
                    }
                )
            return PrometheusResult(output, sources)
        for metric in metrics:
            try:
                result = self.query_range(metric, start, end)
            except PrometheusError as exc:
                output[metric] = []
                sources.append(
                    {
                        "source": f"prometheus:{metric}",
                        "configured": True,
                        "success": False,
                        "error_class": exc.error_class,
                        "detail": exc.detail[:500],
                        "series": 0,
                    }
                )
            else:
                output[metric] = result
                sources.append(
                    {
                        "source": f"prometheus:{metric}",
                        "configured": True,
                        "success": True,
                        "error_class": "",
                        "series": len(result),
                    }
                )
        return PrometheusResult(output, sources)
