"""Durable protocol-probe incidents and an independent, bounded diagnosis queue."""

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_REPORT_BYTES = 128 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_DIAGNOSIS_ERRORS = {
    "Codex diagnosis unavailable or invalid": "Codex 暂不可用或返回结果无效",
    "Codex diagnosis timed out": "Codex 分析超时",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value, limit=4096):
    text = str(value or "")[:MAX_REPORT_BYTES]
    text = re.sub(r'(?i)(?:vless|vmess|trojan|ss|https?)://[^\s<>"\']+', "[redacted URL]", text)
    text = re.sub(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "[redacted UUID]", text)
    text = re.sub(r"(?i)\b(?:sk-[\w-]{12,}|eyJ[\w.-]{20,})\b", "[redacted token]", text)
    text = re.sub(
        r"(?i)(?:private[_ -]?key|public[_ -]?key|password|token|authorization|pbk|sid|short[_ -]?id|uuid)[\"\']?\s*[=:]\s*[\"\']?[^\s,;\"\']+",
        "[redacted credential]",
        text,
    )
    text = re.sub(r"(?<![\w-])[A-Za-z0-9_-]{40,}={0,2}(?![\w-])", "[redacted key]", text)
    return text[:limit]


def safe_evidence(result):
    allowed = (
        "ok",
        "management_error",
        "method",
        "stage",
        "error",
        "error_code",
        "probe_origin",
        "checked_at",
        "http_status",
        "curl_exit_code",
        "duration_ms",
    )
    return {
        key: value if isinstance(value, (bool, int, float)) else sanitize_text(value)
        for key, value in result.items()
        if key in allowed and isinstance(value, (str, bool, int, float))
    }


def diagnosis_error_text(error):
    return sanitize_text(_DIAGNOSIS_ERRORS.get(error, error))


