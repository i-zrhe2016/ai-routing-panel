import hashlib
import os
import shlex
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


def parse_shell_words_env(value, field_name):
    raw = str(value or "").strip()
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw))
    except ValueError as exc:
        raise ValueError(f"{field_name} 配置格式无效。") from exc


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "panel.db"))
XRAY_ENV_FILE_PATH = Path(os.environ.get("XRAY_ENV_FILE_PATH", BASE_DIR / "xray" / ".env"))
XRAY_CONFIG_PATH = Path(os.environ.get("XRAY_CONFIG_PATH", BASE_DIR / "xray" / "runtime" / "config.json"))
XRAY_DYNAMIC_ROUTING_PATH = Path(
    os.environ.get("XRAY_DYNAMIC_ROUTING_PATH", XRAY_CONFIG_PATH.parent / "dynamic-routing.json")
)
XRAY_PANEL_PORTS_PATH = Path(
    os.environ.get("XRAY_PANEL_PORTS_PATH", BASE_DIR / "xray" / "runtime" / "panel-ports.json")
)
XRAY_ACCESS_LOG_PATH = Path(os.environ.get("XRAY_ACCESS_LOG_PATH", BASE_DIR / "xray" / "logs" / "access.log"))
XRAY_API_SERVER = os.environ.get("XRAY_API_SERVER", "127.0.0.1:10085").strip() or "127.0.0.1:10085"
XRAY_LOCAL_BIN = os.environ.get("XRAY_LOCAL_BIN", "").strip()
XRAY_CONTAINER_NAME = os.environ.get("XRAY_CONTAINER_NAME", "xray-reality-local").strip()
XRAY_DOCKER_BIN = os.environ.get("XRAY_DOCKER_BIN", "docker").strip() or "docker"
XRAY_STATS_QUERY_TIMEOUT = parse_nonnegative_env_int(
    os.environ.get("XRAY_STATS_QUERY_TIMEOUT", "5"),
    "XRAY_STATS_QUERY_TIMEOUT",
)
XRAY_PROBE_HOST = os.environ.get("XRAY_PROBE_HOST", "127.0.0.1").strip() or "127.0.0.1"
XRAY_CLIENT_CONFIG_PATH = Path(
    os.environ.get("XRAY_CLIENT_CONFIG_PATH", BASE_DIR / "xray" / "runtime" / "client-test.json")
)
SUBSCRIPTION_NAME_PREFIX = os.environ.get("SUBSCRIPTION_NAME_PREFIX", "reality").strip() or "reality"

DEFAULT_NODE_SSH_TARGET = os.environ.get("DEFAULT_NODE_SSH_TARGET", "").strip()
DEFAULT_NODE_SSH_BIN = os.environ.get("DEFAULT_NODE_SSH_BIN", "ssh").strip() or "ssh"
DEFAULT_NODE_SSH_OPTIONS = parse_shell_words_env(
    os.environ.get("DEFAULT_NODE_SSH_OPTIONS", ""),
    "DEFAULT_NODE_SSH_OPTIONS",
)
DEFAULT_NODE_API_SERVER = os.environ.get("DEFAULT_NODE_API_SERVER", XRAY_API_SERVER).strip() or XRAY_API_SERVER
DEFAULT_NODE_XRAY_BIN = os.environ.get("DEFAULT_NODE_XRAY_BIN", "/usr/local/bin/xray").strip() or "/usr/local/bin/xray"
DEFAULT_NODE_LOCAL_BIN = os.environ.get("DEFAULT_NODE_LOCAL_BIN", XRAY_LOCAL_BIN).strip()
DEFAULT_NODE_CONTAINER_NAME = os.environ.get("DEFAULT_NODE_CONTAINER_NAME", XRAY_CONTAINER_NAME).strip()
DEFAULT_NODE_DOCKER_BIN = os.environ.get("DEFAULT_NODE_DOCKER_BIN", XRAY_DOCKER_BIN).strip() or XRAY_DOCKER_BIN
DEFAULT_NODE_RESTART_COMMAND = os.environ.get("DEFAULT_NODE_RESTART_COMMAND", "").strip()
DEFAULT_NODE_CONFIG_PATH = os.environ.get("DEFAULT_NODE_CONFIG_PATH", "").strip()
DEFAULT_NODE_DYNAMIC_ROUTING_PATH = os.environ.get("DEFAULT_NODE_DYNAMIC_ROUTING_PATH", "").strip()
DEFAULT_NODE_AI_REPORT_PATH = os.environ.get("DEFAULT_NODE_AI_REPORT_PATH", "").strip()
DEFAULT_NODE_PANEL_DB_PATH = os.environ.get("DEFAULT_NODE_PANEL_DB_PATH", "").strip()
DEFAULT_NODE_PANEL_PORTS_PATH = os.environ.get("DEFAULT_NODE_PANEL_PORTS_PATH", "").strip()
DEFAULT_NODE_ACCESS_LOG_PATH = os.environ.get("DEFAULT_NODE_ACCESS_LOG_PATH", "").strip()

