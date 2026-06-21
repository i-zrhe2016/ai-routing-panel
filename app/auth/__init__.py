"""Authentication package.

Split from the former app/auth.py module into focused submodules. Every public
name is re-exported here so existing ``from app.auth import X`` imports keep
working unchanged.
"""

from .credentials import (
    credentials_match,
    customer_credentials_match,
    extract_basic_credentials,
    tenant_credentials_match,
)
from .csrf import ensure_csrf_token, validate_csrf_token
from .responses import (
    auth_required_response,
    current_request_target,
    customer_auth_required_response,
    customer_login_url_for_request,
    login_next_target_for_request,
    login_url_for_request,
    normalize_next_target,
    render_customer_login_page,
    render_customer_register_page,
    render_login_page,
    render_tenant_login_page,
)
from .sessions import (
    clear_admin_session,
    clear_customer_session,
    clear_tenant_session,
    customer_session_marker,
    is_customer_session_authenticated,
    is_session_authenticated,
    is_tenant_session_authenticated,
    mark_customer_session_authenticated,
    mark_session_authenticated,
    mark_tenant_session_authenticated,
    tenant_session_marker,
)

__all__ = [
    "auth_required_response",
    "clear_admin_session",
    "clear_customer_session",
    "clear_tenant_session",
    "credentials_match",
    "current_request_target",
    "customer_auth_required_response",
    "customer_credentials_match",
    "customer_login_url_for_request",
    "customer_session_marker",
    "ensure_csrf_token",
    "extract_basic_credentials",
    "is_customer_session_authenticated",
    "is_session_authenticated",
    "is_tenant_session_authenticated",
    "login_next_target_for_request",
    "login_url_for_request",
    "mark_customer_session_authenticated",
    "mark_session_authenticated",
    "mark_tenant_session_authenticated",
    "normalize_next_target",
    "render_customer_login_page",
    "render_customer_register_page",
    "render_login_page",
    "render_tenant_login_page",
    "tenant_credentials_match",
    "tenant_session_marker",
    "validate_csrf_token",
]
