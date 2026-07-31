from components.xray_ops.redaction import redact_text, redact_value


def test_redact_text_removes_secrets_but_preserves_operational_context():
    raw = (
        "client=123e4567-e89b-12d3-a456-426614174000 "
        "Authorization: Bearer abc.def-123 "
        "password=hunter2 "
        "url=https://user:pass@example.com/path?token=secret-value&region=us "
        "target=203.0.113.10:443 domain=api.example.com"
    )

    result = redact_text(raw)

    assert not result.quarantined
    assert result.replacements >= 4
    assert "123e4567-e89b-12d3-a456-426614174000" not in result.text
    assert "abc.def-123" not in result.text
    assert "hunter2" not in result.text
    assert "secret-value" not in result.text
    assert "user:pass" not in result.text
    assert "203.0.113.10:443" in result.text
    assert "api.example.com" in result.text


def test_private_key_material_is_quarantined():
    result = redact_text("-----BEGIN PRIVATE KEY-----")

    assert result.quarantined
    assert result.text == "[QUARANTINED_SECRET_MATERIAL]"


def test_recursive_redaction_uses_field_names():
    payload = {
        "token": "top-secret",
        "nested": {"password": "another-secret", "host": "node.example.com"},
        "items": ["Bearer credential-value"],
    }

    redacted = redact_value(payload)

    assert redacted["token"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["host"] == "node.example.com"
    assert "credential-value" not in redacted["items"][0]
