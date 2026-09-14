"""P0-B ths 涨停原因单点哨兵。

验收标准：
① 注入"空原因响应"→ 告警触发（AlertEvent 落库 + 通道分发 + 文案含"题材标签可能失效"）；
② 正常响应 / 薄池（样本不足）/ 休市时段 → 不误报；
③ 冷却去重（4h 内不重复刷屏）与恢复自愈（计数清零，重新累计后才再告警）；
④ 连续拉取失败（不可达）同样触发告警；⑤ /api/system/providers 暴露哨兵快照。

告警链 monkeypatch（_ensure_rule / AlertRepository / get_notifier_registry /
get_session_factory）：状态机与告警决策走真实代码，只隔离 SQLite 与通道 IO。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.schemas.market import LimitUpRecord
from app.services import ths_sentinel as sentinel_mod
from app.services.ths_sentinel import ThsReasonSentinel
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛


#: 周三 10:30（连续竞价时段，2026-09-02 为真实交易日）
WED = datetime(2026, 9, 2, 10, 30, tzinfo=BJ_TZ)
SAT = datetime(2026, 9, 5, 10, 30, tzinfo=BJ_TZ)  # 周六
NIGHT = datetime(2026, 9, 2, 20, 0, tzinfo=BJ_TZ)  # 交易日晚间（窗口外）
TRADE_DAYS = [date(2026, 9, 1), date(2026, 9, 2)]


def _rec(symbol: str, reason: str | None) -> LimitUpRecord:
    return LimitUpRecord(symbol=symbol, trade_date=date(2026, 9, 2), source="ths", reason=reason)


def _pool(reasons: list[str | None]) -> list[LimitUpRecord]:
    return [_rec(f"6005{i:02d}", r) for i, r in enumerate(reasons)]


class FakeProvider:
    name = "fake"

    def __init__(self, records=None, error: Exception | None = None):
        self.records = records if records is not None else []
        self.error = error
        self.calls = 0

    async def get_limit_up_pool(self, trade_date):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.records)


class FakeRepo:
    def __init__(self, session_factory=None):
        self.events = []

    def record_trigger(self, rule_id, symbol, trigger_value, threshold, snapshot=None, delivered_channels=None):
        ev = SimpleNamespace(
            id=len(self.events) + 1, rule_id=rule_id, symbol=symbol,
            trigger_value=trigger_value, threshold=threshold, snapshot=snapshot,
        )
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
    """隔离告警 IO：规则固定、事件落内存、通道记录分发。"""
    repo, registry = FakeRepo(), FakeRegistry()
    rule = SimpleNamespace(id=7, name=sentinel_mod.SENTINEL_RULE_NAME)
    monkeypatch.setattr(sentinel_mod, "_ensure_rule", lambda sf: rule)
    monkeypatch.setattr(sentinel_mod, "AlertRepository", lambda sf: repo)
    monkeypatch.setattr(sentinel_mod, "get_notifier_registry", lambda: registry)
    monkeypatch.setattr(sentinel_mod, "get_session_factory", lambda: None)
    return repo, registry


def _probe(sent: ThsReasonSentinel, now=WED):
    return asyncio.run(sent.probe_once(now=now, trade_days=TRADE_DAYS))


def test_calendar_uncovered_defers_visibly(monkeypatch):
    """F7 定点守卫 · 日历未覆盖今天（**未判定**）⇒ 可见降级，不静默 idle、不探测。

    原写法 `(days and not is_trade_day(days, today))`：源头日历是**尾随窗口**
    （09-02 盘中拿到的清单末日只到 09-01，不含未来日期），`is_trade_day` 因此
    返回 `False` ⇒ 被当成**确认休市** ⇒ 静默 `idle`，与「真的休市」在快照里
    **完全无法区分**。2026-09-14 事故当天正是这一条让哨兵整段不探测而无人可见。

    *回退即红*：把 `is_trade_day_on` 改回二态 `is_trade_day` ⇒ `state` 变
    `"idle"`、`last_error` 为空 ⇒ 三条断言全红（实测：注入 4，B1 与本条同红）。
    """
    _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 10))
    sent = ThsReasonSentinel(provider=prov)
    # 日历末日 09-01，而 now=WED(09-02) ⇒ 未覆盖今天（尾随窗口的真实形态）
    snap = asyncio.run(sent.probe_once(now=WED, trade_days=[date(2026, 9, 1)]))
    assert snap["state"] == "calendar_unknown", "未判定不得塌缩成 idle（=「确认休市」）"
    assert snap["last_error"], "未判定必须**可见**（last_error 非空），不得静默"
    assert prov.calls == 0, "未判定不得探测（残留数据会误报）"


# ---------------------------------------------------------------- ①②③ 核心判定


def test_empty_reason_alert_fires(monkeypatch):
    """① 全空原因 ×2 次连续 → 告警触发，文案含处置提示。"""
    repo, registry = _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 10))
    sent = ThsReasonSentinel(provider=prov, bad_required=2)

    snap1 = _probe(sent)
    assert snap1["state"] == "degraded" and snap1["consecutive_bad"] == 1
    assert not repo.events, "单次低覆盖不告警（迟滞防抖）"

    snap2 = _probe(sent)
    assert snap2["state"] == "alert" and snap2["alerts_fired"] == 1
    assert snap2["coverage"] == 0.0
    assert len(repo.events) == 1
    text = repo.events[0].snapshot["text"]
    assert "题材标签可能失效" in text
    assert "非空率" in text and "处置" in text
    assert registry.dispatched, "通道未分发"
    assert registry.dispatched[0][0] is repo.events[0]


def test_healthy_no_false_alert(monkeypatch):
    """② 全部有原因 → 永不告警，state=ok。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(_pool([f"AI 算力 {i}" for i in range(10)]))
    sent = ThsReasonSentinel(provider=prov)
    for _ in range(3):
        snap = _probe(sent)
    assert snap["state"] == "ok" and snap["coverage"] == 1.0
    assert not repo.events and snap["alerts_fired"] == 0


