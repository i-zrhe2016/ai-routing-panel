"""Public + customer-auth server-rendered routes.

The customer dashboard/orders/subscriptions VIEW pages are now the portal SPA
(/portal/*); the routes below for those paths are kept only as 302 redirects so
old links and bookmarks resolve. Login/register/plans/checkout stay
server-rendered (restyled with the design tokens), and the POST action endpoints
(create order, payment proof, renew) remain — they now send customers into the
portal on success.
"""

import sqlite3
from urllib.parse import urlencode

from flask import abort, redirect, render_template, request, url_for

from ..auth import (
    customer_auth_required_response,
    customer_credentials_match,
    ensure_csrf_token,
    mark_customer_session_authenticated,
    normalize_next_target,
    render_customer_login_page,
    render_customer_register_page,
)
from ..errors import ValidationError
from .core import (
    clear_customer_session,
    customer_login_target,
    get_authenticated_customer,
    require_csrf,
    route,
    state,
)

PORTAL_HOME = "/portal"


def _portal_order_url(order_no, **params):
    base = f"{PORTAL_HOME}/orders/{order_no}"
    return f"{base}?{urlencode(params)}" if params else base


@route("/plans", methods=["GET"])
def plans_page():
    return render_template(
        "plans.html",
        plans=state.query_plans(public_only=True),
        current_customer=get_authenticated_customer(),
        commerce_settings=state.get_commerce_settings(),
    )


@route("/checkout/<plan_slug>", methods=["GET"])
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


@route("/customer/register", methods=["GET", "POST"])
def customer_register():
    next_target = normalize_next_target(request.values.get("next"), fallback=PORTAL_HOME)
    customer = get_authenticated_customer()
    if customer is not None:
        return redirect(next_target, code=303)

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


@route("/customer/login", methods=["GET", "POST"])
def customer_login():
    next_target = normalize_next_target(request.values.get("next"), fallback=PORTAL_HOME)
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


@route("/customer/logout", methods=["GET", "POST"])
def customer_logout():
    clear_customer_session()
    return redirect(url_for("plans_page"), code=303)


# --- Legacy customer view pages, now served by the portal SPA. Kept as 302
#     redirects so existing links/bookmarks resolve. ---
@route("/customer/dashboard", methods=["GET"])
def customer_dashboard():
    return redirect(PORTAL_HOME, code=302)


@route("/customer/orders", methods=["GET"])
def customer_orders():
    return redirect(f"{PORTAL_HOME}/orders", code=302)


@route("/customer/orders/<order_no>", methods=["GET"])
def customer_order_detail(order_no):
    return redirect(_portal_order_url(order_no), code=302)


@route("/customer/subscriptions", methods=["GET"])
def customer_subscriptions():
    return redirect(f"{PORTAL_HOME}/subscriptions", code=302)


@route("/customer/subscriptions/<int:service_subscription_id>", methods=["GET"])
def customer_subscription_detail(service_subscription_id):
    return redirect(f"{PORTAL_HOME}/subscriptions/{service_subscription_id}", code=302)


@route("/customer/orders/<order_no>/payment-proof", methods=["POST"])
def customer_submit_order_payment_proof(order_no):
    customer = get_authenticated_customer()
    if customer is None:
        return customer_auth_required_response()
    require_csrf()
    file_storage = request.files.get("proof_image")
    if file_storage is None:
        return redirect(_portal_order_url(order_no, message="请先选择支付截图。", level="error"), code=303)
    try:
        state.submit_order_payment_submission(
            customer["id"],
            order_no,
            file_storage,
            request.form.get("payer_note", ""),
        )
        return redirect(_portal_order_url(order_no, message="支付凭证已提交，等待人工审核。", level="success"), code=303)
    except ValidationError as exc:
        return redirect(_portal_order_url(order_no, message=str(exc), level="error"), code=303)


@route("/customer/subscriptions/<int:service_subscription_id>/renew", methods=["POST"])
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
        return redirect(_portal_order_url(order_no), code=303)
    except ValidationError as exc:
        query = urlencode({"message": str(exc), "level": "error"})
        return redirect(f"{PORTAL_HOME}/subscriptions/{service_subscription_id}?{query}", code=303)


@route("/orders", methods=["POST"])
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
        return redirect(_portal_order_url(order_no), code=303)
    except ValidationError as exc:
        fallback_slug = str(request.form.get("plan_slug", "") or "").strip()
        if fallback_slug:
            return redirect(
                url_for("checkout_plan", plan_slug=fallback_slug, message=str(exc), level="error"),
                code=303,
            )
        return redirect(PORTAL_HOME, code=303)
