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


def parse_positive_env_int(value, field_name):
    number = parse_nonnegative_env_int(value, field_name)
    if number <= 0:
        raise ValueError(f"{field_name} 必须大于 0。")
    return number


def parse_positive_env_float(value, field_name):
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是正数。") from exc
    if number <= 0:
        raise ValueError(f"{field_name} 必须是正数。")
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
XRAY_STATS_QUERY_TIMEOUT = parse_nonnegative_env_int(
    os.environ.get("XRAY_STATS_QUERY_TIMEOUT", "5"),
    "XRAY_STATS_QUERY_TIMEOUT",
)
XRAY_CLIENT_CONFIG_PATH = Path(
    os.environ.get("XRAY_CLIENT_CONFIG_PATH", BASE_DIR / "xray" / "runtime" / "client-test.json")
)
SUBSCRIPTION_NAME_PREFIX = os.environ.get("SUBSCRIPTION_NAME_PREFIX", "reality").strip() or "reality"

DATAPLANE_SSH_TARGET = os.environ.get("DATAPLANE_SSH_TARGET", "").strip()
DATAPLANE_SSH_BIN = os.environ.get("DATAPLANE_SSH_BIN", "ssh").strip() or "ssh"
DATAPLANE_SSH_OPTIONS = parse_shell_words_env(
    os.environ.get("DATAPLANE_SSH_OPTIONS", ""),
    "DATAPLANE_SSH_OPTIONS",
)
DATAPLANE_API_SERVER = os.environ.get("DATAPLANE_API_SERVER", XRAY_API_SERVER).strip() or XRAY_API_SERVER
DATAPLANE_XRAY_BIN = os.environ.get("DATAPLANE_XRAY_BIN", "/usr/local/bin/xray").strip() or "/usr/local/bin/xray"
DATAPLANE_LOCAL_BIN = os.environ.get("DATAPLANE_LOCAL_BIN", "").strip()
DATAPLANE_CONTAINER_NAME = os.environ.get("DATAPLANE_CONTAINER_NAME", "xray-reality-local").strip()
DATAPLANE_DOCKER_BIN = os.environ.get("DATAPLANE_DOCKER_BIN", "docker").strip() or "docker"
DATAPLANE_RESTART_COMMAND = os.environ.get("DATAPLANE_RESTART_COMMAND", "").strip()
DATAPLANE_CONFIG_PATH = os.environ.get("DATAPLANE_CONFIG_PATH", "").strip()
DATAPLANE_DYNAMIC_ROUTING_PATH = os.environ.get("DATAPLANE_DYNAMIC_ROUTING_PATH", "").strip()
DATAPLANE_AI_REPORT_PATH = os.environ.get("DATAPLANE_AI_REPORT_PATH", "").strip()
DATAPLANE_PANEL_DB_PATH = os.environ.get("DATAPLANE_PANEL_DB_PATH", "").strip()
DATAPLANE_PANEL_PORTS_PATH = os.environ.get("DATAPLANE_PANEL_PORTS_PATH", "").strip()
DATAPLANE_ACCESS_LOG_PATH = os.environ.get("DATAPLANE_ACCESS_LOG_PATH", "").strip()
DATAPLANE_PROBE_HOST = os.environ.get("DATAPLANE_PROBE_HOST", "127.0.0.1").strip() or "127.0.0.1"

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
DNS_FAILOVER_ENABLED = parse_bool_env(os.environ.get("DNS_FAILOVER_ENABLED"), default=False)
DNS_FAILOVER_INTERVAL = parse_positive_env_int(
    os.environ.get("DNS_FAILOVER_INTERVAL", "15"),
    "DNS_FAILOVER_INTERVAL",
)
DNS_FAILOVER_TIMEOUT = parse_positive_env_float(
    os.environ.get("DNS_FAILOVER_TIMEOUT", "3"),
    "DNS_FAILOVER_TIMEOUT",
)
DNS_FAILOVER_FAILURE_THRESHOLD = parse_positive_env_int(
    os.environ.get("DNS_FAILOVER_FAILURE_THRESHOLD", "3"),
    "DNS_FAILOVER_FAILURE_THRESHOLD",
)
DNS_FAILOVER_RECOVERY_THRESHOLD = parse_positive_env_int(
    os.environ.get("DNS_FAILOVER_RECOVERY_THRESHOLD", "2"),
    "DNS_FAILOVER_RECOVERY_THRESHOLD",
)
DNS_FAILOVER_PROBE_HOST = os.environ.get("DNS_FAILOVER_PROBE_HOST", "").strip()
DNS_FAILOVER_PROBE_PORT = parse_optional_env_port(
    os.environ.get("DNS_FAILOVER_PROBE_PORT", ""),
    "DNS_FAILOVER_PROBE_PORT",
)
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_ZONE_ID = os.environ.get("CF_ZONE_ID", "").strip()
CF_DNS_RECORD_ID = os.environ.get("CF_DNS_RECORD_ID", "").strip()
CF_DNS_RECORD_TYPE = (os.environ.get("CF_DNS_RECORD_TYPE", "A").strip() or "A").upper()
CF_DNS_RECORD_NAME = os.environ.get("CF_DNS_RECORD_NAME", "").strip()
CF_DNS_RECORD_PROXIED = parse_bool_env(os.environ.get("CF_DNS_RECORD_PROXIED"), default=False)
CF_DNS_RECORD_TTL = parse_positive_env_int(
    os.environ.get("CF_DNS_RECORD_TTL", "60"),
    "CF_DNS_RECORD_TTL",
)
DNS_FAILOVER_PRIMARY_CONTENT = os.environ.get("DNS_FAILOVER_PRIMARY_CONTENT", "").strip()
DNS_FAILOVER_BACKUP_CONTENT = os.environ.get("DNS_FAILOVER_BACKUP_CONTENT", "").strip()
DNS_FAILOVER_BACKUP_LABEL = os.environ.get("DNS_FAILOVER_BACKUP_LABEL", "控制面备用节点").strip() or "控制面备用节点"
CONTROL_PLANE_BACKUP_XRAY_ENABLED = parse_bool_env(
    os.environ.get("CONTROL_PLANE_BACKUP_XRAY_ENABLED"),
    default=False,
)
AI_ROUTING_ENABLED = parse_bool_env(os.environ.get("AI_ROUTING_ENABLED"), default=True)
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
