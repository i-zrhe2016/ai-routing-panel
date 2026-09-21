"""Unit tests for the panel source-address allowlist."""

import importlib
import os
import sys
import unittest


def load_config(allowed_networks=None):
    if allowed_networks is None:
        os.environ.pop("PANEL_ALLOWED_NETWORKS", None)
    else:
        os.environ["PANEL_ALLOWED_NETWORKS"] = allowed_networks
    for name in [name for name in sys.modules if name == "app.config" or name.startswith("app.config.")]:
        sys.modules.pop(name, None)
    return importlib.import_module("app.config")


class PanelAllowedNetworkTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("PANEL_ALLOWED_NETWORKS", None)

    def test_default_allowlist_covers_internal_and_tailscale_sources(self):
        config = load_config()
        for address in ("127.0.0.1", "::1", "10.1.2.3", "172.18.0.4", "192.168.1.9", "100.100.100.100", "fd7a:115c:a1e0::1"):
            self.assertTrue(config.is_allowed_panel_source(address), address)

    def test_default_allowlist_rejects_public_sources(self):
        config = load_config()
        for address in ("203.0.113.9", "8.8.8.8", "2001:4860:4860::8888", "", "not-an-ip", None):
            self.assertFalse(config.is_allowed_panel_source(address), address)

    def test_ipv4_mapped_ipv6_addresses_use_the_ipv4_network(self):
        config = load_config()
        self.assertTrue(config.is_allowed_panel_source("::ffff:127.0.0.1"))

    def test_explicit_allowlist_replaces_the_default(self):
        config = load_config("192.0.2.0/24")
        self.assertTrue(config.is_allowed_panel_source("192.0.2.10"))
        self.assertFalse(config.is_allowed_panel_source("127.0.0.1"))

    def test_invalid_cidr_fails_import(self):
        with self.assertRaises(ValueError):
            load_config("127.0.0.1/not-a-prefix")
        load_config()


if __name__ == "__main__":
    unittest.main()
