"""断链与单向登记：引用解析不到字段、上下游只有一边登记。"""

from __future__ import annotations


def one_sided(consumers: list[dict]) -> list[dict]:
    """A 说 B 在我的下游，但 B 的登记里没有 A 这个上游。"""
    by_name = {item["name"]: item for item in consumers}
    pairs: dict[tuple[str, str], dict] = {}
    for consumer in consumers:
        name = consumer["name"]
        for down in consumer["downstream"]:
            target = by_name.get(down)
            if target is None or name in target["upstream"]:
                continue
            pairs[(name, down)] = {
                "upstream": name,
                "downstream": down,
                "declared": "downstream",
                "missing": "upstream",
                "reason": "REF_ONE_SIDED",
            }
        for up in consumer["upstream"]:
            source = by_name.get(up)
            if source is None or name in source["downstream"]:
                continue
            pairs[(up, name)] = {
                "upstream": up,
                "downstream": name,
                "declared": "upstream",
                "missing": "downstream",
                "reason": "REF_ONE_SIDED",
            }
    return [pairs[key] for key in sorted(pairs)]


def broken_refs(resolution: dict) -> list[dict]:
    return [
        {
            "consumer": item["consumer"],
            "kind": item["kind"],
            "ref": item["ref"],
            "table": item["table"],
            "column": item["column"],
            "via": item.get("via") or "unknown_name",
            "detail": item.get("detail") or "",
            "reason": "REF_BROKEN",
        }
        for item in resolution.get("broken") or []
    ]


def summary(consumers: list[dict], resolution: dict, cycles: dict) -> dict:
    refs_total = sum(len(item["refs"]) for item in consumers)
    reachable = int(resolution.get("reachable") or 0)
    broken = int(resolution.get("broken_count") or 0)
    return {
        "consumers": len(consumers),
        "refs_total": refs_total,
        "reachable": reachable,
        "broken": broken,
        "aliased": int(resolution.get("aliased_count") or 0),
        "one_sided": len(one_sided(consumers)),
        "cycles": int(cycles.get("cycle_count") or 0),
        "self_loops": int(cycles.get("self_loop_count") or 0),
        "balanced": reachable + broken == refs_total,
    }
