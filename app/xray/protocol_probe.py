"""Explicit SSH or isolated local executor for authenticated Xray availability probes."""
from __future__ import annotations

import copy
import json
import os
import shlex
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from scripts import xray_protocol_probe
from scripts.xray_protocol_probe import result, validate_outbound
from .node.backend import DataPlaneConfig
from .node.ssh import SSHBackend


@dataclass(frozen=True)
class ProbeConfig:
    ssh_target: str = ''
    ssh_bin: str = 'ssh'
    ssh_options: tuple[str, ...] = ()
    ssh_known_hosts_file: str = '/root/.ssh/known_hosts'
    script_path: str = '/opt/xray-probe/current/xray_protocol_probe.py'
    xray_bin: str = '/opt/xray-probe/current/xray'
    execution_mode: str = 'ssh'


class ProtocolProbeRunner:
    def __init__(self, config: ProbeConfig, execute=None, failure_hook=None, observation_hook=None):
        self.config = config
        self.failure_hook = failure_hook
        self.observation_hook = observation_hook
        self.transport = SSHBackend(DataPlaneConfig(role='probe', label='Dedicated probe executor',
            ssh_target=config.ssh_target, ssh_bin=config.ssh_bin, ssh_options=config.ssh_options,
            ssh_known_hosts_file=config.ssh_known_hosts_file), run_subprocess=execute)
        # SSHBackend.execute_remote uses execute_subprocess directly; retain the
        # test/host injection without enabling any node-management operation.
        if execute is not None:
            self.transport.execute_subprocess = execute

    def probe_outbound(self, outbound, timeout_seconds=6, *, source="protocol", target=None):
        answer = self._probe_outbound(outbound, timeout_seconds)
        answer["checked_at"] = datetime.now(timezone.utc).isoformat()
        answer["probe_origin"] = "local" if self.config.execution_mode == "local" else self.config.ssh_target
        if target is None:
            try:
                endpoint = validate_outbound(outbound)["settings"]["vnext"][0]
                target = f'{endpoint["address"]}:{endpoint["port"]}'
            except ValueError:
                target = "unknown"
        if self.observation_hook is not None:
            try:
                self.observation_hook(source, target, dict(answer))
            except Exception:
                pass
        if not answer['ok'] and self.failure_hook is not None:
            # Hook gets only sanitized evidence, never the outbound credentials.
            try:
                self.failure_hook(dict(answer))
            except Exception:
                pass  # Reporting cannot alter the routing decision.
        return answer

    def _probe_outbound(self, outbound, timeout_seconds):
        if self.config.execution_mode not in ('ssh', 'local') or (self.config.execution_mode == 'ssh' and not self.config.ssh_target):
            return result(management_error=True, error_code='probe_executor_unconfigured', stage='executor')
        try:
            clean = validate_outbound(outbound)
            timeout = float(timeout_seconds)
            if not 0.1 <= timeout <= 30:
                raise ValueError
        except (ValueError, TypeError):
            return result(management_error=True, error_code='invalid_probe_config', stage='config')
        try:
            execute = self.transport.execute_subprocess if self.config.execution_mode == 'local' else self.transport.execute_remote
            completed = execute(
                ['python3', self.config.script_path, '--xray-bin', self.config.xray_bin],
                'Dedicated probe transport failed', timeout=timeout + min(timeout, 3) + 4,
                input_text=json.dumps({'outbound': clean, 'timeout_seconds': timeout}))
            if len(completed.stdout or '') > 4096:
                raise ValueError
            payload = json.loads(completed.stdout or '')
            if not isinstance(payload, dict) or type(payload.get('ok')) is not bool or type(payload.get('management_error')) is not bool:
                raise ValueError
            if payload.get('method') != 'vless_reality':
                raise ValueError
            # Canonical outcomes match every standalone runner return path.
            outcomes = {
                '': ('request', False, True),
                'invalid_probe_config': ('config', True, False),
                'probe_dependency_missing': ('executor', True, False),
                'probe_execution_failed': ('executor', True, False),
                'xray_start_failed': ('startup', True, False),
                'xray_start_timeout': ('startup', True, False),
                'protocol_request_failed': ('request', False, False),
                'protocol_request_timeout': ('request', False, False),
            }
            code = payload.get('error_code')
            if type(code) is not str or type(payload.get('error')) is not str or payload['error'] != code:
                raise ValueError
            actual = (payload.get('stage'), payload['management_error'], payload['ok'])
            if outcomes.get(code) != actual:
                raise ValueError
            metadata = ('http_status', 'curl_exit_code', 'duration_ms')
            if code in ('', 'protocol_request_failed'):
                # Only a completed curl request emits these fields, all together.
                if any(type(payload.get(key)) is not int for key in metadata):
                    raise ValueError
                http_status, curl_exit, duration = (payload[key] for key in metadata)
                if http_status != 0 and not 100 <= http_status <= 599:
                    raise ValueError
                # Popen can report negative POSIX signals; curl itself uses 0–255.
                if not -64 <= curl_exit <= 255 or not 0 <= duration <= 60000:
                    raise ValueError
                if payload['ok'] != (curl_exit == 0 and http_status == 204):
                    raise ValueError
            elif any(key in payload for key in metadata):
                # Config/startup/executor faults and timeouts have no completed
                # request. Explicit request metadata on them is contradictory.
                raise ValueError
            answer = result(payload['ok'], payload['management_error'], payload.get('error_code', ''), payload.get('stage', ''))
            for key in metadata:
                if key in payload:
                    answer[key] = payload[key]
            return answer
        except (OSError, RuntimeError):
            return result(management_error=True, error_code='probe_transport_failed', stage='executor')
        except (ValueError, TypeError, AttributeError):
            return result(management_error=True, error_code='invalid_probe_response', stage='executor')


