"""站内通知中心：只输出多维门控后的有效个股机会。

`IMP-034`（2026-09-16）删除了本文件里守着**已删除生产者**的用例与夹具：
`_daily_pick_item` 的 F-9 时间戳回归位、以及只为新闻路径存在的
`_FakeRow` / `_FakeDB` / `_FakeStore` / `_event_row`。
**顺带保留一条当时写在夹具里的教训**（夹具没了，教训仍成立）：
构造时间字段的假数据时**不要用 `datetime.now()` 当「北京 naive」**——
宿主若跑在 UTC，它会比北京慢 8h，让「距今多久」凭空多出 8h；
生产口径一律 `app.core.bjtime.beijing_now_naive()`。
原夹具正是「注释自称北京 naive、实现却是宿主墙钟」，属**注释与实现脱钩**（同族见
`docs/kb/09-verification-pitfalls.md`）。
"""
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


# ---------------------------------------------------------------- 路由
def test_route_returns_stock_opportunities_only():
    import app.api.routes.notifications as notif
    from app.main import app

    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__"), _Rule(2, "__picks_watcher__")],
        events=[
            _Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "多维筛选通过"}, 1),
            _Event(12, 2, "000000", {"kind": "board_flow_surge", "direction": "算力", "text": "板块机会"}, 1),
        ],
    )
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


def test_legacy_news_threshold_does_not_change_stock_only_policy():
    import app.api.routes.notifications as notif
    from app.main import app

    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "机会"}, 1)],
    )
    try:
        client = TestClient(app)
        # 参数为旧客户端兼容保留；通知源已不再含新闻与每日精选。
        r = client.get("/api/notifications", params={"news_min_score": 100})
        body = r.json()["data"]
        assert [i["symbol"] for i in body["items"]] == ["600001"]
        assert all(i["category"] == "opportunity" for i in body["items"])
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_route_degrades_explicitly():
    import app.api.routes.notifications as notif
    from app.main import app

    class _BoomRepo:
        def list_rules(self, enabled_only=False):
            raise RuntimeError("db down")

    app.dependency_overrides[notif.get_alert_repo] = lambda: _BoomRepo()
    try:
        client = TestClient(app)
        r = client.get("/api/notifications")
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["errors"] and "alerts" in body["errors"]  # 降级显式可见
        assert body["items"] == []  # 规则源不可用 → 无买点事件可展示
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


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
