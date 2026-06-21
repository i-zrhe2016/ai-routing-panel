"""Session markers for the admin, tenant, and customer identities.

Each ``mark_*`` clears the other two so a session never holds more than one
identity at a time. Markers are SHA256 hashes over the identity's stable fields
so a credential or status change invalidates an existing session.
"""

import hashlib

from flask import session

from ..config import (
    AUTH_ENABLED,
    AUTH_SESSION_KEY,
    AUTH_SESSION_MARKER,
    CUSTOMER_SESSION_ID_KEY,
    CUSTOMER_SESSION_MARKER_KEY,
    TENANT_SESSION_MARKER_KEY,
    TENANT_SESSION_TOKEN_KEY,
)


def is_session_authenticated():
    return AUTH_ENABLED and session.get(AUTH_SESSION_KEY) == AUTH_SESSION_MARKER


def clear_admin_session():
    session.pop(AUTH_SESSION_KEY, None)


def clear_tenant_session():
    session.pop(TENANT_SESSION_TOKEN_KEY, None)
    session.pop(TENANT_SESSION_MARKER_KEY, None)


def clear_customer_session():
    session.pop(CUSTOMER_SESSION_ID_KEY, None)
    session.pop(CUSTOMER_SESSION_MARKER_KEY, None)


def mark_session_authenticated():
    clear_customer_session()
    clear_tenant_session()
    session[AUTH_SESSION_KEY] = AUTH_SESSION_MARKER


def tenant_session_marker(port):
    return hashlib.sha256(
        f"{port.get('tenant_token', '')}\0{port.get('tenant_username', '')}\0{port.get('tenant_password', '')}".encode(
            "utf-8"
        )
    ).hexdigest()


def is_tenant_session_authenticated(port):
    expected_token = str(port.get("tenant_token") or "")
    expected_marker = tenant_session_marker(port)
    return session.get(TENANT_SESSION_TOKEN_KEY) == expected_token and session.get(TENANT_SESSION_MARKER_KEY) == expected_marker


def mark_tenant_session_authenticated(port):
    clear_admin_session()
    clear_customer_session()
    clear_tenant_session()
    session[TENANT_SESSION_TOKEN_KEY] = str(port.get("tenant_token") or "")
    session[TENANT_SESSION_MARKER_KEY] = tenant_session_marker(port)


def customer_session_marker(customer):
    return hashlib.sha256(
        f"{customer.get('id', '')}\0{customer.get('email', '')}\0{customer.get('password_hash', '')}\0{customer.get('status', '')}".encode(
            "utf-8"
        )
    ).hexdigest()


def is_customer_session_authenticated(customer):
    return session.get(CUSTOMER_SESSION_ID_KEY) == int(customer.get("id") or 0) and session.get(
        CUSTOMER_SESSION_MARKER_KEY
    ) == customer_session_marker(customer)


def mark_customer_session_authenticated(customer):
    clear_admin_session()
    clear_tenant_session()
    clear_customer_session()
    session[CUSTOMER_SESSION_ID_KEY] = int(customer.get("id") or 0)
    session[CUSTOMER_SESSION_MARKER_KEY] = customer_session_marker(customer)
