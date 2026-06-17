import io
import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
