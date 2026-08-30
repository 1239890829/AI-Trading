"""统一错误契约（技术评审 B1）。

目标：前端与调用方可以对**所有**错误响应依赖同一形态 `{detail, code}`——
- `detail`：人可读原因（沿用旧契约，前端 api.ts 已按此读取，零破坏）
- `code`：机器可读分支键（`upstream_failed` / `validation_error` / `internal_error` /
  `http_<status>`），前端据此做重试/降级分类，不再靠猜 status

四层 handler（由 `register_error_handlers` 统一挂载）：
1. AppError        业务异常基类，显式 code
2. HTTPException   既有 23 处路由抛出，保持 detail 兼容并补 code
3. RequestValidationError 入参校验失败，格式化明细
4. Exception       兜底 500：log.exception + 统一体（绝不把堆栈漏给客户端）
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常基类。路由在需要自定义 code 时抛这个，而不是裸 HTTPException。"""

    status_code: int = 500
    code: str = "app_error"

    def __init__(self, detail: str, *, code: str | None = None, status_code: int | None = None):
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail)


class UpstreamError(AppError):
    """上游数据源失败（provider 链全挂等）——前端可据此展示"数据源降级"。"""

    status_code = 502
    code = "upstream_failed"


def error_body(detail, code) -> dict:
    return {"detail": detail, "code": code}


def _fmt_validation(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
        parts.append(f"{loc}: {err.get('msg')}")
    return "；".join(parts) or "入参校验失败"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.detail, exc.code))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        headers = getattr(exc, "headers", None) or {}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.detail, f"http_{exc.status_code}"),
            headers=headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=error_body(_fmt_validation(exc), "validation_error"))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=error_body("服务器内部错误", "internal_error"))
