"""采信编排：读文件 → 解析 → 折叠 → 落快照。"""

from __future__ import annotations

import hashlib
import json

from app import config, store
from app.modules.m1_intake import ddl, reader, sheet


def _schema_id(version_label: str, fields: list[dict]) -> str:
    canonical = json.dumps(
        {
            "version": version_label,
            "fields": [
                [f["table"], f["field_id"], f["name"], f["type_raw"], f["required"]]
                for f in fields
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sch-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _stats(diagnostics: list[dict], parsed_columns: int, accepted: int, tables: int) -> dict:
    """采信账：列定义行 = 接受 + 阻断，两边必须相等。"""
    blocked = sum(1 for row in diagnostics if row["blocking"])
    return {
        "column_lines": accepted + blocked,
        "parsed": parsed_columns,
        "parse_failures": accepted + blocked - parsed_columns,
        "accepted": accepted,
        "blocked": blocked,
        "warnings": len(diagnostics) - blocked,
        "tables": tables,
    }


def ingest(rel_name: str) -> dict:
    """采信一份元数据文件，返回快照并写入 SQLite。"""
    path = config.resolve_sample(rel_name)
    raw = reader.read_meta_file(path)

    if path.suffix.lower() == ".sql":
        parsed = ddl.parse_ddl(raw.lines)
    else:
        parsed = sheet.parse_json_metadata(raw.text)

    version_label = parsed["version"] or path.stem
    folded = sheet.fold_columns(parsed["columns"], version_label, raw.name)

    diagnostics = list(parsed["issues"]) + list(folded["diagnostics"])
    if raw.bom:
        diagnostics.append(
            {
                "line": 1,
                "table": "",
                "column": "",
                "reason": "BOM_STRIPPED",
                "blocking": False,
                "detail": raw.encoding,
                "raw": raw.name,
            }
        )
    if raw.fallback:
        diagnostics.append(
            {
                "line": 1,
                "table": "",
                "column": "",
                "reason": "ENCODING_FALLBACK",
                "blocking": False,
                "detail": raw.encoding,
                "raw": raw.name,
            }
        )
    if folded["stats"]["column_lines"] == 0:
        diagnostics.append(
            {
                "line": 0,
                "table": "",
                "column": "",
                "reason": "EMPTY_INPUT",
                "blocking": False,
                "detail": raw.name,
                "raw": raw.name,
            }
        )

    fields = folded["fields"]
    stats = _stats(diagnostics, len(parsed["columns"]), len(fields),
                   folded["stats"]["tables"])
    snapshot = {
        "schema_id": _schema_id(version_label, fields),
        "version_label": version_label,
        "source_path": raw.name,
        "source_sha256": raw.sha256,
        "encoding": raw.encoding,
        "bom": raw.bom,
        "fallback": raw.fallback,
        "bytes": raw.size,
        "line_count": raw.line_count,
        "created_at": config.now_utc().isoformat(),
        "fields": fields,
        "diagnostics": diagnostics,
        "tables": parsed["tables"],
        "stats": stats,
        "field_count": len(fields),
        "blocked_count": stats["blocked"],
    }
    store.save_snapshot(snapshot)
    store.save_quarantine(snapshot["schema_id"], diagnostics)
    return snapshot


def quarantine_of(schema_id: str | None) -> list[dict]:
    if schema_id:
        return store.list_quarantine(schema_id)
    run = store.latest_run()
    if not run:
        return []
    return run.get("quarantine") or []


def snapshot_summary(snapshot: dict) -> dict:
    return {
        "schema_id": snapshot["schema_id"],
        "version_label": snapshot["version_label"],
        "source_path": snapshot["source_path"],
        "source_sha256": snapshot["source_sha256"],
        "encoding": snapshot["encoding"],
        "bom": snapshot["bom"],
        "stats": snapshot["stats"],
        "tables": snapshot["tables"],
    }
