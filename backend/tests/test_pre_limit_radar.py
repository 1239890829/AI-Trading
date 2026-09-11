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
    # 2026-07-06 并轨：主板 ST 不再降档，与主板普通股同为 10%
    assert board_limit_pct("600073", "ST某某") == 10.0
    assert board_limit_pct("832000") == 30.0


def test_board_limit_st_no_longer_narrows_any_board():
    """ST 并轨（2026-07-06）后，ST 状态不改变任何板块的涨跌幅。

    旧实现「ST 名称优先于代码段」会把双创/北交所 ST 误判为 5%——
    这里把三类板的 ST 与大板非 ST 并列断言，锁死回归。
    """
    # 主板 ST = 主板普通股
    assert board_limit_pct("600073", "ST某某") == board_limit_pct("600519")
    # 创业板 ST / 科创板 ST 维持板块口径 20%
    assert board_limit_pct("300123", "*ST某某") == 20.0
    assert board_limit_pct("688123", "ST某某") == 20.0
    # 北交所 ST 维持 30%
    assert board_limit_pct("833123", "ST某某") == 30.0
    # B 股（900xxx）不应被误判为北交所 30%——旧实现 startswith("9") 会踩
    assert board_limit_pct("900901", "某B股") == 10.0


def test_zone_boundaries_10cm():
    # 10cm：临板区 [6.5, 9.7)
    assert in_pre_limit_zone(6.5, 10.0)
    assert in_pre_limit_zone(9.69, 10.0)
    assert not in_pre_limit_zone(6.4, 10.0)
    assert not in_pre_limit_zone(9.7, 10.0)  # 9.7 = 封板判定线
    assert is_sealed(9.7, 10.0)
    assert not is_sealed(9.69, 10.0)
    assert seal_threshold(10.0) == 9.7


def test_zone_boundaries_20cm_and_scaling():
    # 20cm：[13.0, 19.7)
    assert pre_limit_floor(20.0) == 13.0
    assert in_pre_limit_zone(13.0, 20.0)
    assert not in_pre_limit_zone(12.9, 20.0)
    assert is_sealed(19.7, 20.0)
    # 缩放公式对任意板性成立（10cm 下沿 6.5）
    assert pre_limit_floor(10.0) == 6.5
    assert in_pre_limit_zone(6.5, 10.0)


def test_st_now_uses_main_board_zone():
    """ST 并轨（2026-07-06）后主板 ST 走 10cm 临板区。

    用户实测案例：002547（*ST春兴）09-10 收到「临板 3.5%」预警——那是**旧口径**
    （ST=5% → 临板区 [3.2, 4.7)）的产物；并轨后 3.5% 不再是临板，需涨到 6.5%
    才进入预警区。
    """
    assert board_limit_pct("002547", "*ST春兴") == 10.0
    assert not in_pre_limit_zone(3.5, 10.0)   # 旧口径的 3.5% 不再触发
    assert not in_pre_limit_zone(6.4, 10.0)   # 新下沿之下
    assert in_pre_limit_zone(6.6, 10.0)       # 新口径临板区
    assert is_sealed(9.7, 10.0)


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
