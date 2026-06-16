"""Log parsing helpers exposed as a stable app.xray submodule."""

from app.xray.ai_domain_manager import load_json
from app.xray.ai_domain_manager import load_log_state
from app.xray.ai_domain_manager import normalize_log_state
from app.xray.ai_domain_manager import parse_log_line
from app.xray.ai_domain_manager import purge_old_events
from app.xray.ai_domain_manager import save_json
from app.xray.ai_domain_manager import save_log_state
from app.xray.ai_domain_manager import split_target_host
from app.xray.ai_domain_manager import sync_log
from app.xray.ai_domain_manager import utc_now

__all__ = [
    "load_json",
    "load_log_state",
    "normalize_log_state",
    "parse_log_line",
    "purge_old_events",
    "save_json",
    "save_log_state",
    "split_target_host",
    "sync_log",
    "utc_now",
]
