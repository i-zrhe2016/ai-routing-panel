import base64
import hashlib
import hmac
from urllib.parse import urlsplit

from flask import jsonify, redirect, render_template, request, session, url_for

from .config import (
    AUTH_ENABLED,
    AUTH_SESSION_KEY,
    AUTH_SESSION_MARKER,
    PANEL_PASSWORD,
    PANEL_USERNAME,
    TENANT_SESSION_MARKER_KEY,
    TENANT_SESSION_TOKEN_KEY,
)


def credentials_match(username, password):
    return hmac.compare_digest(str(username or ""), PANEL_USERNAME) and hmac.compare_digest(
        str(password or ""),
        PANEL_PASSWORD,
    )


def tenant_credentials_match(port, username, password):
    return hmac.compare_digest(str(username or ""), str(port.get("tenant_username") or "")) and hmac.compare_digest(
        str(password or ""),
        str(port.get("tenant_password") or ""),
    )


def is_session_authenticated():
    return AUTH_ENABLED and session.get(AUTH_SESSION_KEY) == AUTH_SESSION_MARKER


def clear_admin_session():
    session.pop(AUTH_SESSION_KEY, None)


def clear_tenant_session():
    session.pop(TENANT_SESSION_TOKEN_KEY, None)
    session.pop(TENANT_SESSION_MARKER_KEY, None)


def mark_session_authenticated():
    clear_tenant_session()
    session[AUTH_SESSION_KEY] = AUTH_SESSION_MARKER


def tenant_session_marker(port):
    return hashlib.sha256(
        f"{port.get('tenant_token', '')}\0{port.get('tenant_username', '')}\0{port.get('tenant_password', '')}".encode(
            "utf-8"
        )
    ).hexdigest()


def is_tenant_session_authenticated(port):
    expected_token = str(port.get("tenant_token") or "")
    expected_marker = tenant_session_marker(port)
    return session.get(TENANT_SESSION_TOKEN_KEY) == expected_token and session.get(TENANT_SESSION_MARKER_KEY) == expected_marker


def mark_tenant_session_authenticated(port):
    clear_admin_session()
    clear_tenant_session()
    session[TENANT_SESSION_TOKEN_KEY] = str(port.get("tenant_token") or "")
    session[TENANT_SESSION_MARKER_KEY] = tenant_session_marker(port)


def extract_basic_credentials():
    auth = request.authorization
    auth_type = str(getattr(auth, "type", "basic") or "basic").lower()
    if auth and auth_type == "basic":
        return auth.username or "", auth.password or ""

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return None
    return username, password


def current_request_target():
    if not request.query_string:
        return request.path
    query = request.query_string.decode("utf-8", errors="ignore")
    return f"{request.path}?{query}"


def normalize_next_target(value, fallback=None):
    fallback_target = fallback or url_for("index")
    candidate = str(value or "").strip()
    if not candidate:
        return fallback_target

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc != request.host:
            return fallback_target

    path = parsed.path or "/"
    if not path.startswith("/") or path.startswith("//") or path in {url_for("login"), url_for("logout")}:
        return fallback_target

    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def login_next_target_for_request():
    fallback_target = url_for("index")
    if request.path.startswith("/api/"):
        return normalize_next_target(request.referrer, fallback=fallback_target)
    return normalize_next_target(current_request_target(), fallback=fallback_target)


def login_url_for_request():
    return url_for("login", next=login_next_target_for_request())


def auth_required_response():
    if request.path.startswith("/api/"):
        response = jsonify(
            {
                "ok": False,
                "code": "auth_required",
                "message": "请先登录面板。",
                "login_url": login_url_for_request(),
            }
        )
        response.status_code = 401
        response.headers["WWW-Authenticate"] = 'Basic realm="xray-routing-panel"'
        return response
    return redirect(login_url_for_request(), code=303)


def render_login_page(next_target, form_username="", error_message="", status_code=200):
    return (
        render_template(
            "login.html",
            next_target=next_target,
            form_username=form_username,
            error_message=error_message,
            message=request.args.get("message", "").strip(),
            message_level=request.args.get("level", "info").strip() or "info",
        ),
        status_code,
    )


def render_tenant_login_page(port, form_username="", error_message="", status_code=200):
    return (
        render_template(
            "tenant_login.html",
            port=port,
            form_username=form_username,
            error_message=error_message,
            message=request.args.get("message", "").strip(),
            message_level=request.args.get("level", "info").strip() or "info",
        ),
        status_code,
    )
