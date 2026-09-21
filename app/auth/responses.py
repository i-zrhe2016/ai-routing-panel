"""Login-redirect helpers, safe next-target handling, and login page renderers.

The admin console has no login: disallowed source addresses get a 403 from the
access gate instead of a redirect, so only the tenant and customer login pages
live here.
"""

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
    if not path.startswith("/") or path.startswith("//") or path == url_for("login"):
        return fallback_target

    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def customer_login_url_for_request():
    if request.method != "GET":
        return url_for("customer_login", next=normalize_next_target(request.referrer, fallback=url_for("plans_page")))
    return url_for("customer_login", next=normalize_next_target(current_request_target(), fallback=url_for("plans_page")))


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
