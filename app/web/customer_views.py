"""Public + customer-auth server-rendered routes.

The customer dashboard/orders/subscriptions VIEW pages are now the portal SPA
(/portal/*); the routes below for those paths are kept only as 302 redirects so
old links and bookmarks resolve. Login/register/plans stay server-rendered
(restyled with the design tokens), while renewal and payment-proof POST actions
send customers into the portal on success.
"""

import sqlite3
from urllib.parse import urlencode

from flask import Response, abort, redirect, render_template, request, url_for

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
from ..helpers import external_url_for
from .core import (
    clear_customer_session,
    get_authenticated_customer,
    bind_actor,
    log_business_event,
    require_csrf,
    route,
    state,
)

PORTAL_HOME = "/portal"


def _portal_order_url(order_no, **params):
    base = f"{PORTAL_HOME}/orders/{order_no}"
    return f"{base}?{urlencode(params)}" if params else base


# Single source of truth for landing-page SEO copy. Mirrors the Vue components
# (FeaturesSection.vue / FaqSection.vue) but is rendered server-side so crawlers
# get real content + structured data without executing JS. Keep these in sync
# with the SPA copy when it changes.
_LANDING_PRICE = "49"
_LANDING_CURRENCY = "CNY"
_LANDING_TITLE = "AI 家宽 · ChatGPT 防降智节点 · 原生家宽 IP · AI 自动分流 · 不超售"
_LANDING_DESCRIPTION = (
    "原生家宽 IP 出口，不被识别为机房、不触发 ChatGPT 风控降智。支持 ChatGPT / Claude / "
    "Gemini，AI 流量自动分流、独享不超售、多上游高可用、端到端数据安全。¥49/月起。"
)
_LANDING_KEYWORDS = (
    "ChatGPT防降智,AI家宽,原生家宽IP,住宅IP,AI节点,ChatGPT节点,Claude,Gemini,"
    "AI自动分流,不超售,高可用,机场,科学上网,数据安全"
)
_LANDING_FEATURES = [
    {"title": "原生家宽", "desc": "真实家庭宽带出口，不被识别为机房 IP——从根源上避开 ChatGPT 的风控降级，保住原生模型智力。"},
    {"title": "自动分流", "desc": "内置 AI 域名识别，自动把 ChatGPT、Claude、Gemini 的流量分流到优选直连线路，其余流量本地直连——既快又稳，不触发降智。"},
    {"title": "独享不超售", "desc": "独立端口与带宽配额，流量公开透明、绝不超卖。高峰期同样跑满，不与他人抢占资源。"},
    {"title": "多路高可用", "desc": "多上游冗余加 DNS 故障自动切换，节点异常时秒级切到备用线路，连通性实时探测，长期稳定在线。"},
    {"title": "端到端加密", "desc": "全程端到端加密，最小化日志、隐私优先。专属订阅与凭证仅你可见，对话内容不留存、不分析。"},
]
_LANDING_FAQS = [
    {"q": "什么是「防降智」？", "a": "线路被风控、共享 IP 或质量太差时，会触发 AI 服务商的降级策略，让模型回答变笨、变慢甚至拒答。我们把 AI 流量自动分流到优选直连线路，保住原生模型的满血智力。"},
    {"q": "为什么家宽 IP 能防降智？", "a": "ChatGPT 会对机房 / 数据中心 IP 加强风控，触发降级甚至封禁。原生家宽是真实家庭宽带出口，在服务商眼里就是普通住宅用户，从源头避免被降智。"},
    {"q": "你们会超售吗？", "a": "不会。每个套餐分配独立端口与带宽配额，流量公开透明、绝不超卖。高峰期也能跑满，不与他人抢资源。"},
    {"q": "节点掉线或不稳定怎么办？", "a": "多上游冗余加 DNS 故障自动切换，节点异常会秒级切到备用线路，连通性实时探测，长期稳定在线。"},
    {"q": "支持哪些客户端？", "a": "提供 Clash、V2Ray 订阅以及 VLESS 分享链接，主流客户端都能一键导入使用。"},
    {"q": "我的数据安全吗？", "a": "全程端到端加密，最小化日志、隐私优先。专属订阅与凭证仅你可见，对话内容不留存、不分析。"},
    {"q": "如何开始？", "a": "联系管理员确认套餐并完成开通后，系统会生成专属端口与订阅地址，导入客户端即可使用。"},
]


