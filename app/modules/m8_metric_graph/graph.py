"""指标依赖图：同名同版本裁决、依赖边、环、缺依赖与受影响链路。

这里只认声明出来的依赖：`depends_on` 写谁就依赖谁，不从公式里猜列名。
理由有两条：公式里的名字可能是字段、别名或常量，猜错会把影响面放大；
而「缺依赖」只有在声明了目标却找不到定义时才有确定含义。
"""

from __future__ import annotations

from app import config
from app.modules.m3_compat import metric_rules
from app.modules.m5_impact import cycle as cycle_mod

# 锚点原因的主次：冲突最先看，其次是缺依赖，再看环
ANCHOR_RANK = {
    "METRIC_CONFLICT": 10,
    "METRIC_BROKEN_DEP": 20,
    "METRIC_CYCLE": 30,
    "METRIC_REVISION_CHANGED": 40,
}


def normalize_definitions(raw: list) -> dict:
    """校验并归一指标定义；输入顺序不影响结果。"""
    if not isinstance(raw, list) or not raw:
        raise_error("需要非空的 definitions 列表", {"field": "definitions"})
    if len(raw) > 200:
        raise_error("一次最多 200 条指标定义", {"count": len(raw)})

    rows: list[dict] = []
    versions: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise_error("definitions 里出现非对象项", {"index": index})
        name = str(item.get("name") or "").strip()
        if not name:
            raise_error("指标定义缺少 name", {"index": index})
        expression = str(item.get("expression") or "").strip()
        version = str(item.get("version") or "").strip()
        if version:
            versions.add(version)
        priority_raw = item.get("priority", 0)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            raise_error("priority 必须是整数",
                        {"index": index, "value": repr(priority_raw)})
        depends_raw = item.get("depends_on") or []
        if not isinstance(depends_raw, list):
            raise_error("depends_on 必须是列表", {"index": index})
        depends: list[str] = []
        for dep in depends_raw:
            text = str(dep or "").strip()
            if text and text.lower() not in {seen.lower() for seen in depends}:
                depends.append(text)
        rows.append({
            "name": name,
            "name_key": name.lower(),
            "version": version,
            "expression": expression,
            "priority": priority,
            "source": str(item.get("source") or "").strip(),
            "depends_on": sorted(depends, key=str.lower),
        })

    # 一个批次只允许一个定义版本：图上的同名节点必须唯一，否则依赖边指向谁就说不清
    if len(versions) > 1:
        raise_error("同一批次只能有一个指标定义版本", {"versions": sorted(versions)})
    common_version = next(iter(versions)) if versions else ""
    for row in rows:
        row["version"] = common_version

    rows.sort(key=lambda row: (row["name_key"], row["version"],
                               row["source"], row["expression"]))
    return {"definitions": rows, "version": common_version}


def raise_error(message: str, detail: dict) -> None:
    from app import errors

    raise errors.BadInput(message, detail=detail)


def canonical_payload(definitions: list[dict]) -> dict:
    """摘要用的规范形：只留影响裁决的字段，顺序固定。"""
    return {
        "version": definitions[0]["version"] if definitions else "",
        "definitions": [
            {
                "name": row["name_key"],
                "version": row["version"],
                "expression": row["expression"],
                "priority": row["priority"],
                "source": row["source"],
                "depends_on": sorted({dep.lower() for dep in row["depends_on"]}),
            }
            for row in definitions
        ],
    }


