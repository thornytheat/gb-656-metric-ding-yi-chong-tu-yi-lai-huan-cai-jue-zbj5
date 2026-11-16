"""字段差异判定：按 field_id 对齐两个版本，逐字段给出两向结论。

身份只看 field_id：改名不算新字段，同名不同身份也不算同一个字段。
同名不同身份的两行各自留在原有桶里（删除 / 新增），账目才守恒。
"""

from __future__ import annotations

from app.modules.m2_types import service as type_service
from app.modules.m3_compat import direction, priority


def _sort_key(field: dict) -> tuple:
    return (field.get("table") or "", int(field.get("line") or 0), field.get("field_id") or "")


def _index_by_id(fields: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field in fields:
        out.setdefault(field["field_id"], field)
    return out


def _index_by_name(fields: list[dict]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for field in fields:
        out.setdefault((field["table"].lower(), field["name"].lower()), field)
    return out


def _ref_count(ref_counts: dict, field: dict) -> int:
    key = (field["table"].lower(), field["name"].lower())
    return int(ref_counts.get(key, 0))


def _base_row(field_id: str, table: str) -> dict:
    return {
        "field_id": field_id,
        "table": table,
        "old_name": None,
        "new_name": None,
        "old_type": None,
        "new_type": None,
        "old_required": None,
        "new_required": None,
        "change": "same",
        "type_detail": None,
        "rename": False,
        "case_only": False,
        "required_change": "same",
        "bucket": "unchanged",
        "reasons": [],
        "primary": "COMPATIBLE",
        "severity": "info",
        "verdict": "COMPATIBLE",
        "reader": {"ok": True, "reason": "COMPATIBLE", "repair": None},
        "writer": {"ok": True, "reason": "COMPATIBLE", "repair": None},
        "reader_ok": True,
        "writer_ok": True,
        "referenced": False,
        "ref_count": 0,
        "line_old": None,
        "line_new": None,
        "table_changed": False,
        "name_clash": False,
        "type_evidence": {},
    }


def _deleted(old: dict, refs: int, clash: bool) -> dict:
    row = _base_row(old["field_id"], old["table"])
    reasons = ["FIELD_DELETED_REFERENCED"] if refs else ["FIELD_DELETED"]
    if clash:
        reasons.append("NAME_DUP_DIFF_ID")
    row.update({
        "old_name": old["name"],
        "old_type": old.get("type_norm") or old.get("type_raw"),
        "old_required": bool(old["required"]),
        "change": "deleted",
        "bucket": "deleted",
        "reasons": priority.ordered(reasons),
        "primary": priority.primary(reasons),
        "severity": "break" if (refs or clash) else "info",
        "verdict": "BREAKING" if (refs or clash) else "COMPATIBLE",
        "reader": {"ok": not (refs or clash), "reason": reasons[0],
                   "repair": "keep_field" if (refs or clash) else None},
        "reader_ok": not (refs or clash),
        "writer": {"ok": True, "reason": "FIELD_DELETED", "repair": None},
        "referenced": bool(refs),
        "ref_count": refs,
        "line_old": old.get("line"),
        "name_clash": clash,
    })
    return row


def _added(new: dict, clash: bool) -> dict:
    row = _base_row(new["field_id"], new["table"])
    reasons = ["FIELD_ADDED"]
    if clash:
        reasons.append("NAME_DUP_DIFF_ID")
    row.update({
        "new_name": new["name"],
        "new_type": new.get("type_norm") or new.get("type_raw"),
        "new_required": bool(new["required"]),
        "change": "added",
        "bucket": "added",
        "reasons": priority.ordered(reasons),
        "primary": priority.primary(reasons),
        "severity": "break" if clash else "info",
        "verdict": "BREAKING" if clash else "COMPATIBLE",
        "line_new": new.get("line"),
        "name_clash": clash,
    })
    return row


def judge_field(old: dict, new: dict, ref_counts: dict,
                names_new: dict[tuple[str, str], dict]) -> dict:
    type_verdict = type_service.compare_specs(old["type"], new["type"])
    rename = old["name"] != new["name"]
    case_only = rename and old["name"].lower() == new["name"].lower()
    req = direction.required_change(old["required"], new["required"])
    table_changed = old["table"] != new["table"]
    twin = names_new.get((old["table"].lower(), old["name"].lower()))
    name_clash = bool(twin and twin["field_id"] != old["field_id"])
    conflict = rename and type_verdict["change"] != "same"

    reasons: list[str] = [type_verdict["reason"]]
    if rename:
        reasons.append("RENAME_CASE_ONLY" if case_only else "RENAME_ONLY")
    if req != "same":
        reasons.append("REQUIRED_LOOSENED" if req == "loosened" else "REQUIRED_TIGHTENED")
    if conflict:
        reasons.append("RENAME_AND_RETYPE")
    if name_clash:
        reasons.append("NAME_DUP_DIFF_ID")
    if table_changed:
        reasons.append("ID_REUSED")

    primary = priority.primary(reasons)
    cross = conflict or name_clash or table_changed
    if cross:
        bucket = "conflict"
    elif rename:
        bucket = "renamed"
    elif type_verdict["change"] != "same":
        bucket = "retyped"
    elif req != "same":
        bucket = "required"
    else:
        bucket = "unchanged"

    state = {
        "conflict": cross,
        "primary": primary,
        "type_change": type_verdict["change"],
        "required_change": req,
        "rename": rename,
        "deleted_referenced": False,
        "table_changed": table_changed,
    }
    reader_view = direction.reader(state)
    writer_view = direction.writer(state)
    verdict = direction.combine(reader_view, writer_view, rename)
    refs = _ref_count(ref_counts, old)

    row = _base_row(old["field_id"], old["table"])
    row.update({
        "old_name": old["name"],
        "new_name": new["name"],
        "old_type": old.get("type_norm") or old.get("type_raw"),
        "new_type": new.get("type_norm") or new.get("type_raw"),
        "old_required": bool(old["required"]),
        "new_required": bool(new["required"]),
        "change": type_verdict["change"],
        "type_detail": type_verdict["detail"],
        "rename": rename,
        "case_only": case_only,
        "required_change": req,
        "bucket": bucket,
        "reasons": priority.ordered(reasons),
        "primary": primary,
        "severity": priority.severity_of(reasons),
        "verdict": verdict,
        "reader": reader_view,
        "writer": writer_view,
        "reader_ok": reader_view["ok"],
        "writer_ok": writer_view["ok"],
        "referenced": bool(refs),
        "ref_count": refs,
        "line_old": old.get("line"),
        "line_new": new.get("line"),
        "table_changed": table_changed,
        "name_clash": name_clash,
        "type_evidence": type_verdict["evidence"],
    })
    return row


def diff_pair(fields_old: list[dict], fields_new: list[dict],
              ref_counts: dict | None = None) -> dict:
    ref_counts = ref_counts or {}
    by_id_old = _index_by_id(fields_old)
    by_id_new = _index_by_id(fields_new)
    names_new = _index_by_name(fields_new)
    names_old = _index_by_name(fields_old)

    rows: list[dict] = []
    for old in sorted(fields_old, key=_sort_key):
        new = by_id_new.get(old["field_id"])
        if new is None:
            twin = names_new.get((old["table"].lower(), old["name"].lower()))
            clash = bool(twin)
            rows.append(_deleted(old, _ref_count(ref_counts, old), clash))
        else:
            rows.append(judge_field(old, new, ref_counts, names_new))
    for new in sorted(fields_new, key=_sort_key):
        if new["field_id"] in by_id_old:
            continue
        twin = names_old.get((new["table"].lower(), new["name"].lower()))
        clash = bool(twin and twin["field_id"] != new["field_id"])
        rows.append(_added(new, clash))

    buckets = _count(rows, "bucket")
    changes = _count(rows, "change")
    verdicts = _count(rows, "verdict")
    return {
        "rows": rows,
        "buckets": buckets,
        "changes": changes,
        "verdicts": verdicts,
        "counts": {
            "old_total": len(fields_old),
            "new_total": len(fields_new),
            "matched": sum(1 for row in rows if row["bucket"] not in ("added", "deleted")),
            "breaking": verdicts.get("BREAKING", 0),
            "compatible": verdicts.get("COMPATIBLE", 0)
            + verdicts.get("COMPATIBLE_BY_MAPPING", 0),
        },
        "breaking_fields": [row["field_id"] for row in rows
                            if row["verdict"] == "BREAKING"],
        "name_clashes": [
            {"table": row["table"], "old_name": row["old_name"], "new_name": row["new_name"],
             "field_id": row["field_id"]}
            for row in rows if row["name_clash"]
        ],
    }


def _count(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        out[row[key]] = out.get(row[key], 0) + 1
    return out
