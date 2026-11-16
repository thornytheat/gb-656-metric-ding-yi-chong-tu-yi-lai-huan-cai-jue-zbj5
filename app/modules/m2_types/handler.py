"""M2 入口：类型格页与类型比较接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import errors, store, web
from app.modules.m2_types import service

router = APIRouter()


@router.get("/types", response_class=HTMLResponse)
def types_page(request: Request):
    run = store.latest_run()
    rows = (run or {}).get("diff") or []
    return web.page_or_404(
        request,
        "types.html",
        "types",
        run,
        grid=service.grid(),
        rows=rows,
        changes=(run or {}).get("changes") or {},
        verdicts=(run or {}).get("verdicts") or {},
        type_stats=(run or {}).get("type_stats_new") or {},
    )


@router.get("/api/types")
def types_api():
    return {
        "grid": service.grid(),
        "aliases": service.aliases(),
        "super_families": ["numeric", "text", "temporal", "bool", "unknown"],
    }


@router.post("/api/types/compare")
def types_compare(payload: dict | None = None):
    body = payload or {}
    old_raw = str(body.get("old") or "").strip()
    new_raw = str(body.get("new") or "").strip()
    if not old_raw or not new_raw:
        raise errors.BadInput("需要 old 与 new 两个类型", detail={"field": "old/new"})
    if len(old_raw) > 64 or len(new_raw) > 64:
        raise errors.BadInput("类型写法过长", detail={"max": 64})
    return service.compare_types(old_raw, new_raw)
