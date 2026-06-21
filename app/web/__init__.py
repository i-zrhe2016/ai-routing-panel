"""Web package.

Splits the former single-file app/web.py into per-domain view modules plus a
shared app/web/core.py (the app factory, the route/hook/filter collectors, the
PanelState singleton, the before_request guards and the presenter helpers).

Importing the view modules has the side effect of populating the collectors via
their @route decorators; create_app() is then called once here to build the app.
The public names (app, state, main, create_app) are re-exported so
`from app.web import app, main, state` and the test harness keep working.
"""

from . import core
from .core import (
    before_request,
    create_app,
    handle_shutdown,
    main,
    route,
    state,
    template_filter,
)

# Side-effect imports: each module's @route decorators register into the
# collectors that create_app() reads below. Keep importing all of them.
from . import (  # noqa: F401  (imported for route-registration side effects)
    admin_api,
    admin_views,
    customer_api,
    customer_views,
    health,
    portal_views,
    subscription_views,
    tenant_views,
)

# Built once at import so `from app.web import app` and the test harness's
# module.app keep working. Re-imports of the package rebuild a fresh app.
app = create_app()
# Hand the built app back to core so core.main() can run the WSGI server.
core.app = app

__all__ = [
    "app",
    "before_request",
    "create_app",
    "handle_shutdown",
    "main",
    "route",
    "state",
    "template_filter",
]
