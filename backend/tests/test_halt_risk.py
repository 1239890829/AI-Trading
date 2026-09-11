"""停牌核查 / 异动风险评估单测（docs/halt-check-risk-analysis.md 第一批规则）。

覆盖重点：
1. 偏离值必须减指数（不减 = 系统性误判）
2. 连板判定要容忍涨停价四舍五入（9.97% / 10.1% 都算涨停）
3. 三档规则各自触发边界，以及红线命中时不再重复扣黄线分
4. 数据不足时诚实降级，不拿短区间冒充长区间
5. 校准反漂移（P1-22）：参数值、结构约束、核验脚本的「import 生产模块」纪律
"""
from pathlib import Path

import pytest

from app.picks import halt_risk as hr
from app.picks.halt_risk import (
    assess,
    benchmark_symbol,
    board_of,
    consecutive_limit_up_days,
    deviation_pct,
    interval_pct,
    limit_pct,
    risk_labels,
    veto_reasons,
)

_REPO = Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT = _REPO / "backend" / "scripts" / "verify_halt_risk.py"


def _bars(pcts, start=10.0):
    """按涨幅序列构造日 K（close 按复利推进），末尾为最新。"""
    bars, close = [], start
    for p in pcts:
        close = close * (1 + p / 100)
        bars.append({"close": round(close, 4), "change_pct": p})
    return bars


def _flat(n, close=100.0):
    """横盘指数（每日 0%）。"""
    return [{"close": close, "change_pct": 0.0} for _ in range(n)]


# ---- 板块与涨限 ----

@pytest.mark.parametrize(
    "symbol,name,board,lim",
    [
        ("600540", "新赛股份", "sh_main", 10.0),
        ("000017", "深中华A", "sz_main", 10.0),
        ("300750", "宁德时代", "gem", 20.0),
        ("688981", "中芯国际", "star", 20.0),
        ("830799", "艾融软件", "bse", 30.0),
        ("600122", "*ST 宏图", "st", 10.0),
        ("000004", "国华网安 ST", "st", 10.0),
        # 2026-09-08 修复：代码前缀优先于 ST 名称——双创/北交所 ST 归各自板块
        ("300123", "*ST 某某", "gem", 20.0),
        ("301599", "ST 某某", "gem", 20.0),
        ("688123", "ST 某某", "star", 20.0),
        ("833123", "ST 某某", "bse", 30.0),
    ],
)
def test_board_and_limit(symbol, name, board, lim):
    assert board_of(symbol, name) == board
    assert limit_pct(board) == lim


def test_gem_st_limit_up_counted_at_20pct():
    """创业板 ST 一根 19.95% 的收盘涨停必须计入连板——按旧的 ST=5% 口径会漏判。"""
    bars = _bars([19.95, 19.9])
    assert consecutive_limit_up_days(bars, limit_pct("gem")) == 2


def test_benchmark_symbol_maps_every_board():
    assert benchmark_symbol("sh_main") == "sh000001"
    assert benchmark_symbol("sz_main") == "399107"
    assert benchmark_symbol("gem") == "399102"
    assert benchmark_symbol("star") == "sh000688"
    assert benchmark_symbol("bse") == "899050"


def test_st_uses_its_own_market_benchmark():
    """ST 不是独立市场：基准指数仍按所属市场取（"st" 只影响板块键）。

    早期实现直接查 BENCHMARK_INDEX["st"] → KeyError 崩溃，ST 股全部评估失败。
    """
    assert benchmark_symbol("st", "600122") == "sh000001"   # 沪市 ST
    assert benchmark_symbol("st", "000004") == "399107"     # 深市 ST


def test_st_assess_uses_10pct_after_2026_rule():
    """2026-07-06 并轨后主板 ST 涨限为 10%（旧断言 5.0 已过期）。"""
    res = assess(symbol="600122", name="*ST 宏图", bars=_bars([1.0] * 11), index_bars=_flat(12))
    assert res["board"] == "st"
    assert res["limit_pct"] == 10.0
    assert res["benchmark"] == "sh000001"


# ---- 指标计算 ----

def test_interval_pct_needs_n_plus_1_bars():
    bars = _bars([10, 10, 10, 10])  # 4 根
    assert interval_pct(bars, 3) is not None
    assert interval_pct(bars, 4) is None  # 只有 4 根，算 4 日需要 5 根
    assert interval_pct([], 3) is None


