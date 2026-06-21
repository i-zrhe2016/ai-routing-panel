import sqlite3

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
from ..subscriptions import parse_xray_client_profile
from .core import (
    build_customer_dashboard_state,
    build_customer_service_access,
    clear_customer_session,
    customer_dashboard_target,
    customer_login_target,
    get_authenticated_customer,
    require_csrf,
    route,
    state,
)


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


@route("/customer/login", methods=["GET", "POST"])
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


@route("/customer/logout", methods=["GET", "POST"])
def customer_logout():
    clear_customer_session()
    return redirect(url_for("plans_page"), code=303)


@route("/customer/dashboard", methods=["GET"])
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


@route("/customer/orders", methods=["GET"])
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


@route("/customer/orders/<order_no>", methods=["GET"])
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


@route("/customer/orders/<order_no>/payment-proof", methods=["POST"])
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


@route("/customer/subscriptions", methods=["GET"])
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


@route("/customer/subscriptions/<int:service_subscription_id>", methods=["GET"])
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
        return redirect(url_for("customer_order_detail", order_no=order_no), code=303)
    except ValidationError as exc:
        fallback_slug = str(request.form.get("plan_slug", "") or "").strip()
        if fallback_slug:
            return redirect(
                url_for("checkout_plan", plan_slug=fallback_slug, message=str(exc), level="error"),
                code=303,
            )
        return redirect(url_for("customer_dashboard", message=str(exc), level="error"), code=303)
