"""临板雷达（KB-DEC-011）测试：板性阈值 / 临板区边界 / 封板拒绝 / 准入选择。

政策：只有涨停前提醒过的股票才准入盘中跟踪；封板后才发现的一律不入册。
"""

from app.picks.pre_limit_radar import (
    board_limit_pct,
    in_pre_limit_zone,
    is_sealed,
    pre_limit_floor,
    select_candidates,
    seal_threshold,
)


def test_board_limit_by_symbol_and_name():
    assert board_limit_pct("600519") == 10.0
    assert board_limit_pct("000001") == 10.0
    assert board_limit_pct("300750") == 20.0
    assert board_limit_pct("688981") == 20.0
    assert board_limit_pct("600073", "ST某某") == 5.0
    assert board_limit_pct("832000") == 30.0


def test_zone_boundaries_10cm():
    # 10cm：临板区 [6.5, 9.7)
    assert in_pre_limit_zone(6.5, 10.0)
    assert in_pre_limit_zone(9.69, 10.0)
    assert not in_pre_limit_zone(6.4, 10.0)
    assert not in_pre_limit_zone(9.7, 10.0)  # 9.7 = 封板判定线
    assert is_sealed(9.7, 10.0)
    assert not is_sealed(9.69, 10.0)
    assert seal_threshold(10.0) == 9.7


def test_zone_boundaries_20cm_and_st():
    # 20cm：[13.0, 19.7)
    assert pre_limit_floor(20.0) == 13.0
    assert in_pre_limit_zone(13.0, 20.0)
    assert not in_pre_limit_zone(12.9, 20.0)
    assert is_sealed(19.7, 20.0)
    # ST 5%：[3.2, 4.7)
    assert pre_limit_floor(5.0) == 3.2
    assert in_pre_limit_zone(3.5, 5.0)
    assert is_sealed(4.7, 5.0)


def test_select_candidates_skips_sealed_and_registered():
    rows = [
        # 封板 → 涨停后才发现，一律不入册（用户指令）
        {"symbol": "600001", "name": "已封板", "change_pct": 10.01, "price": 11.0},
        # 临板区 → 入选
        {"symbol": "600002", "name": "临板股", "change_pct": 7.2, "price": 10.5, "turnover_rate": 8.1},
        # 20cm 临板区 → 入选
        {"symbol": "300001", "name": "创业临板", "change_pct": 14.0, "price": 22.0},
        # 未达临板下沿 → 拒绝
        {"symbol": "600003", "name": "没动静", "change_pct": 2.1, "price": 5.0},
        # 已登记（当日去重）→ 拒绝
        {"symbol": "600004", "name": "已入册", "change_pct": 8.0, "price": 9.0},
        # 无涨幅数据 → 拒绝（三态：不臆造）
        {"symbol": "600005", "name": "无数据", "change_pct": None, "price": 3.0},
    ]
    out = select_candidates(rows, {"600004"})
    assert [c["symbol"] for c in out] == ["600002", "300001"]  # runway 升序：2.5 < 5.7
    assert out[0]["runway_pct"] == 2.5
    assert out[0]["limit_pct"] == 10.0
    assert out[1]["limit_pct"] == 20.0


def test_broken_seal_reenters_zone():
    """炸板回落到临板区 = 新的涨停前信号（此前从未在涨停前提醒过 → 允许预警）。"""
    rows = [{"symbol": "600006", "name": "炸板股", "change_pct": 8.5, "price": 9.3}]
    out = select_candidates(rows, set())
    assert len(out) == 1 and out[0]["symbol"] == "600006"
    assert out[0]["runway_pct"] == 1.2
