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
        # 记录调用参数：`real_symbol_only` 与读取窗口都是"看不见但决定结果"的点
        # （见 `test_alert_items_reads_wider_window_than_return_limit` 的说明）。
        self.requested: list[tuple[int, bool]] = []

    def list_rules(self, enabled_only=False):
        return self._rules

    def list_events(self, limit=50, rule_id=None, acknowledged=None, real_symbol_only=False):
        self.requested.append((limit, real_symbol_only))
        rows = self._events
        if real_symbol_only:
            # 与 `AlertRepository.list_events` 同口径：排除空串与占位代码。
            # ⚠️ 假仓库必须**实现**这个过滤，否则"板块级事件不得吃掉读取窗口"
            #    这条判据会平凡通过（假仓库照单全收 ⇒ 窗口永远够用）。
            rows = [e for e in rows if e.symbol and e.symbol != "000000"]
        return rows[:limit]


# ---------------------------------------------------------------- 单元
#
# ⚠️ 2026-09-16（用户实盘反馈「盘中机会为什么没提示」）：`_alert_items` 的**白名单
# 口径从"规则名"改为"事件形状"** —— 用户当日 121 条临板预警（`__picks_watcher__`
# 的 `pre_limit`，均带真实代码、10cm 非一字板、有介入机会）一条都没进通知中心，
# 只因白名单只认 `__picks_buy_point__`，而该规则当日**一次都没触发**。
# 因此下列用例的名字与断言同步更新，**不是**把旧断言改宽：
#  - 仍必须挡下 `board_flow_surge` 这类**板块级**形状（哪怕它挂在 watcher 规则上）；
#  - 新增 `pre_limit` **必须放行**（这正是本次用户要补上的可见性）。
# 另：返回值由 `list` 改为 `(items, seen)` 元组（`seen` = 各形状原始计数，
# 供空态诊断区分"买点链没选出票"与"临板预警也没触发"）。


def test_alert_items_filters_by_event_shape_not_rule_name():
    """形状门：`pre_limit`（临板预警，带代码）放行；板块级形状挡下。"""
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__"), _Rule(2, "__picks_watcher__")],
        events=[
            _Event(11, 1, "600001", {"kind": "buy_point", "name": "甲公司", "text": "六维与买入区间均通过"}, 1),
            _Event(12, 2, "600000", {"kind": "board_flow_surge", "direction": "算力", "text": "板块机会不应出现"}, 1),
            _Event(13, 2, "300002", {"kind": "pre_limit", "name": "乙公司", "text": "距封板 1.4pct，10cm"}, 1),
        ],
    )
    items, seen = _alert_items(repo, limit=50)
    # 板块级形状**仍然**不进通知中心（哪怕它挂在新放开的 watcher 规则上）
    assert {i["symbol"] for i in items} == {"600001", "300002"}
    assert all(i["category"] == "opportunity" for i in items)
    assert all(i["session"] in {"pre_open", "intraday", "after_close"} for i in items)
    by_sym = {i["symbol"]: i for i in items}
    assert by_sym["600001"]["label"] == "个股机会"
    assert by_sym["300002"]["label"] == "临板预警"  # 形状标签，不是笼统的"个股机会"
    # `seen` 统计**全部形状**（不只白名单）：板块级 1 条要能与个股级 2 条对上，
    # 否则空态里分不清"没扫到"与"扫到的都不是个股级"。
    assert seen["buy_point"] == 1
    assert seen["pre_limit"] == 1
    assert seen["board_flow_surge"] == 1


def test_alert_items_ignores_placeholder_symbol():
    """占位代码不进通知中心（DB 侧过滤这条路径）。

    ⚠️ 本题与下一题必须**都在**：占位代码有**两层**防护（DB 侧
    `real_symbol_only` + 路由侧 `e.symbol == "000000"`），只测其中一层时，
    删掉另一层仍全绿（两层互相"替对方挡着"）。
    """
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "000000", {"kind": "buy_point", "name": "占位", "text": "无效"}, 1)],
    )
    items, seen = _alert_items(repo, limit=50)
    assert items == []
    assert seen == {"buy_point": 0, "pre_limit": 0}  # DB 侧就排除了，不是"筛掉了"


