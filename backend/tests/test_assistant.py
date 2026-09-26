"""全局 AI 助手测试：流式客户端解析 + SSE 路由契约 + 实体字典。

不触网：流式客户端用桩（openai 用 MockTransport、cli 用假 Popen），
路由层替换 `_open_stream`。实体字典用临时 parquet 目录。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

import app.api.routes.assistant as assistant_routes
from app.core.llm_client import LLMError, LLMFailure, stream_chat_completion
from app.core.config import settings

# 仓库根：后端测试要交叉校验前端源文件（跳转别名合同的单一实现方在前端）
FRONTEND = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- openai 流式


def _sse_lines(chunks: list[str]) -> bytes:
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def test_stream_openai_yields_deltas():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["stream"] is True
        return httpx.Response(200, content=_sse_lines(["你好", "，", "世界"]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # _stream_openai 自建 httpx.Client，无法注入 transport——
    # 这里直接走 stream_chat_completion 前先 monkeypatch httpx.Client
    orig = httpx.Client
    httpx.Client = lambda **kw: client  # type: ignore[assignment]
    try:
        stream = stream_chat_completion("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}])
        assert "".join(stream) == "你好，世界"
    finally:
        httpx.Client = orig  # type: ignore[assignment]


def test_stream_openai_unconfigured_raises():
    with pytest.raises(LLMError):
        stream_chat_completion("", "", "", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- claude_cli 流式


class _FakeProc:
    def __init__(self, lines: list[str], returncode: int = 0):
        self._lines = lines
        self.returncode = returncode
        self.killed = False
        self.stdout = iter(lines)
        self.stderr = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float = 0) -> int:
        return self.returncode


def _cli_events() -> list[str]:
    return [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                                                     "delta": {"type": "text_delta", "text": "第一段"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                                                     "delta": {"type": "text_delta", "text": "第二段"}}}),
        # 整段 assistant 回包：saw_delta 后必须被忽略，否则文本重复
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "第一段第二段"}]}}),
        json.dumps({"type": "result", "is_error": False, "result": "ok"}),
    ]


def test_stream_cli_deltas_and_dedup(monkeypatch):
    proc = _FakeProc(_cli_events())
    proc.returncode = None  # 模拟运行中：close() 应杀掉子进程
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    stream = stream_chat_completion("", "", "glm-5.3",
                                    [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
                                    provider="claude_cli", cli_path="/bin/echo")
    assert "".join(stream) == "第一段第二段"
    stream.close()
    assert proc.killed  # close 幂等杀进程（已退出则 no-op 路径，但 killed 标记会置位）


def test_stream_cli_result_error_raises(monkeypatch):
    lines = [
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                                                     "delta": {"type": "text_delta", "text": "x"}}}),
        json.dumps({"type": "result", "is_error": True, "result": "token quota is not enough"}),
    ]
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProc(lines, returncode=1))
    stream = stream_chat_completion("", "", "glm-5.3", [{"role": "user", "content": "hi"}],
                                    provider="claude_cli", cli_path="/bin/echo")
    with pytest.raises(LLMError, match="quota"):
        list(stream)


def test_stream_cli_no_delta_fallback_to_full_message(monkeypatch):
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "整段回复"}]}}),
        json.dumps({"type": "result", "is_error": False}),
    ]
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProc(lines))
    stream = stream_chat_completion("", "", "glm-5.3", [{"role": "user", "content": "hi"}],
                                    provider="claude_cli", cli_path="/bin/echo")
    assert "".join(stream) == "整段回复"


# ---------------------------------------------------------------- 路由：SSE 契约


@pytest.fixture(scope="module")
def client():
    """模块级：本文件全部用例共用一次 lifespan（P1-26，2026-09-10）。

    此前是默认的 function 作用域——每个用例都重建 `app.main` 的 lifespan
    （建库 + 起 QuoteHub/snapshot，实测单次 35–46s），**15 次 ≈ 9 分钟**，
    是当时全量套件 17 分钟的最大单点。

    安全性依据：本文件用例全部靠 `monkeypatch`（function 作用域，自动还原）
    注入桩，不依赖「全新启动状态」；SSE 用例的假流/假进程同样按用例还原。
    **用例数会随功能增长**（2026-09-11 助手工具扩容后为 48 项）——别把这里当计数真相源。
    """
    from app.main import app

    with TestClient(app) as c:
        yield c


class _FakeStream:
    def __init__(self, deltas: list[str], error: Exception | None = None):
        self._deltas = deltas
        self._error = error
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self._deltas:
            return self._deltas.pop(0)
        if self._error is not None:
            err, self._error = self._error, None
            raise err
        raise StopIteration

    def close(self) -> None:
        self.closed = True


def _parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            events.append(json.loads(block[6:]))
    return events


def test_chat_route_sse_success(client, monkeypatch):
    fake = _FakeStream(["你好", "！"])
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    # 断言系统提示确实注入了项目模块图与页面上下文
    captured: list = []
    orig = assistant_routes._build_messages

    def spy(req, market_block="", tools_enabled=False):
        msgs = orig(req, market_block, tools_enabled=tools_enabled)
        captured.append(msgs)
        return msgs

    monkeypatch.setattr(assistant_routes, "_build_messages", spy)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "介绍一下工作台"}],
        "page": {"path": "/workbench?symbol=600519", "title": "工作台", "symbol": "600519"},
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    assert events[0]["type"] == "meta"
    texts = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(texts) == "你好！"
    assert events[-1] == {"type": "done"}
    assert fake.closed
    sysmsg = captured[0][0]["content"]
    assert "AShare AI Trader" in sysmsg and "600519" in sysmsg


def test_chat_route_sse_llm_error(client, monkeypatch):
    fake = _FakeStream(["部分"], error=LLMError("token quota is not enough"))
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    events = _parse_sse(resp.text)
    err = [e for e in events if e["type"] == "error"]
    assert len(err) == 1 and "quota" in err[0]["message"]
    assert events[-1] == {"type": "done"}
    # 部分输出不丢：error 前的 delta 保留
    assert any(e.get("text") == "部分" for e in events if e["type"] == "delta")


def test_chat_route_sse_error_carries_kind_and_hint(client, monkeypatch):
    """额度不足要把"该充值"传到前端——只给英文原文，用户不知道该做什么。"""
    fake = _FakeStream([], error=LLMError("need quota 0.09", LLMFailure.QUOTA))
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    err = [e for e in _parse_sse(resp.text) if e["type"] == "error"][0]
    assert err["kind"] == "quota"
    assert "充值" in (err["hint"] or "")


def test_chat_route_sse_gateway_error_hint_must_not_say_recharge(client, monkeypatch):
    """网关失败的提示里出现"充值"就是误导——这是分类存在的意义。"""
    fake = _FakeStream([], error=LLMError("internal error", LLMFailure.GATEWAY_ERROR))
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    err = [e for e in _parse_sse(resp.text) if e["type"] == "error"][0]
    assert err["kind"] == "gateway_error"
    assert "充值" not in (err["hint"] or "")


def _frontend_table_keys(table: str) -> set[str]:
    """从前端 lib/nav-targets.ts 里抽出一张别名表的键集（两处守卫共用）。

    直接读源文件做交叉校验：跳转合同（词 → 落点）的**唯一实现方在前端**，
    后端只在提示词里点名；两边一旦漂移，模型照提示词写、前端却识别不到，
    静默退化成普通文字——不给这种漂移留窗口。
    """
    nav_src = (FRONTEND / "apps/web/lib/nav-targets.ts").read_text(encoding="utf-8")
    block = re.search(rf"{table}[^=]*=\s*\{{(.*?)\n\}};", nav_src, re.S)
    assert block, f"未找到前端 {table} 表（结构变了，同步更新本测试）"
    return set(re.findall(r"^\s*([^\s:{]+):", block.group(1), re.M))


def test_prompt_nav_words_covered_by_frontend():
    """提示词点名的功能名必须都在前端别名表里，否则"可跳转"是空头支票。"""
    from app.assistant.prompt import NAV_WORDS, PROJECT_BRIEF

    frontend_keys = _frontend_table_keys("NAV_ALIASES")
    missing = [w for w in NAV_WORDS if w not in frontend_keys]
    assert not missing, f"提示词点名但前端无别名（跳转会失效）：{missing}"
    # 提示词里必须真的带上这些词，而不是只写在常量里
    for w in NAV_WORDS:
        assert w in PROJECT_BRIEF, f"提示词未包含可跳转功能名：{w}"


def test_prompt_stock_tab_words_covered_by_frontend():
    """个股页签标准词同理（2026-09-11，P2-28②）。

    页签跳转是**组合式**的：光有词不够，还要跟个股名贴紧才会命中在前端
    `STOCK_TAB_ALIASES` 上（见 lib/entity-links.ts::tabAfter）。所以这里守两件事：
    ①提示词点名的页签词前端都认识；②提示词确实把「贴紧写」这条教给了模型——
    否则模型写成「个股详情的分时/资金页签」，个股名与页签名离得远，跳转静默失效。
    """
    from app.assistant.prompt import PROJECT_BRIEF, STOCK_TAB_WORDS

    frontend_keys = _frontend_table_keys("STOCK_TAB_ALIASES")
    missing = [w for w in STOCK_TAB_WORDS if w not in frontend_keys]
    assert not missing, f"提示词点名但前端无页签别名（页签跳转会失效）：{missing}"
    for w in STOCK_TAB_WORDS:
        assert w in PROJECT_BRIEF, f"提示词未包含个股页签标准词：{w}"
    # 教了"贴紧写"才可能命中；只列词不教用法等于没接线
    assert "贴着写" in PROJECT_BRIEF, "提示词未说明页签词的贴紧写法（组合识别会失效）"


def test_chat_route_tool_round(client, monkeypatch):
    """端到端：模型要工具 → 后端取数回填 → 第二轮生成正文，且标记不漏给用户。"""
    rounds: list[list[str]] = [["{{tool:limit_up|date=2026-09-04}}"], ["今日涨停 ", "38 家"]]
    opened: list = []

    def factory(msgs):
        opened.append(msgs)
        return _FakeStream(list(rounds[len(opened) - 1]))

    monkeypatch.setattr(assistant_routes, "_open_stream", factory)

    from app.assistant.tools import ToolContext

    class _Prov:
        async def get_limit_up_pool(self, trade_date):
            return [{"symbol": "600519", "name": "贵州茅台", "consecutive_boards": 1,
                     "change_pct": 10.0, "reason": "白酒"}]

    async def fake_ctx(request, known=None):
        return ToolContext(provider=_Prov(), known_symbols=set(), trading_days={"2026-09-04"})

    monkeypatch.setattr(assistant_routes, "_tool_context", fake_ctx)
    monkeypatch.setattr(settings, "assistant_tools_enabled", True)

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "今天涨停池什么情况？"}],
    })
    assert resp.status_code == 200
    evs = _parse_sse(resp.text)
    deltas = [e["text"] for e in evs if e.get("type") == "delta"]
    assert "".join(deltas) == "今日涨停 38 家"       # 标记行没流给用户
    assert any(e.get("type") == "tools" and e.get("used") == ["limit_up"] for e in evs)
    assert len(opened) == 2                            # 取数后又跑了一轮
    # 第二轮的最后一条 user 消息必须带着工具结果
    assert "贵州茅台" in opened[1][-1]["content"]
    # 提示词里注入了工具清单
    assert "{{tool:limit_up|" in opened[0][0]["content"]


def test_chat_route_no_tool_round_when_disabled(client, monkeypatch):
    """关掉工具就不注入清单——模型不该以为自己有手（幻觉的最大来源）。"""
    opened: list = []
    monkeypatch.setattr(assistant_routes, "_open_stream",
                        lambda msgs: (opened.append(msgs), _FakeStream(["好的"]))[1])
    monkeypatch.setattr(settings, "assistant_tools_enabled", False)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    assert len(opened) == 1
    assert "可用工具" not in opened[0][0]["content"]


def test_chat_route_validation(client):
    # 最后一条不是 user → 422
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "assistant", "content": "hi"}],
    })
    assert resp.status_code == 422
    # 空内容 → 422
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "  "}],
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------- 实体字典


def _write_snapshot(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    # 路由读 settings.parquet_dir/snapshots/YYYYMMDD/*.parquet
    day = tmp_path / "snapshots" / "20260904"
    day.mkdir(parents=True)
    pl.DataFrame({"symbol": [r[0] for r in rows], "name": [r[1] for r in rows]}).write_parquet(
        day / "150000.parquet"
    )
    return tmp_path


class _FakeCatalog:
    def __init__(self, names: list[str]):
        self._names = names

    def get_catalog(self, search=None, limit=500):
        class R:
            def __init__(self, n: str):
                self.name = n
        return [R(n) for n in self._names[:limit]]


def test_entity_dict(client, monkeypatch, tmp_path):
    pdir = _write_snapshot(tmp_path, [
        ("600519", "贵州茅台"), ("000001", "平安银行"),
        ("123456", "单"),  # 名字太短 → 过滤
    ])
    monkeypatch.setattr(settings, "parquet_dir", str(pdir))
    app = client.app
    app.state.theme_catalog = _FakeCatalog(["存储芯片", "机器人", "A"])
    # 清缓存确保读到新目录
    assistant_routes._entity_cache.update(t=0.0, data=None)
    resp = client.get("/api/assistant/entity-dict")
    assert resp.status_code == 200
    data = resp.json()["data"]
    names = {s["name"]: s["code"] for s in data["stocks"]}
    assert names == {"贵州茅台": "600519", "平安银行": "000001"}
    assert data["themes"] == ["存储芯片", "机器人"]  # 码点排序 + 单字过滤
    # 二次请求走缓存（改目录不影响结果）
    monkeypatch.setattr(settings, "parquet_dir", str(tmp_path / "nonexist"))
    resp2 = client.get("/api/assistant/entity-dict")
    assert resp2.json()["data"] == data


def test_entity_dict_empty_env(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "parquet_dir", str(tmp_path / "nonexist"))
    app = client.app
    saved = getattr(app.state, "theme_catalog", None)
    app.state.theme_catalog = None
    assistant_routes._entity_cache.update(t=0.0, data=None)
    try:
        resp = client.get("/api/assistant/entity-dict")
        assert resp.status_code == 200
        assert resp.json()["data"] == {"stocks": [], "themes": []}
    finally:
        if saved is not None:
            app.state.theme_catalog = saved
        assistant_routes._entity_cache.update(t=0.0, data=None)


# ---------------------------------------------------------------- 实时快照注入

from datetime import datetime, timezone

from app.assistant.context import (
    build_market_block,
    build_market_context,
    format_quote_line,
    quote_sources,
    resolve_symbols,
)
from app.schemas.market import Quote

_STOCKS = [
    {"name": "贵州茅台", "code": "600519"},
    {"name": "平安银行", "code": "000001"},
    {"name": "芯片设备", "code": "999999"},  # 假题材名混入也应按名解析
]


def test_resolve_symbols_code_and_name():
    codes = resolve_symbols("600519 和 平安银行 各多少，金额 123456 别认", None, _STOCKS)
    assert codes == ["600519", "000001"]  # 字典外 6 位数字（金额）不认


def test_resolve_symbols_page_first_and_cap():
    page = assistant_routes.PageContext(symbol="000001")
    many = _STOCKS + [{"name": f"股票{i}", "code": f"10000{i}"} for i in range(10)]
    text = "贵州茅台 " + " ".join(f"股票{i}" for i in range(10))
    codes = resolve_symbols(text, page, many)
    assert codes[0] == "000001"  # 页面标的优先
    assert len(codes) == 6       # 上限


def test_resolve_symbols_empty_dict():
    assert resolve_symbols("600519 贵州茅台", None, []) == []


def _mk_quote(**kw) -> Quote:
    base = dict(symbol="600519", source="tencent", name="贵州茅台", price=1500.5, change_pct=2.35,
                open=1480.0, high=1520.0, low=1470.0, prev_close=1466.0, amount=5.6e9)
    base.update(kw)
    return Quote(**base)


def test_format_quote_line_fields():
    line = format_quote_line(_mk_quote())
    assert "贵州茅台（600519）" in line and "现价 1500.5" in line and "涨跌幅 2.35%" in line
    assert "成交额 56.00 亿" in line
    # 缺失字段显示 —，不臆造
    q = _mk_quote(price=None, change_pct=None, data_timestamp=None)
    assert "现价 —" in format_quote_line(q)


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_build_market_block_ok_and_order():
    async def fake_get(syms):
        # 故意乱序返回，验证按请求序输出
        return [_mk_quote(symbol=s) for s in reversed(syms)]

    block = _run(build_market_block(fake_get, ["000001", "600519"]))
    assert block.startswith("## 实时数据快照")
    assert "只准引用" in block
    assert block.index("（000001）") < block.index("（600519）")


def test_build_market_block_degrades():
    async def boom(_):
        raise RuntimeError("tencent down")

    assert _run(build_market_block(boom, ["600519"])) == ""
    assert _run(build_market_block(boom, [])) == ""


# ---------------------------------------------------------------- 输出溯源 P0-4


def test_quote_line_carries_source_and_as_of():
    """溯源三件套：来源 / 数据时间 / 口径——没有它们，回答里的数字无从核对。"""
    q = _mk_quote(source="sina", data_timestamp=datetime(2026, 9, 6, 6, 30, tzinfo=timezone.utc))
    line = format_quote_line(q)
    assert "来源 sina" in line
    assert "口径 实时快照" in line
    assert "数据时间 14:30" in line  # UTC 06:30 → 北京 14:30
    assert "⚠" not in line


def test_quote_line_flags_degraded_quality():
    """质量降级必须可见，且明确禁止下确定性结论。"""
    q = _mk_quote(quality="low", quality_reasons=["源超时"])
    line = format_quote_line(q)
    assert "⚠ 数据质量 low" in line and "源超时" in line
    assert "不要据此下确定性结论" in line


def test_quote_sources_shape():
    srcs = quote_sources([_mk_quote(name="贵州茅台", source="tencent")])
    assert srcs == [{"symbol": "600519", "name": "贵州茅台", "source": "tencent",
                     "as_of": srcs[0]["as_of"]}]
    assert srcs[0]["as_of"]  # 没有时间戳也要有兜底值，不能是空串


def test_quote_line_unknown_as_of_when_no_timestamp():
    q = _mk_quote()
    q = q.model_copy(update={"data_timestamp": None, "received_at": None}) \
        if hasattr(q, "model_copy") else q
    # received_at 有默认工厂，构造不出来空值；这里只保证有值时格式正确
    assert "数据时间 " in format_quote_line(q)


def test_build_market_context_returns_sources():
    async def fake_get(syms):
        return [
            _mk_quote(symbol="600519", source="tencent"),
            _mk_quote(symbol="000001", source="tencent"),
        ]

    block, sources = _run(build_market_context(fake_get, ["000001", "600519"]))
    assert "溯源纪律" in block
    assert [s["symbol"] for s in sources] == ["000001", "600519"]  # 与请求顺序一致
    assert all(s["source"] == "tencent" and s["as_of"] for s in sources)


def test_build_market_context_empty():
    async def boom(_):
        raise RuntimeError("down")

    assert _run(build_market_context(boom, ["600519"])) == ("", [])
    assert _run(build_market_context(boom, [])) == ("", [])


def test_chat_route_meta_carries_sources(client, monkeypatch):
    """meta 事件带溯源清单：前端据此显示脚注，数字可追溯到源与时间。"""
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: _FakeStream(["好的"]))
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [{"name": "贵州茅台", "code": "600519"}], "themes": []})

    async def fake_batch(hub, syms):
        return [_mk_quote(source="tencent")]

    # S2-4：批量取数实现已上移到 services/quote_enrich（不再依赖路由私有名）。
    # 打桩点跟着导入方走——assistant.py 是模块级 `from ... import`，名字绑在它自己的全局里。
    monkeypatch.setattr(assistant_routes, "fetch_quotes_list", fake_batch)

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    assert resp.status_code == 200
    first = json.loads(resp.text.split("\n\n")[0].removeprefix("data: "))
    assert first["type"] == "meta"
    assert first["sources"] == [
        {"symbol": "600519", "name": "贵州茅台", "source": "tencent", "as_of": first["sources"][0]["as_of"]}
    ]
    assert first["sources"][0]["as_of"]  # 非空


def test_chat_route_includes_market_block(client, monkeypatch):
    fake = _FakeStream(["好的"])
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    captured: list = []
    orig = assistant_routes._build_messages

    def spy(req, market_block="", tools_enabled=False):
        msgs = orig(req, market_block, tools_enabled=tools_enabled)
        captured.append(msgs[0]["content"])
        return msgs

    monkeypatch.setattr(assistant_routes, "_build_messages", spy)
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [{"name": "贵州茅台", "code": "600519"}], "themes": []})

    async def fake_batch(hub, syms):  # 与真实 _batch_quotes(hub, symbols) 同签名
        return [_mk_quote()]

    # S2-4：批量取数实现已上移到 services/quote_enrich（不再依赖路由私有名）。
    # 打桩点跟着导入方走——assistant.py 是模块级 `from ... import`，名字绑在它自己的全局里。
    monkeypatch.setattr(assistant_routes, "fetch_quotes_list", fake_batch)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    assert resp.status_code == 200
    assert "## 实时数据快照" in captured[0] and "600519" in captured[0]


# ---------------------------------------------------------------- 接地校验 P2-F


def _mock_snapshot(client, monkeypatch):
    """注入茅台快照 → market_block 进入证据池。"""
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [{"name": "贵州茅台", "code": "600519"}], "themes": []})

    async def fake_batch(hub, syms):
        return [_mk_quote()]  # 现价 1500.5 / 涨跌幅 2.35%

    # S2-4：批量取数实现已上移到 services/quote_enrich（不再依赖路由私有名）。
    # 打桩点跟着导入方走——assistant.py 是模块级 `from ... import`，名字绑在它自己的全局里。
    monkeypatch.setattr(assistant_routes, "fetch_quotes_list", fake_batch)


def test_chat_route_grounding_flags_fabricated_number(client, monkeypatch):
    """回答引用快照里不存在的价格 → grounding 事件带 EVIDENCE_NOT_FOUND。"""
    monkeypatch.setattr(assistant_routes, "_open_stream",
                        lambda msgs: _FakeStream(["贵州茅台现价 1888.8 元，走势强劲。"]))
    _mock_snapshot(client, monkeypatch)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    evs = _parse_sse(resp.text)
    g = [e for e in evs if e.get("type") == "grounding"]
    assert len(g) == 1
    assert g[0]["violations"], "编造价格 1888.8 必须被判违例"
    assert any(v["code"] == "EVIDENCE_NOT_FOUND" and "1888.8" in v["detail"]
               for v in g[0]["violations"])
    # 非阻断：done 仍在最后，违例事件在 done 之前
    assert evs[-1] == {"type": "done"}
    assert evs.index(g[0]) < len(evs) - 1


def test_chat_route_grounding_silent_when_supported(client, monkeypatch):
    """回答数字全部有据（1500.5 在快照里）→ 无 grounding 事件（零噪音）。"""
    monkeypatch.setattr(assistant_routes, "_open_stream",
                        lambda msgs: _FakeStream(["贵州茅台现价 1500.5 元。"]))
    _mock_snapshot(client, monkeypatch)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    evs = _parse_sse(resp.text)
    assert not [e for e in evs if e.get("type") == "grounding"]
    assert evs[-1] == {"type": "done"}


def test_chat_semantic_verify_shadow_runs_for_public_market_evidence(client, monkeypatch):
    """Universal verifier may run only when the answer was built from public evidence."""
    captured = []
    monkeypatch.setattr(settings, "jev_assistant_verify_mode", "shadow")
    monkeypatch.setattr(
        assistant_routes,
        "_open_stream",
        lambda msgs: _FakeStream(["贵州茅台现价 1500.5 元。"]),
    )
    _mock_snapshot(client, monkeypatch)
    monkeypatch.setattr(assistant_routes, "_build_extra_context", lambda _req: "")
    monkeypatch.setattr(
        assistant_routes,
        "_run_assistant_semantic_verify",
        lambda payload: captured.append(dict(payload)),
    )

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    evs = _parse_sse(resp.text)
    assert evs[-1] == {"type": "done"}
    assert len(captured) == 1
    assert captured[0]["claims"] == ["贵州茅台现价 1500.5 元"]
    assert "1500.5" in "\n".join(captured[0]["evidence"])


def test_chat_semantic_verify_skips_when_private_extra_context_exists(client, monkeypatch):
    """Claim text itself can reveal private context, so any private extra block disables export."""
    captured = []
    monkeypatch.setattr(settings, "jev_assistant_verify_mode", "shadow")
    monkeypatch.setattr(
        assistant_routes,
        "_open_stream",
        lambda msgs: _FakeStream(["贵州茅台现价 1500.5 元。"]),
    )
    _mock_snapshot(client, monkeypatch)
    monkeypatch.setattr(
        assistant_routes,
        "_build_extra_context",
        lambda _req: "PRIVATE_POSITION_CONTEXT_SHOULD_NOT_LEAVE_PROCESS",
    )
    monkeypatch.setattr(
        assistant_routes,
        "_run_assistant_semantic_verify",
        lambda payload: captured.append(dict(payload)),
    )

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    assert _parse_sse(resp.text)[-1] == {"type": "done"}
    assert captured == [{}]


def test_chat_semantic_verify_skips_when_request_itself_is_private(client, monkeypatch):
    """User-provided portfolio/account wording can leak through the generated claim."""
    captured = []
    monkeypatch.setattr(settings, "jev_assistant_verify_mode", "shadow")
    monkeypatch.setattr(
        assistant_routes,
        "_open_stream",
        lambda msgs: _FakeStream(["贵州茅台现价 1500.5 元。"]),
    )
    _mock_snapshot(client, monkeypatch)
    monkeypatch.setattr(assistant_routes, "_build_extra_context", lambda _req: "")
    monkeypatch.setattr(
        assistant_routes,
        "_run_assistant_semantic_verify",
        lambda payload: captured.append(dict(payload)),
    )

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "我的持仓贵州茅台现在多少？"}],
    })
    assert _parse_sse(resp.text)[-1] == {"type": "done"}
    assert captured == [{}]


def test_chat_semantic_verify_skips_when_deterministic_grounding_already_failed(client, monkeypatch):
    """Do not pay Jev twice when deterministic grounding already proves a violation."""
    captured = []
    monkeypatch.setattr(settings, "jev_assistant_verify_mode", "shadow")
    monkeypatch.setattr(
        assistant_routes,
        "_open_stream",
        lambda msgs: _FakeStream(["贵州茅台现价 1888.8 元。"]),
    )
    _mock_snapshot(client, monkeypatch)
    monkeypatch.setattr(
        assistant_routes,
        "_run_assistant_semantic_verify",
        lambda payload: captured.append(dict(payload)),
    )

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "贵州茅台现在多少？"}],
    })
    evs = _parse_sse(resp.text)
    assert [e for e in evs if e.get("type") == "grounding"]
    assert captured == [{}]


def test_chat_route_grounding_skipped_without_evidence(client, monkeypatch):
    """没有注入任何数据时跳过校验（无基准即校验=全盘误杀，宁缺勿滥）。

    2026-09-09 起必须显式清空全部上下文源：autogen 让真实库每天都有「最近精选」，
    _build_extra_context/build_market_context 不 mock 的话会读到真数据，
    接地校验被触发——本测试验证的是「无基准跳过」分支，不是集成。
    """
    monkeypatch.setattr(assistant_routes, "_open_stream",
                        lambda msgs: _FakeStream(["今天涨了 88.8 个点，听起来不错 38 家。"]))
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [], "themes": []})  # 无字典 → 无快照
    monkeypatch.setattr(assistant_routes, "_build_extra_context", lambda _req: "")  # 无持仓/精选/事件

    async def _no_tools(*a, **k):
        return None

    monkeypatch.setattr(assistant_routes, "_tool_context", _no_tools)

    async def _no_market(*a, **k):
        return ("", [])

    monkeypatch.setattr(assistant_routes, "build_market_context", _no_market)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "随便聊聊"}],
    })
    evs = _parse_sse(resp.text)
    assert not [e for e in evs if e.get("type") == "grounding"]
    assert evs[-1] == {"type": "done"}


# ---------------------------------------------------------------- 2026-09-11 扩容
# 用户实测：「明明系统里都有的数据，助手答『我没有』」。根因两条——
# ① 提示词的能力边界写死了无工具时代的事实，与工具清单自相矛盾；
# ② 分时/K线/资金流/个股龙虎榜/公告财务/指数宽度/题材梯队等**工具覆盖缺口**。
# 下列用例分别把这两条钉住。


def test_system_map_matches_frontend_nav_whitelist():
    """助手「系统地图」的页面集合必须与前端导航白名单完全一致（双向）。

    这是**防认知漂移的机制**，不是一次性检查：地图此前是 prompt.py 里的手写字符串，
    实测已漂移到「每日精选 /picks」「研究 /research」——这两条路由早已 302 下线。
    模型照过期地图指路，用户按图索骥必然找不到。
    现在地图只有一处定义（`app/assistant/system_map.py`），这里按住它与前端对齐。
    """
    from app.assistant.system_map import MODULES, module_paths

    nav_src = (FRONTEND / "apps/web/lib/nav-targets.ts").read_text(encoding="utf-8")
    block = re.search(r"NAV_ALLOWED_PATHS[^=]*=\s*\[(.*?)\]", nav_src, re.S)
    assert block, "未找到前端 NAV_ALLOWED_PATHS（结构变了，同步更新本测试）"
    # 去掉行尾 // 注释再抽字符串，避免把注释里的路径当成白名单
    raw = "\n".join(line.split("//")[0] for line in block.group(1).splitlines())
    frontend_paths = set(re.findall(r'"([^"]+)"', raw))

    assert set(module_paths()) == frontend_paths, (
        "助手系统地图与前端导航白名单不一致 —— "
        f"仅后端有：{sorted(set(module_paths()) - frontend_paths)}；"
        f"仅前端有：{sorted(frontend_paths - set(module_paths()))}"
    )
    # 每个模块的说明不许为空（空说明 = 地图看着有、其实问不出所以然）
    for m in MODULES:
        assert m.name and m.detail, f"{m.path} 缺少名称或说明"


def test_system_map_reaches_system_prompt():
    """地图要真的渲染进提示词（只写在常量里不算），且不含已下线路由。"""
    from app.assistant.prompt import build_system_prompt

    p = build_system_prompt(None, tools_enabled=True)
    for path in ("/workbench", "/tape", "/market", "/hunting", "/agent"):
        assert path in p, f"提示词缺少现役页面：{path}"
    for dead in ("/picks", "/research", "/intraday"):
        assert f" {dead}：" not in p and f" {dead} " not in p, f"提示词仍在指向已下线路由：{dead}"


def test_prompt_capability_never_denies_when_tools_on():
    """启用工具时，提示词里不许再出现「你没有实时数据/资金流/龙虎榜」这类自我否认。

    这是本次问题的**直接根因**：模型信了提示词里那句"你没有"，
    于是答"我没有龙虎榜数据"，而 longhu 工具就在同一份提示词的工具清单里。
    """
    from app.assistant.prompt import build_system_prompt

    on = build_system_prompt(None, tools_enabled=True)
    off = build_system_prompt(None, tools_enabled=False)
    for denied in ("默认你**没有实时行情", "资金流、龙虎榜等任何实时数据"):
        assert denied not in on, f"启用工具时仍含自我否认文案：{denied}"
    assert "默认你**没有实时行情" in off, "关掉工具时必须恢复诚实的无数据声明"
    # 有工具时要点名"先取数再下结论"，并真的带上工具清单
    assert "先调用工具再回答" in on
    assert "{{tool:capital_flow|" in on
    assert "可用工具" not in off


def test_market_context_block_scope_follows_tools_flag():
    """快照块头文案必须与工具开关一致（曾经的"你都没有"是第二处自相矛盾）。

    同一个答复里，「工具清单」说能查龙虎榜、「快照块」说没有龙虎榜——
    模型在冲突指令下选了后者。两处必须同向。
    """
    import asyncio

    from app.assistant.context import build_market_context
    from app.schemas.market import Quote

    async def _get(_syms):
        return [Quote(symbol="600519", name="贵州茅台", price=1500.0, source="tencent")]

    on, _ = asyncio.run(build_market_context(_get, ["600519"], True))
    off, _ = asyncio.run(build_market_context(_get, ["600519"], False))
    assert "可用工具" in on and "你都没有" not in on
    assert "你都没有" in off and "可用工具" not in off
    # 两版都必须保留溯源纪律
    assert "溯源纪律" in on and "溯源纪律" in off


def test_tool_labels_cover_every_spec():
    """进度提示与回执都靠 TOOL_LABELS；新增工具漏标签会静默显示英文名。"""
    from app.assistant.tools import TOOL_LABELS, TOOL_SPECS, tool_label

    missing = sorted(set(TOOL_SPECS) - set(TOOL_LABELS))
    assert not missing, f"这些工具没有中文短标签（进度提示会退化成英文键名）：{missing}"
    stale = sorted(set(TOOL_LABELS) - set(TOOL_SPECS))
    assert not stale, f"这些标签没有对应工具（已删的工具要连标签一起删）：{stale}"
    assert tool_label("longhu") == "龙虎榜"
    assert tool_label("不存在的工具") == "不存在的工具"  # 未登记不抛异常


def _ctx(provider, **kw):
    from app.assistant.tools import ToolContext

    return ToolContext(provider=provider, **kw)


def _call_tool(ctx, name, **args):
    """跑单个工具（名不与本文件既有 `_run(awaitable)` 冲突，2026-09-11 踩过一次）。"""
    import asyncio

    from app.assistant.tools import ToolCall, run_tool

    return asyncio.run(run_tool(ToolCall(name=name, args=args), ctx, cache=None))


def test_quotes_tool_accepts_index_prefix():
    """指数必须能查（sh000001）——裸 6 位仍是股票，带前缀才是指数（项目纪律）。"""
    from app.schemas.market import Quote

    class _P:
        async def get_quotes(self, codes):
            return [Quote(symbol=c, name="上证指数", price=3300.0, source="tencent") for c in codes]

    out = _call_tool(_ctx(_P()), "quotes", symbols="sh000001")
    assert "上证指数" in out and "sh000001" in out
    # 裸代码仍然按股票走词典校验
    bad = _call_tool(_ctx(_P(), known_symbols={"600519"}), "quotes", symbols="000001")
    assert "不在实体词典内" in bad


def test_minute_tool_summarizes_extremes_and_tail():
    """分时工具要把 240 个点压成「开/高/低/振幅/末段」，而不是把点全倒出来。"""
    pts = [
        {"ts": "2026-09-11 09:31", "price": 15.31, "source": "tencent"},
        {"ts": "2026-09-11 09:45", "price": 15.14, "source": "tencent"},
        {"ts": "2026-09-11 10:30", "price": 18.10, "source": "tencent"},
        {"ts": "2026-09-11 11:20", "price": 16.60, "source": "tencent"},
    ]

    class _P:
        async def get_minute_line(self, symbol):
            return pts

    out = _call_tool(_ctx(_P(), known_symbols=set()), "minute", symbol="603042")
    assert "开 15.31" in out
    assert "最高 18.1（2026-09-11 10:30）" in out
    assert "最低 15.14（2026-09-11 09:45）" in out
    assert "日内振幅 19.55%" in out  # (18.10-15.14)/15.14
    assert "tencent" in out
    assert out.count("\n") < 12, "分时工具不该把每个点都渲染出来"


def test_capital_flow_tool_formats_amount_and_streak():
    """个股资金流：元→亿/万换算 + 连续净流入天数（用户问『谁在买』的核心指标）。"""
    rows = [
        {"date": "2026-09-10", "close": 18.67, "change_pct": 5.2, "net_main": 1.2e8,
         "net_super": 8.0e7, "net_big": 4.0e7, "net_mid": -1.0e7, "net_small": -2.0e7,
         "source": "sina"},
        {"date": "2026-09-09", "close": 17.75, "change_pct": 3.1, "net_main": 3.0e7,
         "net_super": 2.0e7, "net_big": 1.0e7, "net_mid": 0.0, "net_small": 0.0,
         "source": "sina"},
        {"date": "2026-09-08", "close": 17.22, "change_pct": -1.4, "net_main": -5.0e7,
         "net_super": -3.0e7, "net_big": -2.0e7, "net_mid": 1.0e7, "net_small": 0.0,
         "source": "sina"},
    ]

    class _P:
        async def get_capital_flow(self, symbol, days):
            return rows[:days]

    out = _call_tool(_ctx(_P()), "capital_flow", symbol="603042", days="3")
    assert "主力净额 1.20 亿" in out
    assert "连续主力净流入 2 日" in out
    assert "个股**口径" in out or "个股" in out
    assert "sina" in out


def test_kline_tool_validates_timeframe_and_bounds_limit():
    from app.assistant.tools import TIMEFRAMES

    class _P:
        async def get_kline(self, symbol, timeframe):
            return [
                {"ts": f"bar-{i:03d}", "open": 10 + i, "high": 11 + i,
                 "low": 9 + i, "close": 10.5 + i, "volume": 1e6, "change_pct": 1.0,
                 "source": "tdx"}
                for i in range(40)
            ]

    bad = _call_tool(_ctx(_P()), "kline", symbol="600519", timeframe="2h")
    assert "参数不合法" in bad and "1d" in bad
    ok = _call_tool(_ctx(_P()), "kline", symbol="600519", timeframe=TIMEFRAMES[0], limit="99")
    assert "最近 30 根" in ok  # limit 夹到上限而不是报错
    assert ok.count("\n") <= 33, "明细行数必须被 limit 夹住"


def _synth_bars(n: int = 200) -> list[dict]:
    """合成日K（升序、含趋势反转以产生成交）；**不触网**。"""
    import math

    bars = []
    for i in range(n):
        close = 10.0 + 2.0 * math.sin(i / 9.0) + i * 0.01
        bars.append({
            "ts": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": close * 0.995, "high": close * 1.01,
            "low": close * 0.99, "close": close, "volume": 1e6,
        })
    return bars


def test_backtest_tool_flags_unknown_strategy_with_options():
    """策略名给错要说清**合法取值**（只回「不合法」模型没法自我纠正）。"""
    out = _call_tool(_ctx(object()), "backtest", symbol="600519", strategy="boll")
    assert "参数不合法" in out and "ma_cross" in out and "boll" in out


def test_backtest_tool_missing_strategy_lists_options():
    out = _call_tool(_ctx(object()), "backtest", symbol="600519")
    assert "参数不合法" in out and "双均线" in out  # 连注册表中文名一起给


def test_backtest_tool_reports_metrics_with_mandatory_caveats(monkeypatch):
    """核心纪律：指标可以给，但**三条口径必须一起给**——否则模型会把「样本内历史
    表现」讲成「这只票能赚钱」，这是本项目最危险的误读之一。"""
    import app.market.tdx_kline as tdx

    monkeypatch.setattr(tdx, "tdx_daily_bars", lambda symbol, count=500: _synth_bars(200))

    out = _call_tool(_ctx(object()), "backtest", symbol="600519",
                     strategy="ma_cross", bars="200")
    assert "日线回测" in out and "ma_cross" in out
    assert "区间收益" in out and "最大回撤" in out and "夏普" in out and "%" in out
    # —— 三条口径逐条钉死（注入验证：删任一条即红）
    assert "不构成买卖建议" in out       # ① 非建议
    assert "样本内" in out               # ② 未做样本外验证
    assert "未做参数优化" in out         # ② 参数未优化
    assert "不得据此外推" in out         # ③ 不得外推为选股依据
    # —— 数值呈现纪律（首版实测踩到）：
    assert "最大回撤 +" not in out, "回撤带 `+` 号会被读成「涨了」——方向恰好反了"
    assert re.search(r"胜率 \+", out) is None, "胜率是比率，不带正负号"
    assert re.search(r"平均持有 \d+\.\d{3}", out) is None, "浮点须截断（原样输出 8.461538）"


def test_backtest_tool_insufficient_bars_says_so(monkeypatch):
    """样本不足就**如实说不足**——绝不拿不够的样本硬算出一个数字。"""
    import app.market.tdx_kline as tdx

    monkeypatch.setattr(tdx, "tdx_daily_bars", lambda symbol, count=500: _synth_bars(30))

    out = _call_tool(_ctx(object()), "backtest", symbol="600519", strategy="ma_cross")
    assert "数据不足" in out and "30 根" in out
    assert "区间收益" not in out


def test_longhu_tool_symbol_branch_returns_seats_and_history():
    """个股龙虎榜：席位 + 上榜历史胜率（此前只有全市场榜，个股问不到）。"""
    class _P:
        async def get_longhu_detail(self, symbol, trade_date):
            return {
                "buy_seats": [{"seat": "机构专用", "seat_type": "机构", "buy": 2.0e8,
                               "sell": 0.0, "net": 2.0e8}],
                "sell_seats": [{"seat": "某某营业部", "seat_type": "游资", "buy": 0.0,
                                "sell": 1.0e8, "net": -1.0e8}],
            }

        async def get_longhu_history(self, symbol, limit=30):
            return [
                {"trade_date": "2026-09-10", "change_pct": 5.2, "net_buy": 1.0e8, "after_5d": 3.0},
                {"trade_date": "2026-08-20", "change_pct": -2.0, "net_buy": -2.0e7, "after_5d": -4.0},
            ]

    out = _call_tool(_ctx(_P()), "longhu", symbols="603042", date="2026-09-10")
    assert "机构专用" in out and "2.00 亿" in out
    assert "某某营业部" in out
    assert "T+5 均值 -0.50%" in out      # (3.0 + -4.0) / 2
    assert "胜率 50%" in out
    assert "不可相加" in out


def test_market_overview_tool_uses_hub_and_snapshot():
    class _Idx:
        def __init__(self, s, n, p, c):
            self.symbol, self.name, self.price, self.change_pct = s, n, p, c

    class _Hub:
        def get_indices(self):
            return [_Idx("000001", "上证指数", 3300.0, 0.42), _Idx("399001", "深证成指", 10500.0, -0.31)]

    class _Svc:
        def breadth_payload(self):
            return {
                "breadth": {"up": 2100, "down": 2600, "flat": 130, "limit_up": 38,
                            "limit_down": 6, "total": 4830, "total_amount": 8.9e11},
                "snapshot_age_seconds": 12.0,
            }

    out = _call_tool(_ctx(provider=object(), hub=_Hub(), snapshot_service=_Svc()), "market_overview")
    assert "上证指数" in out and "+0.42%" in out
    assert "涨 2100 / 跌 2600" in out
    assert "涨停 38 / 跌停 6" in out
    assert "8900.00 亿" in out
    assert "快照新鲜度：12.0 秒前" in out


def test_false_denial_detector():
    """认知缺口自曝：没调工具却说"我没有这项数据"要被抓到；正常表述不许误伤。

    价值在于**不需要用户投诉**就能积累缺口清单——以后新增数据源漏了工具，
    日志会自己报出来，而不是等用户发现"明明系统里有"。
    """
    from app.assistant.cognition import looks_like_false_denial

    # 用户实测的原话形态 → 命中
    assert looks_like_false_denial("我没有该股的分时明细、资金流向和龙虎榜数据")
    assert looks_like_false_denial("这部分行情我无法获取，建议到页面查看")
    # 调过工具再说没有 → 是真取不到，不是认知缺口
    assert not looks_like_false_denial("我没有该股的资金流数据", tools_used=["capital_flow"])
    # 正当声明不许误伤（缺数据类名词）
    assert not looks_like_false_denial("我不具备投资顾问资质，以下不构成投资建议")
    assert not looks_like_false_denial("我没有卖出建议")
    assert not looks_like_false_denial("")


def test_chat_route_logs_cognition_gap_when_no_tool_used(client, monkeypatch, caplog):
    """端到端：助手说"我没有龙虎榜数据"却一次工具没调 → 日志留痕（不改答案）。"""
    monkeypatch.setattr(
        assistant_routes, "_open_stream",
        lambda msgs: _FakeStream(["我没有该股的资金流向和龙虎榜数据，建议到页面查看。"]),
    )
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [], "themes": []})
    monkeypatch.setattr(assistant_routes, "_build_extra_context", lambda _req: "")
    monkeypatch.setattr(settings, "assistant_tools_enabled", True)

    from app.assistant.tools import ToolContext

    async def fake_ctx(request, known=None):
        return ToolContext(provider=object(), known_symbols=set())

    monkeypatch.setattr(assistant_routes, "_tool_context", fake_ctx)

    with caplog.at_level("WARNING", logger="app.api.routes.assistant"):
        resp = client.post("/api/assistant/chat", json={
            "messages": [{"role": "user", "content": "华脉科技的资金流向和龙虎榜？"}],
        })
    assert resp.status_code == 200
    evs = _parse_sse(resp.text)
    assert evs[-1] == {"type": "done"}
    assert any("认知缺口" in r.message for r in caplog.records), caplog.text
    # 只留痕，不改用户看到的答案
    assert "我没有该股的资金流向" in "".join(
        e["text"] for e in evs if e.get("type") == "delta"
    )


def test_news_tool_lists_active_events_with_basis():
    """全网资讯快讯工具（用户实测：助手答「我没有全网新闻/资讯数据源」——系统其实有）。

    事件面板的数据源就是它；输出必须保留方向行的「依据」，且不得写成买卖建议。
    """
    from datetime import datetime

    class _Dir:
        def __init__(self):
            # 实例属性而非类属性：_rec 读 __dict__（真实的是 SQLAlchemy 实例，同理）
            self.target_type = "theme"
            self.target = "商业航天"
            self.direction = "利好"
            self.strength = 3
            self.chain = "政策→产业"
            self.basis = "政策表述首次出现「加快发展」"

    class _Ev:
        def __init__(self):
            self.title = "北京：加快发展商业航天产业"
            self.summary = "提出打造商业航天产业集群"
            self.source = "cls"
            self.published_at = datetime(2026, 9, 11, 10, 30)
            self.directions = [_Dir()]

    class _Store:
        def list_events(self, *, active_only=True, limit=30):
            assert active_only is True
            return [_Ev()][:limit]

    out = _call_tool(_ctx(provider=object(), event_store=_Store()), "news", limit="5")
    assert "北京：加快发展商业航天产业" in out
    assert "商业航天" in out and "利好" in out
    assert "依据：政策表述首次出现" in out
    assert "解释版本：未知" in out
    assert "不构成买卖建议" in out

    current = _Ev()
    current.interpretation_ref = {
        "event_id": 1, "version_id": 7, "observation_id": 12,
        "available_at": "2026-09-11 10:35:00", "state": "accepted",
    }

    class _VersionStore:
        def list_events(self, *, active_only=True, limit=30):
            return [current][:limit]

    versioned = _call_tool(_ctx(provider=object(), event_store=_VersionStore()), "news")
    assert "解释版本：7｜可见：2026-09-11 10:35:00｜状态：accepted" in versioned
    assert "解释版本：未知" not in versioned

    current.directions[0].matched_by = "llm_aux"
    hypothesis = _call_tool(_ctx(provider=object(), event_store=_VersionStore()), "news")
    assert "待验证假设" in hypothesis

    class _CrowdedStore:
        def list_events(self, *, active_only=True, limit=30):
            return [_Ev() for _ in range(limit)]

    crowded = _call_tool(_ctx(provider=object(), event_store=_CrowdedStore()), "news", limit="30")
    assert "不构成买卖建议" in crowded

    # 事件库缺失时如实说明，不编造
    missing = _call_tool(_ctx(provider=object()), "news")
    assert "不可用" in missing and "不要编造" in missing


def test_chat_route_emits_status_progress_events(client, monkeypatch):
    """取数与思考期间必须有 progress 事件——否则界面静止十几秒，用户以为卡死。"""
    rounds = [["{{tool:limit_up|date=2026-09-04}}"], ["今日涨停 ", "38 家"]]
    opened: list = []

    def factory(msgs):
        opened.append(msgs)
        return _FakeStream(list(rounds[len(opened) - 1]))

    monkeypatch.setattr(assistant_routes, "_open_stream", factory)

    from app.assistant.tools import ToolContext

    class _P:
        async def get_limit_up_pool(self, trade_date):
            return [{"symbol": "600519", "name": "贵州茅台", "consecutive_boards": 1,
                     "change_pct": 10.0, "reason": "白酒"}]

    async def fake_ctx(request, known=None):
        return ToolContext(provider=_P(), known_symbols=set(), trading_days={"2026-09-04"})

    monkeypatch.setattr(assistant_routes, "_tool_context", fake_ctx)
    monkeypatch.setattr(settings, "assistant_tools_enabled", True)

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "今天涨停池什么情况？"}],
    })
    evs = _parse_sse(resp.text)
    statuses = [e for e in evs if e.get("type") == "status"]
    assert statuses, "必须有 status 事件"
    assert statuses[0] == {"type": "status", "phase": "thinking"}
    tools_status = [s for s in statuses if s.get("phase") == "tools"]
    assert len(tools_status) == 1
    assert tools_status[0]["used"] == ["limit_up"]
    assert tools_status[0]["label"] == "涨停池", "进度提示要给中文标签，不是英文键名"
    # 顺序：thinking → tools 进度 → 回执 → done
    idx = {id(e): i for i, e in enumerate(evs)}
    assert idx[id(statuses[0])] < idx[id(tools_status[0])]
    assert idx[id(tools_status[0])] < idx[id([e for e in evs if e.get("type") == "tools"][0])]
    assert evs[-1] == {"type": "done"}


def test_chat_route_second_tool_round(client, monkeypatch):
    """取数后可**再取一批**（多跳提问）——此前硬编码只允许一轮。"""
    rounds = [
        ["{{tool:limit_up|date=2026-09-04}}"],
        ["{{tool:capital_flow|symbol=600519}}"],
        ["贵州茅台 600519 主力净流入 1.2 亿"],
    ]
    opened: list = []

    def factory(msgs):
        opened.append(msgs)
        return _FakeStream(list(rounds[len(opened) - 1]))

    monkeypatch.setattr(assistant_routes, "_open_stream", factory)

    from app.assistant.tools import ToolContext

    class _P:
        async def get_limit_up_pool(self, trade_date):
            return [{"symbol": "600519", "name": "贵州茅台", "consecutive_boards": 1,
                     "change_pct": 10.0, "reason": "白酒"}]

        async def get_capital_flow(self, symbol, days):
            return [{"date": "2026-09-10", "net_main": 1.2e8, "source": "sina"}]

    async def fake_ctx(request, known=None):
        return ToolContext(provider=_P(), known_symbols=set(), trading_days={"2026-09-04"})

    monkeypatch.setattr(assistant_routes, "_tool_context", fake_ctx)
    monkeypatch.setattr(settings, "assistant_tools_enabled", True)

    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "今天涨停池怎么样，茅台资金流呢？"}],
    })
    evs = _parse_sse(resp.text)
    assert len(opened) == 3, "应跑三轮：取数→再取数→成文"
    used = [e["used"] for e in evs if e.get("type") == "tools"]
    assert used == [["limit_up"], ["capital_flow"]]
    # 第三轮（成文）的完整上下文里必须同时带着两批取数结果，
    # 否则模型看不到自己第一次取的数，只能拿最近一批硬答。
    final_ctx = "\n".join(m["content"] for m in opened[2])
    assert "贵州茅台" in final_ctx and "涨停池" in final_ctx
    assert "主力净流入" in final_ctx
    # 工具标记绝不外泄
    joined = "".join(e["text"] for e in evs if e.get("type") == "delta")
    assert "{{tool:" not in joined


def test_chat_input_budget_blocks_before_opening_stream(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_model_max_input_chars", 1)
    monkeypatch.setattr(
        assistant_routes, "_open_stream",
        lambda msgs: pytest.fail("input budget must reject before model stream opens"),
    )
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "this is over one char"}],
    })
    events = _parse_sse(resp.text)
    assert not [e for e in events if e.get("type") == "delta"]
    err = [e for e in events if e.get("type") == "error"]
    assert err and "输入" in err[0]["message"] and "上限" in err[0]["message"]
    assert events[-1] == {"type": "done"}


def test_chat_output_budget_blocks_delta_before_user_and_records_failed_receipt(client, monkeypatch):
    from app.core.config import settings
    from app.services import agent_budget

    monkeypatch.setattr(settings, "agent_model_max_input_chars", 250_000)
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 3)
    fake = _FakeStream(["ABCD"])
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    events = _parse_sse(resp.text)
    assert not [e for e in events if e.get("type") == "delta" and e.get("text") == "ABCD"]
    err = [e for e in events if e.get("type") == "error"]
    assert err and "输出" in err[0]["message"] and "上限" in err[0]["message"]
    assert fake.closed is True
    rows = [r for r in agent_budget.recent_usage(limit=20) if r["purpose"] == "assistant.chat"]
    assert rows and rows[0]["state"] == "failed"
    assert rows[0]["error_kind"] == "output_budget_exceeded"
    assert rows[0]["output_chars"] == 4