AI_NODE_HOST = os.environ.get("AI_NODE_HOST", "").strip()
AI_NODE_PORT = parse_optional_env_port(
    os.environ.get("AI_NODE_PORT", ""),
    "AI_NODE_PORT",
)
AI_NODE_SSH_TARGET = os.environ.get("AI_NODE_SSH_TARGET", "").strip()
AI_NODE_SSH_BIN = os.environ.get("AI_NODE_SSH_BIN", "ssh").strip() or "ssh"
AI_NODE_SSH_OPTIONS = parse_shell_words_env(
    os.environ.get("AI_NODE_SSH_OPTIONS", ""),
    "AI_NODE_SSH_OPTIONS",
)
AI_NODE_API_SERVER = os.environ.get("AI_NODE_API_SERVER", "127.0.0.1:10085").strip() or "127.0.0.1:10085"
AI_NODE_XRAY_BIN = os.environ.get("AI_NODE_XRAY_BIN", "/usr/local/bin/xray").strip() or "/usr/local/bin/xray"
AI_NODE_LOCAL_BIN = os.environ.get("AI_NODE_LOCAL_BIN", "").strip()
AI_NODE_CONTAINER_NAME = os.environ.get("AI_NODE_CONTAINER_NAME", "").strip()
AI_NODE_DOCKER_BIN = os.environ.get("AI_NODE_DOCKER_BIN", XRAY_DOCKER_BIN).strip() or XRAY_DOCKER_BIN
AI_NODE_RESTART_COMMAND = os.environ.get("AI_NODE_RESTART_COMMAND", "").strip()
AI_NODE_CONFIG_PATH = os.environ.get("AI_NODE_CONFIG_PATH", "").strip()

PANEL_HOST = os.environ.get("PANEL_HOST", "0.0.0.0")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "18080"))
PANEL_PUBLIC_URL = os.environ.get("PANEL_PUBLIC_URL", "").strip().rstrip("/")
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
PANEL_SECRET_KEY = os.environ.get("PANEL_SECRET_KEY", "").strip()

DEFAULT_UPSTREAM_HOST = os.environ.get("DEFAULT_UPSTREAM_HOST", "127.0.0.1")
DEFAULT_UPSTREAM_PORT = int(os.environ.get("DEFAULT_UPSTREAM_PORT", "443"))
SEED_LISTEN_PORT = os.environ.get("SEED_LISTEN_PORT", "31098").strip()
MAINTENANCE_INTERVAL = int(os.environ.get("MAINTENANCE_INTERVAL", "10"))
PROBE_ENABLED = os.environ.get("PROBE_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}
PROBE_INTERVAL = int(os.environ.get("PROBE_INTERVAL", "60"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "3"))
PROBE_TEST_LISTEN_PORT = parse_optional_env_port(
    os.environ.get("PROBE_TEST_LISTEN_PORT", ""),
    "PROBE_TEST_LISTEN_PORT",
)
PANEL_HEALTH_REQUIRES_XRAY = parse_bool_env(os.environ.get("PANEL_HEALTH_REQUIRES_XRAY"), default=True)
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
