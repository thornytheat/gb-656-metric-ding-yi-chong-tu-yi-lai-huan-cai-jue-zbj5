"""指标依赖裁决编排：绑定输入版本 → 采信定义 → 冲突裁决 → 依赖图 → 链路 → 落盘。

三条不变量在这里落地：

1. 绑定输入版本：批次键由定义摘要 + 输入版本 + 参数 + 规则集决定，修订必然得到新键；
   旧裁决整行保留，不覆盖、不改写。
2. 可续跑：裁决先落 pending 行，再按阶段推进并记录检查点；进程中途死掉后，
   同样的提交会从最后一个检查点接着跑，run_id 不变，结果行数也不增加。
3. 重试不新增：同输入重试命中批次键，直接返回已有裁决（ready）或续跑（pending）。
"""

from __future__ import annotations

import hashlib
import json
import re

from app import config, errors, manifest as manifest_mod, store
from app.modules.m8_metric_graph import graph

# 阶段顺序固定；每个阶段结束时把检查点写进 progress，续跑从这里接着来
STAGES = ("normalize", "evaluate", "graph", "chains", "finalize")

_VERSION_NUMBER_RE = re.compile(r"\d+")


def version_key(label: str) -> tuple:
    """输入版本的排序键：抽出数字段按数值比，其余字符按字典序。

    v2026.11 > v2026.9；v2026_09 与 v2026_9 视为同一版本。
    """
    text = (label or "").strip().lower()
    numbers = tuple(int(part) for part in _VERSION_NUMBER_RE.findall(text))
    letters = _VERSION_NUMBER_RE.sub("", text)
    return (numbers, letters)


def compare_versions(left: str, right: str) -> int:
    left_key, right_key = version_key(left), version_key(right)
    if left_key == right_key:
        return 0
    # 主键比数字段（不同长度先补齐），数字相同再比剩余字符
    left_numbers, right_numbers = left_key[0], right_key[0]
    width = max(len(left_numbers), len(right_numbers))
    padded_left = left_numbers + (0,) * (width - len(left_numbers))
    padded_right = right_numbers + (0,) * (width - len(right_numbers))
    if padded_left != padded_right:
        return 1 if padded_left > padded_right else -1
    if left_key[1] == right_key[1]:
        return 0
    return 1 if left_key[1] > right_key[1] else -1


