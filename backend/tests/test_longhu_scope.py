"""龙虎榜「统计区间」维度回归测试（2026-09-02）。

背景：交易所对同一只股票可同时披露「当日榜」与「三日榜」（触发条件不同：
如日涨幅偏离值达 7% vs 连续三日偏离值累计达 20%），两者是两条独立记录、
buy/sell/net 为不同区间的累计值。此前 provider 未解析 `range_days`，
下游以 `{r.symbol: r for r in records}` 建映射 → 静默覆盖，取到哪条取决于
服务端返回顺序，不报错、不可复现。

实测反例（2026-08-31 ths）：002396 星网锐捷 日榜净额 −97,837,806.84、
三日榜 +72,522,739.51 —— **符号相反**。旧写法会让"游资净买入"证据方向随机反转。

本文件的核心断言是第 1 条：它把"顺序依赖"这个静默失效固化成可执行的回归证明。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.data_providers.ths import ThsFuyaoProvider
from app.predict.collector import pick_daily_board
from app.schemas.market import LongHuRecord

TD = date(2026, 8, 31)

# 2026-08-31 真实样本（同股两榜，净额符号相反）
_DAILY = LongHuRecord(
    symbol="002396", name="星网锐捷", trade_date=TD, change_pct=10.0027,
    net_buy=-97_837_806.84, buy_amount=784_975_015.34, sell_amount=882_812_822.18,
    range_days=1, org_net_value=-43_260_734.89, hot_money_net_value=111_462_997.28,
    hot_rank=27, source="ths",
)
_3DAY = LongHuRecord(
    symbol="002396", name="星网锐捷", trade_date=TD, change_pct=10.0027,
    net_buy=72_522_739.51, buy_amount=1_276_208_898.22, sell_amount=1_203_686_158.71,
    range_days=3, org_net_value=-58_527_366.51, hot_money_net_value=116_173_196.38,
    hot_rank=27, source="ths",
)


def test_pick_daily_board_is_order_independent():
    """核心回归：选榜结果必须与服务端返回顺序无关。

    顺带证明旧写法为何不可用 —— dict 覆盖在两种顺序下会取到不同的记录，
    且两记录净额符号相反，直接导致资金验证结论反向。
    """
    fwd = [_DAILY, _3DAY]
    rev = [_3DAY, _DAILY]

    # 旧写法：结果随顺序变化（这正是被修复的缺陷）
    legacy_fwd = {r.symbol: r for r in fwd}["002396"]
    legacy_rev = {r.symbol: r for r in rev}["002396"]
    assert legacy_fwd.net_buy != legacy_rev.net_buy
    assert legacy_fwd.net_buy * legacy_rev.net_buy < 0  # 符号相反

    # 新写法：两种顺序结果一致，且恒为当日榜
    assert pick_daily_board(fwd)["002396"] is _DAILY
    assert pick_daily_board(rev)["002396"] is _DAILY


def test_pick_daily_board_falls_back_to_3day_when_no_daily():
    """只有三日榜时回退，调用方须用 .range_days 标注口径，不得当日榜读。"""
    out = pick_daily_board([_3DAY])
    assert out["002396"] is _3DAY
    assert out["002396"].range_days == 3


def test_pick_daily_board_treats_missing_range_days_as_daily():
    """range_days 缺失（东财 datacenter 口径，本身就是日榜）当作当日榜，不丢弃。"""
    na = LongHuRecord(symbol="600519", trade_date=TD, net_buy=1.0, source="eastmoney")
    out = pick_daily_board([na])
    assert out["600519"] is na


def test_pick_daily_board_does_not_sum_across_boards():
    """两榜金额不可相加：必须只保留一条，不得合并成 -25,315,067。"""
    out = pick_daily_board([_DAILY, _3DAY])
    assert out["002396"].net_buy == pytest.approx(-97_837_806.84)
    assert out["002396"].net_buy != pytest.approx(-97_837_806.84 + 72_522_739.51)


def test_pick_daily_board_skips_blank_symbol():
    blank = LongHuRecord(symbol="", trade_date=TD, net_buy=9.0, source="ths")
    assert pick_daily_board([blank]) == {}


# ---------------- provider 字段解析 ----------------

_STOCK_ITEMS = [
    {
        "thscode": "002396.SZ", "ticker": "002396", "name": "星网锐捷",
        "concept_list": [{"name": "WiFi 6"}, {"name": "F5G概念"}],
        "change": 0.100027, "buy_value": 784_975_015.34, "sell_value": 882_812_822.18,
        "net_value": -97_837_806.84, "net_rate": -0.01861647,
        "org_net_value": -43_260_734.89, "hot_money_net_value": 111_462_997.28,
        "hot_rank": 27, "range_days": 1,
        "limit_reason": "数据中心交换机+光通信+福建国资",
    },
    # 该榜单无机构席位参与 —— org_net_value 字段整个缺席
    {
        "thscode": "600227.SH", "ticker": "600227", "name": "赤天化",
        "concept_list": [{"name": "煤化工概念"}],
        "change": 0.100437, "buy_value": 172_081_579.0, "sell_value": 107_296_711.2,
        "net_value": 64_784_867.8, "net_rate": 0.0620065,
        "hot_money_net_value": 34_658_482.4,
        "hot_rank": 15, "range_days": 1,
        "limit_reason": "中报扭亏+甲醇+尿素+煤化工",
    },
]


# 项目无 pytest-asyncio，统一用 asyncio.run（与 tests/test_predict.py 同款写法）
def _run_provider(monkeypatch, payload: list[dict]) -> list[LongHuRecord]:
    async def fake_get(self, path: str, params: dict | None = None):  # noqa: ANN001
        fake_get.path = path  # type: ignore[attr-defined]
        fake_get.params = params  # type: ignore[attr-defined]
        return {"stock_items": payload}

    monkeypatch.setattr(ThsFuyaoProvider, "_get", fake_get)
    prov = ThsFuyaoProvider(api_key="test-key")
    return asyncio.run(prov.get_longhu_records(TD))


def test_ths_parses_range_days_and_capital_structure(monkeypatch):
    out = _run_provider(monkeypatch, _STOCK_ITEMS)
    assert len(out) == 2

    first = next(r for r in out if r.symbol == "002396")
    assert first.range_days == 1
    assert first.net_rate == pytest.approx(-0.01861647)
    assert first.org_net_value == pytest.approx(-43_260_734.89)
    assert first.hot_money_net_value == pytest.approx(111_462_997.28)
    assert first.hot_rank == 27
    assert first.concept_tags == "WiFi 6,F5G概念"
    # 回归保护：ths 的 change 是小数比例，须换算成百分数
    assert first.change_pct == pytest.approx(10.0027)


def test_ths_missing_org_net_stays_none_not_zero(monkeypatch):
    """机构席位缺席 ≠ 净额为 0。填 0 会把"无机构参与"误报成"机构多空平衡"。"""
    out = _run_provider(monkeypatch, _STOCK_ITEMS)
    second = next(r for r in out if r.symbol == "600227")
    assert second.org_net_value is None
    assert second.hot_money_net_value == pytest.approx(34_658_482.4)


def test_ths_longhu_uses_documented_params(monkeypatch):
    """请求参数必须是 date(YYYY-MM-DD) + board_type，不是 date_ms/size。"""
    _run_provider(monkeypatch, _STOCK_ITEMS)
    assert ThsFuyaoProvider._get.path == "/api/a-share/special-data/dragon-tiger-list"  # type: ignore[attr-defined]
    assert ThsFuyaoProvider._get.params == {"date": "2026-08-31", "board_type": "all"}  # type: ignore[attr-defined]
