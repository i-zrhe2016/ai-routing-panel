"""Credential checks for the tenant and customer identities."""

import hmac

from werkzeug.security import check_password_hash

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
