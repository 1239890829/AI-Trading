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

    def exploding(p):
        if p["code"] == "bad":
            raise ValueError("bad row")
        return real_to_event(p)

    monkeypatch.setattr(flash, "_to_event", exploding)
    n = _run(flash.poll_once(_app(_store())))
    assert n == 1
