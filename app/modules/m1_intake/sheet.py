"""JSON 元数据解析与列折叠：去重、派生身份、生成诊断行。

折算后的列记录是后续所有阶段的输入，所以字段身份必须在这里定死：
有 fid 就用 fid，没有就按「表名|列名」派生，绝不能用行序或下标。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re

from app import config, errors

COLUMN_KEYS = ("columns", "fields")
TABLE_KEYS = ("tables", "schemas", "tables_schema")
NAME_JUNK_RE = re.compile(r"[\s`\"']")


def derive_field_id(table: str, name: str) -> str:
    """缺 fid 时的确定性身份：同一张表同一个列名永远得到同一个值。"""
    seed = f"{table.strip().lower()}|{name.strip().lower()}".encode("utf-8")
    return "auto:" + hashlib.sha256(seed).hexdigest()[:12]


def _table_list(payload: dict) -> list:
    for key in TABLE_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _column_list(table: dict) -> list:
    for key in COLUMN_KEYS:
        value = table.get(key)
        if isinstance(value, list):
            return value
    return []


def _clean(value) -> str:
    return NAME_JUNK_RE.sub("", str(value or ""))


def parse_json_metadata(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise errors.BadInput("元数据 JSON 无法解析", detail={"reason": exc.msg, "pos": exc.pos})
    if not isinstance(payload, dict):
        raise errors.BadInput("元数据顶层必须是对象", detail={"type": type(payload).__name__})

    version = str(payload.get("version") or "").strip() or None
    tables: list[str] = []
    columns: list[dict] = []
    issues: list[dict] = []
    ordinal = 0

    for table in _table_list(payload):
        if not isinstance(table, dict):
            issues.append({"line": 0, "table": "", "column": "", "reason": "LINE_MALFORMED",
                           "blocking": True, "detail": "表节点不是对象", "raw": ""})
            continue
        table_name = _clean(table.get("table") or table.get("name"))
        tables.append(table_name)
        for field in _column_list(table):
            ordinal += 1
            if not isinstance(field, dict):
                issues.append({"line": ordinal, "table": table_name, "column": "",
                               "reason": "LINE_MALFORMED", "blocking": True,
                               "detail": "列节点不是对象", "raw": ""})
                continue
            name = _clean(field.get("name") or field.get("column"))
            if not name:
                issues.append({"line": ordinal, "table": table_name, "column": "",
                               "reason": "LINE_MALFORMED", "blocking": True,
                               "detail": "缺少列名", "raw": json.dumps(field, ensure_ascii=False)})
                continue
            raw_id = field.get("field_id", field.get("id"))
            if isinstance(raw_id, float):
                # 浮点表示的身份一定已经丢过精度，不接受
                raise errors.BadInput(
                    "字段身份不能是浮点数",
                    detail={"table": table_name, "column": name, "value": repr(raw_id)},
                )
            field_id = str(raw_id).strip() if raw_id not in (None, "") else None
            type_raw = str(field.get("type") or "").strip() or "unknown"
            columns.append(
                {
                    "table": table_name,
                    "name": name,
                    "type_raw": type_raw,
                    "required": field.get("required"),
                    "field_id": field_id,
                    "line": ordinal,
                }
            )

    if not tables:
        issues.append({"line": 0, "table": "", "column": "", "reason": "TABLE_UNPARSED",
                       "blocking": True, "detail": "没有表节点", "raw": ""})
    return {"version": version, "tables": tables, "columns": columns, "issues": issues}


def _is_known_type(type_raw: str) -> bool:
    base = config.base_of(type_raw)
    return base in (
        config.NUMERIC_BASES | config.TEXT_BASES | config.TEMPORAL_BASES | config.BOOL_BASES
    )


def fold_columns(columns: list[dict], version_label: str, source: str) -> dict:
    """把原始列折叠成可用的字段清单，并留下每条诊断。"""
    from app.modules.m1_intake.ddl import detect_required_token

    accepted: list[dict] = []
    diagnostics: list[dict] = []
    seen_name: dict[tuple[str, str], dict] = {}
    seen_id: dict[str, dict] = {}
    column_lines = 0

    for column in sorted(columns, key=lambda item: item["line"]):
        column_lines += 1
        table = (column.get("table") or "").strip()
        name = (column.get("name") or "").strip()
        name_key = name.lower()
        key = (table.lower(), name_key)

        if key in seen_name:
            kept = seen_name[key]
            diagnostics.append(
                {
                    "line": column["line"],
                    "table": table,
                    "column": name,
                    "reason": "DUPLICATE_COLUMN",
                    "blocking": True,
                    "detail": f"已在第 {kept['line']} 行出现",
                    "raw": f"{table}.{name}",
                }
            )
            continue

        field_id = column.get("field_id")
        if field_id:
            field_id = str(field_id)
            if field_id in seen_id:
                kept = seen_id[field_id]
                diagnostics.append(
                    {
                        "line": column["line"],
                        "table": table,
                        "column": name,
                        "reason": "FIELD_ID_DUP",
                        "blocking": True,
                        "detail": f"身份已在 {kept['table']}.{kept['name']} 使用",
                        "raw": f"{field_id}",
                    }
                )
                continue
        else:
            field_id = derive_field_id(table, name)
            diagnostics.append(
                {
                    "line": column["line"],
                    "table": table,
                    "column": name,
                    "reason": "FIELD_ID_DERIVED",
                    "blocking": False,
                    "detail": field_id,
                    "raw": f"{table}.{name}",
                }
            )

        required = detect_required_token(column.get("required"))
        if required is None:
            required = False
            diagnostics.append(
                {
                    "line": column["line"],
                    "table": table,
                    "column": name,
                    "reason": "REQUIRED_TOKEN_UNKNOWN",
                    "blocking": False,
                    "detail": repr(column.get("required")),
                    "raw": f"{table}.{name}",
                }
            )

        type_raw = (column.get("type_raw") or "unknown").strip()
        if not _is_known_type(type_raw):
            diagnostics.append(
                {
                    "line": column["line"],
                    "table": table,
                    "column": name,
                    "reason": "UNKNOWN_TYPE",
                    "blocking": False,
                    "detail": type_raw,
                    "raw": f"{table}.{name}",
                }
            )

        field = {
            "field_id": field_id,
            "table": table,
            "name": name,
            "name_key": name_key,
            "type_raw": type_raw,
            "required": bool(required),
            "line": int(column["line"]),
            "version": version_label,
            "source": source,
        }
        accepted.append(field)
        seen_name[key] = field
        seen_id[field_id] = field

    blocked = [row for row in diagnostics if row["blocking"]]
    warnings = [row for row in diagnostics if not row["blocking"]]
    return {
        "fields": accepted,
        "diagnostics": diagnostics,
        "stats": {
            "column_lines": column_lines,
            "accepted": len(accepted),
            "blocked": len(blocked),
            "warnings": len(warnings),
            "tables": len({field["table"] for field in accepted}),
        },
    }


def diagnostic_csv(rows: list[dict]) -> str:
    """隔离区导出：行号、表、列、原因码、是否阻断、原值。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["line", "table", "column", "reason", "blocking", "detail", "raw"])
    for row in rows:
        writer.writerow([
            row.get("line", 0),
            row.get("table", ""),
            row.get("column", ""),
            row.get("reason", ""),
            "Y" if row.get("blocking") else "N",
            row.get("detail", ""),
            row.get("raw", ""),
        ])
    return buffer.getvalue()
