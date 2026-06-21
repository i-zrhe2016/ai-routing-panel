import atexit
import secrets
import signal
import sqlite3
import threading
from datetime import datetime

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from .auth import (
    auth_required_response,
    clear_customer_session,
    clear_tenant_session,
    customer_auth_required_response,
    customer_credentials_match,
    credentials_match,
    ensure_csrf_token,
    extract_basic_credentials,
    is_customer_session_authenticated,
    is_session_authenticated,
    is_tenant_session_authenticated,
    mark_customer_session_authenticated,
    mark_session_authenticated,
    mark_tenant_session_authenticated,
    normalize_next_target,
    render_customer_login_page,
    render_customer_register_page,
    render_login_page,
    render_tenant_login_page,
    tenant_credentials_match,
    validate_csrf_token,
)
from .config import (
    AUTH_ENABLED,
    AUTH_SESSION_KEY,
    CUSTOMER_SESSION_ID_KEY,
    DEFAULT_UPSTREAM_HOST,
    DEFAULT_UPSTREAM_PORT,
    PANEL_HOST,
    PANEL_HEALTH_REQUIRES_XRAY,
    PANEL_PORT,
    PANEL_PUBLIC_URL,
    PANEL_SECRET_KEY,
    PAYMENT_PROOFS_DIR,
    PROBE_ENABLED,
    TENANT_SESSION_TOKEN_KEY,
    XRAY_CLIENT_CONFIG_PATH,
)
from .errors import ValidationError
from .helpers import human_bytes
from .state import PanelState
from .subscriptions import (
    build_clash_subscription_content,
    build_port_access_payload,
    build_v2ray_subscription_content,
    parse_xray_client_profile,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.update(
    SECRET_KEY=PANEL_SECRET_KEY or secrets.token_hex(32),
    SESSION_COOKIE_NAME="xray-routing-panel-session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PANEL_PUBLIC_URL.startswith("https://"),
)

state = PanelState()


@app.before_request
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


@app.before_request
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


@app.before_request
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


@app.template_filter("human_bytes")
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


@app.route("/login", methods=["GET", "POST"])
def login():
    next_target = normalize_next_target(request.values.get("next"), fallback=url_for("index"))
    authenticated_tenant = get_authenticated_tenant()
    authenticated_customer = get_authenticated_customer()
    if authenticated_tenant is not None:
        return redirect(tenant_panel_target(authenticated_tenant["tenant_token"]), code=303)
    if authenticated_customer is not None:
        return redirect(customer_dashboard_target(), code=303)
    if is_session_authenticated():
        return redirect(next_target, code=303)

    if request.method == "POST":
        state.sync_traffic_state()
        state.disable_auto_stopped_ports(reload_xray=True)
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if AUTH_ENABLED and credentials_match(username, password):
            mark_session_authenticated()
            return redirect(next_target, code=303)
        port = state.get_port_by_tenant_username(username)
        if port is not None and tenant_credentials_match(port, username, password):
            mark_tenant_session_authenticated(port)
            return redirect(tenant_panel_target(port["tenant_token"]), code=303)
        return render_login_page(
            next_target=next_target,
            form_username=username,
            error_message="账号或密码错误。",
            status_code=401,
        )

    return render_login_page(next_target=next_target)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login", message="已退出登录。", level="info"), code=303)


@app.route("/plans", methods=["GET"])
def plans_page():
    return render_template(
        "plans.html",
        plans=state.query_plans(public_only=True),
        current_customer=get_authenticated_customer(),
        commerce_settings=state.get_commerce_settings(),
    )


@app.route("/checkout/<plan_slug>", methods=["GET"])
def checkout_plan(plan_slug):
    customer = get_authenticated_customer()
    if customer is None:
        return redirect(customer_login_target(next_target=request.path), code=303)
    plan = state.get_plan_by_slug(plan_slug, public_only=True)
    if plan is None:
        abort(404)
    return render_template(
        "checkout.html",
        customer=customer,
        plan=plan,
        commerce_settings=state.get_commerce_settings(),
        csrf_token=ensure_csrf_token(),
    )


@app.route("/customer/register", methods=["GET", "POST"])
def customer_register():
    next_target = normalize_next_target(request.values.get("next"), fallback=url_for("plans_page"))
    customer = get_authenticated_customer()
    if customer is not None:
        return redirect(next_target or customer_dashboard_target(), code=303)

    if request.method == "POST":
        require_csrf()
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        try:
            state.validate_customer_password(password, confirm_password)
            state.create_customer(email, password)
            created_customer = state.get_customer_by_email(email)
            if created_customer is None:
                raise ValidationError("客户账号创建失败。")
            mark_customer_session_authenticated(created_customer)
            state.touch_customer_login(created_customer["id"])
            return redirect(next_target, code=303)
        except sqlite3.IntegrityError:
            return render_customer_register_page(
                next_target=next_target,
                form_email=email,
                error_message="该邮箱已注册，请直接登录。",
                status_code=409,
            )
        except ValidationError as exc:
            return render_customer_register_page(
                next_target=next_target,
                form_email=email,
                error_message=str(exc),
                status_code=400,
            )

    return render_customer_register_page(next_target=next_target)


@app.route("/customer/login", methods=["GET", "POST"])
def customer_login():
    next_target = normalize_next_target(request.values.get("next"), fallback=customer_dashboard_target())
    customer = get_authenticated_customer()
    if customer is not None:
        return redirect(next_target, code=303)

    if request.method == "POST":
        require_csrf()
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        try:
            matched_customer = state.get_customer_by_email(email)
        except ValidationError:
            matched_customer = None
        if matched_customer is not None and matched_customer.get("status") == "active" and customer_credentials_match(
            matched_customer, password
        ):
            mark_customer_session_authenticated(matched_customer)
            state.touch_customer_login(matched_customer["id"])
            return redirect(next_target, code=303)
        return render_customer_login_page(
            next_target=next_target,
            form_email=email,
            error_message="邮箱或密码错误。",
            status_code=401,
        )

    return render_customer_login_page(next_target=next_target)


@app.route("/customer/logout", methods=["GET", "POST"])
def customer_logout():
    clear_customer_session()
    return redirect(url_for("plans_page"), code=303)


@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        auth_enabled=AUTH_ENABLED,
        initial_state=build_dashboard_state(
            message=request.args.get("message", "").strip(),
            level=request.args.get("level", "info").strip(),
        ),
    )