class _UnfilteredRepo(_FakeRepo):
    """**故意不实现** `real_symbol_only` 的仓库：模拟"消费方忘了传参"。

    与真实仓库不同，它把全部事件照单返回 —— 用于单独证明**路由自身**那道
    `e.symbol == "000000"` 守卫仍然有效（两层防护要能各自证明）。
    """

    def list_events(self, limit=50, rule_id=None, acknowledged=None, real_symbol_only=False):
        self.requested.append((limit, real_symbol_only))
        return self._events[:limit]


def test_route_level_placeholder_guard_survives_missing_db_filter():
    """DB 侧过滤缺席时，路由侧守卫仍须独立挡下占位代码（两层各自可证）。"""
    from app.api.routes.notifications import _alert_items

    repo = _UnfilteredRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "000000", {"kind": "buy_point", "name": "占位", "text": "无效"}, 1)],
    )
    items, seen = _alert_items(repo, limit=50)
    assert items == []
    assert seen["buy_point"] == 1  # 路由**看见了**它，是自己挡下的


def test_alert_items_rejects_non_notif_kind_and_missing_name():
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[
            _Event(11, 1, "600001", {"kind": "confirm", "name": "甲公司"}, 1),
            _Event(12, 1, "600002", {"kind": "buy_point", "name": ""}, 1),
        ],
    )
    items, _ = _alert_items(repo, limit=50)
    assert items == []


def test_alert_items_without_notif_rules_returns_empty_shapes():
    """连规则行都没有 ⇒ `seen` 是**空 dict**（而非全 0）。

    ⚠️ 这个区分是刻意的：`{}` 与 `{buy_point: 0, pre_limit: 0}` 在前端是两句不同的话
    （"规则从未触发过" vs "触发了但没选到"），合并它们等于把最关键的线索抹平。
    """
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(rules=[_Rule(9, "__something_else__")], events=[])
    items, seen = _alert_items(repo, limit=50)
    assert items == []
    assert seen == {}


def test_alert_items_reads_wider_window_than_return_limit():
    """`limit` 是**返回条数**上限，底层读取窗口更宽（`_NOTIF_FETCH_LIMIT`）。

    ⚠️ 这是"改完看不见效果"那类缺陷的守卫：若直接用 `alert_limit` 去读，
    最近 50 条里临板的期望只有 ~8 条（当日实测 pre_limit 仅占全天 703 条的 17%），
    等于把刚放开的形状又在读取阶段悄悄掐掉。故断言读取时**确实请求了更宽的窗口**。
    """
    from app.api.routes.notifications import _NOTIF_FETCH_LIMIT, _alert_items

    repo = _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")],
        events=[_Event(11, 1, "600001", {"kind": "buy_point", "name": "甲", "text": "x"}, 1)],
    )
    _alert_items(repo, limit=50)
    assert repo.requested == [(max(50, _NOTIF_FETCH_LIMIT), True)]
    assert _NOTIF_FETCH_LIMIT > 50  # 常量本身不得被调成小于默认返回条数


def test_alert_items_asks_repo_for_real_symbols_only():
    """读取必须在 **DB 侧**限定"带真实标的"（`real_symbol_only=True`）。

    ⚠️ 这条守的是 2026-09-16 实测踩到的第二个窗口问题：板块级事件占单日 83%
    （703 条里 582 条 `symbol=000000`），它们一条都不会进通知中心，却把
    **按条数计**的窗口吃光 —— 窗口 500 时当天 121 条临板只出来 89 条。
    若有人把 `real_symbol_only` 去掉（或忘了传），本条立刻红；只断言"窗口够大"
    是抓不到的（把数字调大只是把边界推远，窗口仍会被吃光）。
    """
    from app.api.routes.notifications import _alert_items

    repo = _FakeRepo(
        rules=[_Rule(2, "__picks_watcher__")],
        events=[_Event(11, 2, "300002", {"kind": "pre_limit", "name": "乙", "text": "临板"}, 1)],
    )
    _alert_items(repo, limit=50)
    assert repo.requested[0][1] is True


