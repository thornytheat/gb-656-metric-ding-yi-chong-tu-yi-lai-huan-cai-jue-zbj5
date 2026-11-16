"""账目守恒：字段账与引用账，两边都要对上。

字段账：旧版本 = 未变 + 改名 + 换类型 + 改必填 + 冲突 + 删除
        新版本 = 未变 + 改名 + 换类型 + 改必填 + 冲突 + 新增
引用账：登记引用 = 可达 + 断链
"""

from __future__ import annotations

BUCKETS = ("unchanged", "renamed", "retyped", "required", "conflict", "deleted", "added")

OLD_SIDE = ("unchanged", "renamed", "retyped", "required", "conflict", "deleted")
NEW_SIDE = ("unchanged", "renamed", "retyped", "required", "conflict", "added")


def field_ledger(diff: dict) -> dict:
    buckets = {name: int((diff.get("buckets") or {}).get(name, 0)) for name in BUCKETS}
    old_total = int((diff.get("counts") or {}).get("old_total", 0))
    new_total = int((diff.get("counts") or {}).get("new_total", 0))
    old_side = sum(buckets[name] for name in OLD_SIDE)
    new_side = sum(buckets[name] for name in NEW_SIDE)
    return {
        "buckets": buckets,
        "old_total": old_total,
        "new_total": new_total,
        "old_side": old_side,
        "new_side": new_side,
        "old_gap": old_total - old_side,
        "new_gap": new_total - new_side,
        "balanced": old_side == old_total and new_side == new_total,
    }


def ref_ledger(consumers: list[dict], resolution: dict) -> dict:
    refs_total = sum(len(item["refs"]) for item in consumers)
    reachable = int(resolution.get("reachable") or 0)
    broken = int(resolution.get("broken_count") or 0)
    return {
        "refs_total": refs_total,
        "reachable": reachable,
        "broken": broken,
        "aliased": int(resolution.get("aliased_count") or 0),
        "direct": int(resolution.get("direct_count") or 0),
        "gap": refs_total - reachable - broken,
        "balanced": refs_total == reachable + broken,
    }


def build(diff: dict, consumers: list[dict], resolution: dict) -> dict:
    fields = field_ledger(diff)
    refs = ref_ledger(consumers, resolution)
    verdicts = diff.get("verdicts") or {}
    counts = diff.get("counts") or {}
    return {
        "fields": fields,
        "refs": refs,
        "buckets": fields["buckets"],
        "counts": {
            "old_total": fields["old_total"],
            "new_total": fields["new_total"],
            "breaking": int(counts.get("breaking", 0)),
            "compatible": int(counts.get("compatible", 0)),
            "breaking_by_mapping": int(verdicts.get("COMPATIBLE_BY_MAPPING", 0)),
            "refs_total": refs["refs_total"],
            "refs_reachable": refs["reachable"],
            "refs_broken": refs["broken"],
            "refs_aliased": refs["aliased"],
            "consumers": len(consumers),
        },
        "balanced": {
            "fields_old": fields["old_side"] == fields["old_total"],
            "fields_new": fields["new_side"] == fields["new_total"],
            "refs": refs["balanced"],
            "all": fields["balanced"] and refs["balanced"],
        },
    }