@app.route("/customer/dashboard", methods=["GET"])
def customer_dashboard():
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    return render_template(
        "customer_dashboard.html",
        dashboard=build_customer_dashboard_state(
            customer,
            message=request.args.get("message", "").strip(),
            level=request.args.get("level", "info").strip(),
        ),
    )


@app.route("/customer/orders", methods=["GET"])
def customer_orders():
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    return render_template(
        "customer_orders.html",
        customer=customer,
        orders=state.query_customer_orders(customer["id"]),
        commerce_settings=state.get_commerce_settings(),
        csrf_token=ensure_csrf_token(),
    )


@app.route("/customer/orders/<order_no>", methods=["GET"])
def customer_order_detail(order_no):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    order = state.get_customer_order(customer["id"], order_no)
    if order is None:
        abort(404)
    service = None
    if order.get("service_subscription_id"):
        service = state.get_customer_service_subscription(customer["id"], order["service_subscription_id"])
        if service is not None:
            subscription_profile, _ = parse_xray_client_profile()
            service["access"] = build_customer_service_access(service, subscription_profile)
    return render_template(
        "customer_order_detail.html",
        customer=customer,
        order=order,
        service=service,
        commerce_settings=state.get_commerce_settings(),
        csrf_token=ensure_csrf_token(),
    )


