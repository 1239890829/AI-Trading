"""题材内核 / 个股情绪 / 龙头打分 / 介入时机 测试。

重点同样是"错了也看不出来"的地方：
- 时间字符串位数（"9:35" 与 "09:35:00" 必须落到同一档）
- 缺失维度不能被当成坏消息（否则数据源一降级，所有票都变杂毛）
- 龙头打分不能退化成"连板高=龙头"的结果归因
"""
from __future__ import annotations

from app.services.dragon_service import (
    _hhmmss,
    dragon_score,
    entry_checklist,
    has_repair,
    news_persistence,
    seal_ratio,
    stock_sentiment,
    theme_core,
)

# ---------------------------------------------------------------- 工具

def test_hhmmss_normalizes_various_formats():
    """'9:35' / '09:35' / '093500' / '09:35:00' 必须归一到同一值。

    不归一的话 '9:35' → '935' 会被当成 09:35 之前的早盘，或比大小时乱序。
    """
    assert _hhmmss("09:35:00") == "093500"
    assert _hhmmss("9:35") == "093500"
    assert _hhmmss("093500") == "093500"
    assert _hhmmss("14:52:11") == "145211"
    assert _hhmmss(None) is None
    assert _hhmmss("") is None


def test_seal_ratio():
    assert seal_ratio(1e8, 5e9) == 0.02  # 1亿封单 / 50亿流通 = 2%
    assert seal_ratio(None, 5e9) is None
    assert seal_ratio(1e8, None) is None
    assert seal_ratio(1e8, 0) is None


def test_has_repair():
    assert has_repair("5天3板", 1) is True     # 断过板，今日回封
    assert has_repair("3天3板", 3) is False    # 连续板
    assert has_repair("2天2板", 2) is False
    assert has_repair(None, 1) is False
    assert has_repair("无", 1) is False


# ---------------------------------------------------------------- 个股情绪

def test_stock_sentiment_strong_when_all_good():
    s = stock_sentiment(
        boards=3, seal_amount=4e8, float_market_cap=8e9,  # 封单比 5%
        first_seal_time="09:33:00", break_count=0, turnover_rate=12.0,
        is_theme_highest=True, is_primary_theme=True,
    )
    assert s["level"] == "强"
    assert s["seal_ratio"] == 0.05
    assert not s["risks"]


def test_stock_sentiment_weak_on_late_seal_and_heavy_turnover():
    s = stock_sentiment(
        boards=1, seal_amount=2e7, float_market_cap=8e9,  # 封单比 0.25%
        first_seal_time="14:52:00", break_count=3, turnover_rate=28.0,
    )
    assert s["level"] == "弱"
    assert any("尾盘" in r for r in s["risks"])
    assert any("炸板" in r for r in s["risks"])
    assert any("爆量" in r for r in s["risks"])


def test_stock_sentiment_flags_death_turnover():
    s = stock_sentiment(boards=1, turnover_rate=2.0, break_count=0)
    assert any("庄股" in r for r in s["risks"])


def test_stock_sentiment_flags_non_primary_theme():
    s = stock_sentiment(boards=2, is_primary_theme=False, break_count=0)
    assert any("蹭概念" in r for r in s["risks"])


# ---------------------------------------------------------------- 龙头打分

def test_dragon_score_identifies_leader_at_second_board():
    """核心：二板阶段就能给出「龙头相」，而不是等它涨到 5 板。

    这是"等确认往往已涨一轮"的解法——打分依据全是当日可见的客观数据。
    """
    d = dragon_score(
        boards=2, seal_amount=3e8, float_market_cap=8e9,  # 封单比 3.75%
        first_seal_time="09:32:00", break_count=0, turnover_rate=11.0,
        is_theme_highest=True,
    )
    assert d["grade"] == "龙头相"
    assert d["score"] >= 12
    assert not d["missing"]


def test_dragon_score_is_not_just_board_height():
    """同样是 3 板，早盘硬封 vs 尾盘烂板，评分必须拉开差距。

    若两者同分，说明打分退化成了"连板数定龙头"的结果归因，失去前瞻意义。
    """
    strong = dragon_score(boards=3, seal_amount=3e8, float_market_cap=8e9,
                          first_seal_time="09:31:00", break_count=0,
                          turnover_rate=10.0, is_theme_highest=True)
    weak = dragon_score(boards=3, seal_amount=2e7, float_market_cap=8e9,
                        first_seal_time="14:55:00", break_count=3,
                        turnover_rate=32.0, is_theme_highest=False)
    assert strong["score"] - weak["score"] >= 10
    assert weak["grade"] == "杂毛/回避"