class IncidentStore:
    def __init__(self, database, reports_dir, failure_threshold=5):
        from .config.parsers import parse_positive_env_int

        self.database = database
        self.failure_threshold = parse_positive_env_int(failure_threshold, "INCIDENT_FAILURE_THRESHOLD")
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.reports_dir.chmod(0o700)
        self.ensure_schema()

    def ensure_schema(self):
        with self.database.transaction() as conn:
            # executescript implicitly commits: execute statements individually
            # to retain BEGIN IMMEDIATE across concurrent schema migrations.
            schema = """
                CREATE TABLE IF NOT EXISTS probe_incidents (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
                    kind TEXT NOT NULL, probe_origin TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1,
                    recovered_at TEXT, status TEXT NOT NULL DEFAULT 'queued',
                    claimed_at REAL, finished_at TEXT, evidence TEXT NOT NULL,
                    diagnosis_error TEXT NOT NULL DEFAULT '', report_available INTEGER NOT NULL DEFAULT 0
                );
                DROP INDEX IF EXISTS probe_incidents_open;
                CREATE UNIQUE INDEX IF NOT EXISTS probe_incidents_open_kind
                    ON probe_incidents(source,target,kind) WHERE recovered_at IS NULL;
                CREATE TABLE IF NOT EXISTS probe_incident_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, event_type TEXT NOT NULL, evidence TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES probe_incidents(id)
                );
                CREATE TABLE IF NOT EXISTS probe_failure_streaks (
                    source TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL,
                    probe_origin TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL,
                    evidence TEXT NOT NULL,
                    PRIMARY KEY(source,target,kind,probe_origin)
                );
            """
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(probe_incidents)")}
            if "diagnosis_qualified" not in columns:
                conn.execute("ALTER TABLE probe_incidents ADD COLUMN diagnosis_qualified INTEGER NOT NULL DEFAULT 0")
                # Already accepted jobs/reports retain their lifecycle. Queued
                # history is not proof of consecutive failures and stays gated.
                conn.execute(
                    "UPDATE probe_incidents SET diagnosis_qualified=1 WHERE status IN ('running','completed','failed')"
                )

    def record(self, source, target, result):
        evidence = safe_evidence(result)
        source, target = sanitize_text(source, 128), sanitize_text(target, 256)
        timestamp = now()
        serialized = json.dumps(evidence, ensure_ascii=False)
        origin = evidence.get("probe_origin", "")
        kind = (
            "executor_error"
            if result.get("management_error") or result.get("status") == "executor_error"
            else "node_failure"
        )
        with self.database.transaction() as conn:
            if result.get("ok"):
                conn.execute("DELETE FROM probe_failure_streaks WHERE source=? AND target=?", (source, target))
            else:
                # A changed executor or result kind interrupts consecutive
                # evidence; this does not prove recovery of a node incident.
                conn.execute(
                    "DELETE FROM probe_failure_streaks WHERE source=? AND target=? AND (kind<>? OR probe_origin<>?)",
                    (source, target, kind, origin),
                )
                conn.execute(
                    """INSERT INTO probe_failure_streaks
                        (source,target,kind,probe_origin,first_seen_at,last_seen_at,occurrences,evidence)
                        VALUES (?,?,?,?,?,?,1,?)
                        ON CONFLICT(source,target,kind,probe_origin) DO UPDATE SET
                        occurrences=occurrences+1,last_seen_at=excluded.last_seen_at,evidence=excluded.evidence""",
                    (source, target, kind, origin, timestamp, timestamp, serialized),
                )
                streak = conn.execute(
                    "SELECT * FROM probe_failure_streaks WHERE source=? AND target=? AND kind=? AND probe_origin=?",
                    (source, target, kind, origin),
                ).fetchone()
            # A target request result proves the executor works, even when the
            # target request fails. An executor error says nothing about target
            # recovery, so existing node failures remain open in that case.
            if result.get("ok") or kind == "node_failure":
                recovered = conn.execute(
                    "SELECT id FROM probe_incidents WHERE source=? AND target=? AND recovered_at IS NULL"
                    + ("" if result.get("ok") else " AND kind='executor_error'"),
                    (source, target),
                ).fetchall()
                for row in recovered:
                    conn.execute(
                        "UPDATE probe_incidents SET recovered_at=?,last_seen_at=? WHERE id=?",
                        (timestamp, timestamp, row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO probe_incident_events(incident_id,created_at,event_type,evidence) VALUES (?,?,?,?)",
                        (row["id"], timestamp, "recovered", serialized),
                    )
                if result.get("ok"):
                    return recovered[0]["id"] if recovered else None
            current = conn.execute(
                "SELECT id FROM probe_incidents WHERE source=? AND target=? AND kind=? AND recovered_at IS NULL",
                (source, target, kind),
            ).fetchone()
            if current:
                incident_id = current["id"]
                conn.execute(
                    """UPDATE probe_incidents SET occurrences=occurrences+1,last_seen_at=?,evidence=?,probe_origin=?,
                        diagnosis_qualified=MAX(diagnosis_qualified,?) WHERE id=?""",
                    (timestamp, serialized, origin, int(streak["occurrences"] >= self.failure_threshold), incident_id),
                )
            else:
                if streak["occurrences"] < self.failure_threshold:
                    return None
                incident_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO probe_incidents
                        (id,source,target,kind,probe_origin,first_seen_at,last_seen_at,evidence,occurrences,diagnosis_qualified)
                        VALUES (?,?,?,?,?,?,?,?,?,1)""",
                    (
                        incident_id,
                        source,
                        target,
                        kind,
                        origin,
                        streak["first_seen_at"],
                        timestamp,
                        serialized,
                        streak["occurrences"],
                    ),
                )
            conn.execute(
                "INSERT INTO probe_incident_events(incident_id,created_at,event_type,evidence) VALUES (?,?,?,?)",
                (incident_id, timestamp, "failure", serialized),
            )
        return incident_id

    def list(self, limit=100):
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM probe_incidents ORDER BY first_seen_at DESC,id DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [self._item(row) for row in rows]

    @staticmethod
    def _item(row):
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"])
        item["report_available"] = bool(item["report_available"])
        item["diagnosis_error"] = diagnosis_error_text(item["diagnosis_error"])
        return item

    def events(self, incident_id):
        with self.database.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM probe_incident_events WHERE incident_id=? ORDER BY id", (incident_id,)
                )
            ]

    def recover_stale(self, age=600):
        # Claims younger than the bounded model timeout belong to another active worker.
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE probe_incidents SET status='queued',claimed_at=NULL WHERE status='running' AND claimed_at<=?",
                (time.time() - age,),
            )

    def claim(self):
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM probe_incidents WHERE status='queued' AND diagnosis_qualified=1 ORDER BY first_seen_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE probe_incidents SET status='running',claimed_at=? WHERE id=?", (time.time(), row["id"])
            )
            return self._item(row)

    def finish(self, incident_id, diagnosis=None, error=""):
        if not _ID.fullmatch(str(incident_id)):
            raise ValueError("Invalid incident ID")
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM probe_incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown incident")
        incident = self._item(row)
        facts = json.dumps(
            {
                key: incident[key]
                for key in (
                    "source",
                    "target",
                    "kind",
                    "probe_origin",
                    "first_seen_at",
                    "last_seen_at",
                    "occurrences",
                    "recovered_at",
                    "evidence",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
        report = f"# 探测故障记录 {incident_id}\n\n生成时间：{now()}\n\n## 观测事实\n\n```json\n{facts}\n```\n\n"
        if diagnosis:
            report += "## Codex 分析\n\n" + sanitize_text(diagnosis["diagnosis"], 30000)
            report += "\n\n## 不确定性\n\n" + sanitize_text(diagnosis["uncertainty"], 16000)
            report += (
                "\n\n## 建议检查\n\n"
                + "\n".join("- " + sanitize_text(check) for check in diagnosis["recommended_checks"])
                + "\n"
            )
        else:
            report += (
                "## 分析不可用\n\n"
                + diagnosis_error_text(error or "模型不可用")
                + "\n\n## 不确定性\n\n未获得模型诊断。观测到的失败不足以确定根因。\n\n## 建议检查\n\n- 检查目标前，先核对已脱敏的探测阶段和执行器状态。\n"
            )
        data = sanitize_text(report, MAX_REPORT_BYTES).encode("utf-8")
        if len(data) > MAX_REPORT_BYTES:
            data = data[:MAX_REPORT_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
        with tempfile.NamedTemporaryFile(dir=self.reports_dir, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, self.reports_dir / f"{incident_id}.md")
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE probe_incidents SET status=?,finished_at=?,diagnosis_error=?,report_available=1 WHERE id=?",
                ("failed" if error else "completed", now(), sanitize_text(error), incident_id),
            )

    def read_report(self, incident_id):
        if not _ID.fullmatch(str(incident_id)):
            raise ValueError("Invalid incident ID")
        path = self.reports_dir / f"{incident_id}.md"
        if path.is_symlink():
            raise ValueError("Invalid report path")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as report:
            if os.fstat(report.fileno()).st_size > MAX_REPORT_BYTES:
                raise ValueError("Report exceeds size limit")
            return report.read(MAX_REPORT_BYTES + 1).decode("utf-8")


def database_incident_observer(database_path):
    """Lazy recorder for the separately scheduled AI manager process.

    Construction remains inside the probe's protected observation boundary,
    so an unavailable incident database cannot change the routing decision.
    """
    store = None

    def observe(source, target, result):
        nonlocal store
        if store is None:
            from .config.parsers import parse_positive_env_int
            from .storage.sqlite import SQLiteDatabase

            store = IncidentStore(
                SQLiteDatabase(database_path), Path(database_path).parent / "probe-incidents" / "reports",
                failure_threshold=parse_positive_env_int(
                    os.environ.get("INCIDENT_FAILURE_THRESHOLD", "5"), "INCIDENT_FAILURE_THRESHOLD"
                ),
            )
        return store.record(source, target, result)

    return observe


class CodexDiagnosisRunner:
    """Use the existing image in a per-job container with only safe evidence/auth."""

    def __init__(
        self,
        auth_home,
        *,
        image="",
        work_root=None,
        host_work_root=None,
        timeout=180,
        execute=subprocess.run,
        docker_bin="docker",
    ):
        self.auth_home = str(auth_home)  # Host path consumed by Docker; never read or log credentials.
        self.image = image
        self.work_root = Path(work_root or "/data/probe-incidents/work")
        self.host_work_root = Path(host_work_root or self.work_root)
        self.timeout = max(1, min(float(timeout), 600))
        self.execute = execute
        self.docker_bin = docker_bin

    def __call__(self, snapshot):
        if not self.image or not self.auth_home or not shutil.which(self.docker_bin):
            raise RuntimeError("Isolated Codex image/auth home or Docker unavailable")
        self.work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        prompt = json.dumps(
            {
                "task": "Investigate this sanitized probe incident. Do not execute commands, access other systems, or make changes. Distinguish executor failure from node failure. Use only observed facts; state uncertainty. Return JSON with nonempty diagnosis, uncertainty and recommended_checks (list of strings). Write all prose in diagnosis, uncertainty and every recommended_checks item in Simplified Chinese (简体中文). Keep JSON keys unchanged and preserve technical tokens such as hostnames, protocol names and error codes.",
                "observed_facts": snapshot,
            },
            ensure_ascii=False,
        )
        container = "probe-diagnosis-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=self.work_root) as directory:
            workdir = Path(directory)
            output = workdir / "report.json"
            host_workdir = self.host_work_root / workdir.name
            script = (
                "import os,shutil,signal,subprocess,sys; from pathlib import Path; "
                'h=Path("/tmp/codex-home"); h.mkdir(mode=0o700); '
                '[(shutil.copyfile("/input-codex/"+n,h/n),os.chmod(h/n,0o600)) for n in ("auth.json","config.toml")]; '
                'os.environ["CODEX_HOME"]=str(h); os.environ["HOME"]="/tmp"; '
                'p=subprocess.Popen(["/usr/local/bin/codex","exec","--sandbox","read-only","--skip-git-repo-check","--ignore-rules","--ephemeral","--json","-c","features.shell_tool=false","-c","features.plugins=false","-c","web_search=\\"disabled\\"","--output-last-message","/tmp/report.json","-"],stdin=sys.stdin,start_new_session=True);\n'
                f"try: r=p.wait(timeout={self.timeout});\n"
                "except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait(); r=124\n"
                "if r==0:\n"
                ' with open("/tmp/report.json","rb") as report: data=report.read(131073)\n'
                " if len(data)>131072: r=65\n"
                " else:\n"
                '  with open("/work/report.json","wb") as report: report.write(data)\n'
                "sys.exit(r)"
            )
            command = [
                self.docker_bin,
                "run",
                "--interactive",
                "--rm",
                "--name",
                container,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--network",
                "bridge",
                "--log-driver",
                "none",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,src={self.auth_home},dst=/input-codex,readonly",
                "--mount",
                f"type=bind,src={host_workdir},dst=/work",
                "--workdir",
                "/work",
                "--entrypoint",
                "python3",
                self.image,
                "-c",
                script,
            ]
            try:
                completed = self.execute(
                    command,
                    input=prompt,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.timeout,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                )
                if completed.returncode == 124:
                    raise subprocess.TimeoutExpired("Codex diagnosis", self.timeout)
                if completed.returncode:
                    raise RuntimeError("Codex exited unsuccessfully")
                if not output.is_file() or output.is_symlink() or output.stat().st_size > MAX_REPORT_BYTES:
                    raise RuntimeError("Codex output missing or exceeds limit")
                diagnosis = json.loads(output.read_text(encoding="utf-8"))
                if (
                    not isinstance(diagnosis, dict)
                    or any(
                        not isinstance(diagnosis.get(key), str) or not diagnosis[key].strip()
                        for key in ("diagnosis", "uncertainty")
                    )
                    or not isinstance(diagnosis.get("recommended_checks"), list)
                    or not diagnosis["recommended_checks"]
                    or len(diagnosis["recommended_checks"]) > 30
                    or any(not isinstance(check, str) for check in diagnosis["recommended_checks"])
                ):
                    raise RuntimeError("Invalid Codex diagnosis contract")
                return diagnosis
            finally:
                # A timed-out Docker client does not kill the container. Remove it explicitly.
                try:
                    self.execute(
                        [self.docker_bin, "rm", "-f", container],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    )
                except (OSError, subprocess.SubprocessError):
                    pass


class IncidentWorker:
    def __init__(self, store, runner, stop_event, interval=5):
        self.store, self.runner, self.stop_event, self.interval = store, runner, stop_event, interval

    def process_one(self):
        incident = self.store.claim()
        if incident is None:
            return False
        snapshot = {
            key: incident[key]
            for key in (
                "id",
                "source",
                "target",
                "kind",
                "probe_origin",
                "first_seen_at",
                "last_seen_at",
                "occurrences",
                "evidence",
            )
        }
        try:
            diagnosis = self.runner(snapshot)
            self.store.finish(incident["id"], diagnosis)
        except Exception as exc:  # noqa: BLE001 - provider errors never affect probes/failover
            # Do not persist subprocess diagnostics which can echo private auth material.
            self.store.finish(
                incident["id"],
                error="Codex 分析超时"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "Codex 暂不可用或返回结果无效",
            )
        return True

    def run(self):
        self.store.recover_stale(getattr(self.runner, "timeout", 600) + 30)
        while not self.stop_event.is_set():
            try:
                self.store.recover_stale(getattr(self.runner, "timeout", 600) + 30)
                if self.process_one():
                    continue
            except (OSError, sqlite3.Error, ValueError):
                logging.getLogger(__name__).warning("Incident storage unavailable; durable claims retained")
            self.stop_event.wait(self.interval)
