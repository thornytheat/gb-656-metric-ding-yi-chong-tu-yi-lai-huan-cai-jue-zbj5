"""类型格编排：给字段补上归一类型，供兼容判定与页面使用。"""

from __future__ import annotations

from app import config
from app.modules.m2_types import coerce, lattice, range as value_range


def normalize_fields(fields: list[dict]) -> list[dict]:
    out: list[dict] = []
    for field in fields:
        spec = coerce.parse_type(field.get("type_raw"))
        item = dict(field)
        item["type"] = spec
        item["type_norm"] = coerce.type_text(spec)
        out.append(item)
    return out


def prepare(snapshot: dict) -> dict:
    fields = normalize_fields(snapshot.get("fields") or [])
    prepared = dict(snapshot)
    prepared["fields"] = fields
    prepared["type_stats"] = type_stats(fields)
    return prepared


def type_stats(fields: list[dict]) -> dict:
    counter: dict[str, int] = {}
    unknown = 0
    for field in fields:
        super_name = field["type"]["super"]
        counter[super_name] = counter.get(super_name, 0) + 1
        if not field["type"]["known"]:
            unknown += 1
    return {"by_super": counter, "unknown": unknown, "total": len(fields)}


def compare_specs(old_spec: dict, new_spec: dict) -> dict:
    """给兼容判定用的服务入口：两个归一后的类型直接比。"""
    return lattice.compare(old_spec, new_spec)


def compare_types(old_raw: str, new_raw: str) -> dict:
    old_spec = coerce.parse_type(old_raw)
    new_spec = coerce.parse_type(new_raw)
    verdict = lattice.compare(old_spec, new_spec)
    return {
        "old": {"raw": old_raw, "norm": coerce.type_text(old_spec), "super": old_spec["super"]},
        "new": {"raw": new_raw, "norm": coerce.type_text(new_spec), "super": new_spec["super"]},
        "change": verdict["change"],
        "detail": verdict["detail"],
        "reason": verdict["reason"],
        "evidence": verdict["evidence"],
        "breaking": lattice.is_breaking(verdict["change"]),
    }


def grid() -> list[dict]:
    return value_range.grid_rows()


def aliases() -> dict:
    return dict(config.TYPE_ALIASES)
