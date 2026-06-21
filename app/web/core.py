import atexit
import signal
import threading
from datetime import datetime

from flask import Flask, Response, abort, jsonify, redirect, request, session, url_for

from ..auth import (
    auth_required_response,
    clear_customer_session,
    clear_tenant_session,
    customer_auth_required_response,
    credentials_match,
    ensure_csrf_token,
    extract_basic_credentials,
    is_customer_session_authenticated,
    is_session_authenticated,
    is_tenant_session_authenticated,
    mark_session_authenticated,
    mark_tenant_session_authenticated,
    tenant_credentials_match,
    validate_csrf_token,
)
from ..config import (
    AUTH_ENABLED,
    AUTH_SESSION_KEY,
    CUSTOMER_SESSION_ID_KEY,
    DEFAULT_UPSTREAM_HOST,
    DEFAULT_UPSTREAM_PORT,
    PANEL_HOST,
    PANEL_PORT,
    PANEL_PUBLIC_URL,
    PANEL_SECRET_KEY,
    PROBE_ENABLED,
    TENANT_SESSION_TOKEN_KEY,
    XRAY_CLIENT_CONFIG_PATH,
)
from ..helpers import human_bytes
from ..state import PanelState
from ..subscriptions import (
    build_clash_subscription_content,
    build_port_access_payload,
    build_v2ray_subscription_content,
    parse_xray_client_profile,
)
import secrets

# Routes, before_request hooks and template filters are collected at import time
# and applied by create_app(). This gives the module a real app factory while
# every handler stays a plain module-level function, and — crucially — endpoint
# names stay bare (the function name), so url_for(...) in templates and the
# endpoint-name sets in the before_request guards keep working unchanged.
_ROUTES = []
_BEFORE_REQUEST = []
_TEMPLATE_FILTERS = []


def route(rule, **options):
    def decorator(view_func):
        _ROUTES.append((rule, options, view_func))
        return view_func

    return decorator


def before_request(view_func):
    _BEFORE_REQUEST.append(view_func)
    return view_func


def template_filter(name):
    def decorator(filter_func):
        _TEMPLATE_FILTERS.append((name, filter_func))
        return filter_func

    return decorator


# Set by the package __init__ after the view modules are imported and the app is
# built, so main() can run the WSGI server. Kept here (not in __init__) so the
# whole startup surface lives in one module.
app = None


def create_app():
    # import_name "app" so Flask resolves root_path to the app/ package dir,
    # making template_folder/static_folder point at app/templates and app/static
    # exactly as the former single-file app.web module did.
    flask_app = Flask(
        "app",
        template_folder="templates",
        static_folder="static",
    )
    flask_app.config.update(
        SECRET_KEY=PANEL_SECRET_KEY or secrets.token_hex(32),
        SESSION_COOKIE_NAME="xray-routing-panel-session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=PANEL_PUBLIC_URL.startswith("https://"),
    )
    for rule, options, view_func in _ROUTES:
        endpoint = options.get("endpoint", view_func.__name__)
        extra = {key: value for key, value in options.items() if key != "endpoint"}
        flask_app.add_url_rule(rule, endpoint, view_func, **extra)
    for view_func in _BEFORE_REQUEST:
        flask_app.before_request(view_func)
    for name, filter_func in _TEMPLATE_FILTERS:
        flask_app.add_template_filter(filter_func, name)
    return flask_app


state = PanelState()


