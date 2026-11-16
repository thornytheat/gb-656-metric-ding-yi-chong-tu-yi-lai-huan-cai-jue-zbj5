"""应用装配：路由注册、静态资源、启动时把样例跑一遍。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config, errors, store
from app.modules.m1_intake import handler as m1_handler
from app.modules.m2_types import handler as m2_handler
from app.modules.m3_compat import handler as m3_handler
from app.modules.m4_mapping import handler as m4_handler
from app.modules.m5_impact import handler as m5_handler
from app.modules.m6_batches import handler as m6_handler
from app.modules.m6_batches import service as batch_service
from app.modules.m7_migration import handler as m7_handler
from app.modules.m8_metric_graph import handler as m8_handler
from app.modules.m8_metric_graph import service as metric_service

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    batch_service.ensure_seeded()
    metric_service.ensure_seeded()
    yield


app = FastAPI(title=config.APP_TITLE, docs_url="/docs", redoc_url=None,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

for module_handler in (m1_handler, m2_handler, m3_handler, m4_handler,
                       m5_handler, m6_handler, m7_handler, m8_handler):
    app.include_router(module_handler.router)

errors.install(app)


@app.get("/api/health")
def health() -> dict:
    run = store.latest_run()
    verdict = store.latest_metric_run()
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "ruleset": config.RULESET_VERSION,
        "db": str(store.db_path()),
        "runs": store.count_runs(),
        "run_id": (run or {}).get("run_id"),
        "window_semantics": config.WINDOW_SEMANTICS,
        "metric_verdicts": store.count_metric_runs(),
        "metric_run_id": (verdict or {}).get("run_id"),
        "metric_input_version": (verdict or {}).get("input_version"),
    }


@app.get("/api/reasons")
def reasons_api() -> dict:
    from app import reasons

    return {
        "codes": [
            {"code": code, "label": reasons.label(code), "severity": reasons.severity(code)}
            for code in reasons.known_codes()
        ]
    }
