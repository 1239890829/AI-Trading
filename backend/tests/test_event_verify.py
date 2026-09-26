"""热点验证环单测（P1-6）：事件发酵四态判定。

核心契约：
- 四态互斥、判定可解释（basis 必给）
- 三态纪律：窗口未到/无题材/缺数据 → unknown（显式「未判定」），绝不臆造
- 「可见后新涨停」= 封板时间 ≥ 来源发布与当前解释可见的较晚时点
- 板块资金（f62）与涨停是两套口径，分开采、分开说
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.events.verify import _parse_hhmmss, _seal_after, verify_event

TODAY = date(2026, 9, 10)


def _pool(*seals_and_reasons):
    """构造涨停池：[(first_seal_time, reason), ...]"""
    return [
        {"symbol": f"60000{i}", "reason": reason, "first_seal_time": seal}
        for i, (seal, reason) in enumerate(seals_and_reasons)
    ]


def _fund(net):
    return {"name": "测试板块", "main_net_yi": net} if net is not None else None


# ---------------------------------------------------------------- 纯函数判定


def test_window_not_reached_is_unknown():
    now = datetime(2026, 9, 10, 10, 0, 0)
    pub = now - timedelta(minutes=10)  # 仅 10 分钟前
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(), board_fund=_fund(5.0), trade_date=TODAY, now=now)
    assert v["status"] == "unknown"
    assert "窗口未到" in v["basis"]


def test_confirmed_new_limit_up_plus_inflow():
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    pool = _pool(("10:30:00", "低空经济+飞行汽车"), ("09:00:00", "低空经济"))  # 前者在消息可见后
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=pool, board_fund=_fund(3.2), trade_date=TODAY, now=now)
    assert v["status"] == "confirmed"
    assert v["new_limit_ups"] == 1  # 只有 10:30 那只算消息可见后
    assert v["net_inflow_yi"] == 3.2


def test_fermenting_new_limit_up_but_no_inflow():
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(("10:30:00", "低空经济")),
                     board_fund=_fund(-1.0), trade_date=TODAY, now=now)
    assert v["status"] == "fermenting"
    assert "未净流入" in v["basis"]


def test_fermenting_inflow_but_no_limit_up():
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(), board_fund=_fund(5.0), trade_date=TODAY, now=now)
    assert v["status"] == "fermenting"
    assert "尚无消息可见后新涨停" in v["basis"]


def test_faded_no_limit_up_and_outflow():
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(), board_fund=_fund(-2.5), trade_date=TODAY, now=now)
    assert v["status"] == "faded"
    assert "净流出" in v["basis"]


def test_faded_zero_net_is_not_confirmed():
    """净额为 0（既非流入也非流出）= 未被资金认可 → faded，不判 fermenting。"""
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(), board_fund=_fund(0.0), trade_date=TODAY, now=now)
    assert v["status"] == "faded"


def test_unknown_when_board_fund_missing():
    """板块映射不到 + 无新涨停 → unknown（数据不足，不臆造 confirmed/faded）。"""
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 10, 10, 0, 0)
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=_pool(), board_fund=None, trade_date=TODAY, now=now)
    assert v["status"] == "unknown"


def test_unknown_without_themes_or_published():
    now = datetime(2026, 9, 10, 11, 0, 0)
    assert verify_event(published_at=now, visible_at=now, theme_targets=[],
                        limit_up_pool=_pool(), board_fund=_fund(1.0),
                        trade_date=TODAY, now=now)["status"] == "unknown"
    assert verify_event(published_at=None, visible_at=None, theme_targets=["低空经济"],
                        limit_up_pool=_pool(), board_fund=_fund(1.0),
                        trade_date=TODAY, now=now)["status"] == "unknown"


def test_unknown_when_legacy_visibility_is_missing():
    now = datetime(2026, 9, 10, 11, 0)
    v = verify_event(
        published_at=datetime(2026, 9, 10, 9, 0), visible_at=None,
        theme_targets=["低空经济"],
        limit_up_pool=_pool(("10:30:00", "低空经济")),
        board_fund=_fund(5.0), trade_date=TODAY, now=now,
    )
    assert v["status"] == "unknown"
    assert v["new_limit_ups"] == 0
    assert v["age_minutes"] is None
    assert "可见时点未知" in v["basis"]


def test_window_starts_at_later_visibility_time():
    now = datetime(2026, 9, 10, 10, 20)
    v = verify_event(
        published_at=datetime(2026, 9, 10, 9, 0),
        visible_at=datetime(2026, 9, 10, 10, 10),
        theme_targets=["低空经济"], limit_up_pool=_pool(("10:15:00", "低空经济")),
        board_fund=_fund(5.0), trade_date=TODAY, now=now,
    )
    assert v["status"] == "unknown"
    assert v["age_minutes"] == 10.0


def test_cross_day_event_counts_todays_all_as_after():
    """昨日已可见的事件 → 今日该题材所有封板都晚于该时点。"""
    now = datetime(2026, 9, 10, 11, 0, 0)
    pub = datetime(2026, 9, 9, 20, 0, 0)  # 昨日
    pool = _pool(("09:31:00", "低空经济"), ("10:00:00", "低空经济"))
    v = verify_event(published_at=pub, visible_at=pub, theme_targets=["低空经济"],
                     limit_up_pool=pool, board_fund=_fund(2.0), trade_date=TODAY, now=now)
    assert v["new_limit_ups"] == 2
    assert v["status"] == "confirmed"


# ---------------------------------------------------------------- 时间解析


def test_parse_hhmmss_formats():
    assert _parse_hhmmss("09:35:00") == (9, 35, 0)
    assert _parse_hhmmss("09:35") == (9, 35, 0)
    assert _parse_hhmmss("935") == (9, 35, 0)
    assert _parse_hhmmss("0935") == (9, 35, 0)
    assert _parse_hhmmss("") is None
    assert _parse_hhmmss(None) is None
    assert _parse_hhmmss("abc") is None


def test_seal_after_boundary():
    pub = datetime(2026, 9, 10, 10, 0, 0)
    assert _seal_after(pub, "10:00:00", TODAY) is True   # 含边界
    assert _seal_after(pub, "09:59:59", TODAY) is False
    assert _seal_after(pub, None, TODAY) is False
    assert _seal_after(None, "10:00:00", TODAY) is False
    # 昨日事件跨日
    assert _seal_after(datetime(2026, 9, 9, 20, 0, 0), "09:31:00", TODAY) is True
    # 当前可见版本晚于池的交易日，池里的封板绝不可能算在版本之后。
    assert _seal_after(datetime(2026, 9, 11, 9, 0, 0), "10:30:00", TODAY) is False


# ---------------------------------------------------------------- 编排层


def test_verify_active_events_orchestration(monkeypatch):
    """编排：活跃事件 → 批取板块资金 → 逐个判定；无题材事件如实 unknown。"""
    import asyncio
    from types import SimpleNamespace

    from app.events import verify as ev
    from app.services import theme_service as ts

    class _Store:
        def list_events(self, active_only=True, limit=30):
            return [
                SimpleNamespace(id=1, title="低空经济政策", published_at=datetime(2026, 9, 10, 10, 0, 0),
                                interpretation_ref={"version_id": 1, "available_at": "2026-09-10 10:00:00"},
                                directions=[SimpleNamespace(target_type="theme", target="低空经济")]),
                SimpleNamespace(id=2, title="个股异动", published_at=datetime(2026, 9, 10, 10, 0, 0),
                                directions=[SimpleNamespace(target_type="symbol", target="600519")]),
            ]

    async def fake_rows(names):
        assert "低空经济" in names
        return {"低空经济": {"name": "低空经济", "board_code": "BK1", "main_net_yi": 3.0}}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)

    pool = [{"symbol": "600001", "reason": "低空经济", "first_seal_time": "10:30:00"}]
    out = asyncio.run(ev.verify_active_events(_Store(), None, pool, TODAY, limit=10))
    by_id = {o["event_id"]: o for o in out}
    assert by_id[1]["status"] == "confirmed"
    assert by_id[1]["board_name"] == "低空经济"
    assert by_id[2]["status"] == "unknown"          # 只有 symbol direction，无 theme → 不臆造
    assert by_id[2]["board_name"] is None


def test_verify_active_events_does_not_confirm_llm_hypothesis(monkeypatch):
    """模型猜测的题材即使碰巧与涨停和资金同向，也不能判为消息发酵。"""
    import asyncio
    from types import SimpleNamespace

    from app.events import verify as ev
    from app.services import theme_service as ts

    class _Store:
        def list_events(self, active_only=True, limit=30):
            return [SimpleNamespace(
                id=1, title="模型猜测关联低空经济",
                published_at=datetime(2026, 9, 10, 10, 0),
                interpretation_ref={"version_id": 1, "available_at": "2026-09-10 10:00:00"},
                directions=[SimpleNamespace(target_type="theme", target="低空经济", matched_by="llm_aux")],
            )]

    async def fake_rows(names):
        assert names == []
        return {}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)
    monkeypatch.setattr(ev, "beijing_now_naive", lambda: datetime(2026, 9, 10, 11, 0))
    pool = _pool(("10:30:00", "低空经济"))

    item = asyncio.run(ev.verify_active_events(_Store(), None, pool, TODAY))[0]

    assert item["status"] == "unknown"
    assert item["themes"] == []
    assert item["board_name"] is None
    assert item["new_limit_ups"] == 0
    assert "待验证" in item["basis"]


def test_late_visible_interpretation_excludes_earlier_seal(monkeypatch):
    """晚到解释不能把系统可见前的封板算作消息后发酵。"""
    import asyncio
    from types import SimpleNamespace

    from app.events import verify as ev
    from app.services import theme_service as ts

    class _Store:
        def list_events(self, active_only=True, limit=30):
            return [SimpleNamespace(
                id=1, title="晚到的低空经济消息",
                published_at=datetime(2026, 9, 10, 9, 0),
                interpretation_ref={
                    "version_id": 8, "available_at": "2026-09-10 10:10:00",
                },
                directions=[SimpleNamespace(target_type="theme", target="低空经济")],
            )]

    async def no_fund(_names):
        return {}

    monkeypatch.setattr(ts, "board_rows_for_names", no_fund)
    monkeypatch.setattr(ev, "beijing_now_naive", lambda: datetime(2026, 9, 10, 11, 0))
    pool = _pool(("09:45:00", "低空经济"), ("10:30:00", "低空经济"))

    item = asyncio.run(ev.verify_active_events(_Store(), None, pool, TODAY))[0]

    assert item["new_limit_ups"] == 1
    assert item["age_minutes"] == 50.0


def test_verify_active_events_empty_events_short_circuit(monkeypatch):
    """无活跃事件 → 直接返回空，不触发板块资金批取（零上游调用）。"""
    import asyncio

    from app.events import verify as ev
    from app.services import theme_service as ts

    called = {"n": 0}

    async def boom(names):
        called["n"] += 1
        return {}

    monkeypatch.setattr(ts, "board_rows_for_names", boom)

    class _Empty:
        def list_events(self, active_only=True, limit=30):
            return []

    assert asyncio.run(ev.verify_active_events(_Empty(), None, [], TODAY)) == []
    assert called["n"] == 0


# ---------------------------------------------------------------- 端点 handler（受控桩）


def test_verify_events_endpoint_produces_confirmed(monkeypatch):
    """端点整链路（取涨停池 → 转 dict → 编排 → 判定）在受控数据下产出 confirmed。

    真实库当前无「盘中入库且已过窗口」的样本（事件皆盘后刚入库），故用受控桩
    验证非-unknown 分支能从端点走通，而非只停在纯函数层。
    """
    import asyncio
    from types import SimpleNamespace

    from app.api.routes import events as ev_routes
    from app.market import trade_calendar as tc
    from app.services import theme_service as ts

    class _Provider:
        name = "chain(mock)"

        async def get_limit_up_pool(self, trade_date):
            # 契约：返回 LimitUpRecord（pydantic），端点会调 model_dump() 转 dict
            return [SimpleNamespace(model_dump=lambda: {
                "symbol": "600001", "name": "X", "reason": "低空经济",
                "first_seal_time": "10:30:00",
            })]

    class _Store:
        def list_events(self, active_only=True, limit=30):
            return [SimpleNamespace(id=1, title="低空经济政策",
                                    published_at=datetime(2026, 9, 10, 10, 0, 0),
                                    interpretation_ref={"version_id": 1, "available_at": "2026-09-10 10:00:00"},
                                    directions=[SimpleNamespace(target_type="theme", target="低空经济")])]

    async def fake_trading_days(provider):
        return [date(2026, 9, 10)]

    monkeypatch.setattr(tc, "trading_days", fake_trading_days)

    async def fake_rows(names):
        return {"低空经济": {"name": "低空经济", "board_code": "BK1", "main_net_yi": 5.0}}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)

    hub = SimpleNamespace(provider=_Provider())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=hub)))
    payload = asyncio.run(ev_routes.verify_events(request=request, limit=10, store=_Store()))
    items = payload["data"]["items"]
    assert payload["data"]["trade_date"] == "2026-09-10"
    assert items[0]["status"] == "confirmed"
    assert items[0]["new_limit_ups"] == 1
    assert items[0]["net_inflow_yi"] == 5.0
