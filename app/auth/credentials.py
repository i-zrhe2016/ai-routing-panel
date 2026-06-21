"""Credential checks for the three identities (admin, tenant, customer)."""

import base64
import hmac

from flask import request
from werkzeug.security import check_password_hash

from ..config import PANEL_PASSWORD, PANEL_USERNAME


def credentials_match(username, password):
    return hmac.compare_digest(str(username or ""), PANEL_USERNAME) and hmac.compare_digest(
        str(password or ""),
        PANEL_PASSWORD,
    )


def tenant_credentials_match(port, username, password):
    return hmac.compare_digest(str(username or ""), str(port.get("tenant_username") or "")) and hmac.compare_digest(
        str(password or ""),
        str(port.get("tenant_password") or ""),
    )


def customer_credentials_match(customer, password):
    password_hash = str(customer.get("password_hash") or "")
    if not password_hash:
        return False
    try:
        return check_password_hash(password_hash, str(password or ""))
    except ValueError:
        return False


def extract_basic_credentials():
    auth = request.authorization
    auth_type = str(getattr(auth, "type", "basic") or "basic").lower()
    if auth and auth_type == "basic":
        return auth.username or "", auth.password or ""

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return None
    return username, password
