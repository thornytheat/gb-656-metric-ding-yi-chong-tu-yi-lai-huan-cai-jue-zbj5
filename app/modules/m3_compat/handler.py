"""M3 入口：两版本兼容判定。"""

from __future__ import annotations

from fastapi import APIRouter

from app import config, errors
from app.modules.m3_compat import service
from app.modules.m3_compat.metric_rules import resolve

router = APIRouter()


@router.post("/api/metrics/conflicts")
def metric_conflicts(payload: dict):
    return resolve(payload.get("definitions") or [], payload.get("aliases") or {})


@router.post("/api/compat")
def compat(payload: dict | None = None):
    body = payload or {}
    old_file = str(body.get("old_file") or config.DEFAULT_OLD).strip()
    new_file = str(body.get("new_file") or config.DEFAULT_NEW).strip()
    field_id = body.get("field_id")
    verdict = service.judge_files(old_file, new_file)
    rows = verdict["rows"]
    if field_id not in (None, ""):
        wanted = str(field_id)
        rows = [row for row in rows if row["field_id"] == wanted]
        if not rows:
            raise errors.NotFound("没有这个字段身份", detail={"field_id": wanted})
        verdict["rows"] = rows
    return verdict
