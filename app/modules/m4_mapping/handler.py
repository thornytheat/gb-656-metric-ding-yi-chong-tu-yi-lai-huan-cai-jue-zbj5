"""M4 入口：字段映射页与映射接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import store, web

router = APIRouter()


@router.get("/mapping", response_class=HTMLResponse)
def mapping_page(request: Request):
    run = store.latest_run()
    payload = (run or {}).get("mapping") or {}
    return web.page_or_404(
        request,
        "mapping.html",
        "mapping",
        run,
        mapping=payload,
        stats=payload.get("stats") or {},
        aliases=payload.get("aliases") or [],
    )


@router.get("/api/mapping")
def mapping_api():
    run = store.latest_run()
    if not run:
        return {"run_id": None, "rows": [], "stats": {}, "aliases": []}
    payload = run.get("mapping") or {}
    return {
        "run_id": run["run_id"],
        "stats": payload.get("stats") or {},
        "rows": payload.get("rows") or [],
        "aliases": payload.get("aliases") or [],
        "conflicts": payload.get("conflicts") or [],
        "added": payload.get("added") or [],
        "deleted": payload.get("deleted") or [],
    }
