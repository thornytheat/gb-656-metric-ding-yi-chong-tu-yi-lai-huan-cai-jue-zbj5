"""映射编排：读下游登记、建映射表、解析引用。"""

from __future__ import annotations

import json

from app import config, errors, manifest
from app.modules.m4_mapping import mapping, rename, sqlalias


def load_refs(rel_path: str) -> dict:
    path = config.resolve_sample(rel_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise errors.BadInput("下游登记文件无法解析", detail={"reason": exc.msg})
    raw = payload.get("consumers")
    if not isinstance(raw, list):
        raise errors.BadInput("下游登记缺少 consumers 列表", detail={"file": path.name})

    consumers: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise errors.BadInput("consumers 里出现非对象项", detail={"file": path.name})
        name = str(item.get("name") or "").strip()
        if not name:
            raise errors.BadInput("下游节点缺名字", detail={"file": path.name})
        if name in seen:
            raise errors.BadInput("下游节点重名", detail={"name": name})
        seen.add(name)
        parsed = sqlalias.extract_refs(str(item.get("sql") or ""))
        consumers.append({
            "name": name,
            "kind": str(item.get("kind") or "task"),
            "sql": str(item.get("sql") or ""),
            "refs": parsed["refs"],
            "aliases": parsed["aliases"],
            "unresolved": parsed["unresolved"],
            "upstream": [str(x) for x in (item.get("upstream") or [])],
            "downstream": [str(x) for x in (item.get("downstream") or [])],
        })
    if not consumers:
        raise errors.BadInput("下游登记为空", detail={"file": path.name})
    return {
        "source": path.name,
        "sha256": manifest.sha256_file(path),
        "registered_at": payload.get("registered_at"),
        "consumers": consumers,
        "ref_total": sum(len(item["refs"]) for item in consumers),
    }


def rename_edges(fields_old: list[dict], fields_new: list[dict],
                 allowed_ids: set[str] | None = None) -> dict:
    """只把干净的改名算进别名链；改名又换类型的项不能自动映射。"""
    return rename.build_edges(fields_old, fields_new, allowed_ids)


def merge_edges(*graphs: dict[str, set[str]]) -> dict:
    return rename.merge_edges(*graphs)


def build_mapping(fields_old: list[dict], fields_new: list[dict], diff: dict) -> dict:
    return mapping.build(fields_old, fields_new, diff)


def resolve(consumers: list[dict], fields_old: list[dict], fields_new: list[dict],
            graph: dict) -> dict:
    return mapping.resolve_refs(consumers, fields_old, fields_new, graph)
