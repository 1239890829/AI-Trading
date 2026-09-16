"""统一鉴权边界（R22，2026-09-15）：姿态 fail closed + 默认拒绝 + WebSocket 子协议。

## 为什么单开一个文件而不是并进 `test_write_token.py`

那边守的是「写接口鉴权（B6）」这一条**作用域**；这边守的是**边界本身**——
覆盖读、写、WebSocket 三个面，以及「配置姿态是否 fail closed」。两者判据形态也不同：
那边是**结构式**（遍历依赖树找 `require_write_token`），这边是**行为式**
（遍历 OpenAPI 逐条发真实请求，断言 401）。形态不同就不能共用一套自证。

## 结构式判据在这里**不可用**（实测，务必别改回去）

本版 FastAPI 的 `include_router` 生成 `_IncludedRouter` 包装对象，它保留的是
**原始（未加 prefix）**路由：`original_router.routes` 里的 `route.path` 是 `/health`
而不是 `/api/health`，且 **`include_router(dependencies=[...])` 不会写进这些原始对象**。
⇒ 用「遍历 `route.dependant` 找 `require_api_token`」判定"是否受保护"会得到
**全部 176 条都无守卫**的结论（首版就是这么写的，实测全红）。

行为式判据反过来更强：它不关心守卫是挂在 router 上、路由上还是中间件里，
**只问最终行为**——无凭据时到底有没有被拒。任何"本该拒却放行"的成因都能抓到。

## 首版行为式门禁抓到过什么（留着当"这类判据值得"的证据）

首版把整个 `health_route.router` 当作存活探针豁免，而该 router 里除 `/health` 外
还挂着 **6 个 `/system/*`** 端点，其中 `/system/llm-probe?force=1` 会**真实花钱**。
那个错误让 6 条端点一起返回 200——**逐条探测**才看得见（只看"我豁免了一条"看不见）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import (
    AUTH_EXEMPT_PATHS,
    AUTH_MODE_LOCAL,
    AUTH_MODE_SHARED,
    is_api_token_valid,
    validate_auth_posture,
    ws_decode_subprotocol_token,
    ws_encode_token,
    ws_token_subprotocol,
)
from app.core.config import settings

#: 路径参数占位值：必须能通过**路径匹配**（值本身是否有效无所谓——鉴权先于业务）。
_PATH_VALUES = {
    "symbol": "600519",
    "task_id": "1",
    "rule_id": "1",
    "event_id": "1",
    "trade_id": "1",
    "change_id": "1",
    "triage_id": "1",
    "item_id": "1",
    "code": "BK1024",
    "trade_date": "2026-08-28",
    "name": "default",
    "board_code": "BK1024",
    "order_id": "1",
    "run_id": "missing-run",
}

_METHODS = ("get", "post", "put", "patch", "delete")


# ─────────────────────────── 姿态：fail closed ───────────────────────────


@pytest.fixture
def posture(monkeypatch: pytest.MonkeyPatch):
    """把 `auth_mode` 与 `api_token` 都置为可控值（默认 local + 空 token）。"""

    def _set(mode: str, token: str) -> None:
        monkeypatch.setattr(settings, "auth_mode", mode)
        monkeypatch.setattr(settings, "api_token", token)

    _set(AUTH_MODE_LOCAL, "")
    return _set


def test_local_without_token_starts_and_allows(posture):
    """`local` + 空 token = 本地开发零摩擦（与加固前行为逐字一致，必须仍然成立）。"""
    posture(AUTH_MODE_LOCAL, "")
    validate_auth_posture()  # 不抛
    assert settings.auth_required is False
    assert is_api_token_valid(None) is True


def test_shared_without_token_refuses_to_start(posture):
    """`shared` + 空 token ⇒ 拒绝启动。

    这是本轮的**止险点**：`local` 姿态下「忘记配 token」与「配好了」在运行期
    长得一模一样（都 200、无日志差异）⇒ 静默全开。把"忘了配"从**静默全开**
    变成**起不来**，才是 fail closed。
    """
    posture(AUTH_MODE_SHARED, "")
    with pytest.raises(RuntimeError) as ei:
        validate_auth_posture()
    assert "ASHARE_API_TOKEN" in str(ei.value)


def test_shared_with_token_starts(posture):
    posture(AUTH_MODE_SHARED, "t0p")
    validate_auth_posture()  # 不抛
    assert settings.auth_required is True


@pytest.mark.parametrize("typo", ["share", "SHAREDD", "", "public", "on"])
def test_unknown_auth_mode_refuses_to_start(posture, typo):
    """取值拼错必须**拒绝启动**，而不是静默退回 local。

    `share`（漏个 d）与 `shared` 在肉眼与日志里几乎无差，但会让 `auth_required`
    静默为 False ⇒ **共享部署被降级成全放行**。这是"配置拼错 = 静默降级"的
    典型形态，只能靠启动期白名单堵。
    """
    posture(typo, "t0p")
    with pytest.raises(RuntimeError) as ei:
        validate_auth_posture()
    assert "ASHARE_AUTH_MODE" in str(ei.value)


def test_auth_mode_is_case_and_space_insensitive(posture):
    """取值判定必须归一化——否则 `SHARED` 会被误判成非法并拒绝启动（假红）。"""
    posture("  Shared ", "t0p")
    validate_auth_posture()  # 不抛


def test_shared_empty_token_still_rejects_at_runtime(posture):
    """**运行期**也必须拒绝，不能只靠启动校验。

    理由（写进 `core/auth.py` 的 docstring）：启动校验只覆盖"经 `main.py` 启动"
    这一条路径——测试夹具、脚本直连、自定义 ASGI 入口都能绕过它。若运行期
    沿用"空 token 即放行"，绕过启动校验就等于全开。
    """
    posture(AUTH_MODE_SHARED, "")
    assert is_api_token_valid(None) is False
    assert is_api_token_valid("") is False
    assert is_api_token_valid("anything") is False


# ─────────────────── 默认拒绝：全 API 行为式门禁 ───────────────────


def _probe_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """带真实路由、启用凭据要求的 TestClient。

    刻意**不进 lifespan**：依赖在业务之前执行，401 无需任何 app.state
    （与 `test_write_token.py::test_paper_reset_guarded_by_token` 同法）。
    因此不需要 provider / 数据库 / 网络，本文件可在离线环境运行。
    """
    import app.main as m

    monkeypatch.setattr(settings, "auth_mode", AUTH_MODE_LOCAL)
    monkeypatch.setattr(settings, "api_token", "t0p")
    return TestClient(m.app, raise_server_exceptions=False)


def _concrete(path: str) -> str:
    for key, val in _PATH_VALUES.items():
        path = path.replace("{" + key + "}", val)
    return path


def _iter_operations() -> list[tuple[str, str]]:
    """(METHOD, 已替换占位值的路径) 全量清单——口径取自 OpenAPI。

    为什么用 OpenAPI 而不是遍历路由对象：见模块 docstring（原始路由未加 prefix、
    且 router 级依赖不落在其上）。OpenAPI 是**对外契约**，与客户端实际能打到什么
    完全一致，正是"边界"该用的口径。
    """
    from app.main import app

    out: list[tuple[str, str]] = []
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method in _METHODS:
                out.append((method.upper(), _concrete(path)))
    return sorted(set(out))


def _unauthorized_bypasses(client: TestClient) -> list[tuple[str, str, int]]:
    """无凭据却**没被拒**的 (METHOD, path, 实际状态码) —— 即边界缺口。

    豁免路径（`AUTH_EXEMPT_PATHS`）不在此列：它们本来就该放行，其内容安全由
    `test_exempt_set_is_exactly_health` 与 `test_exempt_route_is_liveness_only`
    单独钉住。豁免清单是**字面量断言**过的常量，不是从别处推来的。
    """
    bad: list[tuple[str, str, int]] = []
    for method, url in _iter_operations():
        if url in AUTH_EXEMPT_PATHS:
            continue
        resp = client.request(method, url, json={} if method != "GET" else None)
        if resp.status_code != 401:
            bad.append((method, url, resp.status_code))
    return bad


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """把真实网络出口焊死（R21 纪律：测试必须离线）。

    本门禁在**守卫正常**时本来就离线——176 个请求全部在依赖阶段 401，业务代码一行
    不跑。但"守卫缺失"恰恰是本门禁**要抓的失败态**：此时被放行的端点会真的去抓数据
    （注入验证实测：`/api/market/fund-flow` 连打 3 次外网才失败，既慢又脏，还留下了
    真实出口流量）。

    故显式焊死：真去联网的端点会立刻抛错 ⇒ 端点返回 5xx（**仍然不是 401**）⇒
    门禁照常变红，但**不产生任何真实流量**。断言口径不变，失败形态更干净。
    """
    import socket

    def _blocked(*_args, **_kwargs):
        raise OSError("network disabled in tests (R21)")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def test_gate_actually_sees_operations():
    """守卫前置自证：探测面必须非空且有足够宽度（防"遍历写窄 ⇒ 恒真"）。"""
    ops = _iter_operations()
    assert len(ops) >= 150, f"只探测到 {len(ops)} 个 (method,path)，口径可能已失效"


def test_every_api_operation_requires_credential(monkeypatch: pytest.MonkeyPatch, offline):
    """**默认拒绝**：除显式豁免外，任何 (method, path) 无凭据都必须 401。

    这条覆盖 R22 点名的全部形态——花钱读（`/assistant/daily-summary`、
    `/news/digest/{symbol}`、`/system/llm-probe`）、敏感读（`/real/positions`、
    `/picks/position-labels`）、有生产写副作用的读（`/events/impact`、
    `/picks/leader-archive`、`/risk/state`）——以及**没被点名但同样敏感**的
    `/system/*` 全家。它们的共同点是：只按"写接口"分类时会被整类漏掉。
    """
    client = _probe_client(monkeypatch)
    bad = _unauthorized_bypasses(client)
    assert bad == [], (
        "以下端点无凭据仍可访问（鉴权边界缺口）：\n"
        + "\n".join(f"  {m} {p} → {code}" for m, p, code in bad)
    )


def test_default_deny_gate_is_not_vacuous(monkeypatch: pytest.MonkeyPatch, offline):
    """注入验证：新加一条**无守卫**路由，判定函数必须报出来（否则门禁是恒真摆设）。

    ⚠️ 必须清 `openapi_schema` 缓存：`app.openapi()` 会把结果缓存到实例上，
    不清缓存的话新注入的路由**根本不出现在探测面里**，于是这条自证会假绿。
    """
    from fastapi import APIRouter

    import app.main as m

    client = _probe_client(monkeypatch)
    router = APIRouter()

    @router.get("/__auth_probe_unprotected")
    async def probe():  # pragma: no cover - 仅注册，不调用
        return {"ok": True}

    before = len(m.app.router.routes)
    m.app.include_router(router)
    m.app.openapi_schema = None
    try:
        bad = _unauthorized_bypasses(client)
        assert any(p == "/__auth_probe_unprotected" for _, p, _ in bad), (
            "注入一条无凭据保护的 GET 后判定函数仍报 0 ⇒ 门禁口径失效（恒真）"
        )
        # 反向：把同一条路由改用带守卫的方式挂载，不得再被误报
        # （防"守卫松到见路由就报"）。守卫挂在 router 级即可——这正是生产写法。
        from app.main import _AUTH_GUARD

        router2 = APIRouter()

        @router2.get("/__auth_probe_protected")
        async def probe2():  # pragma: no cover - 仅注册，不调用
            return {"ok": True}

        m.app.include_router(router2, dependencies=_AUTH_GUARD)
        m.app.openapi_schema = None
        try:
            bad2 = _unauthorized_bypasses(client)
            assert not any(p == "/__auth_probe_protected" for _, p, _ in bad2), (
                "已挂 _AUTH_GUARD 的探针被误报为缺口 ⇒ 守卫判定过宽"
            )
        finally:
            del m.app.router.routes[before + 1 :]
            m.app.openapi_schema = None
    finally:
        del m.app.router.routes[before:]
        m.app.openapi_schema = None


def test_exempt_set_is_exactly_health():
    """豁免集合必须**恰好**是 {"/api/health"}——多一条都得先改这里并通过审查。

    这是"默认拒绝"能把稳定性做出来的关键：唯一的宽松点被钉成一个**字面量断言**，
    于是"悄悄新增豁免"会变红，而不是变成第二份隐性规则。
    """
    assert set(AUTH_EXEMPT_PATHS) == {"/api/health"}


def test_exempt_route_is_liveness_only():
    """豁免路由的内容判据：它**不得**读持仓、也不得触发 LLM。

    只断言"集合没变"还不够——有人可以把 `/real/positions` 换成 `/health` 的路径
    来绕过集合断言（虽然很刻意）。这里直接读 handler 源码，确认它确实只是存活探针。
    """
    import inspect

    from app.api.routes.health import liveness_router

    paths = [r.path for r in liveness_router.routes]
    assert paths == ["/health"], f"存活探针 router 不该承载别的路由：{paths}"

    src = inspect.getsource(liveness_router.routes[0].endpoint)
    for forbidden in ("load_positions", "RealTrade", "chat_completion", "LLMSummarizer", "llm"):
        assert forbidden not in src, f"豁免的存活探针里出现了 {forbidden!r} —— 豁免面被扩大"


def test_named_sensitive_reads_are_guarded(monkeypatch: pytest.MonkeyPatch):
    """点名回归钉子（与全量门禁互补，不是替代）。

    全量门禁失败了还得自己去 176 条里找是哪条；这几条**直接指出**R22 点名的
    三类形态各一条。刻意不写成"这几条就够了"——那正是原实现"只按写接口分类"
    的思维（枚举只能防上一次踩过的）。
    """
    client = _probe_client(monkeypatch)
    for method, url in [
        ("GET", "/api/assistant/daily-summary"),  # 花钱 + 读持仓上下文
        ("GET", "/api/news/digest/600519"),  # 花钱
        ("GET", "/api/real/positions"),  # 真实持仓账本
        ("GET", "/api/picks/leader-archive"),  # 会重建 data/leader_archive.json
        ("POST", "/api/assistant/chat"),  # 花钱（原本就受写守卫保护）
    ]:
        assert client.request(method, url).status_code == 401, f"{method} {url} 未受保护"


def test_health_endpoint_is_not_rejected(monkeypatch: pytest.MonkeyPatch):
    """豁免必须是**真的**豁免——否则 docker healthcheck 会让容器永远不健康。"""
    client = _probe_client(monkeypatch)
    # 本进程没进 lifespan ⇒ 端点内部会 500（无 app.state.hub），这没关系：
    # 本用例只钉"它**没有**被 401 拦下"，也就是"走到了业务代码"。
    assert client.get("/api/health").status_code != 401


def test_system_router_neighbours_are_not_collateral(monkeypatch: pytest.MonkeyPatch):
    """`/health` 与 `/system/*` 同文件 ⇒ 豁免必须是**路由**粒度而不是 router 粒度。

    这条是首版真实踩过的坑的钉子：按 router 豁免曾把 6 条 `/system/*`
    （含会真实花钱的 `/system/llm-probe?force=1`）一起放开。哪怕将来有人
    把两个 router 合并回去，这里也会立刻红。
    """
    client = _probe_client(monkeypatch)
    for url in [
        "/api/system/llm-probe",
        "/api/system/caches",
        "/api/system/metrics",
        "/api/system/providers",
        "/api/system/schedulers",
        "/api/system/marketdb-quality",
    ]:
        assert client.get(url).status_code == 401, f"{url} 被 /health 的豁免连带放开了"


# ─────────────────── WebSocket 子协议边界 ───────────────────


def test_ws_subprotocol_roundtrip():
    """编码 → 解析必须往返一致，且对任意字符（含 base64 的 `=`）都安全。"""
    for token in ["s3cret", "a" * 64, "带中文的token", "ab==", "x/y+z"]:
        proto = f"ashare-token.{ws_encode_token(token)}"
        assert ws_decode_subprotocol_token(proto) == token


def test_ws_subprotocol_is_http_token_safe():
    """编码结果必须是 RFC 6455 允许的 HTTP token 字符集。

    否则浏览器会在 `new WebSocket(url, [proto])` 处**抛异常**——那是前端的失败，
    后端只看到"连接没建立"，属最难排查的一类。
    """
    import re

    for token in ["s3cret", "ab==", "带中文的token", "x/y+z", ""]:
        encoded = ws_encode_token(token)
        assert re.fullmatch(r"[A-Za-z0-9\-_]*", encoded), f"{token!r} 编码出了非 token 字符：{encoded!r}"


def test_ws_subprotocol_picker_ignores_foreign_and_garbage():
    """挑子协议时必须容忍并列项与垃圾值，且不误取别人的子协议。"""
    ours = f"ashare-token.{ws_encode_token('s3cret')}"
    assert ws_token_subprotocol(f"chat, {ours}, superchat") == ours
    assert ws_token_subprotocol("chat, superchat") is None
    assert ws_token_subprotocol(f"ashare-token.{ws_encode_token('s3cret')}") is not None
    assert ws_token_subprotocol(None) is None
    assert ws_token_subprotocol("") is None
    # 前缀对但载荷为空 / 前缀只是别名的更长前缀，都不算
    assert ws_decode_subprotocol_token("ashare-token.") is None
    assert ws_decode_subprotocol_token("ashare-token.!!!not-base64!!!") is None
    assert ws_decode_subprotocol_token("other-token.abc") is None
    assert ws_decode_subprotocol_token(None) is None


class _FakeHub:
    """与 `test_ws_quotes_lifecycle.py::FakeHub` 同形，但只保留连接所需的最小面。"""

    def __init__(self) -> None:
        self.subscribed: list = []

    def next_seq(self) -> int:
        return 1

    def get_quotes(self, symbols=None) -> list:
        return []

    def subscribe(self, symbols=None):
        import asyncio

        q: asyncio.Queue = asyncio.Queue()
        self.subscribed.append(q)
        return q

    def update_symbols(self, queue, symbols) -> bool:
        return True

    def unsubscribe(self, queue) -> None:
        return None


def _ws_app() -> FastAPI:
    from app.websocket.routes import router as ws_router

    app = FastAPI()
    app.include_router(ws_router)
    app.state.hub = _FakeHub()
    return app


def test_ws_rejects_without_credential_when_required(monkeypatch: pytest.MonkeyPatch):
    """`shared` 姿态下不带子协议的握手必须**建不起来**（而不是连上再断）。"""
    from fastapi import WebSocketDisconnect

    monkeypatch.setattr(settings, "auth_mode", AUTH_MODE_SHARED)
    monkeypatch.setattr(settings, "api_token", "t0p")
    client = TestClient(_ws_app())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/quotes?symbols=600519"):
            pass


def test_ws_rejects_wrong_credential(monkeypatch: pytest.MonkeyPatch):
    """凭据错（前缀合法但 token 不对）同样必须拒——不能只看"带了子协议"。"""
    from fastapi import WebSocketDisconnect

    monkeypatch.setattr(settings, "auth_mode", AUTH_MODE_SHARED)
    monkeypatch.setattr(settings, "api_token", "t0p")
    client = TestClient(_ws_app())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/quotes?symbols=600519",
            subprotocols=[f"ashare-token.{ws_encode_token('wrong')}"],
        ):
            pass


def test_ws_accepts_valid_subprotocol_credential(monkeypatch: pytest.MonkeyPatch):
    """凭据正确 ⇒ 连接建立，且**原样回显**客户端提议的子协议。

    回显不是可选动作：客户端提议了子协议而服务端一个都不选时，浏览器会主动
    判定连接失败（RFC 6455 §4.1 / WHATWG 建连算法）。所以这里断言回显值
    与提议值**逐字相同**。
    """
    monkeypatch.setattr(settings, "auth_mode", AUTH_MODE_SHARED)
    monkeypatch.setattr(settings, "api_token", "t0p")
    offered = f"ashare-token.{ws_encode_token('t0p')}"
    client = TestClient(_ws_app())
    with client.websocket_connect("/ws/quotes?symbols=600519", subprotocols=[offered]) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert ws.accepted_subprotocol == offered


def test_ws_allows_without_credential_in_local_posture(monkeypatch: pytest.MonkeyPatch):
    """`local`（本地开发默认）姿态下，不带子协议照常可连——零摩擦不倒退。"""
    monkeypatch.setattr(settings, "auth_mode", AUTH_MODE_LOCAL)
    monkeypatch.setattr(settings, "api_token", "")
    client = TestClient(_ws_app())
    with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
        assert ws.receive_json()["type"] == "snapshot"
