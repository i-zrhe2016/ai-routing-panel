import json
import os
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


REMOTE_FILE_DELTA_SCRIPT = """
import json
import os
import sys

path = sys.argv[1]
recorded_inode = sys.argv[2]
offset = int(sys.argv[3])
result = {
    "exists": False,
    "inode": "",
    "offset": 0,
    "data": "",
}
try:
    stat = os.stat(path)
except FileNotFoundError:
    print(json.dumps(result))
    raise SystemExit(0)

current_inode = str(stat.st_ino)
if recorded_inode != current_inode or stat.st_size < offset:
    offset = 0

with open(path, "r", encoding="utf-8", errors="ignore") as handle:
    handle.seek(offset)
    data = handle.read()
    offset = handle.tell()

result = {
    "exists": True,
    "inode": current_inode,
    "offset": offset,
    "data": data,
}
print(json.dumps(result))
"""

REMOTE_WRITE_FILE_SCRIPT = """
import os
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
tmp_path = target.with_name(target.name + ".codex-tmp")
tmp_path.write_text(sys.stdin.read(), encoding="utf-8")
os.replace(tmp_path, target)
"""

REMOTE_REPLACE_FILE_SCRIPT = """
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
target.parent.mkdir(parents=True, exist_ok=True)
os.replace(source, target)
"""

REMOTE_DELETE_FILE_SCRIPT = """
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.unlink(missing_ok=True)
"""

REMOTE_SOCKET_CHECK_SCRIPT = """
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = float(sys.argv[3])
try:
    with socket.create_connection((host, port), timeout=timeout):
        raise SystemExit(0)
except OSError:
    raise SystemExit(1)
"""


