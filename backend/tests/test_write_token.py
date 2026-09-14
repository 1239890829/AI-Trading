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
    # 查询参数通道已**刻意关闭**（2026-09-14）：token 走 URL 会被浏览器历史、
    # Referer 与反代访问日志留存，而本系统前端是同源反代、由服务端注入请求头。
    assert client.post("/demo-write-guarded?token=s3cret").status_code == 401


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


# ---------- 结构性门禁：非 GET 端点必须持有写鉴权（2026-09-14） ----------
#
# 为什么需要这条守卫（而 test_paper_reset_guarded_by_token 不够）：
# 那个用例**点名**了一条已知受保护的路由，只能证明「这条接了」；
# 而本轮实测发现 **42 个非 GET 端点里有 8 个没接**——其中 4 个是
# 「真实持仓账本」的破坏性写端点（`DELETE /api/real/positions/{symbol}`
# 可清空整只标的的全部流水与覆盖），公网部署下可被任意访客调用。
# 「漏接」与「已接」在运行时长得一模一样：都返回 200，只有配置了 token
# 才分叉。⇒ 判据必须**遍历全部路由**，且以**运行时依赖树**为准（装饰器
# 写法与签名写法两种都能识别），不能靠枚举清单——枚举只能防上一次踩过的。


def _dependency_calls(dependant, seen: set | None = None) -> set:
    """递归收集依赖树里的可调用对象（装饰器级 `dependencies=[...]` 与签名级
    `Depends(...)` 都会以子依赖形式出现，故一处判定即可覆盖两种写法）。"""
    seen = seen if seen is not None else set()
    for sub in dependant.dependencies:
        call = getattr(sub, "call", None)
        if call is not None:
            seen.add(call)
        _dependency_calls(sub, seen)
    return seen


def _iter_api_routes(app):
    """递归展开全部 APIRoute。

    ⚠️ **不能只遍历 `app.routes`**：本版 FastAPI 的 `include_router` 生成的是
    `_IncludedRouter` 包装对象（`app.routes` 里 `APIRoute` 计数为 **0**），
    真实路由挂在它的 `original_router.routes` 下。首版门禁就是栽在这里——
    遍历到 0 条路由 ⇒ 断言恒真 ⇒ **假绿**（由下面的自证用例抓出）。
    依次尝试 `original_router` / `app` / `router` 三种承载形态，并用 id 去重防环。
    """
    from fastapi.routing import APIRoute

    seen: set[int] = set()

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
                continue
            for attr in ("original_router", "app", "router"):
                sub = getattr(route, attr, None)
                if sub is None or not hasattr(sub, "routes") or id(sub) in seen:
                    continue
                seen.add(id(sub))
                yield from walk(sub.routes)

    yield from walk(app.routes)


def _unprotected_write_routes() -> list[tuple[str, list[str]]]:
    from app.main import app as main_app

    out: list[tuple[str, list[str]]] = []
    for route in _iter_api_routes(main_app):
        methods = sorted(set(route.methods) - {"GET", "HEAD", "OPTIONS"})
        if not methods:
            continue
        if require_write_token not in _dependency_calls(route.dependant):
            out.append((route.path, methods))
    return out


def test_gate_walker_actually_sees_routes():
    """守卫前置自证：遍历口径必须真能扫到路由与写路由。

    这条单独存在，是为了让「遍历写窄 ⇒ 无物可查 ⇒ 恒真」这种失效**立刻可见**，
    而不是等到下面那条自证用例（它需要依赖注入才红）。实测基线：176 条 APIRoute、
    其中 42 条含非 GET 方法——数字变化时先确认是不是路由增删，再谈缺口。
    """
    from app.main import app as main_app

    routes = list(_iter_api_routes(main_app))
    assert len(routes) > 100, f"只遍历到 {len(routes)} 条 APIRoute，遍历口径可能已失效"
    writes = [r for r in routes if set(r.methods) - {"GET", "HEAD", "OPTIONS"}]
    assert len(writes) > 20, f"只扫到 {len(writes)} 条写路由，遍历口径可能已失效"


def test_every_non_get_route_declares_write_guard():
    """**全部**非 GET 端点都必须持有 require_write_token（无一例外）。

    例外不存在是刻意的：token 是 opt-in（未配置即全放行），所以「本地开发零影响」
    与「部署后统一写保护」可以同时成立；一旦开出例外清单，它就又会随版本漂移。
    """
    bad = _unprotected_write_routes()
    assert bad == [], (
        "以下非 GET 端点未声明 require_write_token（写接口鉴权缺口）：\n"
        + "\n".join(f"  {m} {p}" for p, m in bad)
    )


def test_write_guard_gate_is_not_vacuous():
    """注入验证：构造一条**真未受保护**的非 GET 路由，断言判定函数能报出来。

    验收判据是「精确变红」——若 `_dependency_calls` 的递归口径或遍历口径退化成
    空集合，本用例先红，而不是让主门禁静默通过（KB-ENG-65 假绿守卫形态③）。
    """
    from fastapi import APIRouter

    from app.main import app as main_app

    router = APIRouter()

    @router.post("/__gate_probe_unprotected")
    async def probe():  # pragma: no cover - 仅注册，不调用
        return {"ok": True}

    before = len(main_app.router.routes)
    main_app.include_router(router)
    try:
        bad = _unprotected_write_routes()
        assert any(p == "/__gate_probe_unprotected" for p, _ in bad), (
            "注入一条无鉴权的 POST 后判定函数仍报 0 ⇒ 门禁口径失效（恒真）"
        )
        # 反向：注入一条**带**鉴权的路由不得被误报（避免守卫松到"见 POST 就报"）
        guarded = APIRouter()

        @guarded.post("/__gate_probe_guarded", dependencies=[Depends(require_write_token)])
        async def probe_guarded():  # pragma: no cover - 仅注册，不调用
            return {"ok": True}

        main_app.include_router(guarded)
        try:
            bad2 = _unprotected_write_routes()
            assert not any(p == "/__gate_probe_guarded" for p, _ in bad2), (
                "已声明 require_write_token 的探针被误报 ⇒ 守卫判定过宽"
            )
        finally:
            del main_app.router.routes[before + 1 :]
    finally:
        del main_app.router.routes[before:]


def test_real_position_writes_guarded_by_token(monkeypatch: pytest.MonkeyPatch):
    """真实持仓账本的破坏性写端点必须 401（鉴权先于业务，无需 lifespan）。

    这是本轮修复的回归钉子：`DELETE /api/real/positions/{symbol}` 会清掉该标的
    **全部流水与覆盖**，此前无任何写保护。
    """
    client = TestClient(_build_main_app(monkeypatch))
    assert client.post("/api/real/trades", json={}).status_code == 401
    assert client.delete("/api/real/trades/1").status_code == 401
    assert client.patch("/api/real/positions/600519", json={}).status_code == 401
    assert client.delete("/api/real/positions/600519").status_code == 401
