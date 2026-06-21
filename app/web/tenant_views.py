from flask import abort, redirect, render_template, request

from ..auth import (
    mark_tenant_session_authenticated,
    render_tenant_login_page,
    tenant_credentials_match,
)
from .core import (
    build_tenant_dashboard_state,
    clear_tenant_session,
    is_session_authenticated,
    is_tenant_session_authenticated,
    route,
    state,
    tenant_login_target,
    tenant_panel_target,
)


@route("/tenant/<tenant_token>/login", methods=["GET", "POST"])
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


@route("/tenant/<tenant_token>/logout", methods=["GET", "POST"])
def tenant_logout(tenant_token):
    clear_tenant_session()
    return redirect(tenant_login_target(tenant_token, message="已退出登录。", level="info"), code=303)


@route("/tenant/<tenant_token>", methods=["GET"])
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