@app.route("/customer/orders/<order_no>/payment-proof", methods=["POST"])
def customer_submit_order_payment_proof(order_no):
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    require_csrf()
    file_storage = request.files.get("proof_image")
    if file_storage is None:
        return redirect(
            url_for("customer_order_detail", order_no=order_no, message="请先选择支付截图。", level="error"),
            code=303,
        )
    try:
        state.submit_order_payment_submission(
            customer["id"],
            order_no,
            file_storage,
            request.form.get("payer_note", ""),
        )
        return redirect(
            url_for("customer_order_detail", order_no=order_no, message="支付凭证已提交，等待人工审核。", level="success"),
            code=303,
        )
    except ValidationError as exc:
        return redirect(
            url_for("customer_order_detail", order_no=order_no, message=str(exc), level="error"),
            code=303,
        )


@app.route("/customer/subscriptions", methods=["GET"])
def customer_subscriptions():
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    services = state.query_customer_service_subscriptions(customer["id"])
    subscription_profile, subscription_error = parse_xray_client_profile()
    for service in services:
        service["access"] = build_customer_service_access(service, subscription_profile)
    return render_template(
        "customer_subscriptions.html",
        customer=customer,
        services=services,
        subscription_available=subscription_profile is not None,
        subscription_error=subscription_error,
        csrf_token=ensure_csrf_token(),
    )


@app.route("/customer/subscriptions/<int:service_subscription_id>", methods=["GET"])
def customer_subscription_detail(service_subscription_id):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    service = state.get_customer_service_subscription(customer["id"], service_subscription_id)
    if service is None:
        abort(404)
    subscription_profile, subscription_error = parse_xray_client_profile()
    service["access"] = build_customer_service_access(service, subscription_profile)
    return render_template(
        "customer_subscription_detail.html",
        customer=customer,
        service=service,
        subscription_available=subscription_profile is not None,
        subscription_error=subscription_error,
        csrf_token=ensure_csrf_token(),
    )


@app.route("/customer/subscriptions/<int:service_subscription_id>/renew", methods=["POST"])
def customer_subscription_renew(service_subscription_id):
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    require_csrf()
    service = state.get_customer_service_subscription(customer["id"], service_subscription_id)
    if service is None:
        abort(404)
    try:
        order_no = state.create_order(
            customer["id"],
            service["plan_id"],
            kind="renewal",
            service_subscription_id=service_subscription_id,
        )
        return redirect(url_for("customer_order_detail", order_no=order_no), code=303)
    except ValidationError as exc:
        return redirect(
            url_for(
                "customer_subscription_detail",
                service_subscription_id=service_subscription_id,
                message=str(exc),
                level="error",
            ),
            code=303,
        )


@app.route("/orders", methods=["POST"])
def create_order():
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    require_csrf()

    kind = str(request.form.get("kind", "new_purchase") or "new_purchase").strip()
    try:
        if kind == "renewal":
            service_subscription_id = int(str(request.form.get("service_subscription_id", "") or "").strip())
            service = state.get_customer_service_subscription(customer["id"], service_subscription_id)
            if service is None:
                raise ValidationError("服务实例不存在。")
            order_no = state.create_order(
                customer["id"],
                service["plan_id"],
                kind="renewal",
                service_subscription_id=service_subscription_id,
            )
        else:
            plan = state.get_plan_by_slug(request.form.get("plan_slug", ""), public_only=True)
            if plan is None:
                raise ValidationError("套餐不存在或已下架。")
            order_no = state.create_order(customer["id"], plan["id"], kind="new_purchase")
        return redirect(url_for("customer_order_detail", order_no=order_no), code=303)
    except ValidationError as exc:
        fallback_slug = str(request.form.get("plan_slug", "") or "").strip()
        if fallback_slug:
            return redirect(
                url_for("checkout_plan", plan_slug=fallback_slug, message=str(exc), level="error"),
                code=303,
            )
        return redirect(url_for("customer_dashboard", message=str(exc), level="error"), code=303)


