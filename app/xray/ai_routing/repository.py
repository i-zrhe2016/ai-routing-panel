"""SQLite persistence for AI domains and AI routing state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .common import DOMAIN_RE, connect_panel_db, format_timestamp, run_with_sqlite_lock_retry, utc_now


def normalize_classifications(domains):
    """Accept only routing classifications with valid, canonical domain names."""
    result = {}
    if not isinstance(domains, dict):
        return result
    for domain, item in domains.items():
        domain = str(domain).strip().lower()
        if not DOMAIN_RE.fullmatch(domain) or not isinstance(item, dict):
            continue
        classification = item.get("classification")
        if classification not in ("ai", "not_ai"):
            continue
        timestamp = str(item.get("classified_at", item.get("updated_at", "")) or "")
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = format_timestamp(parsed)
        except ValueError:
            timestamp = ""
        result[domain] = {
            "classification": classification,
            "reason": str(item.get("reason", "") or ""),
            "source": str(item.get("source", "") or ""),
            "model": str(item.get("model", "") or ""),
            "classified_at": timestamp,
        }
    return result


def ensure_classification_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_domain_classifications (
            domain TEXT PRIMARY KEY,
            classification TEXT NOT NULL CHECK (classification IN ('ai', 'not_ai')),
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            classified_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def load_classifications(panel_db_path):
    """Recover durable decisions, including pre-cache AI history, without writes."""
    if not Path(panel_db_path).is_file():
        return {}

    def read():
        conn = connect_panel_db(panel_db_path)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            domains = {}
            if "ai_domains" in tables:
                domains.update(
                    normalize_classifications(
                        {
                            row["domain"]: dict(row)
                            for row in conn.execute(
                                "SELECT domain, classification, reason, source, model, updated_at FROM ai_domains"
                            )
                        }
                    )
                )
            if "ai_domain_classifications" in tables:
                domains.update(
                    normalize_classifications(
                        {row["domain"]: dict(row) for row in conn.execute("SELECT * FROM ai_domain_classifications")}
                    )
                )
            return domains
        finally:
            conn.close()

    return run_with_sqlite_lock_retry(read)


def save_classifications(panel_db_path, decisions):
    """Persist AI and non-AI decisions independently of observation/report success."""
    if not Path(panel_db_path).is_file():
        return {"status": "skipped", "reason": "panel_db_missing"}
    domains = normalize_classifications(decisions.get("domains", {}))
    now = format_timestamp(utc_now())

    def save():
        conn = connect_panel_db(panel_db_path)
        try:
            ensure_classification_schema(conn)
            conn.executemany(
                """
                INSERT INTO ai_domain_classifications
                    (domain, classification, reason, source, model, classified_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    classification = excluded.classification,
                    reason = excluded.reason,
                    source = excluded.source,
                    model = excluded.model,
                    classified_at = excluded.classified_at,
                    updated_at = excluded.updated_at
                WHERE excluded.classified_at >= ai_domain_classifications.classified_at
                  AND (excluded.classification != ai_domain_classifications.classification
                    OR excluded.reason != ai_domain_classifications.reason
                    OR excluded.source != ai_domain_classifications.source
                    OR excluded.model != ai_domain_classifications.model
                    OR excluded.classified_at != ai_domain_classifications.classified_at)
                """,
                [
                    (
                        domain,
                        item["classification"],
                        item["reason"],
                        item["source"],
                        item["model"],
                        item["classified_at"] or now,
                        now,
                    )
                    for domain, item in domains.items()
                ],
            )
            conn.commit()
            return {"status": "written", "classifications": len(domains)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return run_with_sqlite_lock_retry(save)


def read_panel_target(panel_db_path, preferred_listen_port):
    if not panel_db_path.is_file():
        return None

    def read_target():
        conn = connect_panel_db(panel_db_path)
        try:
            if preferred_listen_port:
                row = conn.execute(
                    """
                    SELECT listen_port, upstream_host, upstream_port, note, updated_at
                    FROM ports
                    WHERE enabled = 1 AND listen_port = ?
                    LIMIT 1
                    """,
                    (preferred_listen_port,),
                ).fetchone()
                if row:
                    return dict(row)
            row = conn.execute(
                """
                SELECT listen_port, upstream_host, upstream_port, note, updated_at
                FROM ports
                WHERE enabled = 1
                ORDER BY updated_at DESC, listen_port ASC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    return run_with_sqlite_lock_retry(read_target)


def ensure_ai_domain_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_domains (
            domain TEXT PRIMARY KEY,
            classification TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            first_seen TEXT,
            last_seen TEXT,
            total_hits INTEGER NOT NULL DEFAULT 0,
            last_protocols TEXT NOT NULL DEFAULT '[]',
            last_report_window_start TEXT,
            last_report_window_end TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_domain_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            hits INTEGER NOT NULL,
            classification TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            protocols TEXT NOT NULL DEFAULT '[]',
            first_seen TEXT,
            last_seen TEXT,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_domain_observations_window
        ON ai_domain_observations(domain, window_start, window_end);

        CREATE INDEX IF NOT EXISTS idx_ai_domain_observations_domain
        ON ai_domain_observations(domain);
        """
    )


def _save_ai_domains_to_panel_db_once(panel_db_path, report, decisions):
    status = {
        "status": "skipped",
        "reason": "",
        "path": str(panel_db_path),
        "domains_upserted": 0,
        "observations_upserted": 0,
    }
    if not panel_db_path.is_file():
        status["reason"] = "panel_db_missing"
        return status

    observed_ai_items = [item for item in report["domains"] if item["classification"] == "ai"]
    observed_ai_by_domain = {item["domain"]: item for item in observed_ai_items}
    conn = connect_panel_db(panel_db_path)
    try:
        ensure_ai_domain_schema(conn)

        historical_ai_domains = {
            row["domain"]
            for row in conn.execute(
                "SELECT DISTINCT domain FROM ai_domain_observations WHERE classification = 'ai'"
            ).fetchall()
        }
        historical_ai_domains.update(
            row["domain"]
            for row in conn.execute(
                "SELECT domain FROM ai_domains WHERE classification = 'ai' AND total_hits > 0"
            ).fetchall()
        )
        ai_domains = sorted(
            domain
            for domain, item in decisions["domains"].items()
            if item.get("classification") == "ai"
            and (domain in observed_ai_by_domain or domain in historical_ai_domains)
        )

        for item in observed_ai_items:
            decision = decisions["domains"].get(item["domain"], {})
            conn.execute(
                """
                INSERT INTO ai_domain_observations (
                    domain, window_start, window_end, hits, classification,
                    reason, source, model, protocols, first_seen, last_seen, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain, window_start, window_end) DO UPDATE SET
                    hits = excluded.hits,
                    classification = excluded.classification,
                    reason = excluded.reason,
                    source = excluded.source,
                    model = excluded.model,
                    protocols = excluded.protocols,
                    first_seen = excluded.first_seen,
                    last_seen = excluded.last_seen,
                    created_at = excluded.created_at
                """,
                (
                    item["domain"],
                    report["window_start"],
                    report["window_end"],
                    item["hits"],
                    item["classification"],
                    item["reason"],
                    str(decision.get("source", "")).strip(),
                    str(decision.get("model", "")).strip(),
                    json.dumps(item["protocols"], ensure_ascii=True),
                    item.get("first_seen"),
                    item.get("last_seen"),
                    report["generated_at"],
                ),
            )
            status["observations_upserted"] += 1

        for domain in ai_domains:
            item = observed_ai_by_domain.get(
                domain,
                {
                    "domain": domain,
                    "classification": "ai",
                    "reason": decisions["domains"].get(domain, {}).get("reason", ""),
                    "protocols": [],
                    "first_seen": None,
                    "last_seen": None,
                },
            )
            decision = decisions["domains"].get(domain, {})
            aggregate = conn.execute(
                """
                SELECT COALESCE(SUM(hits), 0) AS total_hits,
                       MIN(COALESCE(first_seen, last_seen)) AS first_seen,
                       MAX(last_seen) AS last_seen
                FROM ai_domain_observations
                WHERE domain = ?
                """,
                (domain,),
            ).fetchone()
            existing = conn.execute("SELECT last_protocols FROM ai_domains WHERE domain = ?", (domain,)).fetchone()
            protocols = json.dumps(item["protocols"], ensure_ascii=True)
            if not item["protocols"] and existing and existing["last_protocols"]:
                protocols = existing["last_protocols"]
            conn.execute(
                """
                INSERT INTO ai_domains (
                    domain, classification, reason, source, model, first_seen, last_seen,
                    total_hits, last_protocols, last_report_window_start,
                    last_report_window_end, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    classification = excluded.classification,
                    reason = excluded.reason,
                    source = excluded.source,
                    model = excluded.model,
                    first_seen = excluded.first_seen,
                    last_seen = excluded.last_seen,
                    total_hits = excluded.total_hits,
                    last_protocols = excluded.last_protocols,
                    last_report_window_start = excluded.last_report_window_start,
                    last_report_window_end = excluded.last_report_window_end,
                    updated_at = excluded.updated_at
                """,
                (
                    domain,
                    item["classification"],
                    item["reason"],
                    str(decision.get("source", "")).strip(),
                    str(decision.get("model", "")).strip(),
                    aggregate["first_seen"] or item.get("first_seen"),
                    aggregate["last_seen"] or item.get("last_seen"),
                    int(aggregate["total_hits"]),
                    protocols,
                    report["window_start"],
                    report["window_end"],
                    report["generated_at"],
                ),
            )
            status["domains_upserted"] += 1

        if ai_domains:
            placeholders = ", ".join("?" for _ in ai_domains)
            conn.execute(f"DELETE FROM ai_domains WHERE domain NOT IN ({placeholders})", ai_domains)
        else:
            conn.execute("DELETE FROM ai_domains")

        conn.commit()
        status["status"] = "written"
        return status
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_ai_domains_to_panel_db(panel_db_path, report, decisions):
    try:
        return run_with_sqlite_lock_retry(lambda: _save_ai_domains_to_panel_db_once(panel_db_path, report, decisions))
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "database is locked" not in message and "database is busy" not in message:
            raise
        return {
            "status": "skipped",
            "reason": "database_locked",
            "path": str(panel_db_path),
            "domains_upserted": 0,
            "observations_upserted": 0,
        }


def read_ai_routing_manual_mode(panel_db_path):
    path = str(panel_db_path or "").strip()
    if not path or not Path(path).is_file():
        return "auto"

    def read_mode():
        conn = connect_panel_db(path)
        try:
            return conn.execute("SELECT value FROM app_state WHERE key = 'ai_routing_manual_mode'").fetchone()
        finally:
            conn.close()

    try:
        row = run_with_sqlite_lock_retry(read_mode)
    except (OSError, sqlite3.Error):
        return "auto"
    mode = str(row[0] if row else "auto").strip().lower()
    return mode if mode in {"auto", "primary", "backup", "forced_fallback"} else "auto"


def read_ai_routing_traffic_scope(panel_db_path):
    """Old databases intentionally retain classified routing."""
    path = str(panel_db_path or "").strip()
    if not path or not Path(path).is_file():
        return "classified"
    def read_scope():
        conn = connect_panel_db(path)
        try:
            return conn.execute("SELECT value FROM app_state WHERE key = 'ai_routing_traffic_scope'").fetchone()
        finally:
            conn.close()
    try:
        row = run_with_sqlite_lock_retry(read_scope)
    except (OSError, sqlite3.Error):
        return "classified"
    return "all" if row and row[0] == "all" else "classified"



def port_scope_policy(conn, default_scope, override=None):
    """Resolve stable account preferences; old schemas inherit the global default."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info(ports)')}
    if not {'id', 'listen_port', 'enabled'} <= columns:
        if override is not None:
            raise ValueError('Unknown managed port account')
        return []
    scope_column = 'ai_traffic_scope' if 'ai_traffic_scope' in columns else 'NULL AS ai_traffic_scope'
    expiry_column = 'expires_at' if 'expires_at' in columns else 'NULL AS expires_at'
    rows = conn.execute(f'SELECT id, listen_port, enabled, {expiry_column}, {scope_column} FROM ports ORDER BY listen_port').fetchall()
    result = []
    found = override is None
    for row in rows:
        account_id, port, enabled, expiry, preference = row
        if override is not None and account_id == override[0]:
            preference = None if override[1] == 'inherit' else override[1]
            found = True
        if preference not in {None, 'all', 'classified'}:
            raise ValueError('Invalid managed port traffic scope')
        active = bool(enabled)
        if expiry:
            parsed = datetime.fromisoformat(expiry)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            active = active and parsed > utc_now()
        result.append({'id': account_id, 'listen_port': port, 'traffic_scope': preference or default_scope,
                       'inherited': preference is None, 'active': active})
    if not found:
        raise ValueError('Unknown managed port account')
    return result


def read_port_scope_policy(panel_db_path, default_scope, override=None):
    path = str(panel_db_path or '').strip()
    if not path or not Path(path).is_file():
        if override is not None:
            raise ValueError('Unknown managed port account')
        return []
    def read():
        conn = connect_panel_db(path)
        try:
            return port_scope_policy(conn, default_scope, override)
        finally:
            conn.close()
    return run_with_sqlite_lock_retry(read)


def scope_policy_signature(policy):
    return [{key: row[key] for key in ('id', 'listen_port', 'traffic_scope', 'active')} for row in policy]


def effective_traffic_scope(policy, default_scope):
    scopes = {row['traffic_scope'] for row in policy if row['active']}
    return 'mixed' if len(scopes) > 1 else next(iter(scopes), default_scope)

def normalize_ai_routing_manual_mode(panel_db_path, candidate_count):
    """Promote a stale backup override when the configured pool has one node."""
    path = Path(str(panel_db_path or "").strip())
    if not path or not path.is_file():
        return "auto"
    try:
        candidate_count = int(candidate_count)
    except (TypeError, ValueError):
        candidate_count = 0

    mode = read_ai_routing_manual_mode(path)
    if mode != "backup" or candidate_count >= 2:
        return mode
    normalized_mode = "primary" if candidate_count == 1 else "auto"
    updated_at = format_timestamp(utc_now())

    def persist_mode():
        conn = connect_panel_db(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE app_state
                SET value = ?
                WHERE key = 'ai_routing_manual_mode' AND value = 'backup'
                """,
                (normalized_mode,),
            ).rowcount
            if changed:
                conn.execute(
                    """
                    INSERT INTO app_state (key, value)
                    VALUES ('ai_routing_manual_updated_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (updated_at,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    try:
        run_with_sqlite_lock_retry(persist_mode)
    except (OSError, sqlite3.Error):
        # The normalized value still protects this manager cycle; a later
        # cycle can retry persistence when the database becomes writable.
        pass
    return normalized_mode


__all__ = [
    "connect_panel_db",
    "ensure_ai_domain_schema",
    "ensure_classification_schema",
    "load_classifications",
    "normalize_ai_routing_manual_mode",
    "normalize_classifications",
    "read_ai_routing_manual_mode",
    "read_panel_target",
    "run_with_sqlite_lock_retry",
    "save_ai_domains_to_panel_db",
    "save_classifications",
]