def test_placeholder_events_do_not_consume_read_window():
    """板块级事件（占位代码）**不得**占用读取窗口额度。

    构造：`_NOTIF_FETCH_LIMIT` 条板块级（更新）+ 1 条临板（更旧）。
    窗口若被板块级吃光 ⇒ 临板取不到 ⇒ 用户又"看不到机会"。
    """
    from app.api.routes.notifications import _NOTIF_FETCH_LIMIT, _alert_items

    board = [
        _Event(1000 + i, 2, "000000", {"kind": "board_low_absorb", "direction": "算力"}, 0)
        for i in range(_NOTIF_FETCH_LIMIT)
    ]
    board.append(_Event(9999, 2, "300002", {"kind": "pre_limit", "name": "乙公司", "text": "临板"}, 5))
    repo = _FakeRepo(rules=[_Rule(2, "__picks_watcher__")], events=board)
    items, seen = _alert_items(repo, limit=50)
    assert [i["symbol"] for i in items] == ["300002"]
    # 板块级形状不出现在 `seen` 里 —— 它已在 DB 侧被排除，不是"筛掉了"
    assert seen == {"buy_point": 0, "pre_limit": 1}


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
    """空态 ⇒ 端点**确实调用**诊断，并把返回值挂到 `data.diagnostics`。

    另钉一条 2026-09-16 新增的**反向**判据：端点必须用**新 dict** 合并形状计数，
    不得就地 mutate 诊断函数返回的那个对象。若改回就地赋值，下面
    `assert sentinel == {...原始内容...}` 会因被改写而红 —— 而"就地改"正是
    让本用例的 `== sentinel` 变成**恒真**（两侧同一对象）的那类静默失效。
    """
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
        # 接线点：不是 None、不是原样透传别的；形状计数按契约并进去
        assert body["diagnostics"] == {
            "state": "ran_rejected", "polls": 16, "marker": "wired",
            "shapes": {"buy_point": 0, "pre_limit": 0},
        }
        assert len(calls) == 1
        assert calls[0] == ((), {})  # 端点按契约无参调用（trade_date 走默认=今天）
        # 反向判据：桩返回的对象**未被就地改写**（就地改 = 把手写字典当可变状态用）
        assert sentinel == {"state": "ran_rejected", "polls": 16, "marker": "wired"}
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_diagnostics_non_dict_is_coerced_not_returned_as_none(monkeypatch):
    """诊断函数若返回非 dict，端点**不得**让 `diagnostics` 退化成 `None`。

    ⚠️ 这是"同形"缺陷的第三道口：`None` 在本端点已被"非空态"占用 ⇒
    非 dict 直接透传会让「诊断坏了」与「有通知所以不诊断」在响应里完全一样，
    用户又会遇到一次"为什么看不到原因"。
    """
    import app.api.routes.notifications as notif
    from app.main import app

    _patch_diagnostics(monkeypatch, lambda *a, **k: None)
    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(1, "__picks_buy_point__")], events=[]
    )
    try:
        client = TestClient(app)
        body = client.get("/api/notifications").json()["data"]
        assert body["items"] == []
        assert isinstance(body["diagnostics"], dict)
        assert body["diagnostics"]["state"] == "unavailable"
        assert body["diagnostics"]["shapes"] == {"buy_point": 0, "pre_limit": 0}
    finally:
        app.dependency_overrides.pop(notif.get_alert_repo, None)


def test_route_truncates_after_filtering_not_before():
    """截断在**筛选之后**：`alert_limit` 小的时候，临板预警不得被板块级事件挤掉。

    ⚠️ 这条守的是"顺序"这个看不见的点：读取窗口（`_NOTIF_FETCH_LIMIT`）与
    返回条数（`alert_limit`）都在，但**先筛后截**还是**先截后筛**结论完全不同。
    当日实测板块级事件占 83% ⇒ 若先截 50 条，最近 50 条里临板期望仅 ~8 条，
    用户会在"改完看着没变化"的状态里继续漏机会。
    """
    import app.api.routes.notifications as notif
    from app.main import app

    # 30 条板块级（更近）+ 1 条临板（更远）：先截后筛会把它切掉
    events = [
        _Event(100 + i, 2, "000000", {"kind": "board_low_absorb", "direction": "算力"}, 0)
        for i in range(30)
    ]
    events.append(_Event(200, 2, "300002", {"kind": "pre_limit", "name": "乙公司", "text": "临板"}, 5))
    app.dependency_overrides[notif.get_alert_repo] = lambda: _FakeRepo(
        rules=[_Rule(2, "__picks_watcher__")], events=events
    )
    try:
        body = TestClient(app).get("/api/notifications", params={"alert_limit": 5}).json()["data"]
        assert [i["symbol"] for i in body["items"]] == ["300002"]
        assert body["items"][0]["label"] == "临板预警"
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
