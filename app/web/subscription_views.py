from flask import abort, send_file

from ..config import AUTH_ENABLED, PAYMENT_PROOFS_DIR
from .core import (
    build_port_token_subscription_response,
    build_subscription_response,
    get_authenticated_customer,
    is_session_authenticated,
    message_redirect,
    route,
    log_business_event,
    state,
)


@route("/subscriptions/rotate", methods=["POST"])
def rotate_subscription():
    state.rotate_subscription_token()
    log_business_event("subscription.rotated", resource_type="subscription")
    return message_redirect("订阅链接已重新生成，旧链接已失效。", "success")


@route("/<token>/<int:listen_port>", methods=["GET"])
def subscription_default(token, listen_port):
    return build_subscription_response(token, listen_port, "clash")


@route("/<token>/<int:listen_port>/clash", methods=["GET"])
def subscription_clash(token, listen_port):
    return build_subscription_response(token, listen_port, "clash")


@route("/<token>/<int:listen_port>/v2ray", methods=["GET"])
def subscription_v2ray(token, listen_port):
    return build_subscription_response(token, listen_port, "v2ray")


@route("/tenant-subscriptions/<subscription_token>", methods=["GET"])
def tenant_subscription_default(subscription_token):
    return build_port_token_subscription_response(subscription_token, "clash")


@route("/tenant-subscriptions/<subscription_token>/clash", methods=["GET"])
def tenant_subscription_clash(subscription_token):
    return build_port_token_subscription_response(subscription_token, "clash")


@route("/tenant-subscriptions/<subscription_token>/v2ray", methods=["GET"])
def tenant_subscription_v2ray(subscription_token):
    return build_port_token_subscription_response(subscription_token, "v2ray")


@route("/payment-proofs/<int:submission_id>", methods=["GET"])
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
