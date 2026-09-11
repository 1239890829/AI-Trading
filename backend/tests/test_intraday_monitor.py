"""盘中情绪监控（sentiment P2 #14，参考 daben-review 三类纯规则事件）。

验收标准：
① 纯判定函数：炸板率薄池不判 / 高度板交集 / 指数窗口急杀；
② 休市时段不探测（push2ex 日期回退教训）；
③ 高度板炸板单拍即告警、文案含标的与处置提示；
④ 炸板率需连续 confirm 拍确认（单拍噪声不触发）、薄池重置计数；
⑤ 指数急杀窗口不足不判、破位触发；
⑥ 昨日池拉取失败 → 高度板检查跳过（unknown ≠ 无事件）；
⑦ 冷却期内同类事件不重复发；
⑧ /api/system/providers 暴露监控快照。

告警链 monkeypatch（_ensure_rule / AlertRepository / get_notifier_registry /
get_session_factory）：状态机与告警决策走真实代码，只隔离 SQLite 与通道 IO。
时钟用模块级 fake time 对象替换（不打全局 time，防冻结事件循环）。
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.schemas.market import LimitUpRecord
from app.sentiment import intraday_monitor as mon_mod
from app.sentiment.intraday_monitor import (
    SentimentMonitor,
    check_break_rate,
    check_high_board_breaks,
    check_index_plunge,
)
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛


#: 周三 10:30（连续竞价时段，2026-09-02 为真实交易日）
WED = datetime(2026, 9, 2, 10, 30, tzinfo=BJ_TZ)
SAT = datetime(2026, 9, 5, 10, 30, tzinfo=BJ_TZ)  # 周六
TRADE_DAYS = [date(2026, 9, 1), date(2026, 9, 2)]


def _rec(symbol: str, name: str | None = None, boards: int | None = None, td: date = date(2026, 9, 2)) -> LimitUpRecord:
    return LimitUpRecord(symbol=symbol, name=name, trade_date=td, source="ths", consecutive_boards=boards)


def _probe(m: SentimentMonitor, now=WED, **kw):
    return asyncio.run(m.probe_once(now=now, trade_days=TRADE_DAYS, **kw))


# ---------------------------------------------------------------- ① 纯判定函数


def test_check_break_rate():
    # 薄池不判定
    assert check_break_rate(5, 3) is None
    assert check_break_rate(0, 19) is None
    # 正常口径：炸板/(涨停+炸板)
    assert check_break_rate(60, 40) == 0.4
    assert check_break_rate(70, 30, threshold=0.40) < 0.4


def test_check_high_board_breaks():
    # 昨日池缺失 = 判不出（unknown），绝不冒充"无事件"
    assert check_high_board_breaks({"600001"}, None) is None
    # 昨日 ≥3 板且今日在炸板池 → 命中；低板/不在池不命中
    yst = {"600001": 4, "600002": 2, "600003": 3}
    assert check_high_board_breaks({"600001", "600002"}, yst) == ["600001"]
    assert check_high_board_breaks(set(), yst) == []


def test_check_index_plunge():
    now = 1_000_000.0
    # 窗口不足（开盘头 15 分钟）不判
    short = deque([(now - 60, 100.0), (now, 99.0)])
    assert check_index_plunge(short, threshold_pct=-0.8, now=now) is None
    # 窗口内跌 0.9% → 触发并返回实际值
    series = deque([(now - 900, 100.0), (now - 300, 99.5), (now, 99.1)])
    pct = check_index_plunge(series, threshold_pct=-0.8, now=now)
    assert pct is not None and pct <= -0.8
    # 窗口内只跌 0.2% → 不触发
    series2 = deque([(now - 900, 100.0), (now, 99.8)])
    assert check_index_plunge(series2, threshold_pct=-0.8, now=now) is None
    # 基准点取窗口内最早的合格点（更早的过期点不参与）
    series3 = deque([(now - 2000, 200.0), (now - 900, 100.0), (now, 99.0)])
    assert check_index_plunge(series3, threshold_pct=-0.8, now=now) == pytest.approx(-1.0)


# ---------------------------------------------------------------- 打桩


class FakeProvider:
    name = "fake"

    def __init__(self, today_pool=None, break_pool=None, yst_pool=None, yst_error: Exception | None = None):
        self.today_pool = today_pool or []
        self.break_pool = break_pool or []
        self.yst_pool = yst_pool or []
        self.yst_error = yst_error
        self.index_quotes: dict[str, float] = {}
        self.calls = 0

    async def get_limit_up_pool(self, trade_date):
        self.calls += 1
        if trade_date == date(2026, 9, 1):
            if self.yst_error is not None:
                raise self.yst_error
            return list(self.yst_pool)
        return list(self.today_pool)

    async def get_limit_break_pool(self, trade_date):
        return list(self.break_pool)

    async def get_quotes(self, symbols):
        return [
            SimpleNamespace(symbol=s, name=s, price=self.index_quotes.get(s))
            for s in symbols
            if self.index_quotes.get(s)
        ]


class FakeRepo:
    def __init__(self, session_factory=None):
        self.events = []

    def record_trigger(self, rule_id, symbol, trigger_value, threshold, snapshot=None, delivered_channels=None):
        ev = SimpleNamespace(id=len(self.events) + 1, rule_id=rule_id, symbol=symbol, trigger_value=trigger_value, threshold=threshold, snapshot=snapshot)
        self.events.append(ev)
        return ev

    def update_event_channels(self, event_id, channels):
        self.channels = channels


class FakeRegistry:
    def __init__(self):
        self.dispatched = []

    async def dispatch(self, event, rule):
        self.dispatched.append((event, rule))
        return ["log"]


def _capture(monkeypatch):
    repo, registry = FakeRepo(), FakeRegistry()
    rule = SimpleNamespace(id=9, name=mon_mod.SENTIMENT_MONITOR_RULE_NAME)
    monkeypatch.setattr(mon_mod, "_ensure_rule", lambda sf: rule)
    monkeypatch.setattr(mon_mod, "AlertRepository", lambda sf: repo)
    monkeypatch.setattr(mon_mod, "get_notifier_registry", lambda: registry)
    monkeypatch.setattr(mon_mod, "get_session_factory", lambda: None)
    return repo, registry


# ---------------------------------------------------------------- ②③④⑤⑥⑦ 状态机


def test_market_closed_skips_probe(monkeypatch):
    _capture(monkeypatch)
    prov = FakeProvider()
    m = SentimentMonitor(provider=prov)
    assert _probe(m, now=SAT)["state"] == "idle"
    assert prov.calls == 0


def test_high_board_break_alert(monkeypatch):
    """③ 昨日 3 连板今日炸板 → 单拍告警，文案含标的/板数/处置。"""
    repo, registry = _capture(monkeypatch)
    prov = FakeProvider(
        today_pool=[_rec("600100")],
        break_pool=[_rec("600001", "高位妖股"), _rec("600900")],
        yst_pool=[_rec("600001", boards=3), _rec("600900", boards=1)],
    )
    m = SentimentMonitor(provider=prov)
    snap = _probe(m)
    assert snap["last_high_board_breaks"] == ["600001"]
    assert snap["alerts_fired"] == 1 and len(repo.events) == 1
    text = repo.events[0].snapshot["text"]
    assert "600001" in text and "昨 3 板" in text and "处置" in text
    assert registry.dispatched, "通道未分发"


def test_break_rate_needs_confirm_and_thin_pool_resets(monkeypatch):
    """④ 炸板率首拍达阈只计数；连续 2 拍才告警；薄池重置计数。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(
        today_pool=[_rec(f"6001{i:02d}") for i in range(60)],
        break_pool=[_rec(f"3001{i:02d}") for i in range(40)],
    )
    m = SentimentMonitor(provider=prov, break_rate_confirm=2)
    snap = _probe(m)  # rate=0.4 达阈，第 1 拍
    assert snap["last_break_rate"] == 0.4 and snap["break_rate_consecutive"] == 1
    assert not repo.events
    _probe(m)  # 第 2 拍 → 告警
    assert len(repo.events) == 1 and "炸板率" in repo.events[0].snapshot["text"]
    # 薄池（总样本 < 20）：计数清零，不判不告警
    prov.today_pool = [_rec("600199")]
    prov.break_pool = [_rec("300199"), _rec("300299")]
    snap = _probe(m)
    assert snap["last_break_rate"] is None and snap["break_rate_consecutive"] == 0
    assert len(repo.events) == 1


