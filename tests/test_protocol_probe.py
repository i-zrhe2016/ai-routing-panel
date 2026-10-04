import json
import subprocess
from unittest import mock

import pytest

from scripts.xray_protocol_probe import validate_outbound


def outbound():
    return {'protocol': 'vless', 'settings': {'vnext': [{'address': 'target.example', 'port': 443,
            'users': [{'id': '12345678-1234-4321-8123-123456789abc', 'flow': 'xtls-rprx-vision', 'encryption': 'none'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                'serverName': 'www.example.com', 'fingerprint': 'chrome', 'publicKey': 'x' * 43, 'shortId': 'abcd'}}}


def test_dedicated_executor_stdin_credentials_and_strict_result():
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    calls = []
    def execute(command, prefix, timeout=None, input_text=None):
        calls.append((command, timeout, input_text))
        return subprocess.CompletedProcess(command, 0, json.dumps({'ok': True, 'management_error': False,
            'method': 'vless_reality', 'http_status': 204, 'curl_exit_code': 0, 'duration_ms': 1, 'stage': 'request', 'error': '', 'error_code': '', 'secret': 'discard'}), '')
    runner = ProtocolProbeRunner(ProbeConfig(ssh_target='root@example.com'), execute=execute)
    answer = runner.probe_outbound(outbound(), 2)
    assert answer['ok'] is True
    assert 'secret' not in answer
    command, timeout, stdin = calls[0]
    assert 'root@example.com' in command
    assert outbound()['settings']['vnext'][0]['users'][0]['id'] not in ' '.join(command)
    assert json.loads(stdin)['outbound']['settings']['vnext'][0]['users'] == outbound()['settings']['vnext'][0]['users']
    assert timeout <= 10


@pytest.mark.parametrize('response', ['{}', '[]', '{"ok":true,"method":"tcp"}', '{"ok":true,"method":"vless_reality","management_error":false,"http_status":200}'])
def test_invalid_response_fails_closed(response):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    runner = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=lambda *a, **k: subprocess.CompletedProcess([], 0, response, ''))
    assert runner.probe_outbound(outbound(), 2)['management_error'] is True


def test_missing_executor_or_credentials_never_calls_transport():
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    execute = mock.Mock()
    assert ProtocolProbeRunner(ProbeConfig(), execute=execute).probe_outbound(outbound(), 2)['management_error']
    assert ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound({}, 2)['management_error']
    execute.assert_not_called()


def test_transport_exception_is_sanitized():
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    execute = mock.Mock(side_effect=RuntimeError('secret-uuid-full-payload'))
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['management_error']
    assert 'secret' not in json.dumps(answer)


def test_outbound_only_allows_authenticated_reality():
    for protocol in ('freedom', 'socks', 'vmess'):
        value = outbound()
        value['protocol'] = protocol
        with pytest.raises(ValueError):
            validate_outbound(value)


def test_generated_client_uses_exact_account_and_primary_override(tmp_path):
    from app.xray.protocol_probe import client_outbound
    data = {'outbounds': [outbound()], 'panelSubscription': {'port': 443, 'users': {
        '31098': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '32001': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'}}}
    path = tmp_path / 'client.json'
    path.write_text(json.dumps(data))
    chosen = client_outbound(path, 32001, host='primary.example')
    endpoint = chosen['settings']['vnext'][0]
    assert endpoint['address'] == 'primary.example' and endpoint['port'] == 443
    assert endpoint['users'][0]['id'] == data['panelSubscription']['users']['32001']
    assert client_outbound(path, 32002) is None
    del data['panelSubscription']
    path.write_text(json.dumps(data))
    assert client_outbound(path, 32001)['settings']['vnext'][0]['port'] == 32001
    assert client_outbound(path, 32001)['settings']['vnext'][0]['users'] == outbound()['settings']['vnext'][0]['users']


def test_observation_receives_success_and_failures_without_credentials():
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    observations = []
    execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({
        'ok': True, 'management_error': False, 'method': 'vless_reality', 'http_status': 204, 'curl_exit_code': 0, 'duration_ms': 1, 'stage': 'request', 'error': '', 'error_code': ''}), ''))
    runner = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute,
                                 observation_hook=lambda *args: observations.append(args))
    runner.probe_outbound(outbound(), 2, source='ai_upstream')
    execute.side_effect = RuntimeError('credential-leak')
    runner.probe_outbound(outbound(), 2, source='ai_upstream')
    assert [item[2]['ok'] for item in observations] == [True, False]
    assert observations[0][0:2] == ('ai_upstream', 'target.example:443')
    assert observations[0][2]['probe_origin'] == 'root@probe'
    assert observations[0][2]['checked_at']
    assert '12345678' not in json.dumps(observations)
    runner.observation_hook = mock.Mock(side_effect=RuntimeError('database down'))
    assert runner.probe_outbound(outbound(), 2)['management_error']


def test_standalone_client_private_config_concurrent_isolation_and_http_contract(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from scripts import xray_protocol_probe as script
    import os
    import socket
    import threading
    configs = []
    processes = []
    mutex = threading.Lock()
    class FakeXray:
        def __init__(self, command, **kwargs):
            assert kwargs['stdout'] == subprocess.DEVNULL and kwargs['stderr'] == subprocess.DEVNULL
            assert kwargs['start_new_session']
            path = command[-1]
            config = json.loads(open(path).read())
            assert os.stat(path).st_mode & 0o777 == 0o600
            assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
            self.listener = socket.socket()
            self.listener.bind(('127.0.0.1', config['inbounds'][0]['port']))
            self.listener.listen()
            self.pid = 12345
            with mutex:
                configs.append((path, config))
                processes.append(self)
        def poll(self):
            return None
        def wait(self, timeout=None):
            self.listener.close()
            return 0
    def request(command, **kwargs):
        assert '--noproxy' in command and command[command.index('--noproxy') + 1] == ''
        assert command[1] == '--disable'
        assert command[-1] == 'https://www.gstatic.com/generate_204'
        assert command[command.index('--proxy') + 1].startswith('socks5h://127.0.0.1:')
        assert 'no_proxy' not in {key.lower() for key in kwargs['env']}
        assert outbound()['settings']['vnext'][0]['users'][0]['id'] not in str(command)
        return subprocess.CompletedProcess(command, 0, '204', '')
    monkeypatch.setattr(script.shutil, 'which', lambda value: value)
    monkeypatch.setattr(script.subprocess, 'Popen', FakeXray)
    monkeypatch.setattr(script.subprocess, 'run', request)
    monkeypatch.setattr(script.os, 'killpg', lambda *args: None)
    monkeypatch.setenv('NO_PROXY', '*')
    with ThreadPoolExecutor(max_workers=4) as executor:
        answers = list(executor.map(lambda _: script.run_probe({'outbound': outbound(), 'timeout_seconds': 2}), range(4)))
    assert all(item['ok'] for item in answers)
    assert len({path for path, _ in configs}) == 4
    assert all(not os.path.exists(path) for path, _ in configs)


def test_real_xray_rejects_wrong_credential_even_with_open_reality_listener(tmp_path):
    """Real local listener is necessary to distinguish TLS camouflage from auth."""
    import os
    import socket
    import time
    import uuid
    from scripts.xray_protocol_probe import run_probe
    binary = os.environ.get('PROBE_TEST_XRAY_BIN', '/tmp/xray-probe-toolchain/xray')
    if not __import__('pathlib').Path(binary).is_file():
        pytest.skip('real Xray toolchain is unavailable')
    keys = subprocess.run([binary, 'x25519'], capture_output=True, text=True, check=True).stdout
    values = dict(line.split(': ', 1) for line in keys.strip().splitlines())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    valid_uuid = str(uuid.uuid4())
    config = {'log': {'loglevel': 'none'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'vless',
        'settings': {'clients': [{'id': valid_uuid, 'flow': 'xtls-rprx-vision'}], 'decryption': 'none'},
        'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
            'show': False, 'target': socket.getaddrinfo('www.amazon.com', 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0] + ':443', 'serverNames': ['www.amazon.com'],
            'privateKey': values['PrivateKey'], 'shortIds': ['abcd']}}}], 'outbounds': [{'protocol': 'freedom', 'settings': {'domainStrategy': 'UseIPv4'}}]}
    path = tmp_path / 'server.json'
    path.write_text(json.dumps(config))
    server = subprocess.Popen([binary, 'run', '-config', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 3
        while True:
            assert server.poll() is None, 'real Xray server failed to start'
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                    break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        probe = outbound()
        probe['settings']['vnext'][0].update(address='127.0.0.1', port=port)
        probe['streamSettings']['realitySettings'].update(serverName='www.amazon.com', publicKey=values['Password (PublicKey)'])
        answer = run_probe({'outbound': probe, 'timeout_seconds': 6}, xray_bin=binary)
        assert answer['ok'] is False and answer['management_error'] is False
        assert answer['error_code'] in ('protocol_request_failed', 'protocol_request_timeout')
        probe['settings']['vnext'][0]['users'][0]['id'] = valid_uuid
        positive = run_probe({'outbound': probe, 'timeout_seconds': 10}, xray_bin=binary)
        assert positive['ok'] is True and positive['http_status'] == 204
    finally:
        server.terminate()
        server.wait(timeout=3)


def test_periodic_ports_skip_disabled_and_executor_errors_preserve_health(tmp_path, monkeypatch):
    import sqlite3
    import threading
    from app.state import probes as module
    class Repository:
        def connect(self):
            conn = sqlite3.connect(tmp_path / 'panel.db')
            conn.row_factory = sqlite3.Row
            return conn
    repo = Repository()
    runner = mock.Mock()
    service = module.ProbesService(repo, threading.Lock(), runner)
    with repo.connect() as conn:
        conn.execute('CREATE TABLE ports(listen_port INTEGER, enabled INTEGER)')
        conn.executemany('INSERT INTO ports VALUES (?, ?)', [(31098, 1), (32001, 0)])
        service.ensure_probe_schema(conn)
        conn.execute("INSERT INTO upstream_probes VALUES (31098,1,'previous','')")
    monkeypatch.setattr(module, 'PROBE_ENABLED', True)
    monkeypatch.setattr(module, 'client_outbound', lambda *args, **kwargs: outbound())
    runner.probe_outbound.return_value = {'ok': False, 'management_error': True, 'error': 'probe_transport_failed'}
    assert service.run_upstream_probes() == 0
    with repo.connect() as conn:
        assert tuple(conn.execute('SELECT is_reachable,checked_at FROM upstream_probes').fetchone()) == (1, 'previous')
        assert conn.execute('SELECT COUNT(*) FROM upstream_probe_history').fetchone()[0] == 0
    runner.probe_outbound.assert_called_once()
    runner.probe_outbound.return_value = {'ok': False, 'management_error': False, 'error': 'protocol_request_failed'}
    assert service.run_upstream_probes() == 1
    with repo.connect() as conn:
        assert conn.execute('SELECT is_reachable FROM upstream_probes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM upstream_probe_history').fetchone()[0] == 1


def test_dns_and_diagnostics_use_dedicated_protocol_runner(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.dns_failover import DnsFailoverManager
    from app.state import diagnostics as module
    runner = mock.Mock()
    runner.probe_outbound.return_value = {'ok': True, 'management_error': False, 'method': 'vless_reality'}
    path = tmp_path / 'client.json'
    path.write_text(json.dumps({'outbounds': [outbound()]}))
    manager = DnsFailoverManager(SimpleNamespace(probe_host='physical-primary.example', probe_port=443, timeout=2),
                                 client=mock.Mock(), probe_runner=runner, client_path=path)
    assert manager.probe_once()['ok']
    assert runner.probe_outbound.call_args.args[0]['settings']['vnext'][0]['address'] == 'physical-primary.example'
    assert runner.probe_outbound.call_args.kwargs['source'] == 'dns_failover'
    monkeypatch.setattr(module, 'XRAY_CLIENT_CONFIG_PATH', path)
    node = mock.Mock()
    service = module.DiagnosticsService(mock.Mock(), node, probe_runner=runner)
    result = service._diagnose_single_port('physical-primary.example', 'ignored-sni', {'listen_port': 31098}, 2)
    assert result['protocol']['ok'] and result['tcp_reachable']
    assert runner.probe_outbound.call_args.kwargs['source'] == 'diagnostics'
    node.probe_tcp_endpoint.assert_not_called()
    node.probe_reality_endpoint.assert_not_called()


@pytest.mark.parametrize('ok,management_error,stage,error_code,http_status', [
    (False, False, 'config', 'invalid_probe_config', 0),
    (False, False, 'executor', 'probe_dependency_missing', 0),
    (False, False, 'startup', 'xray_start_failed', 0),
    (False, False, 'startup', 'xray_start_timeout', 0),
    (False, False, 'executor', 'probe_execution_failed', 0),
    (False, True, 'request', 'protocol_request_failed', 0),
    (False, True, 'request', 'protocol_request_timeout', 0),
    (False, True, 'request', 'invalid_probe_config', 0),
    (False, False, 'executor', 'protocol_request_failed', 0),
    (False, False, 'request', '', 0),
    (True, False, 'request', 'protocol_request_failed', 204),
    (True, False, 'startup', '', 204),
])
def test_contradictory_executor_result_is_management_error(ok, management_error, stage, error_code, http_status):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    payload = dict(ok=ok, management_error=management_error, stage=stage, error_code=error_code,
                   http_status=http_status, method='vless_reality')
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['ok'] is False
    assert answer['management_error'] is True
    assert answer['error_code'] == 'invalid_probe_response'


@pytest.mark.parametrize('stage,error_code,management_error', [
    ('config', 'invalid_probe_config', True),
    ('executor', 'probe_dependency_missing', True),
    ('executor', 'probe_execution_failed', True),
    ('startup', 'xray_start_failed', True),
    ('startup', 'xray_start_timeout', True),
    ('request', 'protocol_request_failed', False),
    ('request', 'protocol_request_timeout', False),
])
def test_consistent_executor_result_preserves_target_failure_classification(stage, error_code, management_error):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    payload = dict(ok=False, management_error=management_error, stage=stage, error_code=error_code,
                   error=error_code, method='vless_reality')
    if error_code == 'protocol_request_failed':
        payload.update(http_status=0, curl_exit_code=7, duration_ms=1)
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['ok'] is False
    assert answer['management_error'] is management_error
    assert answer['error_code'] == error_code


def executor_response(ok=True, code='', stage='request', management=False):
    value = dict(ok=ok, management_error=management, method='vless_reality', error=code, error_code=code, stage=stage)
    if code in ('', 'protocol_request_failed'):
        value.update(http_status=204 if ok else 0, curl_exit_code=0 if ok else 7, duration_ms=123)
    return value


@pytest.mark.parametrize('override', [
    {'ok': False, 'error': 'protocol_request_failed', 'error_code': 'protocol_request_failed'},
    {'http_status': 204.0}, {'curl_exit_code': False},
    {'http_status': True}, {'http_status': '204'}, {'http_status': None},
    {'curl_exit_code': 0.0}, {'curl_exit_code': '0'}, {'curl_exit_code': None},
    {'duration_ms': False}, {'duration_ms': 123.0}, {'duration_ms': '123'}, {'duration_ms': None},
    {'http_status': -1}, {'http_status': 99}, {'http_status': 600},
    {'curl_exit_code': -1}, {'curl_exit_code': 256},
    {'duration_ms': -1}, {'duration_ms': 60001}, {'curl_exit_code': -65},
    {'error': 'contradictory_error'}, {'error': 0},
])
def test_request_metadata_invalid_types_ranges_or_outcome_fail_closed(override):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    payload = executor_response()
    payload.update(override)
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['ok'] is False
    assert answer['management_error'] is True
    assert answer['error_code'] == 'invalid_probe_response'


@pytest.mark.parametrize('code,stage,management', [('', 'request', False), ('protocol_request_failed', 'request', False)])
@pytest.mark.parametrize('missing', ['http_status', 'curl_exit_code', 'duration_ms'])
def test_completed_request_requires_all_metadata(code, stage, management, missing):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    payload = executor_response(ok=not code, code=code, stage=stage, management=management)
    del payload[missing]
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['management_error'] is True and answer['error_code'] == 'invalid_probe_response'


@pytest.mark.parametrize('code,stage,management', [
    ('invalid_probe_config', 'config', True), ('probe_dependency_missing', 'executor', True),
    ('probe_execution_failed', 'executor', True), ('xray_start_failed', 'startup', True),
    ('xray_start_timeout', 'startup', True), ('protocol_request_timeout', 'request', False),
])
@pytest.mark.parametrize('field', ['http_status', 'curl_exit_code', 'duration_ms'])
def test_uncompleted_outcome_rejects_completed_request_metadata(code, stage, management, field):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    payload = executor_response(ok=False, code=code, stage=stage, management=management)
    payload[field] = 0
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['management_error'] is True and answer['error_code'] == 'invalid_probe_response'


@pytest.mark.parametrize('http_status,curl_exit,duration', [(204, 0, 0), (204, 0, 60000), (503, 0, 1), (0, 7, 1), (204, 52, 1), (0, -9, 1), (0, -64, 1), (0, 255, 1), (599, 0, 1)])
def test_completed_request_metadata_boundaries_and_derived_outcome(http_status, curl_exit, duration):
    from app.xray.protocol_probe import ProtocolProbeRunner, ProbeConfig
    ok = http_status == 204 and curl_exit == 0
    payload = executor_response(ok=ok, code='' if ok else 'protocol_request_failed')
    payload.update(http_status=http_status, curl_exit_code=curl_exit, duration_ms=duration)
    execute = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), '')
    answer = ProtocolProbeRunner(ProbeConfig(ssh_target='root@probe'), execute=execute).probe_outbound(outbound(), 2)
    assert answer['ok'] is ok and answer['management_error'] is False
    assert (answer['http_status'], answer['curl_exit_code'], answer['duration_ms']) == (http_status, curl_exit, duration)
