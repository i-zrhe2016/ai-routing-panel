import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.xray.ai_routing import artifact, classifier, runner
from components.openrouter import OpenRouterConfig


class OpenRouterClassifierTest(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "ai_domain_classifier_provider": "openrouter",
            "openrouter_config": SimpleNamespace(model="openai/gpt-5-nano"),
            "batch_size": 2,
            "codex_classifier_enabled": True,
            "openai_classifier_enabled": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def classify(self, domains, args=None):
        decisions = {"domains": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            pending = classifier.classify_pending_domains(decisions, path, domains, args or self.args())
            saved = json.loads(path.read_text()) if path.exists() else None
        return pending, decisions, saved

    def test_provider_bypasses_legacy_and_persists_actual_model_in_report(self):
        response = SimpleNamespace(
            output={
                "classifications": [
                    {"domain": "research.example", "classification": "ai", "reason": "AI research platform"},
                    {"domain": "store.example", "classification": "not_ai", "reason": "ordinary retail"},
                ]
            },
            model="openai/gpt-5-nano",
            usage={},
        )
        with (
            mock.patch.object(classifier, "OpenRouterClient", create=True) as client,
            mock.patch.object(classifier, "classify_domains_via_codex") as codex,
            mock.patch.object(classifier, "classify_domains_via_openai") as openai,
        ):
            client.return_value.complete.return_value = response
            pending, decisions, saved = self.classify({"research.example", "store.example"})
        self.assertEqual(pending, [])
        codex.assert_not_called()
        openai.assert_not_called()
        self.assertEqual(saved, decisions)
        self.assertEqual(decisions["domains"]["research.example"]["source"], "openrouter")
        self.assertEqual(decisions["domains"]["research.example"]["model"], "openai/gpt-5-nano")
        request = client.return_value.complete.call_args.kwargs
        self.assertEqual(
            request["schema"]["properties"]["classifications"]["items"]["properties"]["classification"]["enum"],
            ["ai", "not_ai"],
        )
        self.assertEqual(
            json.loads(request["messages"][1]["content"])["domains"], ["research.example", "store.example"]
        )
        self.assertIn("classifications", json.loads(request["messages"][1]["content"])["return_format"])
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        report = artifact.build_domain_report(
            {"events": [{"domain": "research.example", "protocol": "tcp", "seen_at": now}]},
            now - timedelta(hours=1),
            now,
            decisions,
            None,
            None,
            {"status": "unchanged"},
        )
        self.assertEqual(report["domains"][0]["source"], "openrouter")
        self.assertEqual(report["domains"][0]["model"], "openai/gpt-5-nano")

    def test_builtin_domains_need_no_provider_request(self):
        with mock.patch.object(classifier, "OpenRouterClient", create=True) as client:
            pending, decisions, saved = self.classify({"api.openai.com", "openrouter.ai"})
        self.assertEqual(pending, [])
        client.assert_not_called()
        self.assertEqual(saved, decisions)
        self.assertEqual(decisions["domains"]["openrouter.ai"]["source"], "builtin")

    def test_failure_preserves_pending_without_fallback_or_raw_error(self):
        with (
            mock.patch.object(classifier, "OpenRouterClient", create=True) as client,
            mock.patch.object(classifier, "classify_domains_via_codex") as codex,
            mock.patch.object(classifier, "classify_domains_via_openai") as openai,
        ):
            client.return_value.complete.side_effect = RuntimeError("secret-key-must-not-leak")
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                pending, decisions, saved = self.classify({"unknown.example"})
        self.assertEqual(pending, ["unknown.example"])
        self.assertEqual(decisions, {"domains": {}})
        self.assertIsNone(saved)
        codex.assert_not_called()
        openai.assert_not_called()
        self.assertIn("openrouter classifier unavailable", stderr.getvalue())
        self.assertNotIn("secret-key-must-not-leak", stderr.getvalue())

    def test_provider_failure_preserves_historical_classifications_and_builtin_rules(self):
        history = {
            "classification": "ai",
            "reason": "previous classification",
            "source": "codex",
            "model": "historical-model",
            "classified_at": "2026-10-01T00:00:00+00:00",
        }
        decisions = {"domains": {"historical.example": history.copy()}}
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(classifier, "OpenRouterClient") as client,
            mock.patch("sys.stderr", io.StringIO()),
        ):
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps(decisions))
            client.return_value.complete.side_effect = RuntimeError("unavailable")
            pending = classifier.classify_pending_domains(
                decisions, path, {"historical.example", "api.openai.com", "unknown.example"}, self.args()
            )
            saved = json.loads(path.read_text())
        self.assertEqual(pending, ["unknown.example"])
        self.assertEqual(saved, decisions)
        self.assertEqual(decisions["domains"]["historical.example"], history)
        self.assertEqual(decisions["domains"]["api.openai.com"]["source"], "builtin")
        self.assertNotIn("unknown.example", decisions["domains"])

    def test_strict_protocol_rejects_invalid_or_incomplete_results(self):
        valid = {"domain": "unknown.example", "classification": "ai", "reason": "AI tool"}
        invalid_outputs = [
            [valid],
            {},
            {"classifications": []},
            {"classifications": [valid, valid]},
            {"classifications": [{**valid, "classification": "yes"}]},
            {"classifications": [{**valid, "classification": "unknown"}]},
            {"classifications": [{**valid, "domain": "other.example"}]},
            {"classifications": [{**valid, "reason": None}]},
            {"classifications": [{**valid, "extra": "value"}]},
        ]
        for output in invalid_outputs:
            with (
                self.subTest(output=output),
                mock.patch.object(classifier, "OpenRouterClient", create=True) as client,
                mock.patch("sys.stderr", io.StringIO()),
            ):
                client.return_value.complete.return_value = SimpleNamespace(output=output, model="openai/gpt-5-nano")
                pending, decisions, _ = self.classify({"unknown.example"})
            self.assertEqual(pending, ["unknown.example"])
            self.assertEqual(decisions, {"domains": {}})

    def test_failed_later_batch_preserves_completed_batch_and_remaining_domains(self):
        with (
            mock.patch.object(classifier, "OpenRouterClient", create=True) as client,
            mock.patch("sys.stderr", io.StringIO()),
        ):
            client.return_value.complete.side_effect = [
                SimpleNamespace(
                    output={
                        "classifications": [
                            {"domain": "a.example", "classification": "ai", "reason": "AI tool"},
                            {"domain": "b.example", "classification": "not_ai", "reason": "retail"},
                        ]
                    },
                    model="openai/gpt-5-nano",
                ),
                RuntimeError("unavailable"),
            ]
            pending, decisions, saved = self.classify({"a.example", "b.example", "c.example"})
        self.assertEqual(pending, ["c.example"])
        self.assertEqual(set(decisions["domains"]), {"a.example", "b.example"})
        self.assertEqual(saved, decisions)

    def test_missing_credential_keeps_pending_without_http_or_legacy(self):
        with (
            mock.patch("components.openrouter._open_request") as request,
            mock.patch.object(classifier, "classify_domains_via_codex") as codex,
            mock.patch.object(classifier, "classify_domains_via_openai") as openai,
        ):
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                pending, decisions, saved = self.classify(
                    {"unknown.example"},
                    self.args(openrouter_config=OpenRouterConfig()),
                )
        self.assertEqual(pending, ["unknown.example"])
        self.assertEqual(decisions, {"domains": {}})
        self.assertIsNone(saved)
        request.assert_not_called()
        codex.assert_not_called()
        openai.assert_not_called()
        self.assertIn("openrouter_credentials_missing", stderr.getvalue())

    def test_transport_auth_and_malformed_json_failures_remain_pending_and_redacted(self):
        for kind in ("auth", "json"):
            with (
                self.subTest(kind=kind),
                mock.patch("components.openrouter._open_request") as request,
                mock.patch.object(classifier, "classify_domains_via_codex") as codex,
                mock.patch.object(classifier, "classify_domains_via_openai") as openai,
            ):
                if kind == "auth":
                    request.side_effect = urllib.error.HTTPError(
                        "https://openrouter.ai",
                        401,
                        "fake-credential",
                        {},
                        io.BytesIO(b"fake-credential"),
                    )
                else:
                    request.return_value = io.BytesIO(
                        json.dumps(
                            {
                                "choices": [{"finish_reason": "stop", "message": {"content": "fake-credential"}}],
                            }
                        ).encode()
                    )
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    pending, decisions, saved = self.classify(
                        {"unknown.example"},
                        self.args(openrouter_config=OpenRouterConfig(api_key="fake-credential")),
                    )
            self.assertEqual(pending, ["unknown.example"])
            self.assertEqual(decisions, {"domains": {}})
            self.assertIsNone(saved)
            self.assertEqual(request.call_count, 1)
            codex.assert_not_called()
            openai.assert_not_called()
            self.assertNotIn("fake-credential", stderr.getvalue())

    def test_real_transport_consumer_sends_structured_tool_free_nano_request(self):
        response = {
            "model": "openai/gpt-5-nano",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "classifications": [
                                    {"domain": "unknown.example", "classification": "not_ai", "reason": "ordinary site"}
                                ],
                            }
                        )
                    },
                }
            ],
        }
        with mock.patch(
            "components.openrouter._open_request", return_value=io.BytesIO(json.dumps(response).encode())
        ) as request:
            pending, decisions, saved = self.classify(
                {"unknown.example"},
                self.args(openrouter_config=OpenRouterConfig(api_key="fake-credential")),
            )
        self.assertEqual(pending, [])
        self.assertEqual(saved, decisions)
        sent = request.call_args.args[0]
        payload = json.loads(sent.data)
        self.assertEqual(sent.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(payload["model"], "openai/gpt-5-nano")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("temperature", payload)

    def test_runner_rejects_unrecognized_provider_without_silent_fallback(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict("os.environ", {"AI_DOMAIN_CLASSIFIER_PROVIDER": "typo"}, clear=True),
            mock.patch("sys.argv", ["manager", "--workspace-dir", directory]),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            runner.build_args()
        self.assertEqual(error.exception.code, 2)

    def test_runner_defaults_to_openrouter_and_reads_key_file_without_opening_it(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OPENROUTER_API_KEY_FILE=/run/secrets/openrouter\nOPENROUTER_MODEL=openai/gpt-5-nano\n")
            with (
                mock.patch.dict("os.environ", {"XRAY_ENV_FILE": str(env_file)}, clear=True),
                mock.patch("sys.argv", ["manager", "--workspace-dir", directory, "--once"]),
            ):
                args = runner.build_args()
        self.assertEqual(args.ai_domain_classifier_provider, "openrouter")
        self.assertEqual(args.openrouter_config.model, "openai/gpt-5-nano")
        self.assertEqual(args.openrouter_config.api_key_file, "/run/secrets/openrouter")

    def test_runner_accepts_explicit_legacy_provider_and_environment_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("AI_DOMAIN_CLASSIFIER_PROVIDER=openrouter\nOPENROUTER_MODEL=ignored-model\n")
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "XRAY_ENV_FILE": str(env_file),
                        "AI_DOMAIN_CLASSIFIER_PROVIDER": "legacy",
                        "OPENROUTER_MODEL": "openai/gpt-5-nano",
                        "OPENROUTER_TIMEOUT_SECONDS": "23",
                        "OPENROUTER_MAX_OUTPUT_TOKENS": "2048",
                    },
                    clear=True,
                ),
                mock.patch("sys.argv", ["manager", "--workspace-dir", directory]),
            ):
                args = runner.build_args()
        self.assertEqual(args.ai_domain_classifier_provider, "legacy")
        self.assertEqual(args.openrouter_config.model, "openai/gpt-5-nano")
        self.assertEqual(args.openrouter_config.timeout_seconds, 23)
        self.assertEqual(args.openrouter_config.max_output_tokens, 2048)