def build_probe_runner(failure_hook=None, observation_hook=None):
    mode = os.environ.get('PROBE_EXECUTION_MODE', 'ssh').strip()
    default_script = str(Path(xray_protocol_probe.__file__).resolve()) if mode == 'local' else '/opt/xray-probe/current/xray_protocol_probe.py'
    return ProtocolProbeRunner(ProbeConfig(
        execution_mode=mode,
        ssh_target=os.environ.get('PROBE_SSH_TARGET', '').strip(),
        ssh_bin=os.environ.get('PROBE_SSH_BIN', 'ssh'),
        ssh_options=tuple(shlex.split(os.environ.get('PROBE_SSH_OPTIONS', ''))),
        ssh_known_hosts_file=os.environ.get('PROBE_SSH_KNOWN_HOSTS', '/root/.ssh/known_hosts'),
        script_path=os.environ.get('PROBE_REMOTE_SCRIPT', default_script),
        xray_bin=os.environ.get('PROBE_XRAY_BIN', '/opt/xray-probe/current/xray')), failure_hook=failure_hook, observation_hook=observation_hook)


def client_outbound(client_path, listen_port=None, host=None, port=None):
    """Use the generated diagnostic credential, or the exact enabled account."""
    try:
        payload = json.loads(Path(client_path).read_text(encoding='utf-8'))
        outbound = copy.deepcopy(next(value for value in payload['outbounds'] if value.get('protocol') == 'vless'))
        endpoint = outbound['settings']['vnext'][0]
        metadata = payload.get('panelSubscription')
        if metadata is not None:
            users = metadata['users']
            if not users:
                raise ValueError
            if listen_port is not None:
                endpoint['users'][0]['id'] = users[str(int(listen_port))]
            endpoint['port'] = int(metadata['port'])
        elif listen_port is not None:
            endpoint['port'] = int(listen_port)
        if host:
            endpoint['address'] = host
        if port is not None:
            endpoint['port'] = int(port)
        return validate_outbound(outbound)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, IndexError, AttributeError):
        return None
