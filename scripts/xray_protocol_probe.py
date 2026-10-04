#!/usr/bin/env python3
"""Standalone bounded VLESS/REALITY probe. Credentials enter only over stdin."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

MAX_INPUT = 65536


def result(ok=False, management_error=False, error_code='', stage='', **extra):
    return dict(ok=ok, management_error=management_error, method='vless_reality',
                error=error_code, error_code=error_code, stage=stage, **extra)


def validate_outbound(raw):
    """Accept one credential-bearing outbound; never accept routes or executables."""
    try:
        value = copy.deepcopy(raw)
        endpoint = value['settings']['vnext'][0]
        users = endpoint['users']
        reality = value['streamSettings']['realitySettings']
        if value['protocol'] != 'vless' or value['streamSettings'].get('network', 'tcp') != 'tcp':
            raise ValueError
        if value['streamSettings']['security'] != 'reality' or len(value['settings']['vnext']) != 1 or len(users) != 1:
            raise ValueError
        uuid.UUID(users[0]['id'])
        if not isinstance(endpoint['address'], str) or not endpoint['address'].strip() or len(endpoint['address']) > 253:
            raise ValueError
        if isinstance(endpoint['port'], bool) or not 1 <= int(endpoint['port']) <= 65535:
            raise ValueError
        if not all(isinstance(reality.get(key), str) and reality[key] for key in ('serverName', 'fingerprint')):
            raise ValueError
        key = reality.get('publicKey') or reality.get('password')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
            raise ValueError
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}){0,8}', reality.get('shortId', '')):
            raise ValueError
        if users[0].get('encryption', 'none') != 'none' or users[0].get('flow', '') not in ('', 'xtls-rprx-vision'):
            raise ValueError
        clean_reality = {key: reality[key] for key in ('serverName', 'fingerprint', 'publicKey', 'password', 'shortId', 'spiderX', 'mldsa65Verify') if key in reality}
        return {'tag': 'probe', 'protocol': 'vless',
                'settings': {'vnext': [{'address': endpoint['address'], 'port': int(endpoint['port']),
                                        'users': [{key: users[0][key] for key in ('id', 'encryption', 'flow') if key in users[0]}]}]},
                'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': clean_reality}}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ValueError('invalid_outbound') from None


def run_probe(payload, xray_bin='/usr/local/bin/xray', curl_bin='curl'):
    started = time.monotonic()
    try:
        outbound = validate_outbound(payload['outbound'])
        timeout = float(payload.get('timeout_seconds', 6))
        if not 0.1 <= timeout <= 30:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return result(management_error=True, error_code='invalid_probe_config', stage='config')
    if not shutil.which(xray_bin) or not shutil.which(curl_bin):
        return result(management_error=True, error_code='probe_dependency_missing', stage='executor')
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='xray-probe-') as directory:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            config = {'log': {'loglevel': 'none'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port,
                       'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}], 'outbounds': [outbound]}
            path = Path(directory) / 'client.json'
            with open(path, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                json.dump(config, stream)
            process = subprocess.Popen([xray_bin, 'run', '-config', str(path)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + min(timeout, 3)
            while True:
                if process.poll() is not None:
                    return result(management_error=True, error_code='xray_start_failed', stage='startup')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        return result(management_error=True, error_code='xray_start_timeout', stage='startup')
                    time.sleep(0.02)
            # --disable prevents ~/.curlrc changing proxy, destination, or output behavior.
            completed = subprocess.run([curl_bin, '--disable', '--silent', '--output', '/dev/null',
                '--write-out', '%{http_code}', '--proxy', f'socks5h://127.0.0.1:{port}', '--noproxy', '',
                '--connect-timeout', str(timeout), '--max-time', str(timeout), '--proto', '=https',
                'https://www.gstatic.com/generate_204'], capture_output=True, text=True, timeout=timeout + 1,
                env={key: val for key, val in os.environ.items() if key.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')})
            if process.poll() is not None:
                return result(management_error=True, error_code='probe_execution_failed', stage='executor')
            code = int(completed.stdout) if re.fullmatch(r'[0-9]{3}', completed.stdout or '') else 0
            ok = completed.returncode == 0 and code == 204
            return result(ok=ok, error_code='' if ok else 'protocol_request_failed', stage='request',
                          http_status=code, curl_exit_code=completed.returncode,
                          duration_ms=round((time.monotonic() - started) * 1000))
    except subprocess.TimeoutExpired:
        return result(error_code='protocol_request_timeout', stage='request')
    except (OSError, ValueError, TypeError):
        return result(management_error=True, error_code='probe_execution_failed', stage='executor')
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--xray-bin', default='/usr/local/bin/xray')
    args = parser.parse_args()
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError
        payload = json.loads(data)
        answer = run_probe(payload, xray_bin=args.xray_bin)
    except (ValueError, TypeError, KeyError):
        answer = result(management_error=True, error_code='invalid_probe_config', stage='config')
    print(json.dumps(answer, separators=(',', ':')))


if __name__ == '__main__':
    main()
