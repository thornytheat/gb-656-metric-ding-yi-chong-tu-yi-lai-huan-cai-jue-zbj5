"""SQLite 读写：批次快照、运行清单、隔离行、键值状态。

只放数据访问，不放判定；调用方拿到的是普通字典。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from app import config

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    schema_id      TEXT PRIMARY KEY,
    version_label  TEXT NOT NULL,
    source_path    TEXT NOT NULL,
    source_sha256  TEXT NOT NULL,
    encoding       TEXT NOT NULL,
    bom            INTEGER NOT NULL,
    field_count    INTEGER NOT NULL,
    blocked_count  INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    payload_json   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    batch_key      TEXT NOT NULL UNIQUE,
    old_schema_id  TEXT NOT NULL,
    new_schema_id  TEXT NOT NULL,
    version_seq    INTEGER NOT NULL,
    input_sha256   TEXT NOT NULL,
    ruleset        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    summary_json   TEXT NOT NULL,
    payload_json   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
    schema_id     TEXT NOT NULL,
    line          INTEGER,
    table_name    TEXT,
    column_name   TEXT,
    reason        TEXT NOT NULL,
    blocking      INTEGER NOT NULL,
    detail        TEXT,
    raw           TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_schema ON quarantine(schema_id);

-- 指标依赖裁决：一条批次键绑定一组输入摘要，修订生成新行而不覆盖旧行
CREATE TABLE IF NOT EXISTS metric_runs (
    run_id           TEXT PRIMARY KEY,
    batch_key        TEXT NOT NULL UNIQUE,
    lineage_id       TEXT NOT NULL,
    parent_run_id    TEXT,
    version_seq      INTEGER NOT NULL,
    input_sha256     TEXT NOT NULL,
    input_version    TEXT NOT NULL,
    digests_json     TEXT NOT NULL,
    input_names_json TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    status           TEXT NOT NULL,
    progress_json    TEXT NOT NULL,
    result_json      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_runs_lineage ON metric_runs(lineage_id, version_seq);

-- 审计日志：只记凭据摘要，原始凭据不落盘
CREATE TABLE IF NOT EXISTS audit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    run_id     TEXT,
    action     TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    role       TEXT NOT NULL,
    token_fp   TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id, id);
"""


def db_path() -> Path:
    raw = os.environ.get("METRICGRAPH_DB", "").strip()
    path = Path(raw) if raw else config.DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)


def reset() -> None:
    """测试与重建用：清空全部业务表。"""
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)
        for table in ("snapshots", "runs", "quarantine", "kv", "metric_runs", "audit_events"):
            conn.execute(f"DELETE FROM {table}")


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value):
    if not value:
        return None
    return json.loads(value)


# ---------- 快照 ----------

def save_snapshot(snapshot: dict) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO snapshots (schema_id, version_label, source_path, source_sha256,
                   encoding, bom, field_count, blocked_count, created_at, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(schema_id) DO UPDATE SET
                   version_label=excluded.version_label,
                   source_path=excluded.source_path,
                   source_sha256=excluded.source_sha256,
                   encoding=excluded.encoding,
                   bom=excluded.bom,
                   field_count=excluded.field_count,
                   blocked_count=excluded.blocked_count,
                   created_at=excluded.created_at,
                   payload_json=excluded.payload_json""",
            (
                snapshot["schema_id"], snapshot["version_label"], snapshot["source_path"],
                snapshot["source_sha256"], snapshot["encoding"], int(bool(snapshot["bom"])),
                int(snapshot["field_count"]), int(snapshot["blocked_count"]),
                snapshot["created_at"], _dumps(snapshot),
            ),
        )


def load_snapshot(schema_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM snapshots WHERE schema_id=?", (schema_id,)
        ).fetchone()
    return _loads(row["payload_json"]) if row else None


def list_snapshots(limit: int = 20) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT schema_id, version_label, source_path, source_sha256, encoding, bom,
                      field_count, blocked_count, created_at
               FROM snapshots ORDER BY created_at DESC, schema_id LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_quarantine(schema_id: str, rows: list[dict]) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM quarantine WHERE schema_id=?", (schema_id,))
        conn.executemany(
            """INSERT INTO quarantine (schema_id, line, table_name, column_name, reason,
                   blocking, detail, raw) VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    schema_id, int(row.get("line") or 0), row.get("table") or "",
                    row.get("column") or "", row["reason"], int(bool(row.get("blocking"))),
                    row.get("detail") or "", row.get("raw") or "",
                )
                for row in rows
            ],
        )


