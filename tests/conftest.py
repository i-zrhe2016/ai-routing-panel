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
It is behavior-preserving for the application code; it only affects test setup.
"""

import sys

import pytest


def _app_module_names():
    return [name for name in sys.modules if name == "app" or name.startswith("app.")]


@pytest.fixture(autouse=True)
def _isolate_app_modules():
    saved = {name: sys.modules[name] for name in _app_module_names()}
    try:
        yield
    finally:
        for name in _app_module_names():
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        # Restoring the sys.modules dict is not enough: a reload rebinds the
        # parent package's submodule attribute (e.g. app.xray.google_search_mcp),
        # which mock.patch's dotted-name lookup walks. Re-attach each saved
        # submodule to its parent so string-target patches resolve correctly.
        for name, module in saved.items():
            parent_name, _, child = name.rpartition(".")
            if parent_name and parent_name in sys.modules:
                setattr(sys.modules[parent_name], child, module)