@app.route("/probe-dashboard", methods=["GET"])
def probe_dashboard():
    if not PROBE_ENABLED:
        return redirect(url_for("index", message="探针检测已停用。", level="info"), code=303)
    state.sync_traffic_state()
    dashboard = state.get_probe_dashboard(request.args.get("range", "24h").strip())
    return render_template(
        "probe_dashboard.html",
        dashboard=dashboard,
        timezone_label=datetime.now().astimezone().strftime("%Z"),
        data_plane_running=state.data_plane_running(),
        panel_host=PANEL_HOST,
        panel_port=PANEL_PORT,
        panel_public_url=(f"{PANEL_PUBLIC_URL}/" if PANEL_PUBLIC_URL else ""),
        auth_enabled=AUTH_ENABLED,
    )


@app.route("/ai-domain-dashboard", methods=["GET"])
def ai_domain_dashboard():
    ai_sync_error = ""
    try:
        state.sync_data_plane_ai_state()
    except RuntimeError as exc:
        ai_sync_error = str(exc)
    dashboard = state.get_ai_domain_dashboard(sync_error=ai_sync_error)
    return render_template(
        "ai_domain_dashboard.html",
        dashboard=dashboard,
        timezone_label=datetime.now().astimezone().strftime("%Z"),
        data_plane_running=state.data_plane_running(),
        panel_host=PANEL_HOST,
        panel_port=PANEL_PORT,
        panel_public_url=(f"{PANEL_PUBLIC_URL}/" if PANEL_PUBLIC_URL else ""),
        auth_enabled=AUTH_ENABLED,
    )


@app.route("/tenant/<tenant_token>/login", methods=["GET", "POST"])
def tenant_login(tenant_token):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    port = state.get_port_by_tenant_token(tenant_token)
    if port is None:
        abort(404)

    if request.method == "GET":
        return redirect(tenant_login_target(tenant_token), code=303)

    if is_session_authenticated():
        return redirect(tenant_panel_target(tenant_token), code=303)
    if is_tenant_session_authenticated(port):
        return redirect(tenant_panel_target(tenant_token), code=303)

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if tenant_credentials_match(port, username, password):
        mark_tenant_session_authenticated(port)
        return redirect(tenant_panel_target(tenant_token), code=303)
    return render_tenant_login_page(
        port,
        form_username=username,
        error_message="用户名或密码错误。",
        status_code=401,
    )


@app.route("/tenant/<tenant_token>/logout", methods=["GET", "POST"])
def tenant_logout(tenant_token):
    clear_tenant_session()
    return redirect(tenant_login_target(tenant_token, message="已退出登录。", level="info"), code=303)


@app.route("/tenant/<tenant_token>", methods=["GET"])
def tenant_panel(tenant_token):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    dashboard = build_tenant_dashboard_state(
        tenant_token,
        message=request.args.get("message", "").strip(),
        level=request.args.get("level", "info").strip(),
    )
    if dashboard is None:
        abort(404)
    return render_template("tenant_panel.html", dashboard=dashboard)


@app.route("/healthz", methods=["GET"])
def healthz():
    state.sync_traffic_state()
    data_plane_running = state.data_plane_running()
    healthy = data_plane_running if PANEL_HEALTH_REQUIRES_XRAY else True
    status_code = 200 if healthy else 500
    return jsonify({"ok": healthy, "data_plane_running": data_plane_running}), status_code


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"ok": True, "dashboard": build_dashboard_state()})


@app.route("/api/plans", methods=["GET"])
def api_plans():
    return jsonify({"ok": True, "plans": state.query_plans(public_only=False)})