def list_quarantine(schema_id: str) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT line, table_name, column_name, reason, blocking, detail, raw
               FROM quarantine WHERE schema_id=? ORDER BY line, reason""",
            (schema_id,),
        ).fetchall()
    return [
        {
            "line": row["line"],
            "table": row["table_name"],
            "column": row["column_name"],
            "reason": row["reason"],
            "blocking": bool(row["blocking"]),
            "detail": row["detail"],
            "raw": row["raw"],
        }
        for row in rows
    ]


# ---------- 运行清单 ----------

def save_run(run: dict) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, batch_key, old_schema_id, new_schema_id, version_seq,
                   input_sha256, ruleset, created_at, effective_from, effective_to,
                   summary_json, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run["run_id"], run["batch_key"], run["old_schema_id"], run["new_schema_id"],
                int(run["version_seq"]), run["input_sha256"], run["ruleset"],
                run["created_at"], run["effective_from"], run["effective_to"],
                _dumps(run["summary"]), _dumps(run),
            ),
        )


def find_run_by_key(batch_key: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runs WHERE batch_key=?", (batch_key,)
        ).fetchone()
    return _loads(row["payload_json"]) if row else None


def run_by_id(run_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
    return _loads(row["payload_json"]) if row else None


def latest_run() -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runs ORDER BY version_seq DESC LIMIT 1"
        ).fetchone()
    return _loads(row["payload_json"]) if row else None


def list_runs(limit: int = 30) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT summary_json FROM runs ORDER BY version_seq DESC, run_id LIMIT ?""",
            (limit,),
        ).fetchall()
    return [_loads(row["summary_json"]) for row in rows]


def count_runs() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    return int(row["n"])


def next_version_seq() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version_seq), 0) AS n FROM runs").fetchone()
    return int(row["n"]) + 1


def current_version_seq() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version_seq), 0) AS n FROM runs").fetchone()
    return int(row["n"])


def list_windows() -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT run_id, effective_from, effective_to FROM runs ORDER BY version_seq"
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- 键值 ----------

def set_kv(key: str, value) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, _dumps(value)),
        )


def get_kv(key: str, default=None):
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return _loads(row["v"]) if row else default


# ---------- 指标裁决批次 ----------