def evaluate(definitions: list[dict]) -> dict:
    """同名同版本冲突裁决 + 可用节点清单。"""
    resolution = metric_rules.resolve(
        [
            {
                "name": row["name"],
                "version": row["version"],
                "expression": row["expression"],
                "priority": row["priority"],
                "source": row["source"],
            }
            for row in definitions
        ]
    )

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in definitions:
        groups.setdefault((row["name_key"], row["version"]), []).append(row)

    conflicts: list[dict] = []
    for conflict in resolution["conflicts"]:
        key = (str(conflict["name"]).lower(), str(conflict["version"]))
        rows = groups.get(key) or []
        reason = conflict["reason"]
        signatures = sorted({metric_rules.signature(row["expression"]) for row in rows})
        conflicts.append({
            "name": rows[0]["name"] if rows else conflict["name"],
            "version": conflict["version"],
            "reason": reason,
            "reason_code": "METRIC_CONFLICT",
            "sources": sorted({row["source"] for row in rows if row["source"]}),
            "signatures": signatures,
            "definition_count": len(rows),
        })

    chosen: list[dict] = []
    for item in resolution["chosen"]:
        key = (str(item["name"]).lower(), str(item["version"]))
        rows = groups.get(key) or []
        ranked = sorted(
            rows,
            key=lambda row: (-row["priority"], row["source"], row["expression"]),
        )
        best = None
        for row in ranked:
            if (row["source"] == item.get("source")
                    and metric_rules.signature(row["expression"]) == item["signature"]):
                best = row
                break
        if best is None:
            best = ranked[0] if ranked else None
        if best is None:
            continue
        chosen.append({
            "name": best["name"],
            "name_key": best["name_key"],
            "version": best["version"],
            "expression": best["expression"],
            "priority": best["priority"],
            "source": best["source"],
            "depends_on": list(best["depends_on"]),
            "signature": item["signature"],
        })

    chosen.sort(key=lambda row: row["name_key"])
    return {
        "conflicts": sorted(conflicts, key=lambda row: (row["name"].lower(), row["version"])),
        "chosen": chosen,
    }


def build_graph(chosen: list[dict]) -> dict:
    """建依赖图，分出可解析边、缺依赖与自引用。"""
    nodes = {row["name_key"]: row for row in chosen}
    edges: dict[str, list[str]] = {}
    broken: list[dict] = []
    self_loops: list[str] = []

    for row in chosen:
        resolved: list[str] = []
        for dep in row["depends_on"]:
            key = dep.lower()
            if key == row["name_key"]:
                self_loops.append(row["name"])
                continue
            if key not in nodes:
                broken.append({"metric": row["name"], "dependency": dep,
                               "reason": "METRIC_BROKEN_DEP"})
                continue
            resolved.append(key)
        edges[row["name_key"]] = sorted(set(resolved))

    detected = cycle_mod.find_cycles([
        {"name": key, "upstream": edges.get(key, []), "downstream": []}
        for key in sorted(nodes)
    ])
    cycles = [
        {
            "nodes": [nodes.get(node, {}).get("name", node) for node in item["nodes"]],
            "node_keys": list(item["nodes"]),
            "length": item["length"],
            "reason": "METRIC_CYCLE",
        }
        for item in detected["cycles"]
    ]

    return {
        "nodes": nodes,
        "edges": edges,                       # metric key -> 它依赖的指标（上游方向）
        "broken_deps": sorted(
            [
                {
                    "metric": item["metric"],
                    "metric_key": item["metric"].lower(),
                    "dependency": item["dependency"],
                    "dependency_key": item["dependency"].lower(),
                    "reason": "METRIC_BROKEN_DEP",
                }
                for item in broken
            ],
            key=lambda item: (item["metric"].lower(), item["dependency"].lower()),
        ),
        "cycles": cycles,
        "self_loops": sorted(
            {nodes[key]["name"] if key in nodes else key
             for key in set(self_loops) | {item["node"] for item in detected["self_loops"]}}
        ),
        "self_loop_keys": sorted(
            set(self_loops) | {item["node"] for item in detected["self_loops"]}
        ),
    }


def downstream_closure(edges: dict[str, list[str]], anchor: str,
                       limit: int | None = None) -> dict:
    """从锚点往「谁依赖我」的方向走，得到受影响链路。"""
    depth_limit = limit or config.MAX_IMPACT_DEPTH
    reverse: dict[str, set[str]] = {key: set() for key in edges}
    for node, deps in edges.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(node)

    depth: dict[str, int] = {anchor: 0}
    via: dict[str, str | None] = {anchor: None}
    queue = [anchor]
    while queue:
        node = queue.pop(0)
        if depth[node] >= depth_limit:
            continue
        for nxt in sorted(reverse.get(node, ())):
            if nxt in depth:
                continue
            depth[nxt] = depth[node] + 1
            via[nxt] = node
            queue.append(nxt)

    reachable = set(depth)
    chain_edges = sorted(
        [parent, child]
        for child, parent in via.items()
        if parent is not None and parent in reachable and child in reachable
    )
    return {
        "anchor": anchor,
        "depths": depth,
        "via": via,
        "edges": chain_edges,
        "affected": sorted(name for name in depth if depth[name] > 0),
    }


