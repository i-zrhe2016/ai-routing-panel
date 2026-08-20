import json

from app.observability.logging import (
    bind_actor,
    clear_request_context,
    emit_event,
    initialize_request_context,
    is_valid_request_id,
)


def test_request_id_validation_rejects_unsafe_and_overlong_values():
    assert is_valid_request_id("req-01_a.b:c")
    assert not is_valid_request_id("bad request")
    assert not is_valid_request_id("x" * 129)


def test_business_event_is_one_line_json_and_redacts_sensitive_fields(capsys):
    initialize_request_context("req-123", method="POST", endpoint="/api/orders")
    bind_actor("customer", 42)
    try:
        try:
            raise RuntimeError("password=do-not-write")
        except RuntimeError as exc:
            emit_event(
                "order.created",
                metadata={"order_no": "ORD-1", "password": "do-not-write"},
                message="authorization=Bearer do-not-write",
                exc=exc,
            )
    finally:
        clear_request_context()

    output = capsys.readouterr()
    lines = [line for line in (output.out + output.err).splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == "1"
    assert payload["event"] == "order.created"
    assert payload["request_id"] == "req-123"
    assert payload["actor_type"] == "customer"
    assert payload["actor_id"] == "42"
    assert "do-not-write" not in lines[0]
    assert "stacktrace" in payload