@before_request
def ensure_basic_auth():
    if request.path == "/healthz":
        return None
    if not AUTH_ENABLED:
        return None
    if request.endpoint in {
        "login",
        "logout",
        "static",
        "plans_page",
        "checkout_plan",
        "create_order",
        "customer_login",
        "customer_register",
        "customer_logout",
        "customer_dashboard",
        "customer_orders",
        "customer_order_detail",
        "customer_subscriptions",
        "customer_subscription_detail",
        "customer_subscription_renew",
        "customer_submit_order_payment_proof",
        "payment_proof_file",
        "tenant_login",
        "tenant_logout",
        "subscription_default",
        "subscription_clash",
        "subscription_v2ray",
        "tenant_panel",
        "tenant_subscription_default",
        "tenant_subscription_clash",
        "tenant_subscription_v2ray",
    }:
        return None
    if session.get(AUTH_SESSION_KEY) and not is_session_authenticated():
        session.clear()
    if is_session_authenticated():
        return None

    basic_credentials = extract_basic_credentials()
    if basic_credentials and credentials_match(*basic_credentials):
        mark_session_authenticated()
        return None
    return auth_required_response()


@before_request
def ensure_tenant_panel_auth():
    if request.endpoint != "tenant_panel":
        return None

    tenant_token = str((request.view_args or {}).get("tenant_token") or "").strip()
    if not tenant_token:
        return None

    port = state.get_port_by_tenant_token(tenant_token)
    if port is None:
        return None

    if session.get(TENANT_SESSION_TOKEN_KEY) and not is_tenant_session_authenticated(port):
        clear_tenant_session()
    if AUTH_ENABLED and is_session_authenticated():
        return None
    if is_tenant_session_authenticated(port):
        return None

    basic_credentials = extract_basic_credentials()
    if basic_credentials:
        if AUTH_ENABLED and credentials_match(*basic_credentials):
            mark_session_authenticated()
            return None
        if tenant_credentials_match(port, *basic_credentials):
            mark_tenant_session_authenticated(port)
            return None

    return redirect(tenant_login_target(tenant_token), code=303)


@before_request
def ensure_customer_portal_auth():
    customer_endpoints = {
        "customer_dashboard",
        "customer_orders",
        "customer_order_detail",
        "customer_subscriptions",
        "customer_subscription_detail",
        "customer_subscription_renew",
        "customer_submit_order_payment_proof",
        "create_order",
    }
    if request.endpoint not in customer_endpoints:
        return None

    customer = get_authenticated_customer()
    if customer is not None:
        return None
    return customer_auth_required_response()


@template_filter("human_bytes")
def human_bytes_filter(value):
    return human_bytes(value)


def build_subscription_snapshot(ports):
    subscription_profile, subscription_error = parse_xray_client_profile()
    subscription = {
        "available": subscription_profile is not None,
        "error": subscription_error,
        "client_config_path": str(XRAY_CLIENT_CONFIG_PATH),
        "server": subscription_profile["server"] if subscription_profile else "",
        "mode": "per-port",
        "tenant_count": len(ports),
        "tenant_panel_path_example": "/login?next=/tenant/<tenant_token>",
        "tenant_subscription_path_example": "/tenant-subscriptions/<subscription_token>/clash",
    }
    for port in ports:
        port["access"] = build_port_access_payload(port, subscription_profile)
    return subscription


def collect_dashboard_state(message="", level="info", ai_sync_error=""):
    ports = state.query_ports()
    summary = state.query_summary(ports)
    subscription = build_subscription_snapshot(ports)
    data_plane_status = state.data_plane_status()
    ai_routing_status = state.ai_routing_status(sync_error=ai_sync_error)
    dns_failover_status = state.dns_failover_status()
    commerce_summary = state.query_commerce_overview()
    commerce_settings = state.get_commerce_settings()
    commerce_plans = state.query_plans(public_only=False)
    commerce_orders = state.query_admin_orders()
    return {
        "flash": {
            "message": message,
            "level": level,
        },
        "meta": {
            "panel_address": PANEL_PUBLIC_URL or f"{PANEL_HOST}:{PANEL_PORT}",
            "data_plane_running": bool(data_plane_status.get("xray_running")),
            "timezone_label": datetime.now().astimezone().strftime("%Z"),
            "probe_enabled": PROBE_ENABLED,
            "probe_dashboard_url": url_for("probe_dashboard") if PROBE_ENABLED else "",
            "ai_domain_dashboard_url": url_for("ai_domain_dashboard"),
            "plans_page_url": url_for("plans_page"),
            "customer_login_url": url_for("customer_login"),
            "csrf_token": ensure_csrf_token(),
            "default_upstream_host": DEFAULT_UPSTREAM_HOST,
            "default_upstream_port": DEFAULT_UPSTREAM_PORT,
            "tenant_panel_prefix": "/tenant/",
            "data_plane_status": data_plane_status,
            "ai_routing_status": ai_routing_status,
            "dns_failover_status": dns_failover_status,
            "ai_domain_stats": state.query_ai_domain_overview(sync_error=ai_sync_error),
        },
        "summary": summary,
        "subscription": subscription,
        "ports": ports,
        "commerce": {
            "summary": commerce_summary,
            "settings": commerce_settings,
            "plans": commerce_plans,
            "orders": commerce_orders,
        },
    }


