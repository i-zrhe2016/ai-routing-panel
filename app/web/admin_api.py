import sqlite3

from flask import jsonify, request

from ..errors import ValidationError
from .core import (
    build_dashboard_state,
    json_error_response,
    json_success_response,
    request_payload,
    require_csrf,
    route,
    state,
)


@route("/api/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"ok": True, "dashboard": build_dashboard_state()})


@route("/api/plans", methods=["GET"])
def api_plans():
    return jsonify({"ok": True, "plans": state.query_plans(public_only=False)})


@route("/api/plans", methods=["POST"])
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


@route("/api/plans/<int:plan_id>", methods=["PUT"])
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


@route("/api/orders", methods=["GET"])
def api_orders():
    return jsonify({"ok": True, "orders": state.query_admin_orders(request.args.get("status", "").strip())})


@route("/api/orders/<int:order_id>/fulfill", methods=["POST"])
def api_fulfill_order(order_id):
    require_csrf()
    try:
        state.fulfill_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已审核通过并完成开通。")
    except sqlite3.IntegrityError:
        return json_error_response("自动分配端口时出现冲突，请重试。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/orders/<int:order_id>/reject", methods=["POST"])
def api_reject_order(order_id):
    require_csrf()
    try:
        state.reject_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已驳回，客户可重新提交支付凭证。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/orders/<int:order_id>/cancel", methods=["POST"])
def api_cancel_order(order_id):
    require_csrf()
    try:
        state.cancel_order(order_id, request_payload().get("review_note", ""))
        return json_success_response("订单已取消。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/commerce-settings", methods=["GET"])
def api_commerce_settings():
    return jsonify({"ok": True, "settings": state.get_commerce_settings()})


@route("/api/commerce-settings", methods=["PUT"])
def api_update_commerce_settings():
    require_csrf()
    try:
        state.update_commerce_settings(request_payload())
        return json_success_response("商业化设置已更新。")
    except ValidationError as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/dns-failover", methods=["GET"])
def api_dns_failover_status():
    return jsonify({"ok": True, "status": state.dns_failover_status()})


@route("/api/dns-failover/check", methods=["POST"])
def api_dns_failover_check():
    try:
        state.run_dns_failover_check(force=True)
        return json_success_response("DNS 故障切换已执行一次即时检测。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/dns-failover/switch", methods=["POST"])
def api_dns_failover_switch():
    try:
        payload = request_payload()
        state.switch_dns_target(payload.get("target"))
        return json_success_response("DNS 记录已更新。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/subscriptions/rotate", methods=["POST"])
def api_rotate_subscription():
    state.rotate_subscription_token()
    return json_success_response("订阅链接已重新生成，旧链接已失效。")


@route("/api/ports", methods=["POST"])
def api_create_port():
    try:
        payload = state.validate_port_payload(request_payload())
        state.create_port(payload)
        return json_success_response("端口已创建并写入 Xray。", status_code=201)
    except sqlite3.IntegrityError:
        return json_error_response("监听端口已存在，请更换其他端口。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>", methods=["PUT"])
def api_update_port(port_id):
    try:
        payload = state.validate_port_payload(request_payload())
        state.update_port(port_id, payload)
        return json_success_response("端口配置已更新。")
    except sqlite3.IntegrityError:
        return json_error_response("监听端口已存在，请更换其他端口。", status_code=409)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>/toggle", methods=["POST"])
def api_toggle_port(port_id):
    try:
        state.toggle_port(port_id)
        return json_success_response("端口状态已切换。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>", methods=["DELETE"])
def api_delete_port(port_id):
    try:
        state.delete_port(port_id)
        return json_success_response("端口已删除。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>/reset-traffic", methods=["POST"])
def api_reset_port_traffic(port_id):
    try:
        restored = state.reset_port_traffic(port_id)
        message = "流量已重置，端口已恢复启用。" if restored else "流量已重置。"
        return json_success_response(message)
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>/rotate-tenant-token", methods=["POST"])
def api_rotate_port_tenant_token(port_id):
    try:
        state.rotate_port_tenant_token(port_id)
        return json_success_response("租户面板地址已重置，旧链接已失效。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>/rotate-tenant-credentials", methods=["POST"])
def api_rotate_port_tenant_credentials(port_id):
    try:
        state.rotate_port_tenant_credentials(port_id)
        return json_success_response("租户登录用户名和密码已重置。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ports/<int:port_id>/rotate-subscription-token", methods=["POST"])
def api_rotate_port_subscription_token(port_id):
    try:
        state.rotate_port_subscription_token(port_id)
        return json_success_response("租户订阅地址已重置，旧链接已失效。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/data-plane/restart", methods=["POST"])
def api_restart_data_plane():
    try:
        state.restart_data_plane_or_raise()
        return json_success_response("数据面已执行重启。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/data-plane/diagnose", methods=["POST"])
def api_diagnose_data_plane():
    try:
        return jsonify({"ok": True, "diagnosis": state.diagnose_data_plane()})
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)


@route("/api/ai-node/status", methods=["GET"])
def api_ai_node_status():
    return jsonify({"ok": True, "status": state.ai_node_status()})


@route("/api/ai-node/restart", methods=["POST"])
def api_restart_ai_node():
    try:
        state.restart_ai_node_or_raise()
        return json_success_response("AI 节点已执行重启。")
    except (ValidationError, RuntimeError) as exc:
        return json_error_response(str(exc), status_code=400)
