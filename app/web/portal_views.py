"""Shell routes for the subscriber-portal SPA.

`/portal` and the `/portal/<path>` catch-all both render the same shell so
vue-router history-mode deep links resolve. The shell is public (the SPA gates
itself via /api/customer/me); the injected boot blob carries the CSRF token and
the current identity (or null).
"""

from flask import render_template

from ..auth import ensure_csrf_token
from .core import get_authenticated_customer, route


def _portal_boot():
    customer = get_authenticated_customer()
    me = {"id": customer["id"], "email": customer["email"]} if customer is not None else None
    return {"csrf_token": ensure_csrf_token(), "me": me}


@route("/portal", methods=["GET"])
def portal_shell():
    return render_template("portal.html", boot=_portal_boot())


@route("/portal/<path:subpath>", methods=["GET"])
def portal_shell_path(subpath):
    return render_template("portal.html", boot=_portal_boot())
