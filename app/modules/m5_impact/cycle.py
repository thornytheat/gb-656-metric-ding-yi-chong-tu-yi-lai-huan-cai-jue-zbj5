"""环检测：下游依赖成环时，闭包会绕回来，必须先把环找出来。

自引用单独归一类，不然一个节点也能被算成环，页面上分不清。
"""

from __future__ import annotations


def _canonical(nodes: list[str]) -> tuple[str, ...]:
    """把环旋转到最小节点开头，保证同一环只有一种写法。"""
    if not nodes:
        return tuple()
    pivot = min(range(len(nodes)), key=lambda i: nodes[i])
    rotated = nodes[pivot:] + nodes[:pivot]
    return tuple(rotated)


def find_cycles(consumers: list[dict]) -> dict:
    names = sorted(item["name"] for item in consumers)
    edges: dict[str, set[str]] = {name: set() for name in names}
    for consumer in consumers:
        name = consumer["name"]
        for down in consumer["downstream"]:
            if down in edges and down != name:
                edges[name].add(down)
        for up in consumer["upstream"]:
            if up in edges and up != name:
                edges[up].add(name)

    self_loops = sorted(
        item["name"] for item in consumers
        if item["name"] in item["downstream"] or item["name"] in item["upstream"]
    )

    color: dict[str, int] = {name: 0 for name in names}
    stack: list[str] = []
    found: dict[tuple[str, ...], tuple[str, ...]] = {}

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            if color.get(nxt, 0) == 0:
                visit(nxt)
            elif color.get(nxt) == 1:
                if nxt in stack:
                    ring = stack[stack.index(nxt):]
                    key = _canonical(ring)
                    found[key] = key
        stack.pop()
        color[node] = 2

    for name in names:
        if color[name] == 0:
            visit(name)

    cycles = [
        {"nodes": list(key), "length": len(key), "reason": "REF_CYCLE"}
        for key in sorted(found)
    ]
    return {
        "cycles": cycles,
        "self_loops": [{"node": name, "reason": "REF_SELF_LOOP"} for name in self_loops],
        "cycle_count": len(cycles),
        "self_loop_count": len(self_loops),
    }


def has_cycle(consumers: list[dict]) -> bool:
    result = find_cycles(consumers)
    return bool(result["cycles"] or result["self_loops"])
