"""指标裁决的导出：页面、CSV、Markdown、运行清单都从同一份已发布结果生成。

导出必须与所选版本一致：调用方给 run_id 就导出那一版；不给就导出该谱系最新已发布版。
未发布的裁决不能导出，避免拿半成品当审计凭据。
"""

from __future__ import annotations

import csv
import io
import json

CHAIN_HEADER = ["chain_anchor", "chain_reasons", "depth", "role", "node", "via",
                "node_status"]
METRIC_HEADER = ["metric", "version", "expression", "depends_on", "status",
                 "reasons", "signature", "source"]
CONFLICT_HEADER = ["metric", "version", "reason", "definition_count", "signatures"]
ANON_HEADER = ["chain_anchor", "chain_reasons", "depth", "role", "node_anon", "via_anon",
               "node_status"]


def _buffer() -> tuple[io.StringIO, "csv.writer"]:
    buffer = io.StringIO()
    return buffer, csv.writer(buffer, lineterminator="\n")


def _node_status(result: dict) -> dict[str, str]:
    return {row["name_key"]: row["status"] for row in result.get("metrics") or []}


def chains_csv(result: dict) -> str:
    """链路账：与页面链路表逐行对应（锚点、原因、深度、角色、节点、入口）。"""
    buffer, writer = _buffer()
    writer.writerow(CHAIN_HEADER)
    status = _node_status(result)
    for chain in result.get("chains") or []:
        for node in chain["nodes"]:
            writer.writerow([
                chain["anchor"],
                ",".join(chain.get("reasons") or []),
                node["depth"],
                node["role"],
                node["name"],
                node.get("via") or "",
                status.get(node["name_key"], ""),
            ])
    return buffer.getvalue()


def metrics_csv(result: dict) -> str:
    buffer, writer = _buffer()
    writer.writerow(METRIC_HEADER)
    for row in result.get("metrics") or []:
        writer.writerow([
            row["name"], row["version"], row["expression"],
            ",".join(row.get("depends_on") or []), row["status"],
            ",".join(row.get("reasons") or []), row["signature"], row["source"],
        ])
    return buffer.getvalue()


def conflicts_csv(result: dict) -> str:
    buffer, writer = _buffer()
    writer.writerow(CONFLICT_HEADER)
    for item in result.get("conflicts") or []:
        writer.writerow([
            item["name"], item["version"], item["reason"],
            item["definition_count"], ",".join(item.get("signatures") or []),
        ])
    return buffer.getvalue()


def chains_csv_anon(result: dict) -> str:
    """未授权导出：只给匿名链路，没有指标名与公式。"""
    from app.modules.m8_metric_graph import auth

    buffer, writer = _buffer()
    writer.writerow(ANON_HEADER)
    status = _node_status(result)
    for chain in result.get("chains") or []:
        anchor = auth.anon_id(chain["anchor"])
        for node in chain["nodes"]:
            writer.writerow([
                anchor, ",".join(chain.get("reasons") or []), node["depth"],
                node["role"], auth.anon_id(node["name"]),
                auth.anon_id(node["via"]) if node.get("via") else "",
                status.get(node["name_key"], ""),
            ])
    return buffer.getvalue()


def manifest_json(run: dict) -> str:
    """运行清单与所选版本严格对应：摘要、状态、谱系、检查点阶段都写清。"""
    from app import config

    result = run.get("result") or {}
    payload = {
        "run_id": run["run_id"],
        "batch_key": run["batch_key"],
        "lineage_id": run["lineage_id"],
        "parent_run_id": run.get("parent_run_id"),
        "version_seq": run["version_seq"],
        "input_version": run["input_version"],
        "input_sha256": run["input_sha256"],
        "digests": run.get("digests") or {},
        "input_names": run.get("input_names") or {},
        "params": run.get("params") or {},
        "ruleset": config.RULESET_VERSION,
        "status": run["status"],
        "overall": result.get("overall"),
        "counts": result.get("counts") or {},
        "changed": result.get("changed") or {},
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "completed_stages": list((run.get("progress") or {}).get("done") or []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def spec_markdown(run: dict) -> str:
    """审计视图的迁移单式摘要：冲突、环、缺依赖、链路、结论。"""
    result = run.get("result") or {}
    counts = result.get("counts") or {}
    lines = [
        f"# 指标依赖裁决 {run['run_id']}",
        "",
        f"- 输入版本：{run['input_version']}",
        f"- 谱系：{run['lineage_id']}　上一版：{run.get('parent_run_id') or '—'}",
        f"- 定义摘要：{run['input_sha256']}",
        f"- 批次序号：{run['version_seq']}　状态：{run['status']}",
        f"- 整体结论：{result.get('overall')}",
        "",
        "## 1 计数",
        "",
        "| 项 | 数 |",
        "|---|---|",
    ]
    for key in ("definitions", "metrics_total", "pass", "warn", "block",
                "conflicts", "duplicates", "cycles", "self_loops",
                "broken_deps", "chains", "affected", "added", "removed", "changed"):
        lines.append(f"| {key} | {counts.get(key, 0)} |")
    lines.extend(["", "## 2 冲突", ""])
    if result.get("conflicts"):
        lines.extend(["| 指标 | 版本 | 原因 | 定义数 |",
                      "|---|---|---|---|"])
        for item in result["conflicts"]:
            lines.append(f"| {item['name']} | {item['version']} | "
                         f"{item['reason']} | {item['definition_count']} |")
    else:
        lines.append("无冲突。")
    lines.extend(["", "## 3 依赖环与缺依赖", ""])
    if result.get("cycles") or result.get("self_loops"):
        for cycle in result.get("cycles") or []:
            lines.append(f"- 环：{' → '.join(cycle['nodes'])}")
        for node in result.get("self_loops") or []:
            lines.append(f"- 自引用：{node}")
    else:
        lines.append("- 无环。")
    if result.get("broken_deps"):
        for item in result["broken_deps"]:
            lines.append(f"- 缺依赖：{item['metric']} → {item['dependency']}")
    else:
        lines.append("- 无缺依赖。")
    lines.extend(["", "## 4 受影响链路", ""])
    for chain in result.get("chains") or []:
        path = " → ".join(node["name"] for node in chain["nodes"])
        lines.append(f"- {path}（锚点原因：{', '.join(chain.get('reasons') or []) or '—'}，"
                     f"受影响 {chain['affected_count']}）")
    if not result.get("chains"):
        lines.append("- 没有链路。")
    lines.append("")
    return "\n".join(lines)
