"""Explicit scope switches keep durable preference and route application coherent."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.errors import ValidationError
from app.state import ai_routing
from app.storage.sqlite import SQLiteDatabase
from app.xray.ai_routing import repository
from app.xray.operation_lock import exclusive_file_lock


@pytest.fixture
def scope_service(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / 'panel.db')
    with database.connect() as conn:
        conn.execute('CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT)')
        conn.commit()
    executor = Mock()
    service = ai_routing.AiRoutingService(repository=database,
        node_controller=SimpleNamespace(is_configured=lambda: True), manager_runner=executor)
    monkeypatch.setattr(ai_routing, 'AI_ROUTING_ENABLED', True)
    monkeypatch.setattr(service, '_manual_mode_lock_path', lambda: tmp_path / 'manual.lock')
    monkeypatch.setattr(service, 'ai_routing_manual_state', lambda: {'candidates': [{'index': 0}]})
    monkeypatch.setattr(service, 'ai_routing_status', lambda: {'traffic_scope': service.ai_routing_traffic_scope()})
    return service, executor


def test_scope_default_commit_and_restart_preserve_fixed_candidate(scope_service):
    service, executor = scope_service
    assert service.ai_routing_traffic_scope() == 'classified'
    with service.repository.connect() as conn:
        service.repository.set_state(conn, 'ai_routing_manual_mode', 'primary')
        conn.commit()
    def applying(*, traffic_scope):
        assert service.ai_routing_traffic_scope() == 'classified'
    executor.run.side_effect = applying
    assert service.set_ai_routing_traffic_scope('all') == {'traffic_scope': 'all'}
    executor.run.assert_called_once_with(traffic_scope='all')
    restarted = ai_routing.AiRoutingService(repository=service.repository)
    assert restarted.ai_routing_traffic_scope() == 'all'
    assert repository.read_ai_routing_traffic_scope(service.repository.path) == 'all'
    assert repository.read_ai_routing_manual_mode(service.repository.path) == 'primary'
    executor.run.side_effect = None
    service.set_ai_routing_traffic_scope('classified')
    assert restarted.ai_routing_traffic_scope() == 'classified'


def test_failed_apply_never_changes_scope(scope_service):
    service, executor = scope_service
    executor.run.side_effect = RuntimeError('apply failed')
    with pytest.raises(RuntimeError, match='apply failed'):
        service.set_ai_routing_traffic_scope('all')
    assert service.ai_routing_traffic_scope() == 'classified'


def test_failed_state_commit_compensates_previous_scope(scope_service, monkeypatch):
    service, executor = scope_service
    monkeypatch.setattr(service.repository, 'apply_state_update', Mock(side_effect=sqlite3.OperationalError('disk full')))
    with pytest.raises(sqlite3.OperationalError, match='disk full'):
        service.set_ai_routing_traffic_scope('all')
    assert [call.kwargs for call in executor.run.call_args_list] == [{'traffic_scope': 'all'}, {'traffic_scope': 'classified'}]
    assert service.ai_routing_traffic_scope() == 'classified'


def test_scope_rejects_invalid_unconfigured_and_busy(scope_service, monkeypatch):
    service, executor = scope_service
    with pytest.raises(ValidationError, match='classified'):
        service.set_ai_routing_traffic_scope('everything')
    monkeypatch.setattr(service, 'ai_routing_manual_state', lambda: {'candidates': []})
    with pytest.raises(ValidationError, match='未配置可用 AI 节点'):
        service.set_ai_routing_traffic_scope('all')
    with exclusive_file_lock(service._manual_mode_lock_path()), pytest.raises(RuntimeError, match='正在应用'):
        service.set_ai_routing_traffic_scope('classified')
    assert not executor.run.called


@pytest.fixture
def port_scope_service(scope_service):
    service, executor = scope_service
    with service.repository.connect() as conn:
        conn.execute('CREATE TABLE ports (id INTEGER PRIMARY KEY AUTOINCREMENT, listen_port INTEGER, enabled INTEGER, expires_at TEXT, ai_traffic_scope TEXT)')
        conn.executemany('INSERT INTO ports(listen_port, enabled) VALUES (?, 1)', [(31001,), (31002,)])
        conn.commit()
    return service, executor


def test_one_port_scope_persists_without_changing_other_or_default(port_scope_service):
    service, executor = port_scope_service
    def applying(**request):
        assert service.ai_routing_port_policy()[0]['traffic_scope'] == 'classified'
    executor.run.side_effect = applying
    service.set_ai_routing_port_scope(1, 'all')
    policy = service.ai_routing_port_policy()
    assert [(p['listen_port'], p['traffic_scope']) for p in policy] == [(31001, 'all'), (31002, 'classified')]
    assert service.ai_routing_traffic_scope() == 'classified'
    executor.run.assert_called_once_with(port_id=1, port_traffic_scope='all')
    restarted = ai_routing.AiRoutingService(repository=service.repository)
    assert restarted.ai_routing_port_policy() == policy
    executor.run.side_effect = None
    service.set_ai_routing_port_scope(1, 'classified')
    assert all(p['traffic_scope'] == 'classified' for p in restarted.ai_routing_port_policy())


def test_port_scope_commit_failure_compensates_previous_inheritance(port_scope_service, monkeypatch):
    service, executor = port_scope_service
    monkeypatch.setattr(service.repository, 'apply_state_update', Mock(side_effect=sqlite3.OperationalError('disk full')))
    with pytest.raises(sqlite3.OperationalError, match='disk full'):
        service.set_ai_routing_port_scope(1, 'all')
    assert [call.kwargs for call in executor.run.call_args_list] == [
        {'port_id': 1, 'port_traffic_scope': 'all'}, {'port_id': 1, 'port_traffic_scope': 'inherit'}]
    assert service.ai_routing_port_policy()[0]['inherited']


def test_port_identity_edits_delete_and_reuse_never_leak_override(port_scope_service):
    service, _executor = port_scope_service
    service.set_ai_routing_port_scope(1, 'all')
    with service.repository.connect() as conn:
        conn.execute('UPDATE ports SET listen_port=31003 WHERE id=1')
        conn.execute('DELETE FROM ports WHERE id=2')
        conn.execute('INSERT INTO ports(listen_port, enabled) VALUES (31002, 1)')
        conn.commit()
    assert [(p['id'], p['listen_port'], p['traffic_scope']) for p in service.ai_routing_port_policy()] == [
        (3, 31002, 'classified'), (1, 31003, 'all')]
    with pytest.raises(ValidationError):
        service.set_ai_routing_port_scope(2, 'all')


def test_explicit_classified_overrides_global_all_and_inherit_restores_default(port_scope_service):
    service, _executor = port_scope_service
    service.set_ai_routing_traffic_scope('all')
    service.set_ai_routing_port_scope(1, 'classified')
    assert [p['traffic_scope'] for p in service.ai_routing_port_policy()] == ['classified', 'all']
    service.set_ai_routing_port_scope(1, 'inherit')
    assert all(p['traffic_scope']=='all' and p['inherited'] for p in service.ai_routing_port_policy())


def test_port_invalid_busy_apply_failure_preserve_policy(port_scope_service):
    service, executor = port_scope_service
    for port_id, scope in [(0, 'all'), (99, 'all'), (True, 'all'), (1, 'bad')]:
        with pytest.raises(ValidationError): service.set_ai_routing_port_scope(port_id, scope)
    assert not executor.run.called
    with exclusive_file_lock(service._manual_mode_lock_path()), pytest.raises(RuntimeError, match='正在应用'):
        service.set_ai_routing_port_scope(1, 'all')
    executor.run.side_effect = RuntimeError('apply failed')
    with pytest.raises(RuntimeError, match='apply failed'): service.set_ai_routing_port_scope(1, 'all')
    assert service.ai_routing_port_policy()[0]['inherited']
