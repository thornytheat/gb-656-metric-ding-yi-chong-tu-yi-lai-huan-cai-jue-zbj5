"""M1 入口：规格单首页、采信接口、隔离区导出。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import errors, store, web
from app.modules.m1_intake import service, sheet

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    run = store.latest_run()
    rows = (run or {}).get("diff") or []
    ledger = (run or {}).get("ledger") or {}
    quarantine = (run or {}).get("quarantine") or []
    blocked = sum(1 for row in quarantine if row["blocking"])
    return web.page_or_404(
        request,
        "index.html",
        "index",
        run,
        rows=rows,
        counts=ledger.get("counts") or {},
        quarantine=quarantine,
        blocked=blocked,
        warnings=len(quarantine) - blocked,
    )


@router.post("/api/schemas/import")
def import_schemas(payload: dict | None = None):
    body = payload or {}
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        raise errors.BadInput("需要给出 files 列表", detail={"field": "files"})
    if len(files) > 6:
        raise errors.BadInput("一次最多采信 6 份", detail={"count": len(files)})
    snapshots = [service.ingest(str(name)) for name in files]
    return {
        "imported": [service.snapshot_summary(item) for item in snapshots],
        "diagnostics": [row for item in snapshots for row in item["diagnostics"]],
    }


@router.get("/api/schemas/quarantine")
def quarantine(schema_id: str | None = None):
    rows = service.quarantine_of(schema_id)
    blocked = sum(1 for row in rows if row["blocking"])
    return {"schema_id": schema_id or (store.latest_run() or {}).get("new_schema_id"),
            "total": len(rows), "blocked": blocked, "warnings": len(rows) - blocked,
            "rows": rows}


@router.get("/api/schemas/quarantine.csv")
def quarantine_csv(schema_id: str | None = None):
    rows = service.quarantine_of(schema_id)
    body = sheet.diagnostic_csv(rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="metricgraph-quarantine.csv"'},
    )
