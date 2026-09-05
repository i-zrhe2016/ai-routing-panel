import base64
import json
from datetime import datetime, timezone

from components.xray_ops.collector import (
    CollectorConfig,
    DEFAULT_AI_KNOWN_HOSTS,
    DEFAULT_AI_TARGET,
    DEFAULT_NORMAL_KNOWN_HOSTS,
    DEFAULT_NORMAL_TARGET,
    CollectorService,
    LogStreamConfig,
    NodeConfig,
)
from components.xray_ops.remote import RemoteCommandError
from components.xray_ops.storage import OpsStore


def test_from_env_defaults_to_direct_password_ssh():
    config = CollectorConfig.from_env()

    normal, ai = config.nodes
    for node in (normal, ai):
        assert "BatchMode=no" in node.ssh_options
        assert "PubkeyAuthentication=no" in node.ssh_options
        assert "PreferredAuthentications=password,keyboard-interactive" in node.ssh_options
        assert "PasswordAuthentication=yes" in node.ssh_options
        assert "KbdInteractiveAuthentication=yes" in node.ssh_options
        assert "ChallengeResponseAuthentication=yes" in node.ssh_options
        assert "StrictHostKeyChecking=yes" in node.ssh_options
        assert "-i" not in node.ssh_options

    assert normal.target == DEFAULT_NORMAL_TARGET
    assert ai.target == DEFAULT_AI_TARGET
    assert f"UserKnownHostsFile={DEFAULT_NORMAL_KNOWN_HOSTS}" in normal.ssh_options
    assert f"UserKnownHostsFile={DEFAULT_AI_KNOWN_HOSTS}" in ai.ssh_options


class FakeExecutor:
    def __init__(self, role, fail=False):
        self.role = role
        self.fail = fail

    def node_snapshot(self, service_kind, service_name, docker_bin):
        if self.fail:
            raise RemoteCommandError("ssh_timeout", "timed out")
        return {
            "service": {
                "running": True,
                "health": "healthy",
                "started_at": "2030-01-01T00:00:00Z",
                "exit_code": 0,
                "oom_killed": False,
                "restart_count": 0,
            },
            "host": {
                "cpu_total_ticks": 1000,
                "cpu_idle_ticks": 800,
                "memory_total_bytes": 1000,
                "memory_available_bytes": 500,
                "load1": 0.5,
                "root_disk_total_bytes": 1000,
                "root_disk_used_bytes": 600,
                "network_rx_bytes": 100,
                "network_tx_bytes": 200,
            },
            "container_log_path": "/docker/container-json.log",
        }

    def read_file_delta(self, path, identity, offset, limit):
        if self.fail:
            raise RemoteCommandError("ssh_timeout", "timed out")
        if path.endswith("container-json.log"):
            raw = (
                json.dumps(
                    {
                        "log": "2030/01/02 12:00:00 [Info] proxy accepted tcp:ai.example.com:443\n",
                        "stream": "stdout",
                        "time": "2030-01-02T12:00:00Z",
                    }
                )
                + "\n"
            ).encode()
        else:
            raw = b"2030/01/02 12:00:00 [Info] proxy accepted tcp:example.com:443\n"
        return {
            "exists": True,
            "segments": [
                {
                    "path": path,
                    "identity": f"{self.role}:inode",
                    "start_offset": offset,
                    "end_offset": offset + len(raw),
                    "data_base64": base64.b64encode(raw).decode(),
                }
            ],
            "cursor": {
                "path": path,
                "identity": f"{self.role}:inode",
                "offset": offset + len(raw),
            },
            "rotation_gap": False,
        }


def _config(tmp_path):
    return CollectorConfig(
        db_path=str(tmp_path / "ops.db"),
        interval_seconds=60,
        command_timeout_seconds=10,
        max_bytes_per_stream=1024 * 1024,
        raw_retention_days=7,
        rollup_retention_days=90,
        nodes=(
            NodeConfig(
                role="normal_data_plane",
                target="normal",
                ssh_options=(),
                service_kind="docker",
                service_name="normal-xray",
                docker_bin="docker",
                streams=(LogStreamConfig("access", "/var/log/xray/access.log"),),
            ),
            NodeConfig(
                role="ai_data_plane",
                target="ai",
                ssh_options=(),
                service_kind="docker",
                service_name="ai-xray",
                docker_bin="docker",
                streams=(
                    LogStreamConfig(
                        "container",
                        "",
                        source_kind="docker_json",
                        docker_json=True,
                        dynamic_container_path=True,
                    ),
                ),
            ),
        ),
    )


def test_collect_once_combines_both_nodes(tmp_path):
    config = _config(tmp_path)
    store = OpsStore(config.db_path)
    service = CollectorService(config, store, executor_factory=lambda node: FakeExecutor(node.role))

    batch = service.collect_once()

    assert batch.status == "success"
    assert len(batch.events) == 2
    assert len(batch.samples) == 2
    assert {event.node_role for event in batch.events} == {"normal_data_plane", "ai_data_plane"}
    assert len(store.query_events("2030-01-02T00:00:00Z", "2030-01-03T00:00:00Z")) == 2


def test_one_node_failure_does_not_discard_other_node(tmp_path):
    config = _config(tmp_path)
    store = OpsStore(config.db_path)
    service = CollectorService(
        config,
        store,
        executor_factory=lambda node: FakeExecutor(node.role, fail=node.role == "ai_data_plane"),
    )

    batch = service.collect_once()

    assert batch.status == "partial"
    assert {sample.node_role for sample in batch.samples} == {"normal_data_plane"}
    assert any(result.node_role == "ai_data_plane" and result.status == "failed" for result in batch.source_results)


class BackfillChunkExecutor:
    def __init__(self, data):
        self.data = data

    def list_log_files(self, path):
        return [
            {
                "path": path,
                "identity": "1:2",
                "size": len(self.data),
                "current": True,
            }
        ]

    def read_log_chunk(self, path, offset, limit):
        assert path == "/var/log/xray/access.log"
        chunk = self.data[offset : offset + limit]
        return {
            "exists": True,
            "identity": "1:2",
            "data_base64": base64.b64encode(chunk).decode(),
            "offset": offset + len(chunk),
            "eof": offset + len(chunk) >= len(self.data),
        }


def test_backfill_cursor_stops_before_unterminated_final_line(tmp_path):
    complete = b"2030/01/02 12:00:00 [Info] proxy accepted tcp:example.com:443\n"
    incomplete = b"2030/01/02 12:01:00 [Info] proxy accepted tcp:later.example.com:443"
    config = _config(tmp_path)
    store = OpsStore(config.db_path)
    service = CollectorService(config, store)
    service.initialize()
    node = config.nodes[0]
    stream = node.streams[0]

    inserted, _bytes_read = service._backfill_stream(
        node,
        BackfillChunkExecutor(complete + incomplete),
        stream,
        stream.path,
        datetime(2029, 1, 1, tzinfo=timezone.utc),
    )

    cursor = store.get_cursor("normal_data_plane", "file", "access")
    assert inserted == 1
    assert cursor is not None
    assert cursor.offset == len(complete)