def build_tenant_dashboard_state(tenant_token, message="", level="info"):
    port = state.get_port_by_tenant_token(tenant_token)
    if port is None:
        return None

    subscription_profile, subscription_error = parse_xray_client_profile()
    access = build_port_access_payload(port, subscription_profile)
    return {
        "flash": {
            "message": message,
            "level": level,
        },
        "meta": {
            "panel_address": PANEL_PUBLIC_URL or f"{PANEL_HOST}:{PANEL_PORT}",
            "timezone_label": datetime.now().astimezone().strftime("%Z"),
            "probe_enabled": PROBE_ENABLED,
            "probe_dashboard_url": url_for("probe_dashboard") if PROBE_ENABLED else "",
            "tenant_login_url": tenant_login_target(tenant_token),
            "tenant_logout_url": url_for("tenant_logout", tenant_token=tenant_token),
            "subscription_available": subscription_profile is not None,
            "subscription_error": subscription_error,
            "client_config_path": str(XRAY_CLIENT_CONFIG_PATH),
        },
        "port": port,
        "access": access,
    }


def build_customer_service_access(service, subscription_profile):
    if not service or not service.get("port_id"):
        return None
    port_like = {
        "tenant_token": service.get("tenant_token"),
        "subscription_token": service.get("subscription_token"),
        "tenant_username": service.get("tenant_username"),
        "tenant_password": service.get("tenant_password"),
        "listen_port": service.get("listen_port"),
        "note": service.get("note"),
    }
    return build_port_access_payload(port_like, subscription_profile)


def build_customer_dashboard_state(customer, message="", level="info"):
    services = state.query_customer_service_subscriptions(customer["id"])
    orders = state.query_customer_orders(customer["id"])
    subscription_profile, subscription_error = parse_xray_client_profile()
    for service in services:
        service["access"] = build_customer_service_access(service, subscription_profile)
    open_order_count = len([item for item in orders if item["status"] in {"pending_payment", "payment_submitted", "payment_rejected"}])
    renewable_count = len([item for item in services if item["renewal_allowed"]])
    return {
        "flash": {"message": message, "level": level},
        "meta": {
            "timezone_label": datetime.now().astimezone().strftime("%Z"),
            "plans_page_url": url_for("plans_page"),
            "orders_url": url_for("customer_orders"),
            "subscriptions_url": url_for("customer_subscriptions"),
            "logout_url": url_for("customer_logout"),
            "checkout_example_url": url_for("plans_page"),
            "subscription_available": subscription_profile is not None,
            "subscription_error": subscription_error,
            "csrf_token": ensure_csrf_token(),
        },
        "customer": {
            "id": customer["id"],
            "email": customer["email"],
            "last_login_at_display": state.format_optional_display_time(customer.get("last_login_at"), default="首次登录"),
        },
        "summary": {
            "service_count": len(services),
            "renewable_count": renewable_count,
            "open_order_count": open_order_count,
        },
        "services": services[:5],
        "orders": orders[:10],
        "commerce_settings": state.get_commerce_settings(),
    }


