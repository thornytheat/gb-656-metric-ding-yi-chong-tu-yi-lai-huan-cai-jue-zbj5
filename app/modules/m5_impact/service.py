"""影响面编排：变更字段 → 反向闭包 → 受影响节点、环、断链。"""

from __future__ import annotations

from app.modules.m5_impact import broken, closure, cycle


def changed_fields(diff: dict) -> list[dict]:
    """只有破坏项才算影响面；新增字段不会让既有下游报错。"""
    return [
        row for row in diff["rows"]
        if row["verdict"] == "BREAKING" and row["bucket"] != "added"
    ]


def build(diff: dict, consumers: list[dict], resolution: dict) -> dict:
    fields = changed_fields(diff)
    by_field = {field_id: sorted(names)
                for field_id, names in (resolution.get("by_field") or {}).items()}
    broken_seeds: dict[str, set[str]] = {}
    for item in resolution.get("broken") or []:
        broken_seeds.setdefault(item["consumer"], set()).add(f"断链 {item['ref']}")
    graph = closure.impact(fields, by_field, consumers, broken_seeds=broken_seeds)
    cycles = cycle.find_cycles(consumers)
    sides = broken.one_sided(consumers)
    refs = broken.broken_refs(resolution)
    return {
        "impact": graph,
        "cycles": cycles,
        "one_sided": sides,
        "broken": refs,
        "summary": broken.summary(consumers, resolution, cycles),
    }
