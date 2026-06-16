import atexit
import secrets
import signal
import sqlite3
import threading
from datetime import datetime

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for

from .auth import (
    auth_required_response,
    clear_tenant_session,
    credentials_match,
    extract_basic_credentials,
    is_session_authenticated,
    is_tenant_session_authenticated,
    mark_session_authenticated,
    mark_tenant_session_authenticated,
    normalize_next_target,
    render_login_page,
    render_tenant_login_page,
    tenant_credentials_match,
)
from .config import (
    AUTH_ENABLED,
    AUTH_SESSION_KEY,
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


def collect_dashboard_state(message="", level="info"):
    ports = state.query_ports()
    summary = state.query_summary(ports)
    subscription = build_subscription_snapshot(ports)
    return {
        "flash": {
            "message": message,
            "level": level,
        },
        "meta": {
            "panel_address": PANEL_PUBLIC_URL or f"{PANEL_HOST}:{PANEL_PORT}",
            "xray_running": state.xray_running(),
            "timezone_label": datetime.now().astimezone().strftime("%Z"),
            "probe_enabled": PROBE_ENABLED,
            "probe_dashboard_url": url_for("probe_dashboard") if PROBE_ENABLED else "",
            "default_upstream_host": DEFAULT_UPSTREAM_HOST,
            "default_upstream_port": DEFAULT_UPSTREAM_PORT,
            "tenant_panel_prefix": "/tenant/",
        },
        "summary": summary,
        "subscription": subscription,
        "ports": ports,
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


def build_dashboard_state(message="", level="info"):
    state.sync_traffic_state()
    state.disable_auto_stopped_ports(reload_xray=True)
    return collect_dashboard_state(message=message, level=level)


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


def get_authenticated_tenant():
    tenant_token = str(session.get(TENANT_SESSION_TOKEN_KEY) or "").strip()
    if not tenant_token:
        return None

    port = state.get_port_by_tenant_token(tenant_token)
    if port is None or not is_tenant_session_authenticated(port):
        clear_tenant_session()
        return None
    return port


@app.route("/login", methods=["GET", "POST"])
def login():
    next_target = normalize_next_target(request.values.get("next"), fallback=url_for("index"))
    authenticated_tenant = get_authenticated_tenant()
    if authenticated_tenant is not None:
        return redirect(tenant_panel_target(authenticated_tenant["tenant_token"]), code=303)
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
        xray_running=state.xray_running(),
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
    healthy = state.xray_running()
    status_code = 200 if healthy else 500
    return jsonify({"ok": healthy, "xray_running": healthy}), status_code


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"ok": True, "dashboard": build_dashboard_state()})


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
