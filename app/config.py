import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path


def parse_optional_env_port(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是数字。") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{field_name} 必须在 1-65535 之间。")
    return port


def parse_nonnegative_env_int(value, field_name):
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是非负整数。") from exc
    if number < 0:
        raise ValueError(f"{field_name} 必须是非负整数。")
    return number


def parse_bool_env(value, default=False):
    raw = str(value if value is not None else ("1" if default else "0")).strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "panel.db"))
NGINX_CONFIG_PATH = Path(os.environ.get("NGINX_CONFIG_PATH", "/etc/nginx/nginx.conf"))
STREAMS_DIR = Path(os.environ.get("STREAMS_DIR", "/etc/nginx/streams-enabled"))
GENERATED_STREAM_CONFIG = STREAMS_DIR / "ports.conf"
STREAM_ACCESS_LOG = Path(os.environ.get("STREAM_ACCESS_LOG", "/var/log/nginx/stream-access.log"))
NGINX_PID_PATH = Path(os.environ.get("NGINX_PID_PATH", "/var/run/nginx.pid"))
XRAY_CLIENT_CONFIG_PATH = Path(os.environ.get("XRAY_CLIENT_CONFIG_PATH", "/xray-runtime/client-test.json"))
SUBSCRIPTION_NAME_PREFIX = os.environ.get("SUBSCRIPTION_NAME_PREFIX", "reality").strip() or "reality"

PANEL_HOST = os.environ.get("PANEL_HOST", "0.0.0.0")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "18080"))
PANEL_PUBLIC_URL = os.environ.get("PANEL_PUBLIC_URL", "").strip().rstrip("/")
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
PANEL_SECRET_KEY = os.environ.get("PANEL_SECRET_KEY", "").strip()

DEFAULT_UPSTREAM_HOST = os.environ.get("DEFAULT_UPSTREAM_HOST", "127.0.0.1")
DEFAULT_UPSTREAM_PORT = int(os.environ.get("DEFAULT_UPSTREAM_PORT", "443"))
SEED_LISTEN_PORT = os.environ.get("SEED_LISTEN_PORT", "31098").strip()
PROXY_CONNECT_TIMEOUT = os.environ.get("PROXY_CONNECT_TIMEOUT", "5s")
PROXY_TIMEOUT = os.environ.get("PROXY_TIMEOUT", "600s")
STREAM_LISTEN_BACKLOG = parse_nonnegative_env_int(
    os.environ.get("STREAM_LISTEN_BACKLOG", "4096"),
    "STREAM_LISTEN_BACKLOG",
)
STREAM_LISTEN_FASTOPEN = parse_nonnegative_env_int(
    os.environ.get("STREAM_LISTEN_FASTOPEN", "256"),
    "STREAM_LISTEN_FASTOPEN",
)
STREAM_LISTEN_SO_KEEPALIVE = os.environ.get("STREAM_LISTEN_SO_KEEPALIVE", "on").strip() or "on"
STREAM_PROXY_SOCKET_KEEPALIVE = parse_bool_env(
    os.environ.get("STREAM_PROXY_SOCKET_KEEPALIVE", "1"),
    default=True,
)
MAINTENANCE_INTERVAL = int(os.environ.get("MAINTENANCE_INTERVAL", "10"))
PROBE_ENABLED = os.environ.get("PROBE_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}
PROBE_INTERVAL = int(os.environ.get("PROBE_INTERVAL", "60"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "3"))
PROBE_TEST_LISTEN_PORT = parse_optional_env_port(
    os.environ.get("PROBE_TEST_LISTEN_PORT", ""),
    "PROBE_TEST_LISTEN_PORT",
)
AUTH_ENABLED = bool(PANEL_USERNAME or PANEL_PASSWORD)
AUTH_SESSION_KEY = "panel_auth_marker"
AUTH_SESSION_MARKER = hashlib.sha256(f"{PANEL_USERNAME}\0{PANEL_PASSWORD}".encode("utf-8")).hexdigest()
TENANT_SESSION_TOKEN_KEY = "tenant_auth_token"
TENANT_SESSION_MARKER_KEY = "tenant_auth_marker"
PROBE_DASHBOARD_RANGES = {
    "1h": {"hours": 1, "label": "1小时"},
    "24h": {"hours": 24, "label": "24小时"},
    "7d": {"hours": 24 * 7, "label": "7天"},
}

LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc

