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


@pytest.fixture()
def client():
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


def test_prompt_nav_words_covered_by_frontend():
    """提示词点名的功能名必须都在前端别名表里，否则"可跳转"是空头支票。

    前端是识别与跳转的唯一实现方（lib/nav-targets.ts::NAV_ALIASES），后端只在
    提示词里点名。两边一旦漂移，模型会照提示词写、前端却识别不到 → 静默退化成
    普通文字。这里直接读前端源文件做交叉校验，不给漂移留窗口。
    """
    from app.assistant.prompt import NAV_WORDS, PROJECT_BRIEF

    nav_src = (FRONTEND / "apps/web/lib/nav-targets.ts").read_text(encoding="utf-8")
    block = re.search(r"NAV_ALIASES[^=]*=\s*\{(.*?)\n\};", nav_src, re.S)
    assert block, "未找到前端 NAV_ALIASES 表（结构变了，同步更新本测试）"
    frontend_keys = set(re.findall(r"^\s*([^\s:{]+):", block.group(1), re.M))
    missing = [w for w in NAV_WORDS if w not in frontend_keys]
    assert not missing, f"提示词点名但前端无别名（跳转会失效）：{missing}"
    # 提示词里必须真的带上这些词，而不是只写在常量里
    for w in NAV_WORDS:
        assert w in PROJECT_BRIEF, f"提示词未包含可跳转功能名：{w}"


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

    from app.api.routes import market as market_routes
    monkeypatch.setattr(market_routes, "_batch_quotes", fake_batch)

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

    from app.api.routes import market as market_routes
    monkeypatch.setattr(market_routes, "_batch_quotes", fake_batch)
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

    from app.api.routes import market as market_routes
    monkeypatch.setattr(market_routes, "_batch_quotes", fake_batch)


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


def test_chat_route_grounding_skipped_without_evidence(client, monkeypatch):
    """没有注入任何数据时跳过校验（无基准即校验=全盘误杀，宁缺勿滥）。"""
    monkeypatch.setattr(assistant_routes, "_open_stream",
                        lambda msgs: _FakeStream(["今天涨了 88.8 个点，听起来不错 38 家。"]))
    monkeypatch.setattr(assistant_routes, "_entity_payload",
                        lambda _req: {"stocks": [], "themes": []})  # 无字典 → 无快照
    resp = client.post("/api/assistant/chat", json={
        "messages": [{"role": "user", "content": "随便聊聊"}],
    })
    evs = _parse_sse(resp.text)
    assert not [e for e in evs if e.get("type") == "grounding"]
    assert evs[-1] == {"type": "done"}
