"""B6 写接口鉴权测试（opt-in 单 token）：未配置放行 / 配置后强制 X-API-Token。"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_write_token
from app.core.config import settings
from app.core.errors import register_error_handlers


def _make_app(monkeypatch: pytest.MonkeyPatch, token: str) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/demo-write-guarded", dependencies=[Depends(require_write_token)])
    async def demo_guarded():
        return {"ok": True}

    monkeypatch.setattr(settings, "api_token", token)
    return app


def test_token_unset_allows_all(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch, token=""))
    assert client.post("/demo-write-guarded").status_code == 200


def test_token_set_requires_header(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch, token="s3cret"))
    assert client.post("/demo-write-guarded").status_code == 401
    r_wrong = client.post("/demo-write-guarded", headers={"X-API-Token": "wrong"})
    assert r_wrong.status_code == 401
    assert r_wrong.json()["code"] == "http_401"
    assert client.post("/demo-write-guarded", headers={"X-API-Token": "s3cret"}).status_code == 200
    # 查询参数形式同样接受（便于浏览器场景）
    assert client.post("/demo-write-guarded?token=s3cret").status_code == 200


def test_paper_reset_guarded_by_token(monkeypatch: pytest.MonkeyPatch):
    """真实路由冒烟：/reset 在启用 token 后未带头 → 401（鉴权先于业务，无需 lifespan）。"""
    # 不用 with：避免触发 lifespan 启动 provider；dependency 在业务前执行，401 即断言目标
    client = TestClient(_build_main_app(monkeypatch))
    r = client.post("/api/paper/reset", json={})
    assert r.status_code == 401


def _build_main_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    import app.main as m

    monkeypatch.setattr(settings, "api_token", "t0p")
    return m.app