def _metric_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "batch_key": row["batch_key"],
        "lineage_id": row["lineage_id"],
        "parent_run_id": row["parent_run_id"],
        "version_seq": row["version_seq"],
        "input_sha256": row["input_sha256"],
        "input_version": row["input_version"],
        "digests": _loads(row["digests_json"]) or {},
        "input_names": _loads(row["input_names_json"]) or {},
        "params": _loads(row["params_json"]) or {},
        "status": row["status"],
        "progress": _loads(row["progress_json"]) or {},
        "result": _loads(row["result_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_metric_run(run: dict) -> None:
    """整行写入。裁决中断时 status 仍是 pending，result 为 null。"""
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO metric_runs (run_id, batch_key, lineage_id, parent_run_id,
                   version_seq, input_sha256, input_version, digests_json,
                   input_names_json, params_json, status, progress_json, result_json,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                   status=excluded.status,
                   progress_json=excluded.progress_json,
                   result_json=excluded.result_json,
                   updated_at=excluded.updated_at""",
            (
                run["run_id"], run["batch_key"], run["lineage_id"],
                run.get("parent_run_id"), int(run["version_seq"]), run["input_sha256"],
                run["input_version"], _dumps(run.get("digests") or {}),
                _dumps(run.get("input_names") or {}), _dumps(run.get("params") or {}),
                run["status"], _dumps(run.get("progress") or {}),
                _dumps(run["result"]) if run.get("result") is not None else None,
                run["created_at"], run.get("updated_at") or run["created_at"],
            ),
        )


def update_metric_progress(run_id: str, status: str, progress: dict) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """UPDATE metric_runs SET status=?, progress_json=?, updated_at=?
               WHERE run_id=?""",
            (status, _dumps(progress or {}), _now_text(), run_id),
        )


def metric_run_by_id(run_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM metric_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    return _metric_row_to_dict(row) if row else None


def metric_run_by_key(batch_key: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM metric_runs WHERE batch_key=?", (batch_key,)
        ).fetchone()
    return _metric_row_to_dict(row) if row else None


def latest_metric_run(lineage_id: str | None = None) -> dict | None:
    sql = "SELECT * FROM metric_runs"
    args: tuple = ()
    if lineage_id:
        sql += " WHERE lineage_id=?"
        args = (lineage_id,)
    sql += " ORDER BY version_seq DESC, run_id LIMIT 1"
    with _LOCK, _connect() as conn:
        row = conn.execute(sql, args).fetchone()
    return _metric_row_to_dict(row) if row else None


def list_metric_runs(limit: int = 30, lineage_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM metric_runs"
    args: tuple = ()
    if lineage_id:
        sql += " WHERE lineage_id=?"
        args = (lineage_id,)
    sql += " ORDER BY version_seq DESC, run_id LIMIT ?"
    with _LOCK, _connect() as conn:
        rows = conn.execute(sql, args + (limit,)).fetchall()
    return [_metric_row_to_dict(row) for row in rows]


def count_metric_runs() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM metric_runs").fetchone()
    return int(row["n"])


def next_metric_version_seq() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_seq), 0) AS n FROM metric_runs"
        ).fetchone()
    return int(row["n"]) + 1


def current_metric_version_seq() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_seq), 0) AS n FROM metric_runs"
        ).fetchone()
    return int(row["n"])


def superseding_run(batch_key: str) -> dict | None:
    """同一条谱系里，比给定批次更新的修订（用于判断「陈旧输入」）。"""
    current = metric_run_by_key(batch_key)
    if current is None:
        return None
    with _LOCK, _connect() as conn:
        row = conn.execute(
            """SELECT * FROM metric_runs
               WHERE lineage_id=? AND version_seq>?
               ORDER BY version_seq ASC, run_id LIMIT 1""",
            (current["lineage_id"], int(current["version_seq"])),
        ).fetchone()
    return _metric_row_to_dict(row) if row else None


# ---------- 审计日志 ----------

def append_audit(run_id: str | None, action: str, outcome: str, role: str,
                 token_fp: str | None, detail: str = "") -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO audit_events (created_at, run_id, action, outcome, role,
                   token_fp, detail) VALUES (?,?,?,?,?,?,?)""",
            (_now_text(), run_id, action, outcome, role, token_fp, detail),
        )


def list_audit(run_id: str | None = None, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM audit_events"
    args: tuple = ()
    if run_id:
        sql += " WHERE run_id=?"
        args = (run_id,)
    sql += " ORDER BY id DESC LIMIT ?"
    with _LOCK, _connect() as conn:
        rows = conn.execute(sql, args + (limit,)).fetchall()
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "run_id": row["run_id"],
            "action": row["action"],
            "outcome": row["outcome"],
            "role": row["role"],
            "token_fp": row["token_fp"],
            "detail": row["detail"],
        }
        for row in rows
    ]


def _now_text() -> str:
    from app import config

    return config.now_utc().isoformat()
