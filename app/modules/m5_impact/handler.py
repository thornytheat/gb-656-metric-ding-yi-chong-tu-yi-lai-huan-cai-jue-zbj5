"""M5 入口：影响面页与影响面接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import store, web

router = APIRouter()


@router.get("/impact", response_class=HTMLResponse)
def impact_page(request: Request):
    run = store.latest_run()
    payload = (run or {}).get("impact") or {}
    return web.page_or_404(
        request,
        "impact.html",
        "impact",
        run,
        graph=payload.get("impact") or {},
        cycles=payload.get("cycles") or {},
        one_sided=payload.get("one_sided") or [],
        broken=payload.get("broken") or [],
        summary=payload.get("summary") or {},
    )


@router.get("/api/impact")
def impact_api():
    run = store.latest_run()
    if not run:
        return {"run_id": None, "impact": {}, "summary": {}}
    payload = run.get("impact") or {}
    return {
        "run_id": run["run_id"],
        "impact": payload.get("impact") or {},
        "cycles": payload.get("cycles") or {},
        "one_sided": payload.get("one_sided") or [],
        "broken": payload.get("broken") or [],
        "summary": payload.get("summary") or {},
    }