def build_dashboard_state(message="", level="info"):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    ai_sync_error = ""
    try:
        state.sync_data_plane_ai_state()
    except RuntimeError as exc:
        ai_sync_error = str(exc)
    return collect_dashboard_state(message=message, level=level, ai_sync_error=ai_sync_error)


def json_success_response(message="", level="success", status_code=200):
    return (
        jsonify(
            {
                "ok": True,
                "message": message,
                "level": level,
                "dashboard": build_dashboard_state(message=message, level=level),
            }
        ),
        status_code,
    )


def json_error_response(message, status_code=400):
    return jsonify({"ok": False, "message": message}), status_code


def request_payload():
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return {}


def tenant_panel_target(tenant_token):
    return url_for("tenant_panel", tenant_token=tenant_token)


def tenant_login_target(tenant_token, **values):
    query = {"next": tenant_panel_target(tenant_token)}
    query.update(values)
    return url_for("login", **query)


def customer_dashboard_target():
    return url_for("customer_dashboard")


def customer_login_target(next_target="", **values):
    query = {"next": next_target or customer_dashboard_target()}
    query.update(values)
    return url_for("customer_login", **query)


def get_authenticated_tenant():
    tenant_token = str(session.get(TENANT_SESSION_TOKEN_KEY) or "").strip()
    if not tenant_token:
        return None

    port = state.get_port_by_tenant_token(tenant_token)
    if port is None or not is_tenant_session_authenticated(port):
        clear_tenant_session()
        return None
    return port


def get_authenticated_customer():
    customer_id = session.get(CUSTOMER_SESSION_ID_KEY)
    if not customer_id:
        return None
    customer = state.get_customer_by_id(customer_id)
    if customer is None or customer.get("status") != "active" or not is_customer_session_authenticated(customer):
        clear_customer_session()
        return None
    return customer


def require_csrf():
    token = request.headers.get("X-CSRF-Token", "")
    if not token:
        token = request.form.get("csrf_token", "")
    if not validate_csrf_token(token):
        abort(400, description="CSRF token 无效。")


def build_subscription_response(token, listen_port, output_format):
    expected_token = state.get_subscription_token()
    if token != expected_token:
        abort(404)

    profile, _ = parse_xray_client_profile()
    if profile is None:
        abort(404)

    port = state.get_port_subscription_record(listen_port)
    if port is None:
        abort(404)

    if output_format == "v2ray":
        content = build_v2ray_subscription_content(profile, listen_port, port["note"])
        content_type = "text/plain; charset=utf-8"
    else:
        content = build_clash_subscription_content(profile, listen_port, port["note"])
        content_type = "text/yaml; charset=utf-8"

    return Response(content, content_type=content_type)


def build_port_token_subscription_response(subscription_token, output_format):
    profile, _ = parse_xray_client_profile()
    if profile is None:
        abort(404)

    port = state.get_port_subscription_record_by_token(subscription_token)
    if port is None:
        abort(404)

    if output_format == "v2ray":
        content = build_v2ray_subscription_content(profile, port["listen_port"], port["note"])
        content_type = "text/plain; charset=utf-8"
    else:
        content = build_clash_subscription_content(profile, port["listen_port"], port["note"])
        content_type = "text/yaml; charset=utf-8"

    return Response(content, content_type=content_type)


def message_redirect(message, level):
    return redirect(url_for("index", message=message, level=level), code=303)


def handle_shutdown(signum, _frame):
    raise KeyboardInterrupt(f"received signal {signum}")


def main():
    state.bootstrap()
    worker = threading.Thread(target=state.maintenance_loop, daemon=True)
    worker.start()
    atexit.register(state.stop)
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    try:
        app.run(host=PANEL_HOST, port=PANEL_PORT, threaded=True, use_reloader=False)
    finally:
        state.stop()
