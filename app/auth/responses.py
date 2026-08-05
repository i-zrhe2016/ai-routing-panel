"""Login-redirect helpers, safe next-target handling, and login page renderers."""

from urllib.parse import urlsplit

from flask import jsonify, redirect, render_template, request, url_for

from .csrf import ensure_csrf_token


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


def customer_login_url_for_request():
    if request.method != "GET":
        return url_for("customer_login", next=normalize_next_target(request.referrer, fallback=url_for("plans_page")))
    return url_for("customer_login", next=normalize_next_target(current_request_target(), fallback=url_for("plans_page")))


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


def customer_auth_required_response():
    return redirect(customer_login_url_for_request(), code=303)


def render_login_page(next_target, form_username="", error_message="", status_code=200):
    return (
        render_template(
            "login.html",
            next_target=next_target,
            form_username=form_username,
            error_message=error_message,
            message=request.args.get("message", "").strip(),
            message_level=request.args.get("level", "info").strip() or "info",
            csrf_token=ensure_csrf_token(),
        ),
            status_code,
    )


def render_customer_login_page(next_target, form_email="", error_message="", status_code=200):
    return (
        render_template(
            "customer_login.html",
            next_target=next_target,
            form_email=form_email,
            error_message=error_message,
            message=request.args.get("message", "").strip(),
            message_level=request.args.get("level", "info").strip() or "info",
            csrf_token=ensure_csrf_token(),
        ),
        status_code,
    )


def render_customer_register_page(next_target, form_email="", error_message="", status_code=200):
    return (
        render_template(
            "customer_register.html",
            next_target=next_target,
            form_email=form_email,
            error_message=error_message,
            message=request.args.get("message", "").strip(),
            message_level=request.args.get("level", "info").strip() or "info",
            csrf_token=ensure_csrf_token(),
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
