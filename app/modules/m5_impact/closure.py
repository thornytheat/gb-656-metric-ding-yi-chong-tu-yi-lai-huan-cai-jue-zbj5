"""引用图反向闭包：从变更字段走到所有受影响的下游节点。

下游节点自己登记上游和下游，两边合并成一张有向图；闭包按层推进，按名字排序取最短层。
"""

from __future__ import annotations

from app import config


def dependency_graph(consumers: list[dict]) -> dict[str, set[str]]:
    """consumer -> 它依赖的上游 consumer（上下游两边登记都算）。"""
    names = {item["name"] for item in consumers}
    graph: dict[str, set[str]] = {name: set() for name in names}
    for item in consumers:
        for up in item["upstream"]:
            if up in names:
                graph[item["name"]].add(up)
        for down in item["downstream"]:
            if down in names:
                graph[down].add(item["name"])
    return graph


def downstream_graph(dependencies: dict[str, set[str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {name: set() for name in dependencies}
    for node, upstreams in dependencies.items():
        for up in upstreams:
            out.setdefault(up, set()).add(node)
    return out


def impact(changed_fields: list[dict], by_field: dict[str, list[str]],
           consumers: list[dict], broken_seeds: dict[str, set[str]] | None = None,
           max_depth: int | None = None) -> dict:
    limit = max_depth or config.MAX_IMPACT_DEPTH
    kinds = {item["name"]: item.get("kind") or "task" for item in consumers}
    downstream = downstream_graph(dependency_graph(consumers))

    seeds: dict[str, set[str]] = {}
    for field in changed_fields:
        label = f"{field['table']}.{field['old_name']}"
        for name in by_field.get(field["field_id"], []):
            seeds.setdefault(name, set()).add(label)

    depth: dict[str, int] = {name: 1 for name in seeds}
    via: dict[str, str] = {name: "field" for name in seeds}
    # 引用已经断开的节点同样受影响，单独标出来源
    for name, labels in sorted((broken_seeds or {}).items()):
        seeds.setdefault(name, set()).update(labels)
        depth.setdefault(name, 1)
        via.setdefault(name, "broken")
    queue = sorted(seeds)
    while queue:
        node = queue.pop(0)
        if depth[node] >= limit:
            continue
        for nxt in sorted(downstream.get(node, ())):
            if nxt == node or nxt in depth:
                continue
            depth[nxt] = depth[node] + 1
            via[nxt] = node
            queue.append(nxt)

    rows = [
        {
            "name": name,
            "kind": kinds.get(name, "task"),
            "depth": depth[name],
            "via": via[name],
            "fields": sorted(seeds.get(name, [])),
        }
        for name in sorted(depth, key=lambda item: (depth[item], item))
    ]
    depth_counts: dict[str, int] = {}
    for row in rows:
        key = str(row["depth"])
        depth_counts[key] = depth_counts.get(key, 0) + 1
    return {
        "rows": rows,
        "total": len(rows),
        "depth_counts": depth_counts,
        "max_depth": max((row["depth"] for row in rows), default=0),
        "seed_fields": sorted({label for labels in seeds.values() for label in labels}),
        "changed_fields": len(changed_fields),
        "node_count": len(consumers),
        "edge_count": sum(len(targets) for targets in downstream.values()),
    }