def _json_digest(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _normalize_params(params: dict | None) -> dict:
    params = dict(params or {})
    if len(params) > config.METRIC_BATCH_PARAMS_LIMIT:
        raise errors.BadInput("params 最多 12 项", detail={"count": len(params)})
    clean: dict = {}
    for key, value in params.items():
        name = str(key).strip()
        if not name:
            raise errors.BadInput("params 的键不能为空", detail={"field": "params"})
        if isinstance(value, (dict, list)):
            clean[name] = value
        elif value is None:
            clean[name] = None
        else:
            clean[name] = value if isinstance(value, (int, float, bool)) else str(value)
    return clean


def _focus_list(focus) -> list[str]:
    if focus in (None, ""):
        return []
    if isinstance(focus, str):
        focus = [focus]
    if not isinstance(focus, list):
        raise errors.BadInput("focus 必须是字符串或列表", detail={"field": "focus"})
    if len(focus) > 20:
        raise errors.BadInput("focus 最多 20 项", detail={"count": len(focus)})
    return sorted({str(item).strip().lower() for item in focus if str(item).strip()})


def batch_identity(definitions, input_version: str | None, lineage_id: str,
                   params: dict | None, focus: list[str]) -> dict:
    """只算身份，不落盘：定义摘要、输入版本、批次键与 run_id。"""
    normalized = graph.normalize_definitions(definitions)
    version = (str(input_version or "").strip()
               or normalized["version"]
               or "v0")
    # 输入版本是本批次的事实：每行定义都用它，避免别名/版本混用导致同名分组落空
    for row in normalized["definitions"]:
        row["version"] = version
    canonical = graph.canonical_payload(normalized["definitions"])
    digest = _json_digest(canonical)
    lineage = (str(lineage_id or "").strip() or "default")
    clean_params = _normalize_params(params)
    key = manifest_mod.batch_key(
        {"definitions": digest},
        {"lineage_id": lineage, "input_version": version,
         "focus": list(focus), "params": clean_params},
        config.RULESET_VERSION,
    )
    return {
        "normalized": normalized,
        "canonical": canonical,
        "digest": digest,
        "input_version": version,
        "lineage_id": lineage,
        "params": clean_params,
        "focus": focus,
        "batch_key": key,
        "run_id": "mv-" + key[:12],
    }


def _input_names(identity: dict) -> dict:
    return {
        "definitions": f"definitions@{identity['input_version']}.json",
        "lineage": identity["lineage_id"],
    }


def _stale_guard(base_version_seq, current_seq: int) -> None:
    if base_version_seq is None:
        return
    try:
        base = int(base_version_seq)
    except (TypeError, ValueError):
        raise errors.BadInput("base_version_seq 必须是整数",
                              detail={"value": repr(base_version_seq)})
    if base < 0:
        raise errors.BadInput("base_version_seq 不能为负", detail={"value": base})
    if base != current_seq:
        raise errors.StaleVersion(
            f"基线版本 {base} 已落后于当前指标版本 {current_seq}",
            detail={"base_version_seq": base, "current_version_seq": current_seq,
                    "reason": "VERSION_STALE"},
        )


def _lineage_guard(identity: dict) -> None:
    """新提交的输入版本守卫：落后于当前版本，或同版本换了内容，都拒绝。

    同输入的重试不经过这里 —— 命中批次键直接复用，不新增结果。
    """
    newest = store.latest_metric_run(identity["lineage_id"])
    if newest is None:
        return
    order = compare_versions(identity["input_version"], newest["input_version"])
    if order < 0:
        raise errors.StaleVersion(
            f"输入版本 {identity['input_version']} 落后于当前 {newest['input_version']}",
            detail={"input_version": identity["input_version"],
                    "current_input_version": newest["input_version"],
                    "run_id": newest["run_id"], "reason": "METRIC_STALE_SUBMIT"},
        )
    if order == 0:
        same_version = [
            item for item in store.list_metric_runs(
                limit=1000, lineage_id=identity["lineage_id"])
            if compare_versions(item["input_version"], identity["input_version"]) == 0
        ]
        if same_version:
            raise errors.Conflict(
                f"输入版本 {identity['input_version']} 已绑定不同的定义内容",
                detail={"input_version": identity["input_version"],
                        "existing_run_id": same_version[0]["run_id"],
                        "reason": "INPUT_VERSION_CONFLICT"},
            )


def _new_run(identity: dict, parent_run_id: str | None) -> dict:
    now = config.now_utc().isoformat()
    return {
        "run_id": identity["run_id"],
        "batch_key": identity["batch_key"],
        "lineage_id": identity["lineage_id"],
        "parent_run_id": parent_run_id,
        "version_seq": store.next_metric_version_seq(),
        "input_sha256": identity["digest"],
        "input_version": identity["input_version"],
        "digests": {"definitions": identity["digest"]},
        "input_names": _input_names(identity),
        "params": identity["params"],
        "status": config.VERDICT_PENDING,
        # 定义在绑定时就随检查点落盘：即使进程在第一个阶段前死掉，也能从同一输入续跑
        "progress": {
            "done": [],
            "data": {
                "definitions": identity["normalized"]["definitions"],
                "definitions_version": identity["normalized"]["version"],
            },
            "focus": identity["focus"],
        },
        "result": None,
        "created_at": now,
        "updated_at": now,
    }


def _checkpoint(run: dict, stage: str, data: dict) -> None:
    """阶段检查点：先算完再写，续跑最多重算当前阶段。"""
    progress = dict(run.get("progress") or {})
    done = list(progress.get("done") or [])
    if stage not in done:
        done.append(stage)
    merged = dict(progress.get("data") or {})
    merged.update(data)
    progress.update({"done": done, "data": merged})
    run["progress"] = progress
    run["updated_at"] = config.now_utc().isoformat()
    store.update_metric_progress(run["run_id"], config.VERDICT_PENDING, progress)


def _stage_done(run: dict, stage: str) -> bool:
    return stage in ((run.get("progress") or {}).get("done") or [])


def _stage_data(run: dict) -> dict:
    return dict((run.get("progress") or {}).get("data") or {})


BLOCK_CODES = ("METRIC_CONFLICT", "METRIC_BROKEN_DEP", "METRIC_CYCLE")


def _metric_rows(chosen: list[dict], conflicts: list[dict], graph_payload: dict,
                 changed: dict, chain_affected: set[str]) -> tuple[list[dict], list[dict]]:
    broken = {item["metric_key"] for item in graph_payload["broken_deps"]}
    cycles: set[str] = set(graph_payload["self_loop_keys"])
    for cycle in graph_payload["cycles"]:
        cycles.update(cycle["node_keys"])
    conflicted = {item["name"].lower() for item in conflicts}

    rows: list[dict] = []
    for item in chosen:
        key = item["name_key"]
        codes: list[str] = []
        if key in conflicted:
            codes.append("METRIC_CONFLICT")
        if key in broken:
            codes.append("METRIC_BROKEN_DEP")
        if key in cycles:
            codes.append("METRIC_CYCLE")
        if key in changed["changed"]:
            codes.append("METRIC_REVISION_CHANGED")
        if key in chain_affected and not (set(codes) & set(BLOCK_CODES)):
            codes.append("METRIC_CHAIN_AFFECTED")
        if set(codes) & set(BLOCK_CODES):
            status = "BLOCK"
        elif codes:
            status = "WARN"
        else:
            status = "PASS"
        rows.append({
            "name": item["name"],
            "name_key": key,
            "version": item["version"],
            "expression": item["expression"],
            "source": item["source"],
            "signature": item["signature"],
            "priority": item["priority"],
            "depends_on": list(item["depends_on"]),
            "status": status,
            "reasons": codes,
            "removed": False,
        })

    removed = [
        {"name": name, "name_key": name, "status": "WARN",
         "reasons": ["METRIC_REMOVED"], "removed": True,
         "version": "", "expression": "", "source": "", "signature": "",
         "priority": 0, "depends_on": []}
        for name in sorted(changed.get("removed") or [])
    ]
    return rows, removed


def _duplicates(definitions: list[dict], conflicts: list[dict]) -> list[dict]:
    """同名同版本里签名一致的重复登记：不算冲突，但要看得出有几份。"""
    conflicted = {
        (item["name"].lower(), item["version"])
        for item in conflicts
    }
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in definitions:
        groups.setdefault((row["name_key"], row["version"]), []).append(row)
    out: list[dict] = []
    for (name_key, version), rows in sorted(groups.items()):
        if len(rows) < 2 or (name_key, version) in conflicted:
            continue
        out.append({"name": rows[0]["name"], "name_key": name_key,
                    "version": version, "definition_count": len(rows)})
    return out


def _assemble(run: dict, identity: dict, stage_data: dict) -> dict:
    chosen = stage_data["chosen"]
    conflicts = stage_data["conflicts"]
    graph_payload = stage_data["graph"]
    changed = stage_data["changed"]
    parent = run.get("parent_run_id")

    focus = set(identity.get("focus") or [])
    anchors = {key: codes for key, codes in stage_data["anchors"].items()
               if key in graph_payload["nodes"]}
    for key in focus:
        if key in graph_payload["nodes"]:
            codes = set(anchors.get(key) or [])
            codes.add("METRIC_FOCUS_ANCHOR")
            if key in changed["changed"]:
                codes.add("METRIC_REVISION_CHANGED")
            anchors[key] = sorted(
                codes,
                key=lambda code: (graph.ANCHOR_RANK.get(code, 500), code),
            )
    chains = graph.build_chains(chosen, graph_payload, anchors)

    # 下游节点的状态要反过来算：先有链路，再标「因链路受影响」
    anchor_keys = {chain["anchor_key"] for chain in chains}
    affected_keys = {node["name_key"] for chain in chains for node in chain["nodes"]}
    metrics, removed = _metric_rows(chosen, conflicts, graph_payload, changed,
                                    affected_keys - anchor_keys)

    blocking = sum(1 for row in metrics if row["status"] == "BLOCK")
    warning = sum(1 for row in metrics if row["status"] == "WARN")
    counts = {
        "definitions": len(stage_data["definitions"]),
        "metrics_total": len(metrics) + len(removed),
        "pass": sum(1 for row in metrics if row["status"] == "PASS"),
        "warn": warning,
        "block": blocking,
        "conflicts": len(conflicts),
        "duplicates": len(stage_data["duplicates"]),
        "cycles": len(graph_payload["cycles"]),
        "self_loops": len(graph_payload["self_loops"]),
        "broken_deps": len(graph_payload["broken_deps"]),
        "anchors": len(anchors),
        "chains": len(chains),
        "affected": len(affected_keys),
        "added": len(changed["added"]),
        "removed": len(changed["removed"]),
        "changed": len(changed["changed"]),
    }
    overall = ("BLOCK" if blocking
               else "WARN" if (warning or anchors)
               else "PASS")

    metrics.sort(key=lambda row: row["name_key"])
    return {
        "lineage_id": identity["lineage_id"],
        "input_version": identity["input_version"],
        "input_sha256": identity["digest"],
        "definitions_version": stage_data["definitions_version"],
        "parent_run_id": parent,
        "metrics": metrics,
        "removed": removed,
        "duplicates": stage_data["duplicates"],
        "conflicts": conflicts,
        "cycles": graph_payload["cycles"],
        "self_loops": graph_payload["self_loops"],
        "broken_deps": graph_payload["broken_deps"],
        "anchors": anchors,
        "chains": chains,
        "changed": {
            "changed": sorted(changed["changed"]),
            "added": sorted(changed["added"]),
            "removed": sorted(changed["removed"]),
        },
        "fingerprints": graph.fingerprints(stage_data["definitions"]),
        "counts": counts,
        "overall": overall,
        "verdict_codes": list(config.VERDICT_CODES),
    }


def _advance(run: dict, identity: dict, parent_fingerprints: dict | None) -> dict:
    """按阶段推进；每个阶段完成即落检查点。异常时保持 pending，可续跑。"""
    if not _stage_done(run, "normalize"):
        # 定义在创建时已落盘；这里只补写 definitions_version，保证老检查点也能续跑
        data = _stage_data(run)
        _checkpoint(run, "normalize", {
            "definitions": data["definitions"],
            "definitions_version": data.get("definitions_version")
            or identity["normalized"]["version"],
        })

    if not _stage_done(run, "evaluate"):
        data = _stage_data(run)
        resolved = graph.evaluate(data["definitions"])
        duplicates = _duplicates(data["definitions"], resolved["conflicts"])
        _checkpoint(run, "evaluate", {"chosen": resolved["chosen"],
                                      "conflicts": resolved["conflicts"],
                                      "duplicates": duplicates})

    if not _stage_done(run, "graph"):
        data = _stage_data(run)
        built = graph.build_graph(data["chosen"])
        changed = graph.changed_metrics(data["definitions"], parent_fingerprints)
        _checkpoint(run, "graph", {"graph": built,
                                   "changed": {key: sorted(value)
                                               for key, value in changed.items()}})

    if not _stage_done(run, "chains"):
        data = _stage_data(run)
        anchors = graph.anchor_reasons(data["chosen"], data["graph"],
                                       data["conflicts"],
                                       set(data["changed"]["changed"]),
                                       set(identity.get("focus") or []))
        _checkpoint(run, "chains", {"anchors": anchors})

    if not _stage_done(run, "finalize"):
        data = _stage_data(run)
        result = _assemble(run, identity, data)
        run["result"] = result
        run["status"] = config.VERDICT_READY
        run["updated_at"] = config.now_utc().isoformat()
        progress = dict(run.get("progress") or {})
        done = list(progress.get("done") or [])
        if "finalize" not in done:
            done.append("finalize")
        progress["done"] = done
        run["progress"] = progress
        store.save_metric_run(run)
        store.append_audit(run["run_id"], "verdict_ready", "ok", "system", None,
                           f"裁决发布，整体 {result['overall']}")

    return run


def execute(definitions, input_version: str | None = None, lineage_id: str = "default",
            params: dict | None = None, base_version_seq=None,
            focus=None) -> dict:
    identity = batch_identity(definitions, input_version, lineage_id, params,
                              _focus_list(focus))

    _stale_guard(base_version_seq, store.current_metric_version_seq())

    existing = store.metric_run_by_key(identity["batch_key"])
    if existing is not None and existing["status"] == config.VERDICT_READY:
        reopened = dict(existing)
        reopened["reused"] = True
        reopened["resumed"] = False
        return reopened

    if existing is not None:
        # 同输入、同批次键，但上次写入没完成：从检查点续跑，run_id 与版本序号都不变
        run = existing
        run["resumed"] = True
        run["reused"] = False
        store.append_audit(run["run_id"], "verdict_resume", "ok", "system", None,
                           "从检查点续跑")
    else:
        _lineage_guard(identity)
        previous = _latest_ready(identity["lineage_id"])
        parent_id = previous["run_id"] if previous else None
        run = _new_run(identity, parent_id)
        store.save_metric_run(run)
        store.append_audit(run["run_id"], "verdict_submit", "ok", "system", None,
                           f"绑定输入版本 {identity['input_version']}")

    parent = store.metric_run_by_id(run["parent_run_id"]) if run.get("parent_run_id") else None
    parent_result = (parent or {}).get("result") if parent else None
    run = _advance(run, identity,
                   (parent_result or {}).get("fingerprints") if parent_result else None)
    run["reused"] = bool(run.get("reused"))
    run["resumed"] = bool(run.get("resumed"))
    return run


def _latest_ready(lineage_id: str) -> dict | None:
    """最近一条已发布裁决：修订对比只跟已落定的结论比，不跟半成品比。"""
    for run in store.list_metric_runs(limit=1000, lineage_id=lineage_id):
        if run["status"] == config.VERDICT_READY:
            return run
    return None


def summary_of(run: dict) -> dict:
    result = run.get("result") or {}
    counts = result.get("counts") or {}
    return {
        "run_id": run["run_id"],
        "version_seq": run["version_seq"],
        "lineage_id": run["lineage_id"],
        "parent_run_id": run.get("parent_run_id"),
        "input_version": run["input_version"],
        "input_sha256": run["input_sha256"],
        "status": run["status"],
        "overall": result.get("overall"),
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "metrics_total": counts.get("metrics_total", 0),
        "block": counts.get("block", 0),
        "warn": counts.get("warn", 0),
        "conflicts": counts.get("conflicts", 0),
        "cycles": counts.get("cycles", 0),
        "broken_deps": counts.get("broken_deps", 0),
        "chains": counts.get("chains", 0),
        "affected": counts.get("affected", 0),
    }


def listing(limit: int = 30, lineage_id: str | None = None) -> list[dict]:
    return [summary_of(run) for run in store.list_metric_runs(limit, lineage_id)]


def get_or_404(run_id: str | None = None) -> dict:
    run = store.metric_run_by_id(run_id) if run_id else store.latest_metric_run()
    if not run:
        raise errors.NotFound("还没有指标裁决，先提交一次 definitions")
    return run


def require_ready(run: dict) -> None:
    """未完成的裁决不能当审计凭据读；已中断的告诉调用方可以续跑。"""
    if run["status"] == config.VERDICT_READY:
        return
    done = list((run.get("progress") or {}).get("done") or [])
    remaining = [stage for stage in STAGES if stage not in done]
    raise errors.Pending(
        f"裁决 {run['run_id']} 尚未完成，已完成 {len(done)}/{len(STAGES)} 阶段",
        detail={"run_id": run["run_id"], "status": run["status"],
                "reason": "METRIC_VERDICT_PENDING", "done": done,
                "remaining": remaining, "resumable": True},
    )


def resume(run_id: str) -> dict:
    """显式续跑：同一 run_id，从检查点继续，不新增结果行。"""
    run = store.metric_run_by_id(run_id)
    if not run:
        raise errors.NotFound("没有这个裁决", detail={"run_id": run_id})
    if run["status"] == config.VERDICT_READY:
        run["reused"] = True
        run["resumed"] = False
        return run
    newer = store.superseding_run(run["batch_key"])
    if newer is not None:
        # 旧版本已被新修订顶掉：补跑旧裁决不会改变审计结论，只会让人误以为它仍有效
        raise errors.StaleVersion(
            f"裁决 {run_id} 已被更新的输入版本 {newer['input_version']} 取代",
            detail={"run_id": run_id, "superseded_by": newer["run_id"],
                    "input_version": newer["input_version"],
                    "reason": "METRIC_VERSION_SUPERSEDED"},
        )
    progress = run.get("progress") or {}
    focus = progress.get("focus") or []
    identity = batch_identity(
        _definitions_for_identity(run, progress),
        run["input_version"], run["lineage_id"], run.get("params") or {}, focus,
    )
    if identity["run_id"] != run["run_id"]:
        # 定义内容与当初绑定的摘要对不上：不能拿别的输入续跑旧裁决
        raise errors.Conflict(
            "检查点里的定义与输入摘要不一致，拒绝续跑",
            detail={"run_id": run_id, "reason": "CHECKPOINT_MISMATCH"},
        )
    parent = store.metric_run_by_id(run["parent_run_id"]) \
        if run.get("parent_run_id") else None
    parent_result = (parent or {}).get("result") if parent else None
    run["resumed"] = True
    run["reused"] = False
    store.append_audit(run["run_id"], "verdict_resume", "ok", "system", None,
                       "显式续跑")
    return _advance(run, identity,
                    (parent_result or {}).get("fingerprints") if parent_result else None)


def _definitions_for_identity(run: dict, progress: dict) -> list[dict]:
    """把检查点里的规范定义还原成提交形状。"""
    rows = (progress.get("data") or {}).get("definitions")
    if not rows:
        raise errors.Conflict(
            "裁决尚未写入定义检查点，无法续跑",
            detail={"run_id": run["run_id"], "reason": "CHECKPOINT_INCOMPLETE"},
        )
    return [
        {
            "name": row["name"],
            "version": row["version"],
            "expression": row["expression"],
            "priority": row["priority"],
            "source": row["source"],
            "depends_on": list(row["depends_on"]),
        }
        for row in rows
    ]


def load_sample(name: str = config.METRIC_SAMPLE) -> tuple[list[dict], str]:
    """从样例目录读指标定义；只接受 sample_data/ 内的文件。"""
    import json

    path = config.resolve_sample(name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise errors.BadInput("指标样例无法解析", detail={"reason": exc.msg})
    if not isinstance(payload, dict):
        raise errors.BadInput("指标样例顶层必须是对象", detail={"file": path.name})
    return payload.get("definitions") or [], str(payload.get("version") or "")


def ensure_seeded() -> dict | None:
    """启动时跑一遍样例；已有裁决则不重复，避免每次重启新增结果行。"""
    if store.count_metric_runs() > 0:
        return store.latest_metric_run()
    definitions, version = load_sample()
    run = execute(definitions, input_version=version,
                  lineage_id=config.METRIC_SAMPLE_LINEAGE)
    store.set_kv("metric_seeded", {"run_id": run["run_id"],
                                   "version_seq": run["version_seq"]})
    return run