def _landing_seo():
    canonical = external_url_for("landing_page")
    og_image = external_url_for("static", filename="landing/og-image.png")
    plans_url = external_url_for("plans_page")
    org_name = "AI 家宽 · ChatGPT 防降智节点"
    # Pre-built JSON-LD graph: rendered as-is by the template via | tojson, so
    # there is no Jinja list-building gymnastics and the FAQ stays in one place.
    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": org_name,
            "url": canonical,
            "logo": og_image,
            "description": _LANDING_DESCRIPTION,
        },
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": org_name,
            "description": _LANDING_DESCRIPTION,
            "brand": {"@type": "Brand", "name": "AI 家宽"},
            "url": plans_url,
            "image": og_image,
            "offers": {
                "@type": "Offer",
                "price": _LANDING_PRICE,
                "priceCurrency": _LANDING_CURRENCY,
                "url": plans_url,
                "availability": "https://schema.org/InStock",
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
                }
                for item in _LANDING_FAQS
            ],
        },
    ]
    return {
        "title": _LANDING_TITLE,
        "description": _LANDING_DESCRIPTION,
        "keywords": _LANDING_KEYWORDS,
        "canonical": canonical,
        "og_image": og_image,
        "features": _LANDING_FEATURES,
        "faqs": _LANDING_FAQS,
        "price": _LANDING_PRICE,
        "currency": _LANDING_CURRENCY,
        "plans_url": plans_url,
        "jsonld": jsonld,
    }


@route("/home", methods=["GET"])
def landing_page():
    # Public marketing landing SPA (app/static/landing/*). Read-only: the shell
    # only needs a CSRF token; the page fetches GET /api/customer/plans for
    # live pricing. Allowlisted in ensure_basic_auth so it is publicly reachable.
    # SEO copy/structured-data is injected server-side via `seo` (see _landing_seo).
    return render_template(
        "landing.html",
        boot={"csrf_token": ensure_csrf_token()},
        seo=_landing_seo(),
    )


@route("/robots.txt", methods=["GET"])
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /home",
        "Allow: /plans",
        "Disallow: /api/",
        "Disallow: /portal",
        "Disallow: /customer/",
        "Disallow: /admin",
        "",
        f"Sitemap: {external_url_for('sitemap_xml')}",
        "",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    # Static lastmod (release date) so the document is stable between requests.
    lastmod = "2026-06-28"
    urls = [
        (external_url_for("landing_page"), "1.0"),
        (external_url_for("plans_page"), "0.8"),
    ]
    entries = "".join(
        f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>"
        f"<changefreq>weekly</changefreq><priority>{priority}</priority></url>"
        for loc, priority in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@route("/plans", methods=["GET"])
def plans_page():
    return render_template(
        "plans.html",
        plans=state.query_plans(public_only=True),
        current_customer=get_authenticated_customer(),
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
            bind_actor("customer", created_customer["id"])
            log_business_event("auth.customer.register", actor_type="customer", actor_id=created_customer["id"])
            return redirect(next_target, code=303)
        except sqlite3.IntegrityError:
            log_business_event("auth.customer.register", result="failure", actor_type="customer", error_code="conflict")
            return render_customer_register_page(
                next_target=next_target,
                form_email=email,
                error_message="该邮箱已注册，请直接登录。",
                status_code=409,
            )
        except ValidationError as exc:
            log_business_event(
                "auth.customer.register",
                result="failure",
                actor_type="customer",
                error_code="validation",
                message=str(exc),
            )
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
            bind_actor("customer", matched_customer["id"])
            log_business_event("auth.customer.login", actor_type="customer", actor_id=matched_customer["id"])
            return redirect(next_target, code=303)
        log_business_event("auth.customer.login", result="failure", actor_type="customer", error_code="invalid_credentials")
        return render_customer_login_page(
            next_target=next_target,
            form_email=email,
            error_message="邮箱或密码错误。",
            status_code=401,
        )

    return render_customer_login_page(next_target=next_target)


@route("/customer/logout", methods=["GET", "POST"])
def customer_logout():
    log_business_event("auth.customer.logout", actor_type="customer")
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
        log_business_event(
            "order.payment_proof_submitted",
            result="failure",
            actor_type="customer",
            actor_id=customer["id"],
            resource_type="order",
            resource_id=order_no,
            error_code="missing_proof",
        )
        return redirect(_portal_order_url(order_no, message="请先选择支付截图。", level="error"), code=303)
    try:
        state.submit_order_payment_submission(
            customer["id"],
            order_no,
            file_storage,
            request.form.get("payer_note", ""),
        )
        log_business_event("order.payment_proof_submitted", actor_type="customer", actor_id=customer["id"], resource_type="order", resource_id=order_no)
        return redirect(_portal_order_url(order_no, message="支付凭证已提交，等待人工审核。", level="success"), code=303)
    except ValidationError as exc:
        log_business_event("order.payment_proof_submitted", result="failure", actor_type="customer", actor_id=customer["id"], resource_type="order", resource_id=order_no, error_code="rejected", message=str(exc))
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
        log_business_event("subscription.renewed", actor_type="customer", actor_id=customer["id"], resource_type="subscription", resource_id=service_subscription_id, metadata={"order_no": order_no})
        return redirect(_portal_order_url(order_no), code=303)
    except ValidationError as exc:
        log_business_event("subscription.renewed", result="failure", actor_type="customer", actor_id=customer["id"], resource_type="subscription", resource_id=service_subscription_id, error_code="rejected", message=str(exc))
        query = urlencode({"message": str(exc), "level": "error"})
        return redirect(f"{PORTAL_HOME}/subscriptions/{service_subscription_id}?{query}", code=303)
