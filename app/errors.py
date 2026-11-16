"""领域异常到 HTTP 的映射。

handler 只负责把坏输入抛成这里的类型，页面与 JSON 都拿到一致的结构。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FieldmapError(Exception):
    status = 400
    code = "BAD_INPUT"
    label = "输入不合法"

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_body(self) -> dict:
        return {"error": self.code, "label": self.label, "message": self.message,
                "detail": self.detail}


class BadInput(FieldmapError):
    status = 400
    code = "BAD_INPUT"
    label = "输入不合法"


class NotFound(FieldmapError):
    status = 404
    code = "NOT_FOUND"
    label = "没有数据"


class StaleVersion(FieldmapError):
    status = 409
    code = "VERSION_STALE"
    label = "提交已过期"


class Conflict(FieldmapError):
    status = 409
    code = "CONFLICT"
    label = "记录冲突"


def install(app: FastAPI) -> None:
    @app.exception_handler(FieldmapError)
    async def _handle_domain(_: Request, exc: FieldmapError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.as_body())


def body_of(exc: FieldmapError) -> dict:
    return exc.as_body()