def test_small_pool_no_judgement(monkeypatch):
    """② 薄池（< min_records）不判定：空池 ≠ 空原因，早盘不误报。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 3))
    sent = ThsReasonSentinel(provider=prov)
    for _ in range(3):
        snap = _probe(sent)
    assert snap["state"] == "no_data" and snap["records"] == 3
    assert not repo.events


def test_market_closed_skips_probe(monkeypatch):
    """② 周六 / 交易日晚间不探测：回退残留数据判定无意义。"""
    _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 10))
    sent = ThsReasonSentinel(provider=prov)
    assert _probe(sent, now=SAT)["state"] == "idle"
    assert _probe(sent, now=NIGHT)["state"] == "idle"
    assert prov.calls == 0


def test_cooldown_suppresses_repeat(monkeypatch):
    """③ 冷却期内坏样本继续累积但不重复发事件（防刷屏）。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 10))
    sent = ThsReasonSentinel(provider=prov, bad_required=1, alert_cooldown_seconds=3600.0)
    _probe(sent)
    assert sent.alerts_fired == 1
    snap = _probe(sent)
    assert sent.alerts_fired == 1 and len(repo.events) == 1
    assert snap["state"] == "alert"


def test_recovery_resets_counters(monkeypatch):
    """③ 恢复自愈：好探测清零计数，再坏需重新累计 bad_required 次才告警。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(_pool([None] * 10))
    sent = ThsReasonSentinel(provider=prov, bad_required=2)
    _probe(sent)
    prov.records = _pool(["芯片"] * 10)
    snap = _probe(sent)
    assert snap["state"] == "ok" and snap["consecutive_bad"] == 0 and not repo.events
    prov.records = _pool([None] * 10)
    snap = _probe(sent)  # 重新累计第 1 次，不到阈值
    assert snap["state"] == "degraded" and not repo.events


# ---------------------------------------------------------------- 不可达告警


def test_unreachable_alert_after_streak(monkeypatch):
    """连续拉取失败达 err_required → 告警（ths 单点、无备源可比对）。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(error=RuntimeError("boom"))
    sent = ThsReasonSentinel(provider=prov, err_required=3)
    _probe(sent)
    _probe(sent)
    assert not repo.events and sent.state == "probe_failed" and sent.consecutive_err == 2
    snap = _probe(sent)
    assert snap["state"] == "alert" and snap["alerts_fired"] == 1
    text = repo.events[0].snapshot["text"]
    assert "题材标签可能失效" in text and "拉取失败" in text


def test_error_streak_resets_on_success(monkeypatch):
    """偶发单次失败后恢复：err 计数清零，不触发告警。"""
    repo, _ = _capture(monkeypatch)
    prov = FakeProvider(error=RuntimeError("boom"))
    sent = ThsReasonSentinel(provider=prov, err_required=3)
    _probe(sent)
    prov.error = None
    prov.records = _pool(["低空经济"] * 10)
    snap = _probe(sent)
    assert snap["state"] == "ok" and snap["consecutive_err"] == 0 and not repo.events


# ---------------------------------------------------------------- ⑤ 端点暴露


def test_system_providers_includes_sentinel(monkeypatch):
    """/api/system/providers 一站式暴露哨兵快照（含未启动态）。"""
    _capture(monkeypatch)

    class _Hub:
        provider = FakeProvider(_pool(["x"] * 10))

    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    app.dependency_overrides[health_route.get_hub] = lambda: _Hub()

    sent = ThsReasonSentinel(provider=FakeProvider(_pool([None] * 10)))
    app.state.ths_sentinel = sent
    with TestClient(app) as client:
        body = client.get("/api/system/providers").json()
    assert body["ths_reason_sentinel"]["state"] == "idle"
    assert "config" in body["ths_reason_sentinel"]

    app2 = FastAPI()
    app2.include_router(health_route.router, prefix="/api")
    app2.dependency_overrides[health_route.get_hub] = lambda: _Hub()
    with TestClient(app2) as client:
        body2 = client.get("/api/system/providers").json()
    assert body2["ths_reason_sentinel"] == {"state": "not_started"}
