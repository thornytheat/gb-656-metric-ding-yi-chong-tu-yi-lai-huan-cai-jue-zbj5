"""M6 入口：跑批次、版本历史页、对账接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import errors, store, web
from app.modules.m6_batches import history, service

router = APIRouter()

ALLOWED_KEYS = {
    "old_file", "new_file", "refs_file", "base_file", "base_version_seq",
    "effective_from", "effective_to", "params",
}


@router.get("/batches", response_class=HTMLResponse)
def batches_page(request: Request):
    run = store.latest_run()
    runs = history.listing()
    return web.page_or_404(
        request,
        "batches.html",
        "batches",
        run,
        runs=runs,
        ledger=(run or {}).get("ledger") or {},
        reconcile=history.reconcile(run) if run else {},
        current_seq=store.current_version_seq(),
    )


@router.post("/api/runs")
def create_run(payload: dict | None = None):
    body = payload or {}
    unknown = set(body) - ALLOWED_KEYS
    if unknown:
        raise errors.BadInput("出现未知参数", detail={"keys": sorted(unknown)})
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise errors.BadInput("params 必须是对象", detail={"field": "params"})
    if len(params) > 12:
        raise errors.BadInput("params 最多 12 项", detail={"count": len(params)})

    run = service.execute(
        old_file=str(body.get("old_file") or "schema_v2026_06.sql"),
        new_file=str(body.get("new_file") or "schema_v2026_09.sql"),
        refs_file=str(body.get("refs_file") or "refs_downstream.json"),
        base_file=str(body.get("base_file") or "schema_v2026_03.sql"),
        base_version_seq=body.get("base_version_seq"),
        effective_from=body.get("effective_from"),
        effective_to=body.get("effective_to"),
        params=params,
    )
    return {
        "run_id": run["run_id"],
        "reused": bool(run.get("reused")),
        "version_seq": run["version_seq"],
        "counts": (run.get("ledger") or {}).get("counts") or {},
        "balanced": (run.get("ledger") or {}).get("balanced") or {},
        "warnings": run.get("warnings") or [],
        "window": run.get("window") or {},
    }


@router.get("/api/batches")
def batches_api():
    return {
        "current_version_seq": store.current_version_seq(),
        "runs": history.listing(),
    }


@router.get("/api/reconcile")
def reconcile_api():
    run = store.latest_run()
    if not run:
        raise errors.NotFound("还没有批次，先在版本历史页跑一次")
    return history.reconcile(run)
