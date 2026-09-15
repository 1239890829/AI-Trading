"""站内通知中心：只输出多维门控后的有效个股机会。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient


# ---------------------------------------------------------------- _session_of
def test_session_boundaries():
    from app.api.routes.notifications import _session_of

    assert _session_of(datetime(2026, 9, 7, 9, 29)) == "pre_open"
    assert _session_of(datetime(2026, 9, 7, 9, 30)) == "intraday"
    assert _session_of(datetime(2026, 9, 7, 11, 40)) == "intraday"  # 午休归盘中（分类口径）
    assert _session_of(datetime(2026, 9, 7, 15, 5)) == "intraday"
    assert _session_of(datetime(2026, 9, 7, 15, 6)) == "after_close"
    assert _session_of(datetime(2026, 9, 7, 2, 0)) == "pre_open"


# ---------------------------------------------------------------- fakes
class _Rule:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name


class _Event:
    def __init__(self, eid, rule_id, symbol, snapshot, hours_ago_utc):
        self.id = eid
        self.rule_id = rule_id
        self.symbol = symbol
        self.snapshot = snapshot
        self.triggered_at = datetime.utcnow() - timedelta(hours=hours_ago_utc)


class _FakeRepo:
    def __init__(self, rules, events):
        self._rules = rules
        self._events = events

    def list_rules(self, enabled_only=False):
        return self._rules

    def list_events(self, limit=50, rule_id=None):
        return self._events[:limit]


class _FakeRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeDB:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, _q):
        return self

    def scalar_one_or_none(self):
        return self._row


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def list_events(self, active_only=True, limit=30):
        return self._rows[:limit]


def _event_row(eid=1, title="工信部发布算力扶持政策", published_hours_ago=2):
    from app.core.bjtime import beijing_now_naive

    return _FakeRow(
        id=eid,
        title=title,
        url="https://example.com/a",
        source="x",
        source_tier=4,
        # 用**北京 naive**（生产同口径），不用 `datetime.now()` 的宿主墙钟：
        # 后者在 UTC 宿主下会比北京慢 8h，让 `published_at` 与「现在」的差值凭空多出 8h。
        # 本行原先注释自称「北京 naive 语义」而实现是 `datetime.now()` —— 属**注释失真**；
        # 下游断言取三态全集 / `score is not None` ⇒ 对 8h 差**不敏感**（故一直全绿），
        # 但语义必须准，否则将来有人在此加时间敏感断言就会踩宿主时区。
        published_at=beijing_now_naive() - timedelta(hours=published_hours_ago),
        fact_kind="fact",
        certainty="done",
        category="policy",
        half_life_hours=48,
        directions=[],
    )


# ---------------------------------------------------------------- 单元
def test_alert_items_filters_buy_point_rule_only():
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__"), _Rule(2, "__picks_watcher__")],
        events=[
            _Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "六维与买入区间均通过"}, 1),
            _Event(12, 2, "600000", {"kind": "board_flow_surge", "direction": "算力", "text": "板块机会不应出现"}, 1),
        ],
    )
    items = _alert_items(repo, limit=50)
    assert len(items) == 1  # 用户规则事件不进通知中心
    assert items[0]["id"] == "alert-11"
    assert items[0]["category"] == "opportunity"
    assert items[0]["label"] == "个股机会"
    assert items[0]["symbol"] == "600001"
    assert items[0]["session"] in {"pre_open", "intraday", "after_close"}


def test_alert_items_ignores_placeholder_symbol():
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "000000", {"kind": "buy_point", "name": "占位", "text": "无效"}, 1)],
    )
    items = _alert_items(repo, limit=50)
    assert items == []


def test_alert_items_rejects_non_buy_point_and_missing_name():
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[
            _Event(11, 1, "600001", {"kind": "confirm", "name": "甲公司"}, 1),
            _Event(12, 1, "600002", {"kind": "buy_point", "name": ""}, 1),
        ],
    )
    assert _alert_items(repo, limit=50) == []


# ---------------------------------------------------------------- _daily_pick_item
def test_daily_pick_item_ts_is_real_generation_time(monkeypatch):
    """**F-9 回归位**：精选通知的 `ts` 取真实生成时刻（UTC naive → 北京 +8），不写死。

    旧实现是 `f"{row.date} 08:40:00"`。它有两处失真，本用例各钉一条：
    ① 08:40 是配置漂移的残留——自动生成实为 09:26（`picks_autogen_scheduler`
       的 run_hour=9/run_minute=26），写死会让真实 09:26 生成的组合显示成 08:40；
    ② 不同 `created_at` 必须得到不同 `ts`（第二条断言），否则"未写死"就没有证据。
    """
    import app.api.routes.notifications as notif

    row = _FakeRow(
        date="2026-09-11",
        items='[{"symbol": "600519", "name": "贵州茅台"}]',
        meta="{}",
        created_at=datetime(2026, 9, 11, 1, 26, 58, 379775),  # naive UTC
    )
    monkeypatch.setattr(notif, "get_session_factory", lambda: lambda: _FakeDB(row))

    item = notif._daily_pick_item()
    assert item["ts"] == "2026-09-11 09:26:58.379775", "created_at(UTC) 必须 +8 转北京"
    # 与 alert/news 同形（空格分隔、无偏移标记），否则 ts 倒序会与其它来源错乱
    assert "T" not in item["ts"] and "+" not in item["ts"]

    row.created_at = datetime(2026, 9, 11, 7, 52, 21, 349819)  # → 北京 15:52（盘后生成）
    assert notif._daily_pick_item()["ts"] == "2026-09-11 15:52:21.349819"


# ---------------------------------------------------------------- 路由
def test_route_returns_stock_opportunities_only(monkeypatch):
    import app.api.routes.notifications as notif
    from app.main import app

    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__"), _Rule(2, "__picks_watcher__")],
        events=[
            _Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "多维筛选通过"}, 1),
            _Event(12, 2, "000000", {"kind": "board_flow_surge", "direction": "算力", "text": "板块机会"}, 1),
        ],
    )
    picks_row = _FakeRow(
        date="2026-09-07",
        items='[{"symbol": "300001", "name": "某某"}]',
        meta='{"gate": {"stand_aside": false}}',
        # created_at 是 naive UTC（`db.utcnow()` 口径），+8 后为北京生成时刻
        created_at=datetime(2026, 9, 7, 1, 26, 58),
    )
    monkeypatch.setattr(notif, "get_session_factory", lambda: lambda: _FakeDB(picks_row))
    old_store = getattr(app.state, "event_store", None)
    app.state.event_store = _FakeStore([_event_row()])
    try:
        client = TestClient(app)
        r = client.get("/api/notifications", params={"news_min_score": 0})
        assert r.status_code == 200
        body = r.json()["data"]
        assert len(body["items"]) == 1
        assert body["items"][0]["category"] == "opportunity"
        assert body["items"][0]["symbol"] == "600001"
        assert body["policy"] == "stock_opportunities_only"
        assert body["errors"] is None
        assert body["count"] == 1
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)
        app.state.event_store = old_store


def test_legacy_news_threshold_does_not_change_stock_only_policy(monkeypatch):
    import app.api.routes.notifications as notif
    from app.main import app

    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "机会"}, 1)],
    )
    picks_row = _FakeRow(date="2026-09-07", items="[]", meta="{}",
                         created_at=datetime(2026, 9, 7, 1, 26, 58))
    monkeypatch.setattr(notif, "get_session_factory", lambda: lambda: _FakeDB(picks_row))
    old_store = getattr(app.state, "event_store", None)
    app.state.event_store = _FakeStore([_event_row()])
    try:
        client = TestClient(app)
        # 参数为旧客户端兼容保留；通知源已不再含新闻与每日精选。
        r = client.get("/api/notifications", params={"news_min_score": 100})
        body = r.json()["data"]
        assert [i["symbol"] for i in body["items"]] == ["600001"]
        assert all(i["category"] == "opportunity" for i in body["items"])
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)
        app.state.event_store = old_store


def test_route_degrades_explicitly(monkeypatch):
    import app.api.routes.notifications as notif
    from app.main import app

    class _BoomRepo:
        def list_rules(self, enabled_only=False):
            raise RuntimeError("db down")

    app.dependency_overrides[notif.get_alert_repo] = lambda: _BoomRepo()
    monkeypatch.setattr(notif, "get_session_factory", lambda: lambda: _FakeDB(None))
    old_store = getattr(app.state, "event_store", None)
    app.state.event_store = _FakeStore([])  # 无事件 → news 空但不报错
    try:
        client = TestClient(app)
        r = client.get("/api/notifications")
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["errors"] and "alerts" in body["errors"]  # 降级显式可见
        assert body["items"] == []  # 无精选（row None）+ 无事件
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)
        app.state.event_store = old_store


# ---------------------------------------------------------------- 条件化/日历修复（§6.25 用户报告：周末被标盘中）


def test_session_non_trading_day_goes_pre_open():
    """周末/节假日（不在交易日集合）→ 一律盘前节拍（下一交易日开盘前消化）。"""
    from app.api.routes.notifications import _session_of
    from datetime import date

    sat = {date(2026, 9, 11)}  # 仅周五是交易日 → 周六/日非交易日
    assert _session_of(datetime(2026, 9, 12, 10, 0), trading_dates=sat) == "pre_open"
    assert _session_of(datetime(2026, 9, 13, 14, 0), trading_dates=sat) == "pre_open"
    # 节假日（工作日但休市）同理
    assert _session_of(datetime(2026, 10, 1, 10, 0), trading_dates=sat) == "pre_open"


def test_session_trading_day_wall_clock_unchanged():
    """交易日维持墙钟语义；日历缺失（None）回退墙钟——显式降级非静默。"""
    from app.api.routes.notifications import _session_of
    from datetime import date

    fri = {date(2026, 9, 11)}
    assert _session_of(datetime(2026, 9, 11, 10, 0), trading_dates=fri) == "intraday"
    assert _session_of(datetime(2026, 9, 11, 16, 0), trading_dates=fri) == "after_close"
    assert _session_of(datetime(2026, 9, 11, 8, 0), trading_dates=fri) == "pre_open"
    # 回退：trading_dates=None → 与旧行为逐字一致
    assert _session_of(datetime(2026, 9, 11, 10, 0), trading_dates=None) == "intraday"
    assert _session_of(datetime(2026, 9, 12, 10, 0), trading_dates=None) == "intraday"  # 回退态周末仍标盘中（已知降级）
