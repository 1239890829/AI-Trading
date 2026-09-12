"""题材内核 / 个股情绪 / 龙头打分 / 介入时机 测试。

重点同样是"错了也看不出来"的地方：
- 时间字符串位数（"9:35" 与 "09:35:00" 必须落到同一档）
- 缺失维度不能被当成坏消息（否则数据源一降级，所有票都变杂毛）
- 龙头打分不能退化成"连板高=龙头"的结果归因
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.bjtime import beijing_today
from app.services.dragon_service import (
    _hhmmss,
    apply_position_with_5d,
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
        is_theme_highest=True, auction_gap_pct=3.5,
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
    assert len(d["missing"]) == 5  # 封单比/封板时间/换手率/流通市值/竞价高开
    assert "竞价高开" in d["missing"]


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


def test_theme_core_prefers_theme_name_over_single_catalyst():
    """题材名说的是"这个题材是什么"，涨停原因说的是"这次为什么涨"。

    「固态电池 + 工信部标准立项」：题材本身是产业趋势，标准立项只是当次催化剂。
    旧实现（顺序取首个命中）会因为原因里出现"标准"而归成政策驱动，
    把题材的持久性从 +1 错降到 0。内核应回答"在炒什么"而非"这次因何而起"。
    """
    r = theme_core("固态电池", ["工信部标准立项"])
    assert r["type"] == "产业趋势"
    # 分歧不隐藏：票型里要能看到「政策驱动」这一票
    assert r["votes"] == {"政策驱动": 1}


def test_theme_core_rumor_is_weakest():
    r = theme_core("某传闻概念", ["市场传闻或将重组"])
    assert r["type"] in {"事件传闻", "资产重组"}


def test_theme_core_unknown_is_explicit():
    r = theme_core("维尔萨塔", [])
    assert r["type"] == "无法归因"
    assert r["confidence"] == "低"


def test_theme_core_is_not_hijacked_by_a_single_reason():
    """核心回归：不能因为**任意一只**成分股提到"业绩"就把整个题材归成业绩兑现。

    2026-08-29 实测：原实现把全部成分股的涨停原因拼成一个大字符串，再按规则表
    顺序返回首个命中，而"业绩兑现"排第一——于是「算力」「创新药」被双双归成
    「业绩兑现」。任意一只符合 ≠ 这个题材符合，这两件事原逻辑分不清。
    """
    # 成员票型打平（产业2 : 业绩2），但题材名明确指向产业趋势
    r = theme_core("算力", ["算力订单落地", "中报业绩预增", "数据中心液冷", "业绩超预期"])
    assert r["type"] == "产业趋势", "题材名优先，不被成员票带偏"
    assert r["votes"] == {"产业趋势": 2, "业绩兑现": 2}, "票型必须回报以便复核"

    r2 = theme_core("创新药", ["创新药出海授权", "业绩预增", "临床试验获批"])
    assert r2["type"] == "产业趋势"


def test_theme_core_name_priority_over_member_majority():
    """题材名是最权威的分类标签，优先于成员原因多数票。"""
    r = theme_core("黄金珠宝", ["国际金价上涨", "黄金提价", "业绩增长", "业绩预增"])
    assert r["type"] == "涨价周期"


def test_theme_core_flags_label_vs_substance_divergence():
    """名称命中但成员票压倒性指向别处 → 降置信度并说明，而不是假装一致。"""
    r = theme_core("算力", ["中报预增"] * 9 + ["算力订单"])  # 9/10 指向业绩
    assert r["type"] == "产业趋势"
    assert r["confidence"] == "低", "标签与实质背离必须显式提示"
    assert "背离" in r["note"]


def test_theme_core_falls_back_to_member_majority_vote():
    """题材名没命中时，按成员原因多数票归因。"""
    r = theme_core("某某概念", ["并购重组", "资产注入", "业绩预增"])
    assert r["type"] == "资产重组", "重组 2 票 > 业绩 1 票"
    assert r["confidence"] == "中", "2/3 过半，置信度中"


def test_theme_core_low_confidence_when_votes_split():
    """票型分散（没有过半）时置信度必须降低。"""
    r = theme_core("某某概念", ["并购重组", "涨价", "业绩预增", "美股大涨"])
    assert r["confidence"] == "低", "1/4 票不足以支撑结论"


def test_theme_core_generic_particles_are_not_rumor_keywords():
    """「或」「拟」是中文高频字，不能当"事件传闻"的关键词。

    原规则里这两个字导致"控制权拟变更""或增资"这类正常表述被判成传闻题材，
    给几乎任何题材都扣上"弱内核"的帽子。
    """
    r = theme_core("控制权变更", ["控制权拟变更", "董事会通过或增资方案"])
    assert r["type"] != "事件传闻"


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


# ---------------------------------------------------------------- P1-6：官方 5 日涨幅升级位置判定

def test_apply_position_with_5d_upgrades_slow_bull_high_position():
    """慢牛题材：active_days 代理判低位，但官方板块 5 日涨幅已超阈值 → 位置必须转高。

    代理口径（连续活跃天数）只对"连板型"题材敏感；"每日温和上涨型"题材
    active_days 不高但板块已悄悄涨了一大段，只有真实区间涨幅能抓住。
    """
    base = news_persistence(core_type="业绩兑现", limit_up_count=8, has_second_board=True,
                            active_days=2, main_net_inflow=5e8)
    assert base["position_risk"] is False
    r = apply_position_with_5d(base, 12.3)
    assert r["position_risk"] is True
    assert "板块 5 日 +12.3%（官方 K 线）" in next(c for c in r["checks"] if c["axis"] == "risk")["value"]
    assert r["grade"] == "主线·位置偏高"
    assert r["evidence"] == 3, "位置是风险轴，只封顶不改证据分"


def test_apply_position_with_5d_low_position_unchanged():
    base = news_persistence(core_type="业绩兑现", limit_up_count=8, has_second_board=True,
                            active_days=2, main_net_inflow=5e8)
    r = apply_position_with_5d(base, -3.2)
    assert r["position_risk"] is False
    assert r["grade"] == "主线候选"


def test_apply_position_with_5d_or_semantics_never_relaxes():
    """取或语义（只收紧不放松）：代理已判高位（active_days≥4），真实涨幅低位也不得放松。"""
    base = news_persistence(core_type="业绩兑现", limit_up_count=8, has_second_board=True,
                            active_days=6, main_net_inflow=5e8)
    assert base["position_risk"] is True
    r = apply_position_with_5d(base, 1.0)
    assert r["position_risk"] is True


def test_apply_position_with_5d_keeps_veto():
    """一票否决优先级最高：位置升级不得把「一日游」洗成别的评级。"""
    base = news_persistence(core_type="政策驱动", limit_up_count=2,
                            has_second_board=False, active_days=1)
    r = apply_position_with_5d(base, 15.0)
    assert r["grade"] == "一日游"
    assert r["veto"] is True


# ---------------------------------------------------------------- B2：竞价强弱维度

def test_dragon_score_auction_gap_bands():
    """竞价分带与介入清单口径一致：3–7% 标准接力 +2，>8% 超高开 -2，低开 -2。"""
    kw = dict(boards=2, seal_amount=5e7, float_market_cap=5e9,
              first_seal_time="09:40:00", turnover_rate=8.0)
    base = dragon_score(**kw)  # 无竞价：缺失中性
    assert next(l for l in base["basis"] if "竞价" in l) == "竞价缺失（0）"
    assert "竞价高开" in base["missing"]

    good = dragon_score(**kw, auction_gap_pct=5.0)
    weak = dragon_score(**kw, auction_gap_pct=1.0)
    ultra = dragon_score(**kw, auction_gap_pct=9.5)
    low = dragon_score(**kw, auction_gap_pct=-2.0)
    high_edge = dragon_score(**kw, auction_gap_pct=7.8)

    assert good["score"] == base["score"] + 2, "标准接力区间 +2"
    assert weak["score"] == base["score"], "弱高开 0 分"
    assert ultra["score"] == base["score"] - 2, "超高开获利盘抛压 -2"
    assert low["score"] == base["score"] - 2, "低开 -2"
    assert high_edge["score"] == base["score"], "7–8% 偏高开观察 0 分"
    assert "竞价高开" not in good["missing"], "有数据即不计入缺失"


def test_dragon_score_auction_tips_grade_at_boundary():
    """竞价维度应能在阈值附近改变评级（证明它真的参与总分）。"""
    kw = dict(boards=1, seal_amount=2e7, float_market_cap=8e9,
              first_seal_time="14:50:00", turnover_rate=3.0)
    without = dragon_score(**kw)
    with_gap = dragon_score(**kw, auction_gap_pct=5.0)
    assert with_gap["score"] > without["score"]


# ---------------------------------------------------------------- B2：竞价数据守卫

def test_auction_gaps_guards_against_cross_day_pollution():
    """历史日期必须返回 None：ths 竞价端点只有当日数据，绝不能让历史回看套上今日竞价。"""
    import asyncio
    from datetime import timedelta

    from app.services.theme_service import _auction_gaps

    calls = {"n": 0}

    async def fake_snapshot(symbols, stage="final"):
        calls["n"] += 1
        return [{"symbol": symbols[0], "auction_pct": 5.0, "data_status": "final"}]

    provider = SimpleNamespace(get_auction_snapshot=fake_snapshot)
    pool = [SimpleNamespace(symbol="600519")]
    past = beijing_today() - timedelta(days=1)
    assert asyncio.run(_auction_gaps(provider, past, pool)) is None
    assert calls["n"] == 0, "历史日期不得发起竞价请求"


def test_auction_gaps_filters_not_ready_and_batches():
    """非就绪条目跳过；>100 只分批；provider 异常只降级不抛。"""
    import asyncio

    from app.services.theme_service import _auction_gaps

    seen_batches: list[int] = []

    async def fake_snapshot(symbols, stage="final"):
        seen_batches.append(len(symbols))
        if len(seen_batches) > 1:  # 模拟第二批整批失败
            raise RuntimeError("boom")
        return [
            {"symbol": s, "auction_pct": 3.0, "data_status": "final"} for s in symbols[:2]
        ] + [{"symbol": "000003", "auction_pct": None, "data_status": "final"},
             {"symbol": "000004", "auction_pct": 9.0, "data_status": "not_ready"}]

    provider = SimpleNamespace(get_auction_snapshot=fake_snapshot)
    pool = [SimpleNamespace(symbol=f"{600000 + i:06d}") for i in range(120)]
    out = asyncio.run(_auction_gaps(provider, beijing_today(), pool))
    assert out is not None
    assert out[f"{600000:06d}"] == 3.0 and out[f"{600001:06d}"] == 3.0
    assert "000003" not in out and "000004" not in out, "缺失/未就绪不标 gap"
    assert max(seen_batches) <= 100, "单批 ≤100"
    assert len(seen_batches) >= 2, "已分批"


def test_auction_gaps_skips_for_mock_provider():
    """provider 无 get_auction_snapshot（mock 桩）→ None，不影响现有链路。"""
    import asyncio

    from app.services.theme_service import _auction_gaps

    provider = SimpleNamespace()  # 无该方法
    assert asyncio.run(_auction_gaps(provider, beijing_today(), [SimpleNamespace(symbol="600519")])) is None


def test_entry_checklist_declares_missing_inputs():
    """三态纪律：缺失的输入要显式列进 missing，不能静默当"缺失=中性"。"""
    e = entry_checklist(symbol="600001", boards=2, market_phase="发酵", theme_stage="发酵")
    for label in ("封单额", "流通市值", "封板时间", "换手率", "炸板次数"):
        assert label in e["missing"]
    assert "市场阶段" not in e["missing"]  # 已给
    full = entry_checklist(
        symbol="600001", boards=2, market_phase="发酵", theme_stage="发酵",
        seal_amount=1e8, float_market_cap=8e9, first_seal_time="09:40:00",
        turnover_rate=9.0, break_count=0, dragon={"grade": "龙头相"},
    )
    assert full["missing"] == []


def test_entry_checklist_note_is_plain_text_and_missing_sentence_is_conditional():
    """note 直接进 UI 纯文本渲染 → 不得含 Markdown 标记；缺项说明只在真有缺项时出现。

    回归：曾把 `missing` 写成 Markdown 代码跨度，前端字面显示反引号；且该句无论有无
    缺项都静态追加，在 missing 为空时反而暗示"有东西没取到"，与三态语义相悖。
    """
    partial = entry_checklist(symbol="600001", boards=2, market_phase="发酵", theme_stage="发酵")
    assert partial["missing"]  # 前置：确实有缺项
    assert "`" not in partial["note"]
    assert "未判定" in partial["note"]  # 有缺项 → 说明「按未判定呈现」

    full = entry_checklist(
        symbol="600001", boards=2, market_phase="发酵", theme_stage="发酵",
        seal_amount=1e8, float_market_cap=8e9, first_seal_time="09:40:00",
        turnover_rate=9.0, break_count=0, dragon={"grade": "龙头相"},
    )
    assert full["missing"] == []
    assert "`" not in full["note"]
    assert "未取到" not in full["note"]  # 无缺项 → 不出现缺项说明
    assert "不构成介入理由" in full["note"]  # 免责声明恒定
