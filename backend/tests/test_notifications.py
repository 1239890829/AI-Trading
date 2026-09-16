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


# ---------------------------------------------------------------- 空态诊断「接线」守卫（BUG-016 子项③，2026-09-16）
#
# ⚠️ 为什么必须单独守这一组（`KB-ENG-100`：**纯函数测过 ≠ 接线正确**）：
# `tests/test_notification_diagnostics.py` 只证明「给定库内容 → 诊断输出什么」，
# 它**完全不经过 HTTP 层**。端点里那三行接线（空态才调 / 调了要挂上 / 抛错别塌）
# 若被删掉或改错，上面那 16 项**照样全绿**，而用户界面上仍然是
# `{"items": [], "count": 0}` —— 即「修了但没生效」，本轮要修的正是这个。
#
# 打桩点选在 **`app.picks.notification_diagnostics` 的模块属性**：端点用的是
# 函数内延迟导入（`from app.picks.notification_diagnostics import ...`），
# 该语句在调用时读 `sys.modules[...].notification_diagnostics`，
# 故 `monkeypatch.setattr` 打在模块属性上即可**精确拦在通路上**，
# 不必碰真实数据库（守接线不该依赖当日库里恰好有什么）。


def _patch_diagnostics(monkeypatch, fn):
    """把端点会调到的那条通路上的诊断函数换成 fn。"""
    import importlib

    return monkeypatch.setattr(
        importlib.import_module("app.picks.notification_diagnostics"),
        "notification_diagnostics",
        fn,
    )


def test_empty_notifications_do_call_diagnostics_and_attach_it(monkeypatch):
    """空态 ⇒ 端点**确实调用**诊断，并把返回值挂到 `data.diagnostics`。"""
    import app.api.routes.notifications as notif
    from app.main import app

    sentinel = {"state": "ran_rejected", "polls": 16, "marker": "wired"}
    calls: list[tuple] = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    _patch_diagnostics(monkeypatch, _fake)
    # 规则存在但无买点事件 → items 为空（模拟"跑了但全被否"的读侧处境）
    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")], events=[]
    )
    try:
        client = TestClient(app)
        r = client.get("/api/notifications")
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["items"] == [] and body["count"] == 0
        assert body["diagnostics"] == sentinel  # 接线点：不是 None、不是原样透传别的
        assert len(calls) == 1
        assert calls[0] == ((), {})  # 端点按契约无参调用（trade_date 走默认=今天）
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_non_empty_notifications_do_not_call_diagnostics(monkeypatch):
    """非空态 ⇒ **刻意不调用**诊断，且字段恒 `None`（判据边界，勿放宽为"顺便也算一下"）。

    反向断言的价值：它把「只在空态附加」这个**省流设计**钉成契约。
    若有人把 `if not items:` 去掉，本用例立刻红——而 mock 侧会先炸，指向明确。
    """
    import app.api.routes.notifications as notif
    from app.main import app

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("非空态不应调用空态诊断（本端点是 30s 轮询热路径）")

    _patch_diagnostics(monkeypatch, _must_not_be_called)
    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "机会"}, 1)],
    )
    try:
        client = TestClient(app)
        r = client.get("/api/notifications")
        body = r.json()["data"]
        assert len(body["items"]) == 1  # 前置：确实非空
        assert body["diagnostics"] is None
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_diagnostics_failure_is_explicit_not_silent(monkeypatch):
    """诊断自身抛错 ⇒ 端点仍 200、通知不受影响，但**必须显式降级**为 `unavailable`。

    ⚠️ 这条是"同形"缺陷的守卫：若失败时回 `None`，则「诊断坏了」与
    「有通知所以不诊断」在响应里完全一样（都是 `diagnostics: null`），
    用户会再遇到一次"为什么看不到原因"——正是本子项要消灭的不可解释。
    """
    import app.api.routes.notifications as notif
    from app.main import app

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated diagnostics crash")

    _patch_diagnostics(monkeypatch, _boom)
    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")], events=[]
    )
    try:
        client = TestClient(app)
        r = client.get("/api/notifications")
        assert r.status_code == 200  # 诊断失败不得拖垮通知端点
        body = r.json()["data"]
        assert body["items"] == []
        diag = body["diagnostics"]
        assert isinstance(diag, dict), "失败态不得回 None（与'非空态'同形）"
        assert diag["state"] == "unavailable"
        assert body["errors"] is None  # 通知源本身正常 ⇒ 不误报 alerts 降级
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_notifications_response_invariant_holds_in_both_branches(monkeypatch):
    """响应不变式：`items` 空 ⇒ diagnostics 是 dict；非空 ⇒ diagnostics is None。

    两条分支合起来断言，防止只守一边（单边守卫在"两分支都被改成恒 None"时会同时放过）。
    """
    import app.api.routes.notifications as notif
    from app.main import app

    _patch_diagnostics(monkeypatch, lambda *a, **k: {"state": "no_pick_set"})
    client = TestClient(app)
    try:
        app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
            rules=[_Rule(1, "__picks_buy_point__")], events=[]
        )
        empty_body = client.get("/api/notifications").json()["data"]
        app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
            rules=[_Rule(1, "__picks_buy_point__")],
            events=[_Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "机会"}, 1)],
        )
        full_body = client.get("/api/notifications").json()["data"]
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)

    assert (empty_body["count"] == 0) is isinstance(empty_body["diagnostics"], dict)
    assert (full_body["count"] > 0) is (full_body["diagnostics"] is None)
