"""Bounded SSH operations used by the evidence collector."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Sequence

from .redaction import redact_text


REMOTE_FILE_DELTA_SCRIPT = r"""
import base64
import json
import os
import sys

path = sys.argv[1]
recorded_identity = sys.argv[2]
recorded_offset = max(0, int(sys.argv[3]))
limit = max(1, int(sys.argv[4]))

def info(candidate):
    try:
        stat = os.stat(candidate)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not os.path.isfile(candidate):
        return None
    return {
        "path": candidate,
        "identity": f"{stat.st_dev}:{stat.st_ino}",
        "size": int(stat.st_size),
        "mtime": float(stat.st_mtime),
    }

current = info(path)
if current is None:
    print(json.dumps({"exists": False, "segments": [], "rotation_gap": False}))
    raise SystemExit(0)

candidates = []
for candidate in (path + ".2", path + ".1", path):
    item = info(candidate)
    if item is not None:
        candidates.append(item)

start_index = len(candidates) - 1
start_offset = 0
rotation_gap = False
if recorded_identity:
    exact = next((index for index, item in enumerate(candidates) if item["identity"] == recorded_identity), None)
    if exact is not None and candidates[exact]["size"] >= recorded_offset:
        start_index = exact
        start_offset = recorded_offset
    elif current["identity"] == recorded_identity and current["size"] < recorded_offset:
        previous = next(
            (
                index
                for index in range(len(candidates) - 2, -1, -1)
                if candidates[index]["size"] >= recorded_offset
            ),
            None,
        )
        if previous is not None:
            start_index = previous
            start_offset = recorded_offset
        else:
            rotation_gap = True
    else:
        rotation_gap = True

remaining = limit
segments = []
final = {
    "path": candidates[start_index]["path"],
    "identity": candidates[start_index]["identity"],
    "offset": start_offset,
}
for index in range(start_index, len(candidates)):
    item = candidates[index]
    offset = start_offset if index == start_index else 0
    if offset > item["size"]:
        offset = 0
        rotation_gap = True
    with open(item["path"], "rb") as handle:
        handle.seek(offset)
        data = handle.read(remaining)
    end_offset = offset + len(data)
    if data:
        segments.append(
            {
                "path": item["path"],
                "identity": item["identity"],
                "start_offset": offset,
                "end_offset": end_offset,
                "data_base64": base64.b64encode(data).decode("ascii"),
            }
        )
    final = {"path": item["path"], "identity": item["identity"], "offset": end_offset}
    remaining -= len(data)
    if remaining <= 0 or end_offset < item["size"]:
        break

print(
    json.dumps(
        {
            "exists": True,
            "segments": segments,
            "cursor": final,
            "current": current,
            "rotation_gap": rotation_gap,
            "limit_reached": remaining <= 0,
        }
    )
)
"""


REMOTE_LIST_LOG_FILES_SCRIPT = r"""
import json
import os
import sys

path = sys.argv[1]
result = []
for candidate in (
    path + ".3.gz",
    path + ".3",
    path + ".2.gz",
    path + ".2",
    path + ".1.gz",
    path + ".1",
    path,
):
    try:
        stat = os.stat(candidate)
    except (FileNotFoundError, PermissionError, OSError):
        continue
    if not os.path.isfile(candidate):
        continue
    result.append(
        {
            "path": candidate,
            "identity": f"{stat.st_dev}:{stat.st_ino}",
            "size": int(stat.st_size),
            "mtime": float(stat.st_mtime),
            "compressed": candidate.endswith(".gz"),
            "current": candidate == path,
        }
    )
print(json.dumps(result))
"""


REMOTE_READ_LOG_CHUNK_SCRIPT = r"""
import base64
import gzip
import json
import os
import sys

path = sys.argv[1]
offset = max(0, int(sys.argv[2]))
limit = max(1, int(sys.argv[3]))
try:
    stat = os.stat(path)
except (FileNotFoundError, PermissionError, OSError):
    print(json.dumps({"exists": False, "data_base64": "", "offset": offset, "eof": True}))
    raise SystemExit(0)

opener = gzip.open if path.endswith(".gz") else open
with opener(path, "rb") as handle:
    handle.seek(offset)
    data = handle.read(limit)
    next_byte = handle.read(1)
print(
    json.dumps(
        {
            "exists": True,
            "identity": f"{stat.st_dev}:{stat.st_ino}",
            "data_base64": base64.b64encode(data).decode("ascii"),
            "offset": offset + len(data),
            "eof": not bool(next_byte),
        }
    )
)
"""


REMOTE_NODE_SNAPSHOT_SCRIPT = r"""
import json
import os
import shutil
import subprocess
import sys

service_kind = sys.argv[1]
service_name = sys.argv[2]
docker_bin = sys.argv[3]
result = {"service": {}, "host": {}, "container_log_path": ""}