def test_deviation_subtracts_index():
    """核心口径：指数大涨时，个股涨 30% 也可能不触发异动。"""
    stock = _bars([0, 10, 10, 10], start=10.0)      # 3 日 +33.1%
    index = _bars([0, 10, 10, 10], start=100.0)     # 同步 +33.1%
    dev = deviation_pct(stock, index, 3)
    assert dev is not None
    assert abs(dev) < 0.01  # 与指数同步 → 偏离值约 0，不该判异动

    dev2 = deviation_pct(stock, _flat(4, close=100.0), 3)
    assert dev2 is not None and abs(dev2 - 33.1) < 0.5  # 指数横盘 → 偏离≈个股涨幅


def test_deviation_none_when_index_missing():
    """指数缺失 → 返回 None，绝不拿个股涨幅冒充偏离值。"""
    assert deviation_pct(_bars([10, 10, 10, 10]), [], 3) is None
    assert deviation_pct([], _flat(4), 3) is None


def test_consecutive_boards_tolerates_price_rounding():
    """涨停价四舍五入到分 → 实际涨幅 9.97%~10.10% 都算涨停。"""
    bars = _bars([10.07, 10.02, 10.10, 9.97, 10.01])
    assert consecutive_limit_up_days(bars, 10.0) == 5
    # 中间断一天则只算最后连续段
    bars2 = _bars([10.02, 10.10, 3.0, 9.97, 10.01])
    assert consecutive_limit_up_days(bars2, 10.0) == 2
    # 大幅低于涨限不算（9.0% 距 10% 差 1pct，超过 0.6 容差）
    bars3 = _bars([10.02, 9.0, 9.99])
    assert consecutive_limit_up_days(bars3, 10.0) == 1


def test_boards_computed_from_close_when_change_pct_missing():
    """change_pct 字段缺失时，用收盘价序列自算。

    实测腾讯源当日 bar 的 change_pct 为 None，且熔断切源后时有时无——
    只认该字段会让连板数恒为 0，全市场看起来都没有停牌风险（静默失效）。
    """
    bars = [
        {"close": 10.0, "change_pct": None},   # 起点（无法自算，但也不需要）
        {"close": 11.0, "change_pct": None},
        {"close": 12.1, "change_pct": None},
        {"close": 13.31, "change_pct": None},
    ]
    assert consecutive_limit_up_days(bars, 10.0) == 3


def test_consecutive_boards_stops_when_pct_uncomputable():
    """涨跌幅彻底算不出来（change_pct 与 close 都缺）→ 中断，不臆造。"""
    # 最新一根无法计算 → 连板数 0（不能拿更旧的数据冒充最新）
    assert consecutive_limit_up_days(
        [{"close": 11.0, "change_pct": 10.0}, {"close": None, "change_pct": None}], 10.0
    ) == 0
    # 中间一根算不出来 → 只累计它之后的连续段
    assert consecutive_limit_up_days(
        [
            {"close": 10.0, "change_pct": 10.0},
            {"close": None, "change_pct": None},
            {"close": 12.1, "change_pct": 10.0},
        ],
        10.0,
    ) == 1


# ---- 红线 ----

def test_red_r1_when_10d_deviation_over_80():
    stock = _bars([6.2] * 11)  # 10 日 1.062^10 ≈ +82.3%（指数横盘）
    res = assess(symbol="600540", name="测试A", bars=stock, index_bars=_flat(12))
    assert 80 <= res["dev_10d"] < 100
    assert any(r.startswith("R1") for r in res["red_lines"])
    assert not res["yellow_lines"]  # 红线命中不再重复扣黄线


def test_no_red_when_just_below_80():
    """边界：79% 不触发红线，但落进 Y3 的 50%~80% 区间。"""
    stock = _bars([6.0] * 11)  # 10 日 1.06^10 ≈ +79.1%
    res = assess(symbol="600540", bars=stock, index_bars=_flat(12))
    assert res["dev_10d"] < 80
    assert res["red_lines"] == []
    assert any(y["code"] == "Y3" for y in res["yellow_lines"])


def test_red_r2_when_10d_over_100():
    stock = _bars([7.5] * 11)  # 10 日约 +106%
    res = assess(symbol="600540", bars=stock, index_bars=_flat(12))
    assert res["dev_10d"] >= 100
    assert any(r.startswith("R2") for r in res["red_lines"])
    assert "严重异常波动" in res["red_lines"][0]


def test_red_r3_when_suspended():
    res = assess(symbol="600540", bars=_bars([1.0] * 11), index_bars=_flat(12), suspended=True)
    assert any(r.startswith("R3") for r in res["red_lines"])


def test_red_30d_fallback_when_10d_below():
    """缓慢推升：10 日未越线但 30 日累计 ≥200%。"""
    stock = _bars([3.8] * 31)
    res = assess(symbol="600540", bars=stock, index_bars=_flat(32))
    assert res["dev_30d"] >= 200
    assert any(r.startswith("R2") for r in res["red_lines"])


