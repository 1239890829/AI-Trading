"""东财快讯流测试（hotspot-pipeline G1）。网络全 mock（FakeClient 脚本化）。

项目无 pytest-asyncio：async 路径统一 `_run(coro)=asyncio.run(coro)` 同步包装。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.db import get_engine, get_session_factory
from app.events.store import EventStore
from app.news import flash
from app.news.flash_state import FlashCursor


def _run(coro):
    return asyncio.run(coro)


class FakeResp:
    def __init__(self, payload: dict):
        self._p = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._p


class FakeClient:
    """按序脚本化 outcomes 队列（同 board_flow 测试姿势）。"""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.hosts: list[str] = []

    async def get(self, url: str, params=None):
        self.hosts.append(url.split("/")[2])
        outcome = self.outcomes.pop(0) if self.outcomes else RuntimeError("exhausted")
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResp(outcome)

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _create_tables():
    from app.models.watchlist import Base as _B

    _B.metadata.create_all(get_engine())
    yield


@pytest.fixture
def fresh_cursor(monkeypatch) -> FlashCursor:
    cur = FlashCursor()
    monkeypatch.setattr(flash, "_FLASH_CURSOR", cur)
    return cur


def _payload(n: int = 2) -> dict:
    items = [
        {
            "code": f"C{i}",
            "title": f"测试快讯{i}：液冷服务器订单落地",
            "summary": "摘要文本",
            "showTime": f"2026-09-07 10:0{i}:00",
        }
        for i in range(n)
    ]
    return {"code": "1", "data": {"fastNewsList": items, "sortEnd": "x"}}


def _store() -> EventStore:
    return EventStore(get_session_factory())


def _app(store: EventStore) -> SimpleNamespace:
    return SimpleNamespace(event_store=store)


# --------------------------------------------------------------- 解析

def test_parse_item_fields_and_garbage():
    ok = flash._parse_item({
        "title": "标题",
        "summary": "摘要",
        "code": "C1",
        "showTime": "2026-09-07 11:11:28",
    })
    assert ok["title"] == "标题" and ok["code"] == "C1"
    assert ok["show_time"] is not None and ok["show_time"].tzinfo is not None
    # 缺标题 → 丢弃（显式，不臆造）
    assert flash._parse_item({"summary": "只有摘要"}) is None
    # showTime 异常 → None 时刻（指纹仍可去重）
    bad_time = flash._parse_item({"title": "T", "showTime": "garbage"})
    assert bad_time is not None and bad_time["show_time"] is None


def test_parse_item_extracts_a_share_symbols():
    """stockList → A 股代码（0./1.）；板块 90. / 基金 150. 显式跳过（2026-09-10 新增）。"""
    row = flash._parse_item({
        "title": "博盈特焊：海外订单充裕 预计未来3至5年HRSG供不应求",
        "code": "202609103870396448",
        "stockList": ["0.301468", "90.BK0800", "150.012322", "1.688496", "垃圾", "0.301468"],
    })
    assert row["symbols"] == ["301468", "688496"], "只取 A 股 + 保序去重"
    # 无 stockList → 空（不臆造归属）
    assert flash._parse_item({"title": "T"})["symbols"] == []


def test_parse_item_extracts_board_codes():
    """stockList → 东财板块代码（90.BKxxxx → BKxxxx），保序去重（retro P0-3 后半）。"""
    row = flash._parse_item({
        "title": "某板块消息",
        "code": "C1",
        "stockList": ["90.BK0800", "90.BK0800", "0.301468", "150.012322", "90.BK0157"],
    })
    assert row["board_codes"] == ["BK0800", "BK0157"]
    assert row["symbols"] == ["301468"]


def test_board_theme_map_exact_align_only(monkeypatch):
    """板块代码 → ths 题材名：同名/词干精确对齐才映射，无对应则跳过（不模糊猜）。"""
    async def fake_list(kind):
        return [
            {"board_code": "BK0800", "name": "房地产开发"},
            {"board_code": "BK1024", "name": "绿色电力"},
            {"board_code": "BK0157", "name": "某东财独有板块"},
        ], []

    import app.market.board_flow as bf
    monkeypatch.setattr(bf, "get_board_list", fake_list)
    themes = ["房地产开发", "绿色电力概念", "算力"]  # 绿色电力概念 词干=绿色电力
    m = _run(flash._board_theme_map(SimpleNamespace(), themes))
    assert m["BK0800"] == {"theme_name": "房地产开发", "board_name": "房地产开发", "board_code": "BK0800"}
    assert m["BK1024"] == {"theme_name": "绿色电力概念", "board_name": "绿色电力", "board_code": "BK1024"}, "词干对齐应落到 ths 目录名"
    assert "BK0157" not in m, "无 ths 对应 → 不映射（跨源口径不臆造）"


def test_to_event_passes_first_symbol_as_source_symbol():
    """公司快讯挂个股：首个 A 股 → source_symbol（retro P0-3 前半）。"""
    ev = flash._to_event({
        "title": "博盈特焊：海外订单充裕",
        "summary": None, "code": "C9", "url": None, "show_time": None,
        "symbols": ["301468", "688496"],
    })
    assert ev["source_symbol"] == "301468"
    assert ev["source_symbols"] == ["301468", "688496"]
    assert ev["source_item_id"] == "C9"
    # 无关联标的 → None（显式空，不猜）
    ev2 = flash._to_event({
        "title": "某宏观消息", "summary": None, "code": "C10",
        "url": None, "show_time": None, "symbols": [],
    })
    assert ev2["source_symbol"] is None


# --------------------------------------------------------------- 配置与多频道


def test_configured_columns_parsing(monkeypatch):
    """逗号分隔解析：去重、丢弃非法项、空值回退 [100]（绝不静默不拉）。"""
    from app.core import config as _cfg

    monkeypatch.setattr(_cfg.settings, "flash_news_columns", "100, 104,bad,100")
    assert flash.configured_columns() == [100, 104]
    monkeypatch.setattr(_cfg.settings, "flash_news_columns", "  ")
    assert flash.configured_columns() == [100]


def test_fetch_multi_merges_and_dedupes(monkeypatch):
    """多频道并发：按 code 去重；单频道失败不拖累其他。"""
    calls: list[int] = []

    async def fake(**kwargs):
        calls.append(kwargs["column"])
        if kwargs["column"] == 101:
            return [{"title": "宏观", "code": "A", "symbols": []}]
        return [
            {"title": "公司", "code": "B", "symbols": []},
            {"title": "宏观", "code": "A", "symbols": []},   # 与 101 重复 → 去
        ]

    monkeypatch.setattr(flash, "fetch_fast_news", fake)
    out = _run(flash.fetch_fast_news_multi([100, 101]))
    assert sorted(calls) == [100, 101]
    assert [p["code"] for p in out] == ["B", "A"]


def test_fetch_multi_all_fail_returns_none(monkeypatch):
    """全频道失败 → None（显式失败，游标据此记 last_ok=False）。"""
    async def fail(**kwargs):
        return None

    monkeypatch.setattr(flash, "fetch_fast_news", fail)
    assert _run(flash.fetch_fast_news_multi([100, 101])) is None


# --------------------------------------------------------------- 拉取：双域 failover

def test_fetch_failover_to_backup_host(monkeypatch):
    fake = FakeClient([
        RuntimeError("primary down"),          # 主域失败
        _payload(1),                            # 备域成功
    ])
    monkeypatch.setattr(flash, "_HTTP", fake)
    items = _run(flash.fetch_fast_news())
    assert items is not None and len(items) == 1
    assert fake.hosts == ["np-weblist.eastmoney.com", "np-listapi.eastmoney.com"]


def test_fetch_all_fail_returns_none(monkeypatch):
    fake = FakeClient([RuntimeError("e1"), RuntimeError("e2")])
    monkeypatch.setattr(flash, "_HTTP", fake)
    assert _run(flash.fetch_fast_news()) is None


def test_fetch_http_200_but_empty_then_ok(monkeypatch):
    """200 但 fastNewsList 空 = 数据异常，换域重试；两域都空才算失败（不静默当空列表）。"""
    empty = {"code": "1", "data": {"fastNewsList": []}}
    fake = FakeClient([empty, _payload(2)])
    monkeypatch.setattr(flash, "_HTTP", fake)
    items = _run(flash.fetch_fast_news())
    assert items is not None and len(items) == 2


def test_fetch_pagination_passes_cursor_and_stops(monkeypatch):
    """P0-2 多页：sortEnd 游标逐页传递、跨页去重、空页/空游标提前停。"""
    pages = [
        {"code": "1", "data": {"fastNewsList": [
            {"code": "A", "title": "标题A", "showTime": "2026-09-07 10:00:00"},
            {"code": "B", "title": "标题B", "showTime": "2026-09-07 09:59:00"},
        ], "sortEnd": "c2"}},
        {"code": "1", "data": {"fastNewsList": [
            {"code": "B", "title": "标题B", "showTime": "2026-09-07 09:59:00"},  # 跨页重复 → 去
            {"code": "C", "title": "标题C", "showTime": "2026-09-07 09:58:00"},
        ], "sortEnd": "c3"}},
        {"code": "1", "data": {"fastNewsList": [], "sortEnd": ""}},  # 空页 → 提前停
    ]
    sort_ends: list[str] = []

    class Paged:
        async def get(self, url, params=None):
            sort_ends.append((params or {}).get("sortEnd", ""))
            return FakeResp(pages[len(sort_ends) - 1])

        async def aclose(self):
            return None

    monkeypatch.setattr(flash, "_HTTP", Paged())
    items = _run(flash.fetch_fast_news(pages=5))
    assert [p["code"] for p in items] == ["A", "B", "C"], "翻页应并入并去重"
    assert sort_ends == ["", "c2", "c3"], f"游标应逐页传递，实际 {sort_ends}"


# --------------------------------------------------------------- 入库：指纹去重 + 心跳

def test_poll_once_dedupes_by_fingerprint(fresh_cursor, monkeypatch):
    async def fake_fetch(**kwargs):
        return [
            {"title": "同一标题快讯", "summary": None, "code": "C1", "show_time": None},
            {"title": "另一条快讯", "summary": None, "code": "C2", "show_time": None},
        ]
    monkeypatch.setattr(flash, "fetch_fast_news", fake_fetch)
    app = _app(_store())

    n1 = _run(flash.poll_once(app))
    assert n1 == 2
    n2 = _run(flash.poll_once(app))
    assert n2 == 0, "同一批重复拉取必须被指纹去重"
    snap = fresh_cursor.snapshot()
    assert snap["last_ok"] is True and snap["last_created"] == 0


def test_poll_once_all_fail_records_cursor(fresh_cursor, monkeypatch):
    async def fake_fail(**kwargs):
        return None
    monkeypatch.setattr(flash, "fetch_fast_news", fake_fail)
    assert _run(flash.poll_once(_app(_store()))) == 0
    snap = fresh_cursor.snapshot()
    assert snap["last_ok"] is False and snap["last_count"] == 0


def test_poll_once_single_item_failure_does_not_kill_round(fresh_cursor, monkeypatch):
    """单条入库抛异常 → 跳过该条，整轮其余照常入库（轮次永不炸）。"""
    rows = [
        {"title": "液冷服务器订单落地", "summary": None, "code": "bad", "show_time": None},
        {"title": "算力硬件持续走高", "summary": None, "code": "ok", "show_time": None},
    ]

    async def fake_fetch(**kwargs):
        return rows

    monkeypatch.setattr(flash, "fetch_fast_news", fake_fetch)
    real_to_event = flash._to_event

    # 签名随契约同步（2026-09-09）：poll_once 现传 theme_names 给 _to_event
    def exploding(p, *args, **kwargs):
        if p["code"] == "bad":
            raise ValueError("bad row")
        return real_to_event(p, *args, **kwargs)

    monkeypatch.setattr(flash, "_to_event", exploding)
    n = _run(flash.poll_once(_app(_store())))
    assert n == 1
