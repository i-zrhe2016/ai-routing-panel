import re
import unittest

from app.subscriptions import (
    _CLASH_RULE_PROVIDERS,
    _CLASH_RULES,
    build_clash_subscription_content,
)


class ClashSubscriptionRulesTest(unittest.TestCase):
    PROFILE = {
        "server": "panel.example.com",
        "uuid": "11111111-1111-1111-1111-111111111111",
        "flow": "xtls-rprx-vision",
        "server_name": "www.microsoft.com",
        "public_key": "public-key-example",
        "short_id": "0123456789abcdef",
        "fingerprint": "chrome",
    }

    def test_subscription_contains_lean_provider_set(self):
        content = build_clash_subscription_content(self.PROFILE, 443, "test")

        provider_names = {name for name, _ in _CLASH_RULE_PROVIDERS}
        self.assertEqual(len(provider_names), 25)
        self.assertNotIn("BanEasyList", content)
        self.assertNotIn("BanEasyListChina", content)
        self.assertNotIn("BanEasyPrivacy", content)
        self.assertNotIn("ChinaCompanyIp", content)
        for name in provider_names:
            self.assertIn(f"  {name}:\n", content)
            self.assertIn(f"RULE-SET,{name},", content)

    def test_rules_preserve_first_match_priority(self):
        content = build_clash_subscription_content(self.PROFILE, 443, "test")

        def position(rule):
            return content.index(f"  - {rule}")

        self.assertLess(position("RULE-SET,LAN,DIRECT"), position("RULE-SET,ADS,REJECT"))
        self.assertLess(position("RULE-SET,UNBAN,DIRECT"), position("RULE-SET,ADS,REJECT"))
        self.assertLess(position("RULE-SET,GOOGLE_CN,DIRECT"), position("RULE-SET,CHINA_DOMAIN,DIRECT"))
        self.assertLess(position("RULE-SET,NETFLIX,PROXY"), position("RULE-SET,CHINA_DOMAIN,DIRECT"))
        self.assertLess(position("RULE-SET,PROXY_GFW,PROXY"), position("RULE-SET,CHINA_DOMAIN,DIRECT"))
        self.assertLess(position("GEOIP,CN,DIRECT"), position("MATCH,PROXY"))

    def test_every_rule_set_references_a_declared_provider(self):
        provider_names = {name for name, _ in _CLASH_RULE_PROVIDERS}
        referenced = {
            match.group(1)
            for rule in _CLASH_RULES
            if (match := re.fullmatch(r"RULE-SET,([^,]+),[^,]+", rule))
        }

        self.assertEqual(referenced, provider_names)

    def test_subscription_is_rendered_as_lines(self):
        content = build_clash_subscription_content(self.PROFILE, 443, "test")

        self.assertIn("\nrule-providers:\n", content)
        self.assertIn("\nrules:\n", content)
        self.assertTrue(content.endswith("\n"))
        self.assertNotIn("\\n", content)