@app.route("/api/plans", methods=["POST"])
def api_create_plan():
    require_csrf()
    try:
        payload = state.validate_plan_payload(request_payload())
        state.create_plan(payload)
        return json_success_response("套餐已创建。", status_code=201)
    except sqlite3.IntegrityError:
        return json_error_response("套餐 slug 已存在，请修改后重试。", status_code=409)
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/plans/<int:plan_id>", methods=["PUT"])
def api_update_plan(plan_id):
    require_csrf()
    try:
        payload = state.validate_plan_payload(request_payload())
        state.update_plan(plan_id, payload)
        return json_success_response("套餐已更新。")
    except sqlite3.IntegrityError:
        return json_error_response("套餐 slug 已存在，请修改后重试。", status_code=409)
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/orders", methods=["GET"])
def api_orders():
    return jsonify({"ok": True, "orders": state.query_admin_orders(request.args.get("status", "").strip())})


@app.route("/api/orders/<int:order_id>/fulfill", methods=["POST"])
def api_fulfill_order(order_id):
    require_csrf()
    try:
        state.fulfill_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已审核通过并完成开通。")
    except sqlite3.IntegrityError:
        return json_error_response("自动分配端口时出现冲突，请重试。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/orders/<int:order_id>/reject", methods=["POST"])
def api_reject_order(order_id):
    require_csrf()
    try:
        state.reject_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已驳回，客户可重新提交支付凭证。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/orders/<int:order_id>/cancel", methods=["POST"])
def api_cancel_order(order_id):
    require_csrf()
    try:
        state.cancel_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已取消。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/commerce-settings", methods=["GET"])
def api_commerce_settings():
    return jsonify({"ok": True, "settings": state.get_commerce_settings()})


@app.route("/api/commerce-settings", methods=["PUT"])
def api_update_commerce_settings():
    require_csrf()
    try:
        state.update_commerce_settings(request_payload())
        return json_success_response("商业化设置已更新。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/dns-failover", methods=["GET"])
def api_dns_failover_status():
    return jsonify({"ok": True, "status": state.dns_failover_status()})


@app.route("/api/dns-failover/check", methods=["POST"])
def api_dns_failover_check():
    try:
        state.run_dns_failover_check(force=True)
        return json_success_response("DNS 故障切换已执行一次即时检测。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/dns-failover/switch", methods=["POST"])
def api_dns_failover_switch():
    try:
        payload = request_payload()
        state.switch_dns_target(payload.get("target"))
        return json_success_response("DNS 记录已更新。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/subscriptions/rotate", methods=["POST"])
def api_rotate_subscription():
    state.rotate_subscription_token()
    return json_success_response("订阅链接已重新生成，旧链接已失效。")


@app.route("/api/ports", methods=["POST"])
def api_create_port():
    try:
        payload = state.validate_port_payload(request_payload())
        state.create_port(payload)
        return json_success_response("端口已创建并写入 Xray。", status_code=201)
    except sqlite3.IntegrityError:
        return json_error_response("监听端口已存在，请更换其他端口。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>", methods=["PUT"])
def api_update_port(port_id):
    try:
        payload = state.validate_port_payload(request_payload())
        state.update_port(port_id, payload)
        return json_success_response("端口配置已更新。")
    except sqlite3.IntegrityError:
        return json_error_response("监听端口已存在，请更换其他端口。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>/toggle", methods=["POST"])
def api_toggle_port(port_id):
    try:
        state.toggle_port(port_id)
        return json_success_response("端口状态已切换。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>", methods=["DELETE"])
def api_delete_port(port_id):
    try:
        state.delete_port(port_id)
        return json_success_response("端口已删除。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>/reset-traffic", methods=["POST"])
def api_reset_port_traffic(port_id):
    try:
        restored = state.reset_port_traffic(port_id)
        message = "流量已重置，端口已恢复启用。" if restored else "流量已重置。"
        return json_success_response(message)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>/rotate-tenant-token", methods=["POST"])
def api_rotate_port_tenant_token(port_id):
    try:
        state.rotate_port_tenant_token(port_id)
        return json_success_response("租户面板地址已重置，旧链接已失效。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>/rotate-tenant-credentials", methods=["POST"])
