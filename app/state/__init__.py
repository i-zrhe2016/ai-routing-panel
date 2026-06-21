"""State package. PanelState is composed from per-domain mixins.

Re-exports the names the former flat app/state.py module exposed as attributes
(PanelState plus ValidationError / CloudflareApiError, which tests reference via
the module object) so importers and the test harness are unaffected.
"""

from ..dns_failover import CloudflareApiError
from ..errors import ValidationError
from ._constants import PLAN_SLUG_RE, XRAY_ACCESS_LOG_LINE_RE
from .panel import PanelState

__all__ = [
    "CloudflareApiError",
    "PLAN_SLUG_RE",
    "PanelState",
    "ValidationError",
    "XRAY_ACCESS_LOG_LINE_RE",
]
