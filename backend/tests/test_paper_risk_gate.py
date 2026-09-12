"""风控硬拦截接入模拟撮合（retro §6.5b #2，2026-09-13）的引擎级测试。

口径（见 PaperTradingEngine 构造函数注释）：
- 仅 main 账户 + 仅买入方向；shadow 豁免（研究仪器）、卖出永不拦截（减风险）；
- 挂单撮合期不复查——风控时点是下单，撮合期否决会留下冻结挂单；
- 预检异常**保守拒单**（不知道是否安全时宁可拒并说明原因）；
- 预检数据组装与 /api/risk/check-order 同口径（risk_check_context 单点）。
"""
import sys

sys.path.insert(0, ".")

import asyncio

from app.core.db import get_session_factory
from app.paper.engine import PaperTradingEngine
from app.risk.config import get_params
from app.risk.engine import RiskEngine
from app.schemas.market import Quote


class FakeHub:
    def __init__(self, quotes=None, error: Exception | None = None):
        self._quotes = quotes or []
        self._error = error

    def get_quotes(self):
        if self._error is not None:
            raise self._error
        return self._quotes


def make_risk(state: str = "震荡偏多", hub=None) -> RiskEngine:
    """构造带缓存态的真实 RiskEngine（不经 refresh，直接钉状态——同 test_risk）。"""
    re_ = RiskEngine(hub=hub if hub is not None else FakeHub(), snapshot_service=None, session_factory=None)
    re_._state = state
    re_._params = get_params(state)
    return re_


def make_engine(quote_map, *, scope="main", risk_engine=None):
    async def quote_fn(symbol):
        return quote_map.get(symbol)

    return PaperTradingEngine(get_session_factory(), quote_fn, scope=scope, risk_engine=risk_engine)


def reset():
    from app.core.db import get_engine
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        for m in (PaperAccount, PaperOrder, PaperPosition):
            db.query(m).delete()
        db.commit()


def q600519(price=100.0):
    return Quote(symbol="600519", price=price, limit_up_price=110.0, limit_down_price=90.0, source="t")


def test_risk_gate_blocks_buy_in_bear_on_main():
    reset()
    engine = make_engine({"600519": q600519()}, risk_engine=make_risk("下跌趋势"))
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "rejected", order.reason
    assert "风控拦截" in order.reason
    assert "下跌趋势" in order.reason
    # 资金未被占用、持仓未建立
    acc = engine.ensure_account()
    assert acc.cash == 1_000_000.0
    from app.models.paper import PaperPosition

    with engine._sf() as db:
        assert db.query(PaperPosition).count() == 0


def test_risk_gate_allows_small_buy():
    reset()
    engine = make_engine({"600519": q600519()}, risk_engine=make_risk("震荡偏多"))
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "filled", order.reason


def test_sell_never_blocked_by_risk_even_in_bear():
    reset()
    engine = make_engine({"600519": q600519()}, risk_engine=make_risk("震荡偏多"))
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "filled", order.reason
    # 市场转入下跌趋势后：买入会被拦，但卖出（减风险）必须畅通
    engine._risk_engine = make_risk("下跌趋势")
    with engine._sf() as db:
        from app.models.paper import PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        pos.frozen_today = 0  # 模拟 T+1 次日
        db.commit()
    sell = asyncio.run(engine.place_order("600519", "sell", 99.0, 100))
    assert sell.status == "filled", sell.reason


def test_shadow_scope_not_gated():
    reset()
    engine = make_engine({"600519": q600519()}, scope="shadow", risk_engine=make_risk("下跌趋势"))
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "filled", order.reason


def test_risk_precheck_exception_fails_closed():
    reset()
    broken_hub = FakeHub(error=RuntimeError("hub down"))
    engine = make_engine({"600519": q600519()}, risk_engine=make_risk("震荡偏多", hub=broken_hub))
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "rejected", order.reason
    assert "风控预检异常" in order.reason
    assert "保守拒单" in order.reason


def test_match_pending_does_not_recheck_risk():
    """风控时点是下单：挂单期间市场状态转差，撮合期**不**重复风控。

    撮合期再否决会留下既不可成交也难自解释的冻结挂单（资金已扣）；
    用户保留撤单（cancel）作为退出路径。
    """
    reset()
    quote_map = {"600519": q600519(100.0)}
    engine = make_engine(quote_map, risk_engine=make_risk("震荡偏多"))
    # 95 < 100 → 挂单（通过下单时风控）
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 100))
    assert order.status == "pending", order.reason
    # 市场转入下跌趋势 + 价格回落到挂单价
    engine._risk_engine = make_risk("下跌趋势")
    quote_map["600519"] = q600519(95.0)
    pending_left = asyncio.run(engine.match_pending())
    assert pending_left == 0
    from app.models.paper import PaperOrder

    with engine._sf() as db:
        o = db.query(PaperOrder).filter(PaperOrder.id == order.id).one()
        assert o.status == "filled", o.reason


def test_risk_gate_respects_total_position_cap():
    """「数据不足」保守参数下，超上限的买入被拦——机制对仓位类理由同样生效。"""
    reset()
    engine = make_engine({"600519": q600519()}, risk_engine=make_risk("数据不足"))
    # 数据不足：总仓位上限 20%（20 万）。2000 股 × 100 = 20 万，含费用后超线
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 2000))
    assert order.status == "rejected", order.reason
    assert "风控拦截" in order.reason