def test_dragon_score_missing_dimensions_do_not_punish():
    """数据缺失时记 0 分而不是负分——否则数据源一降级，所有票都被打成杂毛。"""
    d = dragon_score(boards=1, seal_amount=None, float_market_cap=None,
                     first_seal_time=None, turnover_rate=None, break_count=None)
    assert d["score"] == 0
    assert len(d["missing"]) == 4


def test_dragon_score_cap_fit_penalizes_extremes():
    tiny = dragon_score(boards=1, float_market_cap=12e8, break_count=0)
    mid = dragon_score(boards=1, float_market_cap=80e8, break_count=0)
    huge = dragon_score(boards=1, float_market_cap=800e8, break_count=0)
    # 过小与过大都该被扣，两者同分属正常；关键是中间区间显著优于两端
    assert mid["score"] > tiny["score"]
    assert mid["score"] > huge["score"]


# ---------------------------------------------------------------- 介入时机

def test_entry_checklist_blocks_in_retreat():
    e = entry_checklist(symbol="600001", boards=3, market_phase="退潮",
                        theme_stage="高潮", dragon={"grade": "龙头相"})
    assert e["market_layer"]["blocked"] is True
    assert any("退潮" in a for a in e["avoid"])
    assert any("空仓" in a for a in e["avoid"])


def test_entry_checklist_blocks_in_theme_retreat():
    e = entry_checklist(symbol="600001", boards=2, market_phase="发酵",
                        theme_stage="退潮")
    assert e["theme_layer"]["blocked"] is True


def test_entry_checklist_always_has_invalidation():
    """没有失效条件的判断不是判断，是许愿。"""
    for ph in ("冰点", "修复", "发酵", "高潮", "分歧", "退潮", None):
        e = entry_checklist(symbol="600001", boards=2, market_phase=ph)
        assert e["invalidation"], f"阶段 {ph} 缺少失效条件"
        assert e["conditions"], f"阶段 {ph} 缺少介入条件"


def test_entry_checklist_first_board_vs_lianban_conditions_differ():
    """首板看竞价承接与一进二，连板看均价线与板块合力，条件不应混用。"""
    fb = entry_checklist(symbol="600001", boards=1)
    lb = entry_checklist(symbol="600001", boards=4)
    assert any("竞价" in c for c in fb["conditions"])
    assert any("均价线" in c for c in lb["conditions"])
    assert any("监管" in c or "断板" in c for c in lb["conditions"])


def test_entry_checklist_flags_prev_day_weakness():
    e = entry_checklist(symbol="600001", boards=2, first_seal_time="14:55:00",
                        turnover_rate=31.0, break_count=3,
                        seal_amount=1e7, float_market_cap=8e9)
    assert len(e["avoid"]) >= 4
    assert any("尾盘" in a for a in e["avoid"])


def test_entry_checklist_contains_no_buy_instruction():
    """红线 3：输出必须是条件清单，不能出现确定性买卖结论。"""
    e = entry_checklist(symbol="600001", boards=2, market_phase="发酵",
                        theme_stage="发酵", dragon={"grade": "龙头相"})
    blob = str(e)
    for banned in ("建议买入", "必涨", "满仓", "稳赚"):
        assert banned not in blob
    assert "而非买卖建议" in e["note"]


# ---------------------------------------------------------------- 题材内核

def test_theme_core_earnings():
    assert theme_core("业绩预增", ["中报预增"])["type"] == "业绩兑现"


def test_theme_core_policy():
    assert theme_core("固态电池", ["工信部标准立项"])["type"] == "政策驱动"


def test_theme_core_rumor_is_weakest():
    r = theme_core("某传闻概念", ["市场传闻或将重组"])
    assert r["type"] in {"事件传闻", "资产重组"}


def test_theme_core_unknown_is_explicit():
    r = theme_core("维尔萨塔", [])
    assert r["type"] == "无法归因"
    assert r["confidence"] == "低"


def test_theme_core_persistence_ranking():
    """业绩/产业硬，传闻/外围软——这是资金给不给耐心的分野。"""
    assert news_persistence(core_type="业绩兑现", limit_up_count=6,
                            has_second_board=True, active_days=3)["evidence"] >= \
           news_persistence(core_type="事件传闻", limit_up_count=6,
                            has_second_board=True, active_days=3)["evidence"]


# ---------------------------------------------------------------- 消息持续性

def test_news_persistence_veto_without_second_board():
    """一票否决：无二板承接 + 涨停 ≤2 只 → 直接判一日游，消息再大也没用。"""
    r = news_persistence(theme="可控核聚变", core_type="政策驱动",
                         limit_up_count=2, has_second_board=False, active_days=1)
    assert r["grade"] == "一日游"
    assert r["veto"] is True


