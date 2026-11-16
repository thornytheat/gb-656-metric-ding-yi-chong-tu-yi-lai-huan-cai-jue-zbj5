"""类型格：判定从旧类型到新类型是放宽、收窄还是换族。

放宽 = 新类型能无损容纳旧类型的全部取值；收窄 = 会丢范围、丢刻度或丢精度。
"""

from __future__ import annotations

from app.modules.m2_types import coerce, range as value_range

CHANGE_REASON = {
    "same": "TYPE_SAME",
    "widen": "TYPE_WIDEN",
    "narrow": "TYPE_NARROW",
    "cross_family": "TYPE_CROSS_FAMILY",
    "unknown": "TYPE_UNKNOWN",
}


def compare(old_spec: dict, new_spec: dict) -> dict:
    """两两比较，返回 change/detail/reason/evidence 四件套。"""
    if not old_spec.get("known") or not new_spec.get("known"):
        return {
            "change": "unknown",
            "detail": None,
            "reason": "TYPE_UNKNOWN",
            "evidence": {
                "old_raw": old_spec.get("raw"),
                "new_raw": new_spec.get("raw"),
                "note": "类型不在类型格内，不能证明兼容",
            },
        }

    if coerce.type_text(old_spec) == coerce.type_text(new_spec):
        return {"change": "same", "detail": None, "reason": "TYPE_SAME",
                "evidence": {"type": coerce.type_text(old_spec)}}

    if old_spec["super"] != new_spec["super"]:
        return {
            "change": "cross_family",
            "detail": "super_changed",
            "reason": "TYPE_CROSS_FAMILY",
            "evidence": {"old_super": old_spec["super"], "new_super": new_spec["super"]},
        }

    if old_spec["super"] == "numeric":
        verdict = value_range.capacity(old_spec, new_spec)
        change = "widen" if verdict["fits"] else "narrow"
        return {"change": change, "detail": verdict["detail"],
                "reason": CHANGE_REASON[change], "evidence": verdict["evidence"]}

    if old_spec["super"] == "text":
        return _compare_text(old_spec, new_spec)

    if old_spec["super"] == "temporal":
        return _compare_temporal(old_spec, new_spec)

    return {"change": "same", "detail": None, "reason": "TYPE_SAME",
            "evidence": {"type": coerce.type_text(old_spec)}}


def _compare_text(old_spec: dict, new_spec: dict) -> dict:
    old_len = value_range.text_length(old_spec)
    new_len = value_range.text_length(new_spec)
    evidence = {
        "old_length": "不限" if old_len is None else str(old_len),
        "new_length": "不限" if new_len is None else str(new_len),
    }
    if old_len == new_len:
        return {"change": "same", "detail": None, "reason": "TYPE_SAME", "evidence": evidence}
    if new_len is None or (old_len is not None and new_len >= old_len):
        return {"change": "widen", "detail": None, "reason": "TYPE_WIDEN", "evidence": evidence}
    return {"change": "narrow", "detail": "length_narrow", "reason": "TYPE_NARROW",
            "evidence": evidence}


def _compare_temporal(old_spec: dict, new_spec: dict) -> dict:
    old_rank = value_range.temporal_rank(old_spec["base"])
    new_rank = value_range.temporal_rank(new_spec["base"])
    evidence = {"old_rank": str(old_rank), "new_rank": str(new_rank)}
    if new_rank > old_rank:
        return {"change": "widen", "detail": None, "reason": "TYPE_WIDEN", "evidence": evidence}
    return {"change": "narrow", "detail": "temporal_narrow", "reason": "TYPE_NARROW",
            "evidence": evidence}


def is_widening(change: str) -> bool:
    return change == "widen"


def is_breaking(change: str) -> bool:
    return change in ("narrow", "cross_family", "unknown")
