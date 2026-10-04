import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from components.openrouter import OpenRouterClient, OpenRouterConfig, OpenRouterError

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def response(output=None, **overrides):
    data = {
        "model": "openai/gpt-5-nano",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output or {"answer": "ok"})}}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 4},
        },
    }
    data.update(overrides)
    return io.BytesIO(json.dumps(data).encode())


def config(**kwargs):
    return OpenRouterConfig(api_key="dummy-provider-credential", **kwargs)


def test_sends_requested_model_strict_schema_without_tools_or_temperature(monkeypatch):
    captured = {}

    def open_request(request, timeout):
        captured.update(
            url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data), timeout=timeout
        )
        return response()

    monkeypatch.setattr("components.openrouter._open_request", open_request)
    result = OpenRouterClient(config()).complete([{"role": "user", "content": "classify"}], SCHEMA, "classification")
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer dummy-provider-credential"
    assert captured["body"]["model"] == "openai/gpt-5-nano"
    assert captured["body"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "classification", "strict": True, "schema": SCHEMA},
    }
    assert "temperature" not in captured["body"] and "tools" not in captured["body"]
    assert captured["body"]["max_completion_tokens"] == 8192
    assert result.output == {"answer": "ok"}
    assert result.provider == "openrouter" and result.model == "openai/gpt-5-nano"
    assert result.usage == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "cached_input_tokens": 3,
        "reasoning_output_tokens": 4,
    }


def test_file_key_takes_precedence_and_is_not_in_config_repr(tmp_path, monkeypatch):
    secret = tmp_path / "key"
    secret.write_text("dummy-file-credential\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-env-credential")
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", str(secret))
    c = OpenRouterConfig.from_env()
    captured = []

    def opener(request, timeout):
        captured.append(request.get_header("Authorization"))
        return response()

    monkeypatch.setattr("components.openrouter._open_request", opener)
    assert "dummy-env-credential" not in repr(c)
    OpenRouterClient(c).complete([], SCHEMA, "classification")
    assert captured == ["Bearer dummy-file-credential"]
    secret.write_text("dummy-rotated-credential")
    OpenRouterClient(c).complete([], SCHEMA, "classification")
    assert captured[-1] == "Bearer dummy-rotated-credential"


@pytest.mark.parametrize(
    "error_class,c",
    [
        ("openrouter_credentials_missing", OpenRouterConfig()),
        ("openrouter_credentials_unavailable", OpenRouterConfig(api_key="dummy-unused-credential", api_key_file="/missing/key")),
    ],
)
def test_missing_credentials_fail_before_network(error_class, c, monkeypatch):
    monkeypatch.setattr("components.openrouter._open_request", lambda *a, **kw: pytest.fail("must not call network"))
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterClient(c).complete([], SCHEMA, "classification")
    assert caught.value.error_class == error_class and not caught.value.retryable
    assert "dummy-unused-credential" not in str(caught.value)


@pytest.mark.parametrize(
    "status,error_class,retryable",
    [
        (401, "openrouter_auth_failed", False),
        (403, "openrouter_auth_failed", False),
        (400, "openrouter_request_rejected", False),
        (429, "openrouter_rate_limited", True),
        (502, "openrouter_unavailable", True),
        (302, "openrouter_request_rejected", False),
    ],
)
def test_http_failures_are_safe_and_classified(status, error_class, retryable, monkeypatch):
    def opener(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            status,
            "provider echoed dummy-provider-credential",
            {},
            io.BytesIO(b"dummy-provider-credential"),
        )

    monkeypatch.setattr("components.openrouter._open_request", opener)
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterClient(config()).complete([], SCHEMA, "classification")
    assert caught.value.error_class == error_class and caught.value.retryable == retryable
    assert "dummy-provider-credential" not in str(caught.value)