if service_kind == "docker":
    completed = subprocess.run(
        [docker_bin, "inspect", service_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0:
        result["service"] = {
            "running": False,
            "health": "inspect_failed",
            "error_class": "container_not_found_or_unavailable",
        }
    else:
        payload = json.loads(completed.stdout)
        item = payload[0] if payload else {}
        state = item.get("State") or {}
        health = state.get("Health") or {}
        result["service"] = {
            "running": bool(state.get("Running")),
            "health": health.get("Status") or ("running" if state.get("Running") else "stopped"),
            "started_at": state.get("StartedAt") or None,
            "exit_code": state.get("ExitCode"),
            "oom_killed": state.get("OOMKilled"),
            "restart_count": item.get("RestartCount"),
            "status": state.get("Status") or "",
            "error": state.get("Error") or "",
        }
        result["container_log_path"] = str(item.get("LogPath") or "")
elif service_kind == "systemd":
    completed = subprocess.run(
        [
            "systemctl",
            "show",
            service_name,
            "--property=ActiveState,SubState,ExecMainStartTimestamp,ExecMainStatus,NRestarts",
            "--no-pager",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
        check=False,
    )
    values = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    result["service"] = {
        "running": values.get("ActiveState") == "active",
        "health": values.get("SubState") or values.get("ActiveState") or "unknown",
        "started_at": values.get("ExecMainStartTimestamp") or None,
        "exit_code": int(values["ExecMainStatus"]) if values.get("ExecMainStatus", "").isdigit() else None,
        "oom_killed": None,
        "restart_count": int(values["NRestarts"]) if values.get("NRestarts", "").isdigit() else None,
        "error_class": "" if completed.returncode == 0 else "systemd_show_failed",
    }
else:
    result["service"] = {"running": None, "health": "unknown", "error_class": "unsupported_service_kind"}

try:
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        parts = handle.readline().split()[1:]
    ticks = [int(value) for value in parts]
    result["host"]["cpu_total_ticks"] = sum(ticks)
    result["host"]["cpu_idle_ticks"] = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
except (OSError, ValueError, IndexError):
    result["host"]["cpu_total_ticks"] = None
    result["host"]["cpu_idle_ticks"] = None

memory = {}
try:
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                memory[key] = int(rest.strip().split()[0]) * 1024
except (OSError, ValueError, IndexError):
    memory = {}
result["host"]["memory_total_bytes"] = memory.get("MemTotal")
result["host"]["memory_available_bytes"] = memory.get("MemAvailable")

try:
    result["host"]["load1"] = float(os.getloadavg()[0])
except (OSError, ValueError):
    result["host"]["load1"] = None

try:
    usage = shutil.disk_usage("/")
    result["host"]["root_disk_total_bytes"] = usage.total
    result["host"]["root_disk_used_bytes"] = usage.used
except OSError:
    result["host"]["root_disk_total_bytes"] = None
    result["host"]["root_disk_used_bytes"] = None

rx = 0
tx = 0
try:
    with open("/proc/net/dev", "r", encoding="utf-8") as handle:
        for line in handle.readlines()[2:]:
            name, separator, raw = line.partition(":")
            if not separator or name.strip() == "lo":
                continue
            fields = raw.split()
            rx += int(fields[0])
            tx += int(fields[8])
    result["host"]["network_rx_bytes"] = rx
    result["host"]["network_tx_bytes"] = tx
except (OSError, ValueError, IndexError):
    result["host"]["network_rx_bytes"] = None
    result["host"]["network_tx_bytes"] = None

print(json.dumps(result))
"""


class RemoteCommandError(RuntimeError):
    def __init__(self, error_class: str, detail: str):
        super().__init__(detail)
        self.error_class = error_class
        self.detail = detail


def _join_shell_args(values: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(value)) for value in values)


@dataclass(frozen=True, slots=True)
class SshExecutor:
    target: str
    ssh_bin: str = "ssh"
    ssh_options: tuple[str, ...] = ()
    timeout_seconds: int = 20

    def run_python(self, script: str, *arguments: object) -> Any:
        remote_command = _join_shell_args(["python3", "-c", script, *(str(value) for value in arguments)])
        command = [self.ssh_bin, *self.ssh_options, self.target, remote_command]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteCommandError("ssh_timeout", f"remote command timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise RemoteCommandError("ssh_exec_failed", redact_text(str(exc)).text) from exc
        if completed.returncode != 0:
            detail = redact_text(completed.stderr.strip() or completed.stdout.strip() or "remote command failed").text
            raise RemoteCommandError("ssh_remote_failed", detail[:500])
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = redact_text(completed.stdout[:500]).text
            raise RemoteCommandError("remote_output_invalid", detail) from exc

    def read_file_delta(self, path: str, identity: str, offset: int, limit: int) -> dict[str, Any]:
        payload = self.run_python(REMOTE_FILE_DELTA_SCRIPT, path, identity, offset, limit)
        if not isinstance(payload, dict):
            raise RemoteCommandError("remote_output_invalid", "file delta output is not an object")
        return payload

    def list_log_files(self, path: str) -> list[dict[str, Any]]:
        payload = self.run_python(REMOTE_LIST_LOG_FILES_SCRIPT, path)
        if not isinstance(payload, list):
            raise RemoteCommandError("remote_output_invalid", "log file list output is not an array")
        return [item for item in payload if isinstance(item, dict)]

    def read_log_chunk(self, path: str, offset: int, limit: int) -> dict[str, Any]:
        payload = self.run_python(REMOTE_READ_LOG_CHUNK_SCRIPT, path, offset, limit)
        if not isinstance(payload, dict):
            raise RemoteCommandError("remote_output_invalid", "log chunk output is not an object")
        return payload

    def node_snapshot(self, service_kind: str, service_name: str, docker_bin: str = "docker") -> dict[str, Any]:
        payload = self.run_python(REMOTE_NODE_SNAPSHOT_SCRIPT, service_kind, service_name, docker_bin)
        if not isinstance(payload, dict):
            raise RemoteCommandError("remote_output_invalid", "node snapshot output is not an object")
        return payload
