"""Shared pytest fixtures for the test suite.

The panel tests (``test_commerce``, ``test_tenant_panel``) rebuild the app under
a temporary ``/data`` + ``/xray`` sandbox by popping every ``app.*`` entry from
``sys.modules`` and re-importing ``app.panel`` (see ``load_panel_module``). That
reload mutates the *global* module table, so a later test file whose top-level
``from app.xray import X`` was bound at collection time ends up holding a stale
module object while ``mock.patch("app.xray.X....")`` patches the reloaded one —
the patch silently misses and real network code runs.

The autouse fixture below snapshots the ``app.*`` modules around each test and
restores them afterwards, isolating that reload so it cannot leak across files.
It also restores the retained Web module's route collectors: removing newly
imported views without rolling back their registrations would append duplicate
handlers on the next import. It only affects test setup.
"""

import sys
from types import ModuleType

import pytest


def _app_module_names():
    return [name for name in sys.modules if name == "app" or name.startswith("app.")]


def _is_app_module(value):
    return isinstance(value, ModuleType) and (value.__name__ == "app" or value.__name__.startswith("app."))


@pytest.fixture(autouse=True)
def _isolate_app_modules():
    saved = {name: sys.modules[name] for name in _app_module_names()}
    saved_package_attrs = {
        name: dict(vars(module))
        for name, module in saved.items()
        if hasattr(module, "__path__")
    }
    web_core = saved.get("app.web.core")
    saved_registrations = {
        name: list(getattr(web_core, name))
        for name in ("_ROUTES", "_BEFORE_REQUEST", "_TEMPLATE_FILTERS")
        if web_core is not None
    }
    try:
        yield
    finally:
        # The saved core survives module rollback, so its decorator registries
        # must match the saved view modules rather than retain fresh imports.
        for name, registrations in saved_registrations.items():
            getattr(web_core, name)[:] = registrations
        for name in _app_module_names():
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        # Imports also bind children on retained packages. Restore those
        # bindings, including removing children first imported by this test;
        # otherwise `from app import config` can bypass the restored module table.
        for name, original_attrs in saved_package_attrs.items():
            package = saved[name]
            current_attrs = vars(package)
            for child in original_attrs.keys() | current_attrs.keys():
                if not (_is_app_module(original_attrs.get(child)) or _is_app_module(current_attrs.get(child))):
                    continue
                if child in original_attrs:
                    setattr(package, child, original_attrs[child])
                else:
                    delattr(package, child)
