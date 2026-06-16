"""Domain classification helpers exposed as a stable app.xray submodule."""

from app.xray.ai_domain_manager import FORCED_AI_ROUTE_DOMAIN_SUFFIXES
from app.xray.ai_domain_manager import KNOWN_AI_DOMAIN_SUFFIXES
from app.xray.ai_domain_manager import classify_domains_via_codex
from app.xray.ai_domain_manager import classify_domains_via_openai
from app.xray.ai_domain_manager import classify_pending_domains
from app.xray.ai_domain_manager import load_decisions
from app.xray.ai_domain_manager import matches_forced_ai_route_domain
from app.xray.ai_domain_manager import matches_known_ai_domain
from app.xray.ai_domain_manager import normalize_classification
from app.xray.ai_domain_manager import sync_builtin_domain_decisions

__all__ = [
    "FORCED_AI_ROUTE_DOMAIN_SUFFIXES",
    "KNOWN_AI_DOMAIN_SUFFIXES",
    "classify_domains_via_codex",
    "classify_domains_via_openai",
    "classify_pending_domains",
    "load_decisions",
    "matches_forced_ai_route_domain",
    "matches_known_ai_domain",
    "normalize_classification",
    "sync_builtin_domain_decisions",
]
