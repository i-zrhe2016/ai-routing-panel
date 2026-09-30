import json
import sys
from pathlib import Path

from app.storage import SchemaBootstrap, SQLiteDatabase
from app.xray.node import DataPlaneConfig, NodeController


GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


def _initialize_domain_schema(path):
    from app.state.ai_routing import AiRoutingService
    from app.state.commerce import CommerceService
    from app.state.dns_failover import DnsFailoverService
    from app.state.ports import PortsService
    from app.state.probes import ProbesService
    from app.state.traffic import TrafficService

    database = SQLiteDatabase(path)
    ports = PortsService(repository=database)
    traffic = TrafficService(repository=database)
    probes = ProbesService(repository=database)
    ai_routing = AiRoutingService(
        repository=database,
        node_controller=object(),
        manager_runner=object(),
    )
    dns_failover = DnsFailoverService(repository=database)
    commerce = CommerceService(repository=database)
    SchemaBootstrap().initialize(
        database,
        schema_initializers=(
            traffic.ensure_traffic_schema,
            probes.ensure_probe_schema,
            ports.ensure_port_schema,
            ai_routing.ensure_ai_schema,
            dns_failover.ensure_dns_failover_schema,
            commerce.ensure_commerce_schema,
        ),
    )
    return database


def _schema_snapshot(database):
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE (type = 'table' AND name NOT LIKE 'sqlite_%')
               OR (type = 'index' AND sql IS NOT NULL)
            ORDER BY type, name
            """
        )
        objects = [
            "|".join((row["type"], row["name"], row["tbl_name"], " ".join(row["sql"].split())))
            for row in rows
        ]
    return "\n".join(objects) + "\n"


def _node_backend_snapshot():
    common = {
        "role": "data_plane",
        "label": "contract-test",
        "api_server": "127.0.0.1:10085",
        "config_path": "/xray/config.json",
        "source_config_path": Path("/source/config.json"),
        "dynamic_routing_path": "/xray/dynamic-routing.json",
        "source_dynamic_routing_path": Path("/source/dynamic-routing.json"),
        "ai_report_path": "/xray/ai-report.json",
        "source_ai_report_path": Path("/source/ai-report.json"),
        "panel_db_path": "/xray/panel.db",
        "access_log_path": "/xray/access.log",
    }
    configurations = {
        "ssh": {"ssh_target": "node.example:22", "restart_command": "systemctl restart xray"},
        "docker": {"container_name": "xray"},
        "local": {"local_bin": sys.executable},
        "unmanaged": {"upstream_host": "upstream.example", "upstream_port": 443},
    }
    snapshot = {}
    for expected_mode, options in configurations.items():
        controller = NodeController(DataPlaneConfig(**common, **options))
        assert controller.mode == expected_mode
        snapshot[expected_mode] = {
            "is_remote": controller.is_remote,
            "supports_restart": controller.supports_restart(),
            "supports_sync": controller.supports_sync(),
            "supports_dynamic_routing_pull": controller.supports_dynamic_routing_pull(),
            "supports_ai_report_pull": controller.supports_ai_report_pull(),
            "supports_ai_domains_snapshot_pull": controller.supports_ai_domains_snapshot_pull(),
            "supports_stats": controller.supports_stats(),
            "supports_logs": controller.supports_logs(),
        }
    return snapshot


def test_schema_from_clean_domain_bootstrap_matches_the_golden_snapshot(tmp_path):
    database = _initialize_domain_schema(tmp_path / "empty-panel.db")
    actual = _schema_snapshot(database)
    expected = (GOLDEN_DIR / "control-plane-schema.tsv").read_text(encoding="utf-8")

    assert actual == expected


def test_node_backend_operation_capabilities_match_the_golden_snapshot():
    actual = _node_backend_snapshot()
    expected = json.loads((GOLDEN_DIR / "node-backend-capabilities.json").read_text(encoding="utf-8"))

    assert actual == expected