# ---- 黄线与仓位 ----

def test_yellow_y1_triggered_at_board_threshold():
    # 首根是起点（0%），其后 3 个交易日各 +7% → 3 日累计 +22.5%
    stock = _bars([0.0, 7.0, 7.0, 7.0])
    res = assess(symbol="600540", bars=stock, index_bars=_flat(5))
    assert res["dev_3d"] >= 20
    assert [y["code"] for y in res["yellow_lines"]] == ["Y1"]
    assert res["penalty"] == 6.0
    assert res["position_factor"] == round(1 / 3, 3)  # P2


def _y2_penalty(bars):
    """只看 Y2 那一项的扣分。

    两个隔离点，否则测不出 Y2 单独的值：
    ① 连板必然同时触发 Y1（3 个涨停 = 33% 偏离），总扣分不等于 Y2 的值；
    ② 高连板会先触发 R2 红线，而红线命中时黄线不再扣分 → 必须用同步大涨的
       指数把偏离值压下来，才能单独观察连板这一维。
    """
    idx = _bars([8.0] * (len(bars) + 1))  # 指数 10 日 +115.9%，抵消个股连板涨幅
    res = assess(symbol="600540", bars=bars, index_bars=idx)
    return next((y["penalty"] for y in res["yellow_lines"] if y["code"] == "Y2"), 0.0)


def test_yellow_y2_scales_with_boards():
    base = [0.0] * 5
    assert _y2_penalty(_bars(base + [10.0] * 4)) == 5.0
    assert _y2_penalty(_bars(base + [10.0] * 5)) == 10.0
    assert _y2_penalty(_bars(base + [10.0] * 6)) == 15.0
    assert _y2_penalty(_bars(base + [10.0] * 8)) == 15.0  # ≥6 板封顶
    assert _y2_penalty(_bars(base + [10.0] * 3)) == 0.0   # 3 板不扣


def test_position_p1_halves_at_three_boards():
    """3 连板触发 P1（仓位减半）；指数同步大涨抵消掉 3 日偏离，隔离出 P1 单独作用。"""
    stock = _bars([0.0] * 8 + [10.0] * 3)        # 3 日 +33.1%
    index = _bars([0.0] * 8 + [5.0, 5.0, 5.0])   # 指数 3 日 +15.8% → 偏离 17.3% < 20
    res = assess(symbol="600540", bars=stock, index_bars=index)
    assert res["boards"] == 3
    assert res["dev_3d"] < 20          # 未触发 Y1
    assert res["position_factor"] == 0.5
    assert any("P1" in n for n in res["notes"])
    assert not any("P2" in n for n in res["notes"])


def test_penalties_accumulate_and_position_takes_strictest():
    """同时命中 Y1 + Y2 + Y3 → 扣分累加（6+5+8），仓位取最严 1/3。"""
    # 11 根：起点 + 6 日小涨 + 4 个涨停 → 10 日偏离约 +75%（落在 Y3 区间、未到红线）
    stock = _bars([0.0] + [3.0] * 6 + [10.0] * 4)
    res = assess(symbol="600540", bars=stock, index_bars=_flat(12))
    codes = [y["code"] for y in res["yellow_lines"]]
    assert set(codes) == {"Y1", "Y2", "Y3"}
    assert res["penalty"] == 19.0
    assert res["position_factor"] == round(1 / 3, 3)


# ---- 诚实降级 ----

def test_insufficient_bars_disables_rules_and_says_so():
    res = assess(symbol="600540", bars=_bars([10.0, 10.0]), index_bars=_flat(12))
    assert res["available"] is False
    assert res["dev_10d"] is None
    assert res["red_lines"] == [] and res["yellow_lines"] == []
    assert any("数据不足" in n for n in res["notes"])


def test_missing_index_disables_deviation_rules_only():
    """指数缺失时连板规则仍可算（不依赖指数），偏离值规则降级。"""
    stock = _bars([0.0] * 5 + [10.0] * 4)
    res = assess(symbol="600540", bars=stock, index_bars=[])
    assert res["boards"] == 4
    assert res["dev_3d"] is None
    assert any("基准指数日K不足" in n for n in res["notes"])
    # 连板仍是有效风险信号，照常扣分
    assert any(y["code"] == "Y2" for y in res["yellow_lines"])


def test_no_bars_at_all():
    res = assess(symbol="600540", bars=[], index_bars=[])
    assert res["available"] is False
    assert res["boards"] == 0
    assert res["penalty"] == 0.0


