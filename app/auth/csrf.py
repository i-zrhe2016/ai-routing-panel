"""CSRF token issue/verify, backed by the Flask session."""

import hmac
import secrets

from flask import session

from ..config import CSRF_SESSION_KEY


def ensure_csrf_token():
    token = str(session.get(CSRF_SESSION_KEY) or "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(value):
    expected = str(session.get(CSRF_SESSION_KEY) or "").strip()
    return bool(expected) and hmac.compare_digest(expected, str(value or "").strip())