def api_rotate_port_tenant_credentials(port_id):
    try:
        state.rotate_port_tenant_credentials(port_id)
        return json_success_response("租户登录用户名和密码已重置。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/ports/<int:port_id>/rotate-subscription-token", methods=["POST"])
def api_rotate_port_subscription_token(port_id):
    try:
        state.rotate_port_subscription_token(port_id)
        return json_success_response("租户订阅地址已重置，旧链接已失效。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@app.route("/api/data-plane/restart", methods=["POST"])
def api_restart_data_plane():
    try:
        state.restart_data_plane_or_raise()
        return json_success_response("数据面已执行重启。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


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


@app.route("/subscriptions/rotate", methods=["POST"])
def rotate_subscription():
    state.rotate_subscription_token()
    return message_redirect("订阅链接已重新生成，旧链接已失效。", "success")


@app.route("/<token>/<int:listen_port>", methods=["GET"])
def subscription_default(token, listen_port):
    return build_subscription_response(token, listen_port, "clash")


@app.route("/<token>/<int:listen_port>/clash", methods=["GET"])
def subscription_clash(token, listen_port):
    return build_subscription_response(token, listen_port, "clash")


@app.route("/<token>/<int:listen_port>/v2ray", methods=["GET"])
def subscription_v2ray(token, listen_port):
    return build_subscription_response(token, listen_port, "v2ray")


@app.route("/tenant-subscriptions/<subscription_token>", methods=["GET"])
def tenant_subscription_default(subscription_token):
    return build_port_token_subscription_response(subscription_token, "clash")


@app.route("/tenant-subscriptions/<subscription_token>/clash", methods=["GET"])
def tenant_subscription_clash(subscription_token):
    return build_port_token_subscription_response(subscription_token, "clash")


@app.route("/tenant-subscriptions/<subscription_token>/v2ray", methods=["GET"])
def tenant_subscription_v2ray(subscription_token):
    return build_port_token_subscription_response(subscription_token, "v2ray")


@app.route("/payment-proofs/<int:submission_id>", methods=["GET"])
def payment_proof_file(submission_id):
    record = state.get_payment_submission_record(submission_id)
    if record is None:
        abort(404)

    allowed = False
    if not AUTH_ENABLED:
        allowed = True
    elif is_session_authenticated():
        allowed = True
    else:
        customer = get_authenticated_customer()
        if customer is not None and int(record["customer_id"]) == int(customer["id"]):
            allowed = True
    if not allowed:
        abort(403)

    path = PAYMENT_PROOFS_DIR.parent / str(record["proof_image_path"])
    if not path.is_file():
        abort(404)
    return send_file(path)


@app.route("/ports/create", methods=["POST"])
def create_port():
    try:
        payload = state.validate_port_payload(request.form)
        state.create_port(payload)
        return message_redirect("端口已创建并写入 Xray。", "success")
    except sqlite3.IntegrityError:
        return message_redirect("监听端口已存在，请更换其他端口。", "error")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@app.route("/ports/<int:port_id>/update", methods=["POST"])
def update_port(port_id):
    try:
        payload = state.validate_port_payload(request.form)
        state.update_port(port_id, payload)
        return message_redirect("端口配置已更新。", "success")
    except sqlite3.IntegrityError:
        return message_redirect("监听端口已存在，请更换其他端口。", "error")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@app.route("/ports/<int:port_id>/toggle", methods=["POST"])
def toggle_port(port_id):
    try:
        state.toggle_port(port_id)
        return message_redirect("端口状态已切换。", "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@app.route("/ports/<int:port_id>/delete", methods=["POST"])
def delete_port(port_id):
    try:
        state.delete_port(port_id)
        return message_redirect("端口已删除。", "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@app.route("/ports/<int:port_id>/reset-traffic", methods=["POST"])
def reset_port_traffic(port_id):
    try:
        restored = state.reset_port_traffic(port_id)
        message = "流量已重置，端口已恢复启用。" if restored else "流量已重置。"
        return message_redirect(message, "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


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
