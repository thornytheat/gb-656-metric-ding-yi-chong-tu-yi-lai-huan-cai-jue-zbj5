"""字段映射表与引用解析：旧名 → 新名、别名 SQL、映射冲突。"""

from __future__ import annotations

from app.modules.m4_mapping import rename, sqlalias

CONFLICT_PRIMARY = "MAPPING_CONFLICT"


def build(fields_old: list[dict], fields_new: list[dict], diff: dict) -> dict:
    rows: list[dict] = []
    for row in diff["rows"]:
        if row["bucket"] in ("added", "deleted"):
            continue
        status = "same"
        alias_sql = None
        if row["bucket"] == "conflict":
            status = "conflict"
        elif row["new_name"] != row["old_name"]:
            status = "renamed"
            alias_sql = f"{row['new_name']} as {row['old_name']}"
        rows.append({
            "field_id": row["field_id"],
            "table": row["table"],
            "old_name": row["old_name"],
            "new_name": row["new_name"],
            "old_type": row["old_type"],
            "new_type": row["new_type"],
            "status": status,
            "alias_sql": alias_sql,
            "change": row["change"],
            "verdict": row["verdict"],
            "primary": row["primary"],
            "reasons": list(row["reasons"]),
            "reader_ok": row["reader_ok"],
            "writer_ok": row["writer_ok"],
        })

    conflicts = _merge_conflicts(rows)
    added = [
        {"field_id": row["field_id"], "table": row["table"], "name": row["new_name"],
         "type": row["new_type"], "required": row["new_required"], "primary": row["primary"]}
        for row in diff["rows"] if row["bucket"] == "added"
    ]
    deleted = [
        {"field_id": row["field_id"], "table": row["table"], "name": row["old_name"],
         "type": row["old_type"], "referenced": row["referenced"],
         "ref_count": row["ref_count"], "primary": row["primary"]}
        for row in diff["rows"] if row["bucket"] == "deleted"
    ]
    renamed = [row for row in rows if row["status"] == "renamed"]
    aliases = [{"table": row["table"], "sql": row["alias_sql"]} for row in renamed]
    return {
        "rows": rows,
        "renamed": renamed,
        "conflicts": conflicts,
        "added": added,
        "deleted": deleted,
        "aliases": aliases,
        "stats": {
            "rows": len(rows),
            "renamed": len(renamed),
            "conflicts": len(conflicts),
            "added": len(added),
            "deleted": len(deleted),
            "aliased": len(aliases),
        },
    }


def _merge_conflicts(rows: list[dict]) -> list[dict]:
    """两类冲突单独列出：多个旧名落到同一个新名；改名同时换类型。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if not row["new_name"]:
            continue
        groups.setdefault((row["table"].lower(), row["new_name"].lower()), []).append(row)

    conflicts: list[dict] = []
    for (_, _new_name), items in sorted(groups.items()):
        if len({item["field_id"] for item in items}) < 2:
            continue
        for item in items:
            item["status"] = "conflict"
            item["alias_sql"] = None
            if CONFLICT_PRIMARY not in item["reasons"]:
                item["reasons"] = sorted(set(item["reasons"] + [CONFLICT_PRIMARY]))
            item["primary"] = CONFLICT_PRIMARY
            item["verdict"] = "BREAKING"
        conflicts.append({
            "table": items[0]["table"],
            "new_name": items[0]["new_name"],
            "old_names": sorted({item["old_name"] for item in items}),
            "field_ids": sorted({item["field_id"] for item in items}),
            "reason": CONFLICT_PRIMARY,
        })

    for row in rows:
        if row["status"] != "conflict" or row["primary"] == CONFLICT_PRIMARY:
            continue
        conflicts.append({
            "table": row["table"],
            "new_name": row["new_name"],
            "old_names": [row["old_name"]],
            "field_ids": [row["field_id"]],
            "reason": row["primary"],
        })
    return conflicts


def resolve_refs(consumers: list[dict], fields_old: list[dict],
                 fields_new: list[dict], edges: dict[str, set[str]]) -> dict:
    """把下游写的旧名字解析到新字段上。

    解析必须过身份：先看这个名字在旧版本里是谁，再按 field_id 找到新版本里的它。
    名字还在、但已经换了身份的，一律算断链，不能悄悄绑到新列上。
    """
    old_index = {(field["table"].lower(), field["name"].lower()): field
                 for field in fields_old}
    new_by_id = {field["field_id"]: field for field in fields_new}
    new_index = {(field["table"].lower(), field["name"].lower()): field
                 for field in fields_new}
    resolved: list[dict] = []
    broken: list[dict] = []
    aliased: list[dict] = []
    ref_counts: dict[tuple[str, str], int] = {}
    by_field: dict[str, set[str]] = {}
    direct_count = 0

    for consumer in consumers:
        for ref in consumer["refs"]:
            table, column = sqlalias.split_ref(ref)
            key = (table.lower(), column.lower())
            ref_counts[key] = ref_counts.get(key, 0) + 1

            field = None
            via = "direct"
            detail = ""
            old_field = old_index.get(key)
            if old_field is not None:
                target = new_by_id.get(old_field["field_id"])
                if target is not None and target["name"].lower() == column.lower():
                    field = target
                    via = "direct"
                    direct_count += 1
                elif target is not None:
                    chain = rename.resolve(edges, table, column)
                    if (not chain["cycle"]
                            and chain["canonical"].lower() == target["name"].lower()):
                        field = target
                        via = "alias"
                    else:
                        via = "unsafe_alias"
                        detail = "改名同时换类型，别名不安全"
                else:
                    rebound = new_index.get(key)
                    via = "rebound" if rebound is not None else "deleted"
                    detail = ("名字已被别的身份占用" if rebound is not None
                              else "字段已删除")
            else:
                chain = rename.resolve(edges, table, column)
                canonical = chain["canonical"]
                target = new_index.get((table.lower(), canonical.lower()))
                if target is not None:
                    field = target
                    via = "cycle" if chain["cycle"] else "alias"
                else:
                    via = "unknown_name"
                    detail = "旧版本里没有这个名字"

            item = {
                "consumer": consumer["name"],
                "kind": consumer.get("kind") or "task",
                "ref": ref,
                "table": table,
                "column": column,
                "via": via,
                "field_id": field["field_id"] if field else None,
                "new_name": field["name"] if field else None,
                "detail": detail,
            }
            if field is None:
                broken.append(item)
                continue
            resolved.append(item)
            by_field.setdefault(field["field_id"], set()).add(consumer["name"])
            if via != "direct":
                aliased.append(item)

    return {
        "total": len(resolved) + len(broken),
        "reachable": len(resolved),
        "broken_count": len(broken),
        "aliased_count": len(aliased),
        "direct_count": direct_count,
        "resolved": resolved,
        "broken": broken,
        "aliased": aliased,
        "ref_counts": ref_counts,
        "by_field": by_field,
    }


