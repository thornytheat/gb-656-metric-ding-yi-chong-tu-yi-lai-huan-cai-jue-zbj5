"""M7 入口：迁移单页与导出物。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import store, web
from app.modules.m7_migration import service

router = APIRouter()

CSV_HEADERS = {"Content-Disposition": 'attachment; filename="metricgraph.csv"'}


def _csv(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/spec", response_class=HTMLResponse)
def spec_page(request: Request):
    run = store.latest_run()
    return web.page_or_404(
        request,
        "spec.html",
        "spec",
        run,
        clauses=service.clauses(run) if run else [],
        ledger=(run or {}).get("ledger") or {},
        aliases=((run or {}).get("mapping") or {}).get("aliases") or [],
        warnings=(run or {}).get("warnings") or [],
    )


@router.get("/api/export/catalog.csv")
def export_catalog():
    run = service.latest_or_404()
    return _csv(service.fields_csv_text(run), "metricgraph-catalog.csv")
