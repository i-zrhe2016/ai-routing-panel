import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.xray import google_search_mcp


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        if isinstance(self._payload, str):
            return self._payload.encode("utf-8")
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_args(**overrides):
    base = {
        "default_min_hits": 1,
        "google_search_url": "https://www.google.com/search",
        "google_user_agent": "Mozilla/5.0 Test Browser",
        "google_timeout_seconds": 10,
        "google_num_results": 5,
        "google_gl": "",
        "google_hl": "",
        "google_accept_language": "en-US,en;q=0.9",
        "google_safe": "off",
        "google_query_template": '"{domain}"',
        "openrouter_api_key": "secret-openrouter-key",
        "openrouter_base_url": "https://openrouter.ai/api/v1/chat/completions",
        "openrouter_model": "openai/gpt-5-nano",
        "openrouter_timeout_seconds": 30,
        "openrouter_referer": "https://panel.example.com",
        "openrouter_title": "xray-routing-panel tests",
        "report_path": Path("/tmp/latest.json"),
        "classification_state_path": Path("/tmp/ai-domain-decisions.json"),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class GoogleSearchMcpTest(unittest.TestCase):
    def test_collect_uncategorized_domains_returns_missing_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            report_path = tmpdir / "latest.json"
            decisions_path = tmpdir / "decisions.json"
            report_path.write_text(
                json.dumps(
                    {
                        "domains": [
                            {"domain": "unknown.example", "hits": 7, "protocols": ["tcp"], "classification": "unknown"},
                            {"domain": "missing.example", "hits": 5, "protocols": ["tcp"], "classification": "unknown"},
                            {"domain": "known.example", "hits": 9, "protocols": ["tcp"], "classification": "unknown"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            decisions_path.write_text(
                json.dumps(
                    {
                        "domains": {
                            "unknown.example": {"classification": "unknown", "reason": "pending"},
                            "known.example": {"classification": "not_ai", "reason": "already classified"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            service = google_search_mcp.GoogleOpenRouterDomainClassifierService(_make_args())
            result = service.collect_uncategorized_domains(
                reportPath=str(report_path),
                classificationStatePath=str(decisions_path),
            )

            self.assertEqual(result["domain_count"], 2)
            self.assertEqual(
                [item["domain"] for item in result["domains"]],
                ["unknown.example", "missing.example"],
            )
            self.assertEqual(result["domains"][0]["existing_classification"], "unknown")
            self.assertEqual(result["domains"][1]["existing_classification"], "missing")

    def test_collect_uncategorized_domains_uses_service_default_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            report_path = tmpdir / "latest.json"
            decisions_path = tmpdir / "decisions.json"
            report_path.write_text(
                json.dumps({"domains": [{"domain": "default.example", "hits": 2, "protocols": ["tcp"]}]}),
                encoding="utf-8",
            )
            decisions_path.write_text(json.dumps({"domains": {}}), encoding="utf-8")

            service = google_search_mcp.GoogleOpenRouterDomainClassifierService(
                _make_args(report_path=report_path, classification_state_path=decisions_path)
            )
            result = service.collect_uncategorized_domains()

            self.assertEqual(result["report_path"], str(report_path))
            self.assertEqual(result["classification_state_path"], str(decisions_path))
            self.assertEqual(result["domains"][0]["domain"], "default.example")

    def test_parse_args_loads_root_and_xray_env_files(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(os.environ, {}, clear=True):
            tmpdir = Path(tmpdir)
            root_env = tmpdir / ".env"
            xray_env = tmpdir / "app-xray.env"
            root_env.write_text(
                "OPENROUTER_API_KEY=key-from-root\n",
                encoding="utf-8",
            )
            xray_env.write_text(
                "OPENROUTER_MODEL=openai/gpt-5-nano\nGOOGLE_SEARCH_NUM_RESULTS=7\nGOOGLE_SEARCH_USER_AGENT=Custom Agent\n",
                encoding="utf-8",
            )

            with mock.patch.object(google_search_mcp, "ROOT_ENV_PATH", root_env), mock.patch.object(
                google_search_mcp, "XRAY_ENV_PATH", xray_env
            ):
                args = google_search_mcp.parse_args([])

            self.assertEqual(args.openrouter_api_key, "key-from-root")
            self.assertEqual(args.openrouter_model, "openai/gpt-5-nano")
            self.assertEqual(args.google_num_results, 7)
            self.assertEqual(args.google_user_agent, "Custom Agent")

    @mock.patch("urllib.request.urlopen")
    def test_search_domains_with_google_uses_google_html_endpoint(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeHttpResponse(
            """
            <html><body>
            <a href="/url?q=https://example.com&amp;sa=U"><h3>Example AI</h3></a>
            <div class="VwiC3b">AI product homepage</div>
            </body></html>
            """
        )

        service = google_search_mcp.GoogleOpenRouterDomainClassifierService(_make_args())
        result = service.search_domains_with_google(["example.com"], numResults=3)

        self.assertEqual(result["domain_count"], 1)
        self.assertEqual(result["results"][0]["results"][0]["title"], "Example AI")
        self.assertEqual(result["results"][0]["results"][0]["snippet"], "AI product homepage")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("https://www.google.com/search?", request.full_url)
        self.assertIn("num=3", request.full_url)
        self.assertIn("q=%22example.com%22", request.full_url)
        self.assertEqual(request.get_header("User-agent"), "Mozilla/5.0 Test Browser")

    def test_parse_google_search_html_extracts_direct_results(self):
        parsed = google_search_mcp.parse_google_search_html(
            "example.com",
            '"example.com"',
            """
            <html><body>
            <a href="https://support.google.com/websearch">Google Help</a>
            <a href="/url?q=https://example.com&amp;sa=U"><h3>Example AI</h3></a>
            <div class="VwiC3b">Official AI coding assistant</div>
            <a href="/url?url=https://docs.example.com&amp;sa=U">Docs</a>
            <span class="aCOpRe">Documentation for the platform</span>
            </body></html>
            """,
            5,
        )

        self.assertEqual(len(parsed["results"]), 2)
        self.assertEqual(parsed["results"][0]["link"], "https://example.com")
        self.assertEqual(parsed["results"][1]["link"], "https://docs.example.com")
        self.assertEqual(parsed["results"][1]["snippet"], "Documentation for the platform")

    @mock.patch("urllib.request.urlopen")
    def test_classify_domain_with_google_results_via_openrouter_uses_chat_completions(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "domain": "example.com",
                                    "classification": "ai",
                                    "reason": "official site describes an AI coding assistant",
                                    "evidence": [{"rank": 1, "signal": "homepage says AI coding assistant"}],
                                }
                            )
                        }
                    }
                ]
            }
        )

        result = google_search_mcp.classify_domain_with_google_results_via_openrouter(
            "example.com",
            {
                "query": '"example.com"',
                "results": [{"rank": 1, "title": "Example AI", "link": "https://example.com", "snippet": "AI coding"}],
            },
            _make_args(),
        )

        self.assertEqual(result["classification"], "ai")
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-openrouter-key")
        self.assertEqual(request.get_header("Http-referer"), "https://panel.example.com")
        self.assertEqual(request.get_header("X-openrouter-title"), "xray-routing-panel tests")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "openai/gpt-5-nano")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")

    def test_classify_domains_with_google_writes_back_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            report_path = tmpdir / "latest.json"
            decisions_path = tmpdir / "decisions.json"
            report_path.write_text(
                json.dumps(
                    {
                        "domains": [
                            {"domain": "example.com", "hits": 3, "protocols": ["tcp"], "classification": "unknown"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            decisions_path.write_text(json.dumps({"domains": {}}), encoding="utf-8")

            service = google_search_mcp.GoogleOpenRouterDomainClassifierService(_make_args())
            with mock.patch.object(
                service,
                "google_search_domain",
                return_value={
                    "domain": "example.com",
                    "query": '"example.com"',
                    "total_results": "10",
                    "search_time_seconds": 0.1,
                    "results": [
                        {
                            "rank": 1,
                            "title": "Example AI",
                            "link": "https://example.com",
                            "display_link": "example.com",
                            "snippet": "AI coding assistant",
                        }
                    ],
                },
            ), mock.patch(
                "app.xray.google_search_mcp.classify_domain_with_google_results_via_openrouter",
                return_value={
                    "domain": "example.com",
                    "classification": "ai",
                    "reason": "official site says AI coding assistant",
                    "evidence": [{"rank": 1, "signal": "homepage describes an AI coding assistant"}],
                },
            ):
                result = service.classify_domains_with_google(
                    reportPath=str(report_path),
                    classificationStatePath=str(decisions_path),
                    writeBack=True,
                )

            self.assertEqual(result["requested_domain_count"], 1)
            self.assertEqual(result["updated_count"], 1)
            self.assertEqual(result["results"][0]["classification"], "ai")

            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
            saved = decisions["domains"]["example.com"]
            self.assertEqual(saved["classification"], "ai")
            self.assertEqual(saved["source"], "google_openrouter_mcp")
            self.assertEqual(saved["model"], "openai/gpt-5-nano")

    def test_mcp_tools_call_returns_text_payload(self):
        service = google_search_mcp.GoogleOpenRouterDomainClassifierService(_make_args())
        transport = mock.Mock()
        server = google_search_mcp.GoogleOpenRouterDomainClassifierMcpServer(service, transport, mock.Mock())
        with mock.patch.object(
            service,
            "call_tool",
            return_value={"domain_count": 1, "domains": [{"domain": "example.com"}]},
        ):
            response = server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "collect_uncategorized_domains",
                        "arguments": {},
                    },
                }
            )

        self.assertEqual(response["id"], 7)
        content = response["result"]["content"][0]["text"]
        payload = json.loads(content)
        self.assertEqual(payload["domain_count"], 1)
        self.assertEqual(payload["domains"][0]["domain"], "example.com")


if __name__ == "__main__":
    unittest.main()