# ---- 文案输出 ----

def test_veto_reasons_and_labels():
    stock = _bars([7.5] * 11)
    res = assess(symbol="600540", bars=stock, index_bars=_flat(12))
    vetoes = veto_reasons(res)
    assert vetoes and "停牌核查" in vetoes[0]
    labels = risk_labels(res)
    assert labels and labels[0].startswith("🔴")

    safe = assess(symbol="600540", bars=_bars([0.1] * 11), index_bars=_flat(12))
    assert veto_reasons(safe) == []
    assert risk_labels(safe) == []


# ---- 校准反漂移（P1-22，2026-09-11）----
#
# 背景：这些参数长期以「经验初值」挂着。P1-22 用
# `scripts/verify_halt_risk.py` 做了实证核验，结论**不是"按均值调参"**——
# 红线组前瞻超额均值不显著（t=−0.20）、全池剔除红线仅 +0.0016pp/日，
# 但停牌 = 资金锁死、不可申报不可撤单 ⇒ 依「安全不对称性」保留硬排除。
# 完整判读见 `halt_risk.py` 末尾「校准结论」段与 [[KB-STOCK-31]]。
#
# 下面这些断言**不是**在测试数学能力（那由上面的边界用例覆盖），
# 而是在防止「数值被静默改动、而结论与理由没跟着改」——即文档与代码脱钩。


def test_calibrated_constants_match_documented_values():
    """参数值即校准结论的一部分：改动必须同时更新 KB-STOCK-31 与模块注释。

    若此断言失败，先问「新值有实测依据吗」——**不要**为了让测试变绿而回退数值。
    """
    assert hr.RED_DEV_10D == 80.0
    assert hr.YELLOW_DEV_10D_LO == 50.0
    assert hr.PENALTY_Y1 == 6.0
    assert hr.PENALTY_Y3 == 8.0
    assert hr.PENALTY_BOARDS == {4: 5.0, 5: 10.0}
    assert hr.PENALTY_BOARDS_MAX == 15.0
    assert hr.POSITION_FACTOR_P1 == 0.5
    assert hr.POSITION_FACTOR_P2 == 1.0 / 3


def test_threshold_ordering_invariants_hold():
    """结构约束（比具体数值更本质，调参时不可破坏）：

    ① 黄线区间左界 < 红线界——否则 Y3 与硬排除重叠，扣分永远轮不到；
    ② 红线界 < 监管强制停牌线（SEVERE_10D=100%）——硬排除必须**提前**于停牌发生，
       等触发停牌再排除毫无意义；
    ③ 连板扣分随板数单调递增且有封顶。
    """
    assert hr.YELLOW_DEV_10D_LO < hr.RED_DEV_10D < hr.SEVERE_10D
    boards = sorted(hr.PENALTY_BOARDS)
    penalties = [hr.PENALTY_BOARDS[b] for b in boards]
    assert penalties == sorted(penalties), "连板扣分必须随板数单调不减"
    assert penalties[-1] < hr.PENALTY_BOARDS_MAX, (
        "最高处罚档应留有余量，封顶值本身是给 ≥6 板用的"
    )


def test_verify_script_imports_production_module():
    """核验脚本必须 `import` 生产模块（[[KB-ENG-44]]）。

    否则脚本会各自复制一份阈值与判定逻辑 ⇒ 跑的是「核验副本」而不是线上规则，
    结论再漂亮也和线上无关。这条守卫同时锁住 `assess` 被真实调用（口径守卫段）。
    """
    assert _VERIFY_SCRIPT.exists(), f"核验脚本缺失：{_VERIFY_SCRIPT}"
    src = _VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "from app.picks import halt_risk" in src
    assert "hr.assess(" in src, "必须用生产 assess() 复算抽样行，否则口径守卫不成立"


def test_module_keeps_calibration_rationale():
    """参数可以保留，但**理由不可删除**。

    本项的真正风险不是数值错，而是「后人只看均值显著性就把红线删掉」——
    均值上红线确实不显著（这是实测结论，见模块注释）。故把「为什么不按均值调参」
    的段落固化为断言：删掉理由必须先删掉这条测试，即强制一次显式取舍。
    """
    doc = hr.__doc__ or ""
    tail = _read_module_tail()
    assert "安全不对称性" in tail
    assert "校准结论" in tail
    assert "均值" in tail
    # 模块头部仍须声明口径选择（监管口径 vs 封板质量口径）与免责
    assert "收盘涨停连续天数" in doc
    assert "不构成买卖建议" in doc


def _read_module_tail() -> str:
    return Path(hr.__file__).read_text(encoding="utf-8")
