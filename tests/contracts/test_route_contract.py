import os
from pathlib import Path

import pytest

from app.web import create_app
from app.web.core import ensure_allowed_panel_source


GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "control-plane-routes.tsv"

_ACCESS_BOUNDARIES = {
    "api_customer_me": "customer-session",
    "api_customer_overview": "customer-session",
    "api_customer_subscriptions": "customer-session",
    "api_customer_subscription_detail": "customer-session",
    "api_customer_subscription_renew": "customer-session",
    "api_customer_orders": "customer-session",
    "api_customer_order_detail": "customer-session",
    "api_customer_submit_payment_proof": "customer-session",
    "customer_dashboard": "customer-session",
    "customer_orders": "customer-session",
    "customer_order_detail": "customer-session",
    "customer_subscriptions": "customer-session",
    "customer_subscription_detail": "customer-session",
    "customer_subscription_renew": "customer-session",
    "customer_submit_order_payment_proof": "customer-session",
    "api_tenant_subscription": "tenant-session",
    "subscription_default": "subscription-token",
    "subscription_clash": "subscription-token",
    "subscription_v2ray": "subscription-token",
    "tenant_subscription_default": "subscription-token",
    "tenant_subscription_clash": "subscription-token",
    "tenant_subscription_v2ray": "subscription-token",
    "payment_proof_file": "optional-customer-ownership",
    "tenant_panel": "tenant-token-shell",
    "tenant_login": "tenant-token-credentials",
    "api_tenant_login": "tenant-token-credentials",
    "tenant_logout": "tenant-token-route",
}


def _access_class(endpoint):
    boundary = _ACCESS_BOUNDARIES.get(endpoint)
    if boundary is None:
        return "source-allowlist"
    return f"source-allowlist+{boundary}"


def _registered_routes(flask_app):
    routes = []
    for rule in flask_app.url_map.iter_rules():
        view = flask_app.view_functions[rule.endpoint]
        methods = ",".join(sorted(rule.methods))
        routes.append(
            f"{methods}|{rule.rule}|{rule.endpoint}|{view.__module__}|{_access_class(rule.endpoint)}"
        )
    return "\n".join(sorted(routes)) + "\n"


@pytest.fixture(scope="module")
def flask_app():
    return create_app(object())


def test_registered_route_surface_matches_the_golden_snapshot(flask_app):
    actual = _registered_routes(flask_app)
    if os.environ.get("CONTROL_PLANE_UPDATE_GOLDENS") == "1":
        GOLDEN_PATH.write_text(actual, encoding="utf-8")
    assert actual == GOLDEN_PATH.read_text(encoding="utf-8")


def test_every_route_inherits_the_panel_source_allowlist(flask_app):
    hooks = flask_app.before_request_funcs[None]

    assert ensure_allowed_panel_source in hooks
    assert all(
        route.rsplit("|", maxsplit=1)[-1].startswith("source-allowlist")
        for route in _registered_routes(flask_app).splitlines()
    )
    with flask_app.test_client() as client:
        for path in ("/", "/api/dashboard", "/api/customer/plans", "/healthz"):
            response = client.get(
                path,
                environ_overrides={"REMOTE_ADDR": "203.0.113.27"},
            )
            assert response.status_code == 403