def parse_api_endpoint(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("tcp://"):
        text = text.removeprefix("tcp://")
    if text.startswith("["):
        closing = text.find("]")
        if closing <= 0 or closing + 2 >= len(text) or text[closing + 1] != ":":
            return None
        host = text[1:closing]
        port_text = text[closing + 2:]
    else:
        host, separator, port_text = text.rpartition(":")
        if not separator:
            return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    return (host or "127.0.0.1", port)


def resolve_local_bin(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if os.path.sep not in text:
        return shutil.which(text)
    return text if os.path.isfile(text) else None


def join_shell_args(args):
    return " ".join(shlex.quote(str(arg)) for arg in args)


@dataclass(frozen=True)
class ManagedNodeConfig:
    role: str
    label: str
    api_server: str = ""
    xray_bin: str = "/usr/local/bin/xray"
    local_bin: str = ""
    docker_bin: str = "docker"
    container_name: str = ""
    restart_command: str = ""
    ssh_target: str = ""
    ssh_bin: str = "ssh"
    ssh_options: tuple[str, ...] = ()
    config_path: str = ""
    access_log_path: str = ""
    panel_ports_path: str = ""
    source_config_path: Path | None = None
    source_panel_ports_path: Path | None = None
    upstream_host: str = ""
    upstream_port: int | None = None


class ManagedNodeController:
    def __init__(self, config: ManagedNodeConfig):
        self.config = config

    @property
    def mode(self):
        if self.config.ssh_target:
            return "ssh"
        if resolve_local_bin(self.config.local_bin):
            return "local"
        if self.config.container_name:
            return "docker"
        return "unmanaged"

    @property
    def is_remote(self):
        return self.mode == "ssh"

    def is_configured(self):
        if self.mode != "unmanaged":
            return True
        return bool(self.config.upstream_host and self.config.upstream_port)

    def supports_restart(self):
        if self.config.restart_command:
            return True
        if self.mode == "docker":
            return bool(self.config.container_name)
        if self.mode == "ssh":
            return bool(self.config.container_name or self.config.restart_command)
        return False

    def supports_sync(self):
        return bool(
            self.mode == "ssh"
            and self.config.config_path
            and self.config.source_config_path
        )

    def supports_stats(self):
        return bool(self.mode in {"ssh", "local", "docker"} and self.config.api_server)

    def supports_logs(self):
        return bool(self.mode == "ssh" and self.config.access_log_path)

    def display_target(self):
        if self.mode == "ssh":
            return self.config.ssh_target
        if self.mode == "local":
            return resolve_local_bin(self.config.local_bin) or self.config.local_bin
        if self.mode == "docker":
            return self.config.container_name
        if self.config.upstream_host and self.config.upstream_port:
            return f"{self.config.upstream_host}:{self.config.upstream_port}"
        return ""

    def _run_subprocess(self, command, error_prefix, timeout=None, input_text=None):
        completed = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode == 0:
            return completed
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"{error_prefix}: {detail}")

    def _run_remote(self, args, error_prefix, timeout=None, input_text=None):
        remote_command = join_shell_args(args)
        command = [
            self.config.ssh_bin,
            *self.config.ssh_options,
            self.config.ssh_target,
            remote_command,
        ]
        return self._run_subprocess(command, error_prefix, timeout=timeout, input_text=input_text)

    def _run_local_shell(self, shell_command, error_prefix, timeout=None):
        return self._run_subprocess(["sh", "-lc", shell_command], error_prefix, timeout=timeout)

    def _run_remote_shell(self, shell_command, error_prefix, timeout=None):
        return self._run_remote(["sh", "-lc", shell_command], error_prefix, timeout=timeout)

    def _container_exists(self):
        if not self.config.container_name:
            return False
        command = [self.config.docker_bin, "container", "inspect", self.config.container_name]
        try:
            if self.mode == "ssh":
                completed = subprocess.run(
                    [
                        self.config.ssh_bin,
                        *self.config.ssh_options,
                        self.config.ssh_target,
                        join_shell_args(command),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return completed.returncode == 0
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            return completed.returncode == 0
        except OSError:
            return False

    def _docker_running(self):
        if not self._container_exists():
            return False
        command = [
            self.config.docker_bin,
            "inspect",
            "--format",
            "{{.State.Running}}",
            self.config.container_name,
        ]
        try:
            if self.mode == "ssh":
                completed = subprocess.run(
                    [
                        self.config.ssh_bin,
                        *self.config.ssh_options,
                        self.config.ssh_target,
                        join_shell_args(command),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
            return completed.returncode == 0 and completed.stdout.strip().lower() == "true"
        except OSError:
            return False

    def _socket_running_locally(self, timeout_seconds=1):
        endpoint = parse_api_endpoint(self.config.api_server)
        if endpoint is None:
            return False
        try:
            with socket.create_connection(endpoint, timeout=timeout_seconds):
                return True
        except OSError:
            return False

    def is_running(self, timeout_seconds=1):
        if self.mode == "local":
            return self._socket_running_locally(timeout_seconds=timeout_seconds)
        if self.mode == "docker":
            return self._docker_running()
        if self.mode == "ssh":
            endpoint = parse_api_endpoint(self.config.api_server)
            if endpoint is None:
                return False
            try:
                completed = subprocess.run(
                    [
                        self.config.ssh_bin,
                        *self.config.ssh_options,
                        self.config.ssh_target,
                        join_shell_args(
                            [
                                "python3",
                                "-c",
                                REMOTE_SOCKET_CHECK_SCRIPT,
                                endpoint[0],
                                str(endpoint[1]),
                                str(timeout_seconds),
                            ]
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return completed.returncode == 0
            except OSError:
                return False
        if self.config.upstream_host and self.config.upstream_port:
            try:
                with socket.create_connection(
                    (self.config.upstream_host, int(self.config.upstream_port)),
                    timeout=timeout_seconds,
                ):
                    return True
            except OSError:
                return False
        return False

    def sync_generated_files(self, validate_config=False):
        if self.mode != "ssh":
            return []

        uploaded = []
        if self.config.source_config_path and self.config.config_path:
            source = self.config.source_config_path
            if not source.is_file():
                raise RuntimeError(f"{self.config.label} source config not found: {source}")
            content = source.read_text(encoding="utf-8")
            remote_tmp = f"{self.config.config_path}.codex-tmp"
            try:
                self._run_remote(
                    ["python3", "-c", REMOTE_WRITE_FILE_SCRIPT, remote_tmp],
                    f"{self.config.label} 配置上传失败",
                    input_text=content,
                )
                if validate_config:
                    self.test_config(config_path=remote_tmp)
                self._run_remote(
                    ["python3", "-c", REMOTE_REPLACE_FILE_SCRIPT, remote_tmp, self.config.config_path],
                    f"{self.config.label} 配置替换失败",
                )
            except Exception:
                self._run_remote(
                    ["python3", "-c", REMOTE_DELETE_FILE_SCRIPT, remote_tmp],
                    f"{self.config.label} 临时配置清理失败",
                )
                raise
            uploaded.append(self.config.config_path)

        if self.config.source_panel_ports_path and self.config.panel_ports_path:
            source = self.config.source_panel_ports_path
            if not source.is_file():
                raise RuntimeError(f"{self.config.label} panel ports file not found: {source}")
            content = source.read_text(encoding="utf-8")
            self._run_remote(
                ["python3", "-c", REMOTE_WRITE_FILE_SCRIPT, self.config.panel_ports_path],
                f"{self.config.label} 面板端口文件上传失败",
                input_text=content,
            )
            uploaded.append(self.config.panel_ports_path)

        return uploaded

    def test_config(self, config_path=None):
        active_config_path = str(config_path or self.config.config_path or "").strip()
        if not active_config_path:
            return None

        if self.mode == "local":
            local_bin = resolve_local_bin(self.config.local_bin)
            if not local_bin:
                return None
            return self._run_subprocess(
                [local_bin, "run", "-test", "-config", active_config_path],
                f"{self.config.label} 配置校验失败",
            )

        if self.mode == "docker":
            if not self._container_exists() or not self._docker_running():
                return None
            return self._run_subprocess(
                [
                    self.config.docker_bin,
                    "exec",
                    self.config.container_name,
                    self.config.xray_bin,
                    "run",
                    "-test",
                    "-config",
                    active_config_path,
                ],
                f"{self.config.label} 配置校验失败",
            )

        if self.mode == "ssh":
            if self.config.container_name:
                return self._run_remote(
                    [
                        self.config.docker_bin,
                        "exec",
                        self.config.container_name,
                        self.config.xray_bin,
                        "run",
                        "-test",
                        "-config",
                        active_config_path,
                    ],
                    f"{self.config.label} 配置校验失败",
                )
            return self._run_remote(
                [self.config.xray_bin, "run", "-test", "-config", active_config_path],
                f"{self.config.label} 配置校验失败",
            )
        return None

    def restart(self):
        if self.config.restart_command:
            if self.mode == "ssh":
                self._run_remote_shell(self.config.restart_command, f"{self.config.label} 重启失败")
            else:
                self._run_local_shell(self.config.restart_command, f"{self.config.label} 重启失败")
            return True

        if self.mode == "docker":
            if not self._container_exists():
                return False
            action = "start" if not self._docker_running() else "restart"
            self._run_subprocess(
                [self.config.docker_bin, action, self.config.container_name],
                f"{self.config.label} 重启失败",
            )
            return True

        if self.mode == "ssh":
            if not self.config.container_name or not self._container_exists():
                return False
            action = "start" if not self._docker_running() else "restart"
            self._run_remote(
                [self.config.docker_bin, action, self.config.container_name],
                f"{self.config.label} 重启失败",
            )
            return True

        return False

    def read_access_log_delta(self, recorded_inode, offset):
        if not self.supports_logs():
            return {"exists": False, "inode": "", "offset": 0, "data": ""}
        completed = self._run_remote(
            [
                "python3",
                "-c",
                REMOTE_FILE_DELTA_SCRIPT,
                self.config.access_log_path,
                str(recorded_inode or ""),
                str(int(offset or 0)),
            ],
            f"{self.config.label} 访问日志读取失败",
        )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.config.label} 访问日志返回无效 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self.config.label} 访问日志返回格式无效")
        return {
            "exists": bool(payload.get("exists")),
            "inode": str(payload.get("inode", "")),
            "offset": int(payload.get("offset", 0) or 0),
            "data": str(payload.get("data", "")),
        }

    def run_statsquery(self, timeout_seconds, pattern):
        if not self.supports_stats():
            return None

        if self.mode == "local":
            local_bin = resolve_local_bin(self.config.local_bin)
            if not local_bin:
                return None
            return self._run_subprocess(
                [
                    local_bin,
                    "api",
                    "statsquery",
                    f"--server={self.config.api_server}",
                    "-timeout",
                    str(timeout_seconds),
                    "-pattern",
                    pattern,
                    "-reset",
                ],
                f"{self.config.label} 流量查询失败",
            )

        if self.mode == "docker":
            return self._run_subprocess(
                [
                    self.config.docker_bin,
                    "exec",
                    self.config.container_name,
                    self.config.xray_bin,
                    "api",
                    "statsquery",
                    f"--server={self.config.api_server}",
                    "-timeout",
                    str(timeout_seconds),
                    "-pattern",
                    pattern,
                    "-reset",
                ],
                f"{self.config.label} 流量查询失败",
            )

        if self.mode == "ssh":
            if self.config.container_name:
                return self._run_remote(
                    [
                        self.config.docker_bin,
                        "exec",
                        self.config.container_name,
                        self.config.xray_bin,
                        "api",
                        "statsquery",
                        f"--server={self.config.api_server}",
                        "-timeout",
                        str(timeout_seconds),
                        "-pattern",
                        pattern,
                        "-reset",
                    ],
                    f"{self.config.label} 流量查询失败",
                )
            return self._run_remote(
                [
                    self.config.xray_bin,
                    "api",
                    "statsquery",
                    f"--server={self.config.api_server}",
                    "-timeout",
                    str(timeout_seconds),
                    "-pattern",
                    pattern,
                    "-reset",
                ],
                f"{self.config.label} 流量查询失败",
            )
        return None

    def status_summary(self):
        running = None
        error = ""
        if self.is_configured():
            try:
                running = self.is_running()
            except Exception as exc:
                error = str(exc)
        return {
            "role": self.config.role,
            "label": self.config.label,
            "configured": self.is_configured(),
            "mode": self.mode,
            "target": self.display_target(),
            "api_server": self.config.api_server,
            "config_path": self.config.config_path,
            "access_log_path": self.config.access_log_path,
            "upstream_host": self.config.upstream_host,
            "upstream_port": self.config.upstream_port,
            "supports_sync": self.supports_sync(),
            "supports_restart": self.supports_restart(),
            "running": running,
            "last_error": error,
        }
