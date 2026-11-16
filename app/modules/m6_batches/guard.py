"""版本守卫：陈旧提交拒绝、生效区间重叠标记。"""

from __future__ import annotations

from app import errors
from app.modules.m6_batches import window as window_mod


def check_stale(base_version_seq, current_seq: int) -> None:
    """提交里带的基线版本落后于当前版本 → 拒绝，避免旧结果覆盖新结果。"""
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
            f"基线版本 {base} 已落后于当前版本 {current_seq}",
            detail={"base_version_seq": base, "current_version_seq": current_seq,
                    "reason": "VERSION_STALE"},
        )


def overlap_warnings(target: dict, existing: list[dict]) -> list[dict]:
    hits = window_mod.find_overlaps(target, existing)
    return [
        {
            "code": "VERSION_OVERLAP",
            "detail": f"{hit['run_id']} 已占用 {window_mod.describe(hit)}",
            "run_id": hit["run_id"],
        }
        for hit in hits
    ]
