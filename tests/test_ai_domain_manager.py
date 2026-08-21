import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.xray import ai_domain_manager


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AiDomainManagerTest(unittest.TestCase):
    def test_read_ai_routing_manual_mode_defaults_and_reads_persisted_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "panel.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO app_state (key, value) VALUES (?, ?)",
                    ("ai_routing_manual_mode", "forced_fallback"),
                )
                conn.commit()

            self.assertEqual(ai_domain_manager.read_ai_routing_manual_mode(db_path), "forced_fallback")
            self.assertEqual(ai_domain_manager.read_ai_routing_manual_mode(Path(tmpdir) / "missing.db"), "auto")

    def test_gemini_session_domains_are_forced_to_ai_route(self):
        for domain in (
            "gemini.google.com",
            "chat.gemini.google.com",
            "accounts.google.com",
            "generativelanguage.googleapis.com",
            "scholar.google.com",
        ):
            self.assertTrue(ai_domain_manager.matches_forced_ai_route_domain(domain))

    def test_default_ai_redirect_uses_ipv4(self):
        payload, reason = ai_domain_manager.render_proxy_template(
            Path("/does/not/exist"),
            {
                "upstream_host": "nat.qq.pw",
                "upstream_port": 27166,
            },
            None,
        )

        self.assertEqual(reason, "builtin_freedom_redirect")
        self.assertEqual(payload["outbounds"][0]["settings"]["domainStrategy"], "UseIPv4")

    def test_resolve_openai_endpoint_defaults_to_responses_for_remote(self):
        endpoint, api_style = ai_domain_manager.resolve_openai_endpoint("https://api.openai.com")
        self.assertEqual(endpoint, "https://api.openai.com/v1/responses")
        self.assertEqual(api_style, "responses")

    def test_resolve_openai_endpoint_defaults_to_chat_completions_for_local(self):
        endpoint, api_style = ai_domain_manager.resolve_openai_endpoint("http://127.0.0.1:11434/v1")
        self.assertEqual(endpoint, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(api_style, "chat_completions")

    def test_local_openai_base_url_detection(self):
        self.assertTrue(ai_domain_manager.is_local_openai_base_url("http://127.0.0.1:11434/v1"))
        self.assertTrue(ai_domain_manager.is_local_openai_base_url("http://192.168.1.10:8000"))
        self.assertFalse(ai_domain_manager.is_local_openai_base_url("https://api.openai.com/v1/responses"))

    @mock.patch("urllib.request.urlopen")
    def test_classify_domains_via_chat_completions_without_api_key(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "domain": "chatgpt.com",
                                        "classification": "ai",
                                        "reason": "AI chat product",
                                    },
                                    {
                                        "domain": "example.com",
                                        "classification": "not_ai",
                                        "reason": "general website",
                                    },
                                ]
                            )
                        }
                    }
                ]
            }
        )

        result = ai_domain_manager.classify_domains_via_openai(
            ["chatgpt.com", "example.com"],
            api_key="",
            model="qwen2.5",
            base_url="http://127.0.0.1:11434/v1",
            timeout_seconds=5,
            allow_no_key=True,
        )

        self.assertEqual(result["chatgpt.com"]["classification"], "ai")
        self.assertEqual(result["example.com"]["classification"], "not_ai")
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertIsNone(request.get_header("Authorization"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")

    @mock.patch("urllib.request.urlopen")
    def test_classify_domains_via_responses_keeps_authorization_header(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeHttpResponse(
            {
                "output_text": json.dumps(
                    [
                        {
                            "domain": "openai.com",
                            "classification": "ai",
                            "reason": "AI model provider",
                        }
                    ]
                )
            }
        )

        result = ai_domain_manager.classify_domains_via_openai(
            ["openai.com"],
            api_key="secret-key",
            model="gpt-5.5",
            base_url="https://api.openai.com",
            timeout_seconds=5,
        )

        self.assertEqual(result["openai.com"]["classification"], "ai")
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("input", payload)

    @mock.patch("app.xray.ai_domain_manager.classify_domains_via_openai")
    def test_classify_pending_domains_keeps_pending_when_openai_unavailable(self, mocked_openai):
        mocked_openai.side_effect = RuntimeError("openai http 401")
        decisions = {"domains": {}}
        observed_domains = {"unknown.example"}
        with tempfile.TemporaryDirectory() as tmpdir:
            decisions_path = f"{tmpdir}/ai-domain-decisions.json"
            args = mock.Mock(
                batch_size=50,
                codex_classifier_enabled=False,
                openai_classifier_enabled=True,
                openai_api_key="bad-key",
                openai_model="openai/gpt-5-nano",
                openai_base_url="https://openrouter.ai/api/v1/chat/completions",
                openai_timeout_seconds=45,
                openai_allow_no_key=False,
            )

            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                pending = ai_domain_manager.classify_pending_domains(
                    decisions,
                    decisions_path,
                    observed_domains,
                    args,
                )

        self.assertEqual(pending, ["unknown.example"])
        self.assertEqual(decisions["domains"], {})
        self.assertIn("openai classifier unavailable", stderr.getvalue())

    def test_probe_uses_reality_callback_when_candidate_has_sni(self):
        controller = mock.Mock()
        controller.probe_reality_endpoint.return_value = {
            "ok": False,
            "error": "TLS handshake failed",
            "method": "reality",
        }

        result = ai_domain_manager.probe_ai_upstream_candidate(
            {
                "upstream_host": "ai.example.com",
                "upstream_port": 443,
                "probe_server_name": "www.example.com",
            },
            2.0,
            probe_controller=controller,
        )

        self.assertFalse(result["is_reachable"])
        self.assertEqual(result["probe_method"], "reality")
        controller.probe_reality_endpoint.assert_called_once_with(
            "ai.example.com",
            443,
            "www.example.com",
            2.0,
        )

    def test_select_ai_target_does_not_call_all_unreachable_on_probe_management_error(self):
        controller = mock.Mock()
        controller.probe_tcp_endpoint.return_value = {
            "ok": False,
            "error": "ssh authentication failed",
            "management_error": True,
            "method": "tcp",
        }

        result = ai_domain_manager.select_ai_target(
            [{"upstream_host": "ai.example.com", "upstream_port": 443}],
            2.0,
            probe_controller=controller,
        )

        self.assertEqual(result["probe_status"], "probe_error")
        self.assertFalse(ai_domain_manager.should_fallback_to_primary_route(result))

    @mock.patch("app.xray.ai_domain_manager.build_data_plane_controller")
    @mock.patch("app.xray.ai_domain_manager.rerender_config")
    @mock.patch("app.xray.ai_domain_manager.probe_ai_upstream_candidate")
    def test_run_once_falls_back_to_primary_route_when_all_ai_upstreams_are_unreachable(
        self,
        mocked_probe,
        mocked_rerender,
        mocked_controller_builder,
    ):
        mocked_probe.return_value = {
            "upstream_host": "ai.example.com",
            "upstream_port": 443,
            "candidate_type": "template",
            "is_reachable": False,
            "failure_reason": "timed out",
            "checked_at": "2026-06-22T00:00:00+00:00",
        }
        mocked_rerender.side_effect = (
            lambda _render_script, _env_file, config_out, _client_out, _share_out, _dynamic_routing_file:
            config_out.write_text("{}", encoding="utf-8")
        )
        mocked_controller_builder.return_value = mock.Mock(
            is_configured=mock.Mock(return_value=False),
            supports_restart=mock.Mock(return_value=False),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_path = root / "access.log"
            log_state_path = root / "log-state.json"
            decisions_path = root / "ai-domain-decisions.json"
            dynamic_routing_path = root / "dynamic-routing.json"
            config_out = root / "config.json"
            client_out = root / "client.json"
            share_out = root / "share.txt"
            report_output_dir = root / "reports"

            decisions_path.write_text(
                json.dumps(
                    {
                        "domains": {
                            "openai.com": {
                                "classification": "ai",
                                "reason": "known ai",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            dynamic_routing_path.write_text('{"stale": true}', encoding="utf-8")

            args = mock.Mock(
                log_state_path=log_state_path,
                log_path=log_path,
                lookback_seconds=3600,
                classification_state_path=decisions_path,
                ai_upstream_candidates=[
                    {
                        "upstream_host": "ai.example.com",
                        "upstream_port": 443,
                        "candidate_type": "template",
                    }
                ],
                ai_upstream_probe_timeout_seconds=2.0,
                panel_db_path=root / "panel.db",
                panel_route_listen_port=None,
                batch_size=50,
                codex_classifier_enabled=False,
                openai_classifier_enabled=False,
                proxy_template_path=root / "missing-ai-proxy-outbound.json",
                dynamic_routing_path=dynamic_routing_path,
                render_script="app.xray.render_config",
                env_file=root / "xray.env",
                config_out=config_out,
                client_out=client_out,
                share_out=share_out,
                restart_command="",
                restart_container_name="",
                docker_timeout_seconds=5,
                report_output_dir=report_output_dir,
            )

            ai_domain_manager.run_once(args)

            self.assertFalse(dynamic_routing_path.exists())
            report = json.loads((report_output_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report["route_status"]["status"], "fallback_to_primary")
            self.assertEqual(report["route_status"]["reason"], "ai_upstream_unreachable")
            self.assertEqual(report["ai_target"]["probe_status"], "all_unreachable")


if __name__ == "__main__":
    unittest.main()