def test_news_persistence_mainline_when_all_pass():
    r = news_persistence(theme="创新药", core_type="业绩兑现", limit_up_count=8,
                         active_days=3, has_second_board=True, board_change_pct=3.0,
                         main_net_inflow=5e8)
    assert r["grade"] == "主线候选"
    assert r["veto"] is False
    assert r["evidence"] == 3
    assert r["position_risk"] is False


def test_news_persistence_position_is_driven_by_active_days():
    """位置看「已活跃天数」，不看板块当日涨幅。

    这条是 2026-08-29 改语义后的回归。旧实现用板块当日涨幅 >8% 判「已提前大涨」，
    但新题材第一天板块本来就大涨——那是启动不是位置高，会把所有刚启动的题材误杀。
    """
    fresh = news_persistence(core_type="政策驱动", limit_up_count=5,
                             has_second_board=True, active_days=1, board_change_pct=15.0)
    high = news_persistence(core_type="政策驱动", limit_up_count=5,
                            has_second_board=True, active_days=6, board_change_pct=15.0)

    pos = lambda r: next(c for c in r["checks"] if "位置" in c["q"])  # noqa: E731
    # 关键：同样板块当日 +15%，首日启动不算位置高，连涨 6 天才算
    assert pos(fresh)["pass"] is True
    assert pos(high)["pass"] is False
    assert fresh["position_risk"] is False
    assert high["position_risk"] is True


def test_news_persistence_risk_caps_grade_without_changing_evidence():
    """核心回归：位置是风险封顶，不是减分项。

    旧实现把位置塞进同一个计数器，而它和「二板承接」都挂在 active_days 上、
    方向相反，导致首日启动与连涨 6 天拿到的总分一样（实测都是 2 分），
    评级对这个变量完全不敏感。现在必须：evidence 不变，只有 grade 被封顶。
    """
    kw = dict(core_type="业绩兑现", limit_up_count=8, has_second_board=True,
              main_net_inflow=5e8)
    low = news_persistence(active_days=3, **kw)    # 未到位置门槛
    high = news_persistence(active_days=6, **kw)   # 已连涨 6 天

    assert low["evidence"] == high["evidence"] == 3, "证据轴与位置无关"
    assert low["position_risk"] is False
    assert high["position_risk"] is True
    assert low["grade"] == "主线候选"
    assert high["grade"] == "主线·位置偏高", "位置只封顶，不扣证据分"


def test_news_persistence_single_day_rally_is_not_position_risk():
    """反例固化：当日板块大涨但题材是第一天 → 不得判为位置高。"""
    r = news_persistence(core_type="产业趋势", limit_up_count=6, active_days=1,
                         board_change_pct=9.8)
    assert next(c for c in r["checks"] if "位置" in c["q"])["pass"] is True


def test_news_persistence_main_net_inflow_participates():
    """主力净流出必须让「资金认可」这一项转否——该参数曾经收了却从不参与判定。"""
    inflow = news_persistence(core_type="政策驱动", limit_up_count=5,
                              has_second_board=True, active_days=3,
                              main_net_inflow=5e8)
    outflow = news_persistence(core_type="政策驱动", limit_up_count=5,
                               has_second_board=True, active_days=3,
                               main_net_inflow=-5e8)
    money = lambda r: next(c for c in r["checks"] if "真金白银" in c["q"])  # noqa: E731
    assert money(inflow)["pass"] is True
    assert money(outflow)["pass"] is False, "涨停多但主力净流出 = 拉抬出货嫌疑"
    assert outflow["evidence"] < inflow["evidence"]


def test_news_persistence_missing_inflow_is_neutral():
    """数据缺失既不奖也不罚：不能把「没取到」当成「主力在出货」。"""
    unknown = news_persistence(core_type="政策驱动", limit_up_count=5,
                               has_second_board=True, active_days=3,
                               main_net_inflow=None)
    known_good = news_persistence(core_type="政策驱动", limit_up_count=5,
                                  has_second_board=True, active_days=3,
                                  main_net_inflow=5e8)
    assert unknown["evidence"] == known_good["evidence"]
    assert "缺失" in next(c for c in unknown["checks"] if "真金白银" in c["q"])["value"]


def test_news_persistence_requires_breadth():
    narrow = news_persistence(core_type="政策驱动", limit_up_count=1,
                              has_second_board=True, active_days=3)
    assert not next(c for c in narrow["checks"] if "真金白银" in c["q"])["pass"]