def anchor_reasons(chosen: list[dict], graph: dict, conflicts: list[dict],
                   changed: set[str], focus: set[str] | None = None) -> dict[str, list[str]]:
    """每个指标作为链路锚点的原因；同一锚点命中多条时按 ANCHOR_RANK 排。"""
    reasons: dict[str, set[str]] = {}

    def add(name_key: str, code: str) -> None:
        reasons.setdefault(str(name_key).lower(), set()).add(code)

    for item in conflicts:
        add(item["name"].lower(), "METRIC_CONFLICT")
    for item in graph["broken_deps"]:
        add(item["metric_key"], "METRIC_BROKEN_DEP")
    # 一个环只留一个锚点：取环上最小的节点，其余成员作为链上下游出现，
    # 避免同一个环在页面上被拆成两条重复链路
    for cycle in graph["cycles"]:
        node_keys = list(cycle["node_keys"])
        if node_keys:
            add(min(node_keys), "METRIC_CYCLE")
    for key in graph["self_loop_keys"]:
        add(key, "METRIC_CYCLE")
    for name_key in changed:
        add(name_key, "METRIC_REVISION_CHANGED")
    for name_key in focus or set():
        if name_key in graph["nodes"]:
            add(name_key, "METRIC_FOCUS_ANCHOR")

    return {
        key: sorted(codes, key=lambda code: (ANCHOR_RANK.get(code, 500), code))
        for key, codes in reasons.items()
    }


def build_chains(chosen: list[dict], graph: dict,
                 anchors: dict[str, list[str]]) -> list[dict]:
    """按锚点展开受影响链路，节点与边都按确定顺序排列。"""
    nodes = graph["nodes"]
    chains: list[dict] = []
    for anchor in sorted(anchors):
        closure = downstream_closure(graph["edges"], anchor)
        node_rows = []
        for name_key in sorted(closure["depths"],
                               key=lambda key: (closure["depths"][key], key)):
            row = nodes.get(name_key)
            parent_key = closure["via"].get(name_key)
            node_rows.append({
                "name": row["name"] if row else name_key,
                "name_key": name_key,
                "depth": closure["depths"][name_key],
                "role": "anchor" if name_key == anchor else "dependent",
                "via": nodes[parent_key]["name"] if parent_key in nodes else None,
            })
        chains.append({
            "anchor": nodes[anchor]["name"] if anchor in nodes else anchor,
            "anchor_key": anchor,
            "reasons": anchors[anchor],
            "nodes": node_rows,
            "edges": closure["edges"],
            "affected_count": len(closure["affected"]),
        })
    chains.sort(key=lambda item: (item["anchor_key"], item["reasons"]))
    return chains


def fingerprints(definitions: list[dict]) -> dict[str, dict]:
    """每个指标的内容指纹：同版多份定义时把签名集排序后一起比。

    裁决之间「修订了什么」只认这张表，不认提交顺序或批次号。
    """
    groups: dict[str, list[dict]] = {}
    for row in definitions:
        groups.setdefault(row["name_key"], []).append(row)
    out: dict[str, dict] = {}
    for name_key, rows in groups.items():
        out[name_key] = {
            "name": rows[0]["name"],
            "version": rows[0]["version"],
            "signatures": sorted({metric_rules.signature(row["expression"])
                                  for row in rows}),
            "depends_on": sorted({dep.lower() for row in rows
                                  for dep in row["depends_on"]}),
        }
    return out


def changed_metrics(definitions: list[dict], parent_fingerprints: dict | None) -> dict:
    """与上一裁决比，哪些指标的定义变了；新增与删除单独列出。"""
    current = fingerprints(definitions)
    if not parent_fingerprints:
        return {"changed": set(), "added": set(), "removed": set()}

    changed = {
        key for key, row in current.items()
        if key in parent_fingerprints and (
            row["signatures"] != sorted(parent_fingerprints[key].get("signatures") or [])
            or row["depends_on"] != sorted(parent_fingerprints[key].get("depends_on") or [])
            or row["version"] != parent_fingerprints[key].get("version")
        )
    }
    added = set(current) - set(parent_fingerprints)
    removed = set(parent_fingerprints) - set(current)
    return {"changed": changed, "added": added, "removed": removed}
