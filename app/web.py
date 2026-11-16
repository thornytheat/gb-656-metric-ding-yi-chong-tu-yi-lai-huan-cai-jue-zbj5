"""模板渲染与页面公共上下文。

页面只放标题、字段状态、数值单位与失败原因；定义写进 docs/DATA-DICTIONARY.md。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config, reasons, store

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

NAV = [
    {"slug": "index", "href": "/", "label": "规格单"},
    {"slug": "types", "href": "/types", "label": "类型与兼容"},
    {"slug": "mapping", "href": "/mapping", "label": "字段映射"},
    {"slug": "impact", "href": "/impact", "label": "影响面"},
    {"slug": "batches", "href": "/batches", "label": "版本历史"},
    {"slug": "spec", "href": "/spec", "label": "迁移单"},
]


def _ts(value: str | None) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return config.to_local_text(moment)


def _label(code: str | None) -> str:
    if not code:
        return "—"
    return reasons.label(code)


def _sev(code: str | None) -> str:
    if not code:
        return "none"
    return reasons.severity(code)


def _narrow(detail: str | None) -> str:
    return reasons.narrow_label(detail)


def _day(value: str | None) -> str:
    """只留日期，表格窄一点。"""
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return config.to_local_text(moment)[:10]


VIA_LABELS = {"field": "字段", "broken": "断链", "alias": "别名"}


def _via(value: str | None) -> str:
    if not value:
        return "—"
    return VIA_LABELS.get(value, value)


templates.env.filters["ts"] = _ts
templates.env.filters["reason"] = _label
templates.env.filters["sev"] = _sev
templates.env.filters["narrow"] = _narrow
templates.env.filters["day"] = _day
templates.env.filters["via"] = _via


def shell_context(active: str) -> dict:
    run = store.latest_run()
    created = (run or {}).get("created_at")
    return {
        "active": active,
        "nav": NAV,
        "run_id": (run or {}).get("run_id") or "—",
        "version_label": (run or {}).get("version_label_new") or "—",
        "version_old": (run or {}).get("version_label_old") or "—",
        "ruleset": config.RULESET_VERSION,
        "generated_at": _ts(created),
        "tz_label": "+08:00",
        "window_semantics": config.WINDOW_SEMANTICS,
        "run": run,
    }


def render(request: Request, template: str, active: str, **extra) -> HTMLResponse:
    context = shell_context(active)
    context["request"] = request
    context.update(extra)
    return templates.TemplateResponse(request, template, context)


def page_or_404(request: Request, template: str, active: str, run: dict | None,
                **extra) -> HTMLResponse:
    """没有批次时给出空态，页面仍返回 200。"""
    context = shell_context(active)
    context["request"] = request
    context["empty"] = run is None
    context.update(extra)
    return templates.TemplateResponse(request, template, context)