def test_index_plunge_alert_after_window(monkeypatch):
    """⑤ 上证 15 分钟累计跌 0.9%（阈 0.8%）→ 告警；窗口建立前不判。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider()
    m = SentimentMonitor(provider=prov)
    fake = SimpleNamespace(time=lambda: 1_000_000.0)
    monkeypatch.setattr(mon_mod, "time", fake)
    price = 100.0
    snaps = []
    for i in range(10):
        prov.index_quotes = {"sh000001": price, "sz399006": price}
        snaps.append(_probe(m))
        fake.time = lambda t=1_000_000.0 + (i + 1) * 60: t  # 每拍 +60s
        price *= 0.999  # 每拍 -0.1%
    # 窗口未满 15 分钟：无指数告警
    assert not [e for e in repo.events if e.snapshot["kind"] == "index_plunge"]
    # 继续推进到窗口满（累计跌约 1%）
    for i in range(10):
        prov.index_quotes = {"sh000001": price, "sz399006": price}
        snap = _probe(m)
        fake.time = lambda t=1_000_000.0 + (20 + i) * 60: t
        price *= 0.999
        if [e for e in repo.events if e.snapshot["kind"] == "index_plunge"]:
            break
    plunge = [e for e in repo.events if e.snapshot["kind"] == "index_plunge"]
    assert plunge, "15 分钟急杀未触发"
    assert "上证指数" in plunge[0].snapshot["text"]
    assert snap["last_index_pcts"]["sh000001"] is not None and snap["last_index_pcts"]["sh000001"] < 0


def test_yst_pool_failure_skips_high_board(monkeypatch):
    """⑥ 昨日池拉取失败 → 高度板判 unknown（None），不误报也不崩溃。"""
    _capture(monkeypatch)
    prov = FakeProvider(
        today_pool=[_rec("600100")],
        break_pool=[_rec("600001", "高位妖股")],
        yst_pool=[_rec("600001", boards=3)],
        yst_error=RuntimeError("yst down"),
    )
    m = SentimentMonitor(provider=prov)
    snap = _probe(m)
    assert snap["last_high_board_breaks"] is None
    assert snap["alerts_fired"] == 0
    assert snap["state"] == "ok"


def test_cooldown_suppresses_repeat(monkeypatch):
    """⑦ 高度板连续多拍在炸板池 → 冷却期内不重复发事件。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(
        today_pool=[_rec("600100")],
        break_pool=[_rec("600001", "高位妖股")],
        yst_pool=[_rec("600001", boards=3)],
    )
    m = SentimentMonitor(provider=prov, alert_cooldown_seconds=3600.0)
    _probe(m)
    assert m.alerts_fired == 1
    _probe(m)
    _probe(m)
    assert m.alerts_fired == 1 and len(repo.events) == 1


# ---------------------------------------------------------------- ⑧ 端点暴露


def test_system_providers_includes_monitor(monkeypatch):
    """/api/system/providers 一站式暴露监控快照（含未启动态）。"""
    _capture(monkeypatch)
    prov = FakeProvider()
    m = SentimentMonitor(provider=prov)
    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    app.dependency_overrides[health_route.get_hub] = lambda: SimpleNamespace(provider=prov)
    app.state.sentiment_monitor = m
    with TestClient(app) as client:
        body = client.get("/api/system/providers").json()
    assert body["sentiment_monitor"]["state"] == "idle"
    assert "config" in body["sentiment_monitor"]

    app2 = FastAPI()
    app2.include_router(health_route.router, prefix="/api")
    app2.dependency_overrides[health_route.get_hub] = lambda: SimpleNamespace(provider=prov)
    with TestClient(app2) as client:
        body2 = client.get("/api/system/providers").json()
    assert body2["sentiment_monitor"] == {"state": "not_started"}
