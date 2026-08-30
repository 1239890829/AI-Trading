"""B1 统一错误契约：所有错误响应必须形态一致 {detail, code}。

用最小 app 挂同一 register_error_handlers 验证四层 handler——
不 import app.main（避免 lifespan 拉起 provider 链）。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.errors import AppError, UpstreamError, register_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/app-error")
    async def app_error():
        raise UpstreamError("涨停池数据源失败：ths HTTP 500")

    @app.get("/custom-code")
    async def custom():
        raise AppError("参数不合法", code="bad_symbol", status_code=400)

    @app.get("/http-exc")
    async def http_exc():
        raise HTTPException(status_code=404, detail="000001 无盘口数据")

    @app.get("/boom")
    async def boom():
        raise ValueError("意外炸了")

    @app.get("/needs-int")
    async def needs_int(n: int):
        return {"n": n}

    return app


def test_app_error_shape():
    c = TestClient(_app())
    r = c.get("/app-error")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "upstream_failed"
    assert "涨停池数据源失败" in body["detail"]


def test_app_error_custom_code():
    c = TestClient(_app())
    r = c.get("/custom-code")
    assert r.status_code == 400
    assert r.json()["code"] == "bad_symbol"


def test_http_exception_keeps_detail_adds_code():
    """前端 api.ts 读 body.detail——旧契约必须保持，code 是增量。"""
    c = TestClient(_app())
    r = c.get("/http-exc")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "000001 无盘口数据"
    assert body["code"] == "http_404"


def test_validation_error_422_shape():
    c = TestClient(_app())
    r = c.get("/needs-int", params={"n": "abc"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "n" in body["detail"]


def test_unhandled_500_unified_body():
    c = TestClient(_app(), raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert body["detail"] == "服务器内部错误"
    assert "意外炸了" not in r.text  # 堆栈细节不外漏