def test_timeout_and_untrusted_network_details_are_sanitized(monkeypatch):
    for error, expected in [
        (TimeoutError("dummy-provider-credential"), "openrouter_timeout"),
        (urllib.error.URLError("dummy-provider-credential"), "openrouter_unavailable"),
    ]:

        def opener(*args, _error=error, **kwargs):
            raise _error

        monkeypatch.setattr("components.openrouter._open_request", opener)
        with pytest.raises(OpenRouterError) as caught:
            OpenRouterClient(config()).complete([], SCHEMA, "classification")
        assert caught.value.error_class == expected and caught.value.retryable
        assert "dummy-provider-credential" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "no", "content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]},
    ],
)
def test_invalid_incomplete_or_refused_outputs_are_rejected(payload, monkeypatch):
    monkeypatch.setattr("components.openrouter._open_request", lambda *a, **kw: response(**payload))
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterClient(config()).complete([], SCHEMA, "classification")
    assert caught.value.error_class in {"openrouter_invalid_output", "openrouter_refused"}
    assert caught.value.usage["total_tokens"] == 20


def test_response_and_request_sizes_are_bounded(monkeypatch):
    monkeypatch.setattr(
        "components.openrouter._open_request", lambda *a, **kw: io.BytesIO(b"x" * (2 * 1024 * 1024 + 1))
    )
    with pytest.raises(OpenRouterError, match="response"):
        OpenRouterClient(config()).complete([], SCHEMA, "classification")
    monkeypatch.setattr(
        "components.openrouter._open_request", lambda *a, **kw: pytest.fail("oversized input must not call network")
    )
    with pytest.raises(OpenRouterError, match="request"):
        OpenRouterClient(config()).complete([{"role": "user", "content": "x" * (128 * 1024)}], SCHEMA, "classification")


def test_bad_config_and_token_usage_do_not_forge_data(monkeypatch):
    with pytest.raises(OpenRouterError):
        OpenRouterClient(config(timeout_seconds=0)).complete([], SCHEMA, "classification")
    monkeypatch.setattr(
        "components.openrouter._open_request",
        lambda *a, **kw: response(usage={"prompt_tokens": True, "completion_tokens": -2, "total_tokens": "20"}),
    )
    assert OpenRouterClient(config()).complete([], SCHEMA, "classification").usage == {}


def test_redirects_cannot_forward_credentials(monkeypatch):
    from components.openrouter import _NoRedirect

    handler = _NoRedirect()
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://attacker.test/") is None


@pytest.mark.parametrize("model", [None, "", "  ", 42])
def test_missing_actual_model_is_not_replaced_with_requested_model(model, monkeypatch):
    monkeypatch.setattr("components.openrouter._open_request", lambda *a, **kw: response(model=model))
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterClient(config()).complete([], SCHEMA, "classification")
    assert caught.value.error_class == "openrouter_invalid_output"
    assert caught.value.usage["total_tokens"] == 20


@pytest.mark.parametrize("redirect", [False, True])
def test_http_transport_contract_and_redirect_credential_isolation(monkeypatch, redirect):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers["Authorization"], json.loads(payload)))
            if redirect:
                self.send_response(307)
                self.send_header("Location", "/redirected")
                self.end_headers()
            else:
                body = response().read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr("components.openrouter.ENDPOINT", f"http://127.0.0.1:{server.server_port}/chat")
        try:
            if redirect:
                with pytest.raises(OpenRouterError) as error:
                    OpenRouterClient(config()).complete([], SCHEMA, "classification")
                assert error.value.error_class == "openrouter_request_rejected"
            else:
                result = OpenRouterClient(config()).complete([], SCHEMA, "classification")
                assert result.output == {"answer": "ok"}
                assert result.model == "openai/gpt-5-nano"
        finally:
            server.shutdown()
            thread.join()
    assert len(requests) == 1
    path, authorization, payload = requests[0]
    assert path == "/chat" and authorization == "Bearer dummy-provider-credential"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["model"] == "openai/gpt-5-nano"
    assert "temperature" not in payload and "tools" not in payload
