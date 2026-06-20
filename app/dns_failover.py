import json
import ipaddress
import socket
from dataclasses import dataclass
from typing import Optional
from urllib import error, request


class CloudflareApiError(RuntimeError):
    pass


def resolve_public_ip(timeout=5.0):
    urls = [
        "https://ipv4.icanhazip.com",
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://ifconfig.co/ip",
    ]
    for url in urls:
        try:
            with request.urlopen(url, timeout=timeout) as response:
                value = response.read().decode("utf-8", errors="ignore").strip()
            ipaddress.ip_address(value)
            return value
        except Exception:
            continue
    raise RuntimeError("控制面公网 IP 自动获取失败。")


@dataclass(frozen=True)
class DnsFailoverConfig:
    enabled: bool
    interval: int
    timeout: float
    failure_threshold: int
    recovery_threshold: int
    probe_host: str
    probe_port: Optional[int]
    api_token: str
    zone_id: str
    record_id: str
    record_type: str
    record_name: str
    record_proxied: bool
    record_ttl: int
    primary_content: str
    backup_content: str
    backup_label: str

    def configured(self):
        return all(
            [
                self.probe_host,
                self.probe_port,
                self.api_token,
                self.zone_id,
                self.record_id,
                self.record_name,
            ]
        )

    def record_type_supported(self):
        return self.record_type in {"A", "AAAA", "CNAME"}


class CloudflareDnsClient:
    def __init__(self, config: DnsFailoverConfig):
        self.config = config

    def _request(self, method, path, payload=None):
        body = None
        headers = {
            "Authorization": f"Bearer {self.config.api_token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"https://api.cloudflare.com/client/v4/zones/{self.config.zone_id}{path}",
            method=method,
            data=body,
            headers=headers,
        )
        try:
            with request.urlopen(req, timeout=max(self.config.timeout, 5.0)) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise CloudflareApiError(self._format_api_error(raw, exc.reason)) from exc
        except error.URLError as exc:
            raise CloudflareApiError(f"Cloudflare API 不可达: {exc.reason}") from exc

        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise CloudflareApiError("Cloudflare API 返回了无法解析的响应。") from exc
        if not data.get("success"):
            raise CloudflareApiError(self._format_api_error(data, "Cloudflare API 返回失败。"))
        return data.get("result") or {}

    def _format_api_error(self, payload, fallback):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload or "{}")
            except json.JSONDecodeError:
                return str(fallback)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            messages = []
            for item in errors[:3]:
                if isinstance(item, dict):
                    code = item.get("code")
                    message = str(item.get("message") or "").strip()
                    if code and message:
                        messages.append(f"{code}: {message}")
                    elif message:
                        messages.append(message)
            if messages:
                return "; ".join(messages)
        return str(fallback)

    def get_record(self):
        return self._request("GET", f"/dns_records/{self.config.record_id}")

    def update_record(self, content):
        payload = {
            "type": self.config.record_type,
            "name": self.config.record_name,
            "content": content,
            "proxied": self.config.record_proxied,
            "ttl": 1 if self.config.record_proxied else self.config.record_ttl,
        }
        return self._request("PUT", f"/dns_records/{self.config.record_id}", payload)


class DnsFailoverManager:
    def __init__(self, config: DnsFailoverConfig, client=None):
        self.config = config
        self.client = client or CloudflareDnsClient(config)

    def probe_once(self):
        try:
            with socket.create_connection(
                (self.config.probe_host, int(self.config.probe_port)),
                timeout=self.config.timeout,
            ):
                return {"ok": True, "error": ""}
        except OSError as exc:
            return {"ok": False, "error": str(exc)[:200]}

    def current_target_from_content(self, content, primary_content=None, backup_content=None):
        primary = str(primary_content if primary_content is not None else self.config.primary_content or "").strip()
        backup = str(backup_content if backup_content is not None else self.config.backup_content or "").strip()
        if str(content or "").strip() == primary:
            return "primary"
        if str(content or "").strip() == backup:
            return "backup"
        return "unknown"

    def target_label(self, target):
        if target == "primary":
            return "主数据面"
        if target == "backup":
            return self.config.backup_label
        return "未知"

    def desired_content(self, target, primary_content=None, backup_content=None):
        primary = str(primary_content if primary_content is not None else self.config.primary_content or "").strip()
        backup = str(backup_content if backup_content is not None else self.config.backup_content or "").strip()
        if target == "primary":
            return primary
        if target == "backup":
            return backup
        raise ValueError("unsupported dns failover target")

    def get_record(self):
        return self.client.get_record()

    def sync_target(self, target, primary_content=None, backup_content=None):
        return self.client.update_record(self.desired_content(target, primary_content, backup_content))

    def evaluate_transition(self, current_target, consecutive_failures, consecutive_successes, probe_ok):
        if probe_ok:
            if current_target == "backup" and consecutive_successes >= self.config.recovery_threshold:
                return {"target": "primary", "reason": "auto_recovery"}
            return None
        if current_target == "primary" and consecutive_failures >= self.config.failure_threshold:
            return {"target": "backup", "reason": "auto_failover"}
        return None
