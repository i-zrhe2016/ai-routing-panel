from datetime import datetime
import sqlite3

from flask import redirect, render_template, request, session, url_for

from ..auth import (
    credentials_match,
    ensure_csrf_token,
    mark_session_authenticated,
    mark_tenant_session_authenticated,
    normalize_next_target,
    render_login_page,
    tenant_credentials_match,
)
from ..config import (
    AUTH_ENABLED,
    PANEL_HOST,
    PANEL_PORT,
    PANEL_PUBLIC_URL,
    PROBE_ENABLED,
)
from ..errors import ValidationError
from .core import (
    customer_dashboard_target,
    get_authenticated_customer,
    get_authenticated_tenant,
    is_session_authenticated,
    message_redirect,
    require_csrf,
    route,
    state,
    tenant_panel_target,
)
from .sqlite_errors import is_listen_port_conflict


@route("/login", methods=["GET", "POST"])
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
        require_csrf()
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


@route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login", message="已退出登录。", level="info"), code=303)


@route("/", methods=["GET"])
def index():
    # The admin is now a built SPA (app/static/admin/*). The shell only needs the
    # CSRF token and auth flag; the SPA fetches GET /api/dashboard on mount (which
    # runs the same build_dashboard_state side effects the page render used to).
    return render_template(
        "index.html",
        boot={"csrf_token": ensure_csrf_token(), "auth_enabled": AUTH_ENABLED},
    )


@route("/probe-dashboard", methods=["GET"])
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


@route("/ai-domain-dashboard", methods=["GET"])
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


@route("/ports/create", methods=["POST"])
def create_port():
    try:
        payload = state.validate_port_payload(request.form)
        state.create_port(payload)
        return message_redirect("端口已创建并写入 Xray。", "success")
    except sqlite3.IntegrityError as exc:
        if is_listen_port_conflict(exc):
            return message_redirect("监听端口已存在，请更换其他端口。", "error")
        raise
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@route("/ports/<int:port_id>/update", methods=["POST"])
def update_port(port_id):
    try:
        payload = state.validate_port_payload(request.form)
        state.update_port(port_id, payload)
        return message_redirect("端口配置已更新。", "success")
    except sqlite3.IntegrityError as exc:
        if is_listen_port_conflict(exc):
            return message_redirect("监听端口已存在，请更换其他端口。", "error")
        raise
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@route("/ports/<int:port_id>/toggle", methods=["POST"])
def toggle_port(port_id):
    try:
        state.toggle_port(port_id)
        return message_redirect("端口状态已切换。", "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@route("/ports/<int:port_id>/delete", methods=["POST"])
def delete_port(port_id):
    try:
        state.delete_port(port_id)
        return message_redirect("端口已删除。", "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")


@route("/ports/<int:port_id>/reset-traffic", methods=["POST"])
def reset_port_traffic(port_id):
    try:
        restored = state.reset_port_traffic(port_id)
        message = "流量已重置，端口已恢复启用。" if restored else "流量已重置。"
        return message_redirect(message, "success")
    except (ValidationError, RuntimeError) as exc:
        return message_redirect(str(exc), "error")
