"""兼容判定编排：拿到两个已归一版本，产出逐字段结论。"""

from __future__ import annotations

from app import reasons
from app.modules.m1_intake import service as intake
from app.modules.m2_types import service as type_service
from app.modules.m3_compat import judge


def diff_snapshots(old_snapshot: dict, new_snapshot: dict,
                   ref_counts: dict | None = None) -> dict:
    return judge.diff_pair(old_snapshot["fields"], new_snapshot["fields"], ref_counts)


def judge_files(rel_old: str, rel_new: str) -> dict:
    """独立入口：给两份文件就跑一次判定，不依赖已有批次。"""
    old_snapshot = type_service.prepare(intake.ingest(rel_old))
    new_snapshot = type_service.prepare(intake.ingest(rel_new))
    diff = diff_snapshots(old_snapshot, new_snapshot)
    return {
        "old": {
            "schema_id": old_snapshot["schema_id"],
            "version_label": old_snapshot["version_label"],
            "field_count": old_snapshot["field_count"],
        },
        "new": {
            "schema_id": new_snapshot["schema_id"],
            "version_label": new_snapshot["version_label"],
            "field_count": new_snapshot["field_count"],
        },
        "counts": diff["counts"],
        "buckets": diff["buckets"],
        "verdicts": diff["verdicts"],
        "rows": diff["rows"],
    }
