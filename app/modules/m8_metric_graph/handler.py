"""M8 入口：指标依赖裁决的提交、续跑、链路视图与导出。

授权边界在这里统一收口：
- 未带审计凭据 → 匿名链路视图（指标名、公式、来源都不下发）；
- 自称 auditor 但凭据不对 → 403，不降级；
- 只有凭据正确才返回指标名与公式，并把这次查看写进审计日志。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import config, errors, store, web
from app.modules.m8_metric_graph import auth, export, service

router = APIRouter()

ALLOWED_KEYS = {
    "definitions", "input_version", "lineage_id", "params", "base_version_seq",
    "focus",
}


def _role(request: Request, token: str | None = None) -> dict:
    return auth.resolve_role(
        request.query_params.get("role"),
        token if token is not None else request.query_params.get("audit_token")
        or request.headers.get("X-Audit-Token"),
    )


def _log_view(run_id: str, action: str, role_info: dict) -> None:
    auth.audit_event(run_id, action, role_info["role"], "ok", role_info["token_fp"],
                     "view")


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, run_id: str | None = None,
                 lineage_id: str | None = None):
    """页面永远先给匿名链路；授权信息决定是否显示指标名与公式。

    页面核对的是「所选版本」：给 run_id 就看那一版，否则看该谱系最新一版。
    """
    try:
        role_info = _role(request)
    except errors.Forbidden as exc:
        auth.audit_event(None, "view_denied", config.AUDIT_ROLE, "denied", None,
                         "页面越权查看")
        return web.render(request, "metrics_denied.html", "metrics",
                          reason=exc.message, detail=exc.detail)

    if run_id:
        run = store.metric_run_by_id(run_id)
    elif lineage_id:
        run = store.latest_metric_run(lineage_id)
    else:
        run = store.latest_metric_run()
    ready = run is not None and run["status"] == config.VERDICT_READY
    result = (run or {}).get("result") or {}
    view_payload = auth.view(result, role_info["role"]) if (ready and result) else {}
    if run is not None:
        _log_view(run["run_id"], "view_page", role_info)
    runs = service.listing(lineage_id=run["lineage_id"] if run else lineage_id)
    return web.render(
        request,
        "metrics.html",
        "metrics",
        empty=run is None,
        run=run,
        ready=ready,
        role=role_info["role"],
        scope=auth.scope_of(role_info["role"]),
        token=(request.query_params.get("audit_token") or ""),
        view=view_payload,
        runs=runs,
        audit=store.list_audit(run["run_id"] if run else None)
        if role_info["role"] == config.AUDIT_ROLE else [],
        current_seq=store.current_metric_version_seq(),
    )


@router.post("/api/metrics/verdicts")
def submit_verdict(payload: dict | None = None):
    body = payload or {}
    unknown = set(body) - ALLOWED_KEYS
    if unknown:
        raise errors.BadInput("出现未知参数", detail={"keys": sorted(unknown)})
    if "definitions" not in body:
        raise errors.BadInput("需要 definitions 列表", detail={"field": "definitions"})
    run = service.execute(
        definitions=body.get("definitions"),
        input_version=body.get("input_version"),
        lineage_id=str(body.get("lineage_id") or "default"),
        params=body.get("params"),
        base_version_seq=body.get("base_version_seq"),
        focus=body.get("focus"),
    )
    summary = service.summary_of(run)
    summary["reused"] = bool(run.get("reused"))
    summary["resumed"] = bool(run.get("resumed"))
    summary["changed"] = (run.get("result") or {}).get("changed") or {}
    return summary


@router.post("/api/metrics/verdicts/{run_id}/resume")
def resume_verdict(run_id: str):
    run = service.resume(run_id)
    summary = service.summary_of(run)
    summary["reused"] = bool(run.get("reused"))
    summary["resumed"] = bool(run.get("resumed"))
    return summary


@router.post("/metrics/resume/{run_id}", response_class=HTMLResponse)
def resume_from_page(run_id: str, request: Request):
    """页面上的续跑按钮：续跑后回到同一版本，不跳走。"""
    try:
        service.resume(run_id)
    except errors.FieldmapError as exc:
        return web.page_or_404(request, "metrics.html", "metrics",
                               store.metric_run_by_id(run_id),
                               error=exc.message)
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/metrics?run_id={run_id}", status_code=303)


@router.get("/api/metrics/verdicts")
def list_verdicts(lineage_id: str | None = None, limit: int = 30):
    return {
        "current_version_seq": store.current_metric_version_seq(),
        "runs": service.listing(limit=max(1, min(int(limit or 30), 200)),
                                lineage_id=lineage_id),
    }


@router.get("/api/metrics/verdicts/{run_id}")
def get_verdict(run_id: str, request: Request):
    role_info = _role(request)
    run = service.get_or_404(run_id)
    service.require_ready(run)
    _log_view(run["run_id"], "view_json", role_info)
    return {"run_id": run["run_id"], "summary": service.summary_of(run),
            "verdict": auth.view(run.get("result") or {}, role_info["role"])}


@router.get("/api/metrics/verdicts/{run_id}/audit")
def verdict_audit(run_id: str, request: Request):
    """审计日志只有授权审计能看。"""
    role_info = _role(request)
    if role_info["role"] != config.AUDIT_ROLE:
        auth.audit_event(run_id, "view_audit", role_info["role"], "denied",
                         role_info["token_fp"], "越权查看审计日志")
        raise errors.Forbidden("审计日志需要审计凭据",
                               detail={"reason": "AUDIT_DENIED"})
    run = service.get_or_404(run_id)
    auth.audit_event(run_id, "view_audit", role_info["role"], "ok",
                     role_info["token_fp"], "查看审计日志")
    return {"run_id": run["run_id"], "events": store.list_audit(run_id)}


def _export_run(run_id: str | None) -> dict:
    run = service.get_or_404(run_id)
    service.require_ready(run)
    return run


def _response(body: str, media_type: str, filename: str) -> Response:
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/export/metrics.csv")
def export_metrics(request: Request, run_id: str | None = None):
    """授权导出才含指标名与公式；未授权导出只给匿名链路。"""
    role_info = _role(request)
    run = _export_run(run_id)
    _log_view(run["run_id"], "export_metrics", role_info)
    if role_info["role"] != config.AUDIT_ROLE:
        return _response(export.chains_csv_anon(run["result"]),
                         "text/csv; charset=utf-8", "metricgraph-chains-anon.csv")
    return _response(export.metrics_csv(run["result"]),
                     "text/csv; charset=utf-8", "metricgraph-metrics.csv")


@router.get("/api/export/chains.csv")
def export_chains(request: Request, run_id: str | None = None):
    role_info = _role(request)
    run = _export_run(run_id)
    _log_view(run["run_id"], "export_chains", role_info)
    if role_info["role"] != config.AUDIT_ROLE:
        return _response(export.chains_csv_anon(run["result"]),
                         "text/csv; charset=utf-8", "metricgraph-chains-anon.csv")
    return _response(export.chains_csv(run["result"]),
                     "text/csv; charset=utf-8", "metricgraph-chains.csv")


@router.get("/api/export/metrics.md")
def export_markdown(request: Request, run_id: str | None = None):
    role_info = _role(request)
    if role_info["role"] != config.AUDIT_ROLE:
        auth.audit_event(run_id, "export_markdown", role_info["role"], "denied",
                         role_info["token_fp"], "越权导出裁决摘要")
        raise errors.Forbidden("裁决摘要含指标名与公式，需要审计凭据",
                               detail={"reason": "AUDIT_DENIED"})
    run = _export_run(run_id)
    _log_view(run["run_id"], "export_markdown", role_info)
    return _response(export.spec_markdown(run), "text/markdown; charset=utf-8",
                     "metricgraph-metrics.md")


@router.get("/api/export/metrics.conflicts.csv")
def export_conflicts(request: Request, run_id: str | None = None):
    """冲突清单也含指标名，按授权收口。"""
    role_info = _role(request)
    if role_info["role"] != config.AUDIT_ROLE:
        auth.audit_event(run_id, "export_conflicts", role_info["role"], "denied",
                         role_info["token_fp"], "越权导出冲突清单")
        raise errors.Forbidden("冲突清单含指标名，需要审计凭据",
                               detail={"reason": "AUDIT_DENIED"})
    run = _export_run(run_id)
    _log_view(run["run_id"], "export_conflicts", role_info)
    return _response(export.conflicts_csv(run["result"]), "text/csv; charset=utf-8",
                     "metricgraph-conflicts.csv")


@router.get("/api/export/metrics-manifest.json")
def export_manifest(run_id: str | None = None):
    """运行清单不含指标名与公式，不需要授权，但必须对应所选版本。"""
    run = _export_run(run_id)
    return Response(content=export.manifest_json(run),
                    media_type="application/json")
