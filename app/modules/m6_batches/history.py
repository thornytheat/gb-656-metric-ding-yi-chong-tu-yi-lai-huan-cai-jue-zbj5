"""批次历史：列表与对账摘要，只读已存结果。"""

from __future__ import annotations

from app import config, store


def summary_of(run: dict) -> dict:
    ledger = run.get("ledger") or {}
    counts = ledger.get("counts") or {}
    balanced = ledger.get("balanced") or {}
    return {
        "run_id": run["run_id"],
        "version_seq": run["version_seq"],
        "version_label_old": run.get("version_label_old"),
        "version_label_new": run.get("version_label_new"),
        "created_at": run.get("created_at"),
        "effective_from": run.get("effective_from"),
        "effective_to": run.get("effective_to"),
        "old_total": counts.get("old_total", 0),
        "new_total": counts.get("new_total", 0),
        "breaking": counts.get("breaking", 0),
        "refs_total": counts.get("refs_total", 0),
        "refs_broken": counts.get("refs_broken", 0),
        "consumers": counts.get("consumers", 0),
        "balanced": bool(balanced.get("all")),
        "reused": bool(run.get("reused")),
        "warnings": len(run.get("warnings") or []),
    }


def listing(limit: int = 30) -> list[dict]:
    return store.list_runs(limit)


def reconcile(run: dict) -> dict:
    ledger = run.get("ledger") or {}
    fields = ledger.get("fields") or {}
    refs = ledger.get("refs") or {}
    return {
        "run_id": run["run_id"],
        "fields": {
            "old_total": fields.get("old_total", 0),
            "new_total": fields.get("new_total", 0),
            "buckets": fields.get("buckets") or {},
            "old_side": fields.get("old_side", 0),
            "new_side": fields.get("new_side", 0),
            "old_gap": fields.get("old_gap", 0),
            "new_gap": fields.get("new_gap", 0),
        },
        "refs": refs,
        "balanced": ledger.get("balanced") or {},
        "warnings": run.get("warnings") or [],
        "window": {
            "from": run.get("effective_from"),
            "to": run.get("effective_to"),
            "semantics": config.WINDOW_SEMANTICS,
        },
    }
