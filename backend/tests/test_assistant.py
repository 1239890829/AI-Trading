"""全局 AI 助手测试：流式客户端解析 + SSE 路由契约 + 实体字典。

不触网：流式客户端用桩（openai 用 MockTransport、cli 用假 Popen），
路由层替换 `_open_stream`。实体字典用临时 parquet 目录。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

import app.api.routes.assistant as assistant_routes
from app.core.llm_client import LLMError, stream_chat_completion
from app.core.config import settings

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

    def spy(req, market_block=""):
        msgs = orig(req, market_block)
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

from app.assistant.context import build_market_block, format_quote_line, resolve_symbols
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


def test_chat_route_includes_market_block(client, monkeypatch):
    fake = _FakeStream(["好的"])
    monkeypatch.setattr(assistant_routes, "_open_stream", lambda msgs: fake)
    captured: list = []
    orig = assistant_routes._build_messages

    def spy(req, market_block=""):
        msgs = orig(req, market_block)
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
