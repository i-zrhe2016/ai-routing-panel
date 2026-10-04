import json
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

    def first_domain_match(self, domain, provider_matches=(), country=None):
        """Evaluate emitted domain rules with synthetic remote-provider matches."""
        content = build_clash_subscription_content(self.PROFILE, 443, "test")
        for line in content.split("\nrules:\n", 1)[1].splitlines():
            rule = line.removeprefix("  - ")
            parts = rule.split(",")
            kind, value = parts[:2]
            matched = (
                (kind == "DOMAIN" and domain == value)
                or (kind == "DOMAIN-SUFFIX" and (domain == value or domain.endswith("." + value)))
                or (kind == "RULE-SET" and value in provider_matches)
                or (kind == "GEOIP" and country == value)
                or kind == "MATCH"
            )
            if matched:
                return parts[-1], rule
        self.fail("subscription has no fallback rule")

    def test_common_services_and_dependencies_win_before_broad_providers(self):
        domains = (
            "chatgpt.com", "api.openai.com", "cdn.oaistatic.com", "files.oaiusercontent.com",
            "openai.auth0.com", "chatgpt.livekit.cloud", "claude.ai", "claude.com",
            "api.anthropic.com", "files.claudeusercontent.com", "challenges.cloudflare.com",
            "gemini.google.com", "aistudio.google.com", "generativelanguage.googleapis.com",
            "ai.google.dev", "github.com", "raw.githubusercontent.com", "assets.githubassets.com",
            "example.github.io", "api.githubcopilot.com", "githubapp.com", "githubstatus.com", "discord.com",
            "discord.gg", "cdn.discordapp.com", "media.discordapp.net", "gateway.discord.gg",
            "discord.media", "discord.gift", "discordactivities.com", "discordstatus.com", "dis.gd",
            "web.whatsapp.com", "mmg.whatsapp.net", "wa.me",
        )
        for domain in domains:
            with self.subTest(domain=domain):
                target, rule = self.first_domain_match(
                    domain, {"GOOGLE_CN", "CHINA_DOMAIN", "PROXY_GFW"}, country="CN",
                )
                self.assertEqual(target, "PROXY")
                self.assertTrue(rule.startswith(("DOMAIN,", "DOMAIN-SUFFIX,")), rule)

    def test_common_rules_preserve_narrow_exceptions_and_direct_china(self):
        for provider, expected in (
            ("LAN", "DIRECT"), ("UNBAN", "DIRECT"), ("ADS", "REJECT"),
            ("PROGRAM_ADS", "REJECT"), ("DOWNLOAD", "DIRECT"),
        ):
            with self.subTest(provider=provider):
                target, _ = self.first_domain_match("github.com", {provider, "CHINA_DOMAIN"})
                self.assertEqual(target, expected)
        self.assertEqual(self.first_domain_match("printer.lan")[0], "DIRECT")
        self.assertEqual(self.first_domain_match("example.cn", {"CHINA_DOMAIN"})[0], "DIRECT")
        self.assertEqual(self.first_domain_match("example.cn", country="CN")[0], "DIRECT")
        self.assertEqual(self.first_domain_match("unclassified.example")[0], "PROXY")

    def test_service_suffixes_do_not_capture_unrelated_domains(self):
        for domain in ("notgithub.com", "github.com.example.cn", "notwhatsapp.net", "other.auth0.com"):
            with self.subTest(domain=domain):
                self.assertEqual(self.first_domain_match(domain, {"CHINA_DOMAIN"})[0], "DIRECT")

    def test_subscription_quotes_proxy_name_without_injecting_yaml(self):
        content = build_clash_subscription_content(self.PROFILE, 443, 'service: "test" # note\nrules: []')
        name_line = next(line for line in content.splitlines() if line.startswith("  - name:"))
        name = json.loads(name_line.split(": ", 1)[1])
        self.assertEqual(name, 'service: "test" # note rules: []-443')
        self.assertEqual(content.count("\nrules:\n"), 1)

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
