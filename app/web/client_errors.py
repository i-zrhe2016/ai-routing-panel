from flask import request

from .core import json_success_response, log_business_event, request_payload, route, require_csrf


def _text(value, limit=200):
    return str(value or "").strip()[:limit]


@route("/api/client-errors", methods=["POST"])
def api_client_errors():
    require_csrf()
    payload = request_payload()
    error_message = _text(payload.get("message"), 500) or "客户端请求失败。"
    source = _text(payload.get("source"), 64)
    is_request_error = source in {"fetch", "http", "response.read", "response.parse"} or not source
    metadata = {
        "client_error_name": _text(payload.get("error_name")),
        "client_source": source,
        "client_stack": _text(payload.get("stack"), 4000),
        "client_method": _text(payload.get("method"), 16).upper(),
        "client_online": bool(payload.get("online", True)),
        "client_status": payload.get("status") if isinstance(payload.get("status"), int) else None,
        "client_url_path": _text(payload.get("url_path")),
        "client_user_agent": _text(request.headers.get("User-Agent"), 300),
    }
    log_business_event(
        "frontend.fetch_failed" if is_request_error else "frontend.runtime_error",
        result="failure",
        error_code="client_fetch_failed" if is_request_error else "client_runtime_error",
        message=error_message,
        metadata=metadata,
    )
    return json_success_response("客户端错误已记录。", status_code=202)
