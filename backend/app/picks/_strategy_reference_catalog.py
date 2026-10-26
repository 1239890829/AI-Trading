"""Reference metadata owned by strategy_registry; explicit manual-only adapters.

The reference names, codes and declared versions identify the supplied TSP
catalog snapshot. Conditions below are concise independent descriptions of
ideas, not copied engine code, captured picks or performance evidence.
"""
from copy import deepcopy


_CATEGORIES = {
    "theme_event": "题材与事件", "trend": "趋势与动量", "reversal": "反转与回踩",
    "pattern": "形态与量价", "quality": "价值与质量", "structure": "结构研究", "etf": "ETF参考",
}
_SESSIONS = {"any": "通用参考", "after_hours": "盘后参考", "intraday": "竞价参考"}


def _entry(code, name, version, category, session, summary, criteria, coverage, limitations=(), *, asset="stock"):
    return {
        "key": f"reference:{code}", "name": name, "catalog_kind": "reference",
        "status": "out_of_scope" if asset == "etf" else "candidate",
        "basis": "not_evaluated", "source": "tsp_reference_catalog", "evaluable": False,
        "discovery": {"category": category, "category_label": _CATEGORIES[category],
                      "session": session, "session_label": _SESSIONS[session],
                      "summary": summary, "supports_saved_runs": False},
        "candidate": {
            "source_label": "TSP规则参考（自有表述）", "source_code": code,
            "source_version": f"20261025:v{version}", "asset": asset,
            "criteria": list(criteria), "existing_coverage": list(coverage),
            "limitations": ["完整策略未通过本系统效果准入；关注不启用选股或模拟执行。",
                            *limitations, *(["ETF范围尚未开放，不并入当前A股执行域。"] if asset == "etf" else [])],
            "runnable": False,
        },
    }


_ENTRIES = (
    _entry("hot_theme", "热点题材", 4, "theme_event", "any", "对照题材热度与公司关联。",
           ("观察政策、产业事件与题材联动。", "核对个股相对题材的表现与真实业务关联。"),
           ("已有热门题材、官方成分和事件公司关联。",), ("题材标签不等于事件因果，热度不等于收益。",)),
    _entry("low_rebound_quality", "低位反弹精选", 3, "reversal", "after_hours", "先核硬条件，再比较低位候选。",
           ("参考范围为沪深主板与创业板。", "先核价格、流动性、换手与回撤，再作池内排序。"),
           ("已有流动性、技术与基本面读取。",), ("低位界限、四维定义和权重未取得完整可重算规则。",)),
    _entry("limit_up_pullback", "打板涨停回调", 4, "reversal", "after_hours", "观察曾涨停后的缩量整理。",
           ("曾出现涨停，再观察缩量回落。", "核对MA5、MA10附近的支撑是否仍成立。"),
           ("已有涨停历史、均线和量能观察。",), ("近期窗口、回踩容差与确认条件尚未确定。",)),
    _entry("etf_cold_rebound", "ETF极寒反弹", 5, "etf", "after_hours", "研究ETF超跌后的修复条件。",
           ("观察ETF的超跌、回撤与价格企稳。", "逐项定义超跌和修复确认条件。"),
           ("已有通用日线与指标计算，不含ETF策略准入。",), ("超跌不保证反弹，参考未给完整阈值。",), asset="etf"),
    _entry("etf_bottom_volume", "ETF底部放量", 5, "etf", "after_hours", "核对ETF底部放量是否伴随突破。",
           ("先定义底部区间，再比较成交量。", "观察价格是否突破该区间。"),
           ("已有量能和日线基础工具。",), ("底部窗口、放量分母与倍数尚未规定。",), asset="etf"),
    _entry("etf_momentum_rotation", "ETF动量轮动", 5, "etf", "after_hours", "比较ETF的相对动量。",
           ("在同一ETF范围和窗口比较收益。", "独立定义轮换、费用与退出条件。"),
           ("研究因子库已有多周期动量。",), ("轮动窗口、持有期和调仓规则未取得。",), asset="etf"),
    _entry("blue_chip_income", "蓝筹收益质量", 4, "quality", "any", "比较蓝筹估值与成交稳定性。",
           ("先定义蓝筹范围与流动性要求。", "核对估值和成交稳定性。"),
           ("已有基本面、估值与流动性读取。",), ("不能用日线替代估值；蓝筹口径和阈值待定。",)),
    _entry("etf_trend_takeoff", "ETF趋势起航", 5, "etf", "after_hours", "研究ETF长期趋势的转强。",
           ("观察长期趋势方向。", "明确起涨确认与失效条件后再研究。"),
           ("已有均线与动量基础工具。",), ("长期窗口和起涨确认规则未规定。",), asset="etf"),
    _entry("dragon_head", "龙头策略", 4, "theme_event", "after_hours", "核对题材启动时的领涨角色。",
           ("比较同题材成员的启动与涨停先后。", "结合换手、角色和后续承接核对。"),
           ("已有题材梯队、角色和辨识度观察。",), ("龙头标签不是可成交或上涨保证。",)),
    _entry("ma_golden_cross", "均线金叉", 4, "trend", "after_hours", "观察均线交叉与量能是否同步。",
           ("观察MA5向上穿越MA10或MA20。", "同时核对量能变化。"),
           ("已有MA序列、趋势和量能信号。",), ("交叉窗口、放量阈值与过滤条件待定义。",)),
    _entry("dual_low", "双低选股", 4, "quality", "any", "把估值条件与活跃度分开核对。",
           ("明确所指两项低估值指标。", "另核成交活跃度与形态条件。"),
           ("已有估值与成交观察，不含该完整组合。",), ("两项指标、时点和阈值未规定，不能补猜。",)),
    _entry("ma_bullish", "均线多头", 4, "trend", "after_hours", "核对均线顺序、方向与价格位置。",
           ("核收盘价位于MA5、MA10、MA20上方。", "核均线是否向上发散。"),
           ("现役技术维度已有均线排列与MA20位置。",), ("存在同类信号不代表此策略已独立验证。",)),
    _entry("limit_up_gene", "涨停基因", 4, "pattern", "after_hours", "研究历史涨停后的均线结构。",
           ("核对近20个交易日是否出现涨停。", "再检查回踩及均线支撑。"),
           ("已有涨停记录与均线基础工具。",), ("须按板块和当日价格规则识别涨停；回踩容差待定。",)),
    _entry("momentum_quality", "趋势质量", 4, "trend", "any", "分别核趋势与财务质量。",
           ("核对价格趋势是否得到确认。", "独立核对基本面质量。"),
           ("已有技术维度、动量研究因子和财务摘要。",), ("质量指标、权重及点时版本未取得完整定义。",)),
    _entry("growth_quality", "成长质量", 4, "quality", "any", "核对增长、盈利能力和现金流。",
           ("比较收入与利润增长。", "同时核ROE与现金流质量。"),
           ("已有财务摘要读取，不等于完整成长筛选器。",), ("参考仅声明日线依赖；财报版本和完整规则仍缺。",)),
    _entry("event_driven", "事件驱动", 4, "theme_event", "any", "核实催化事件如何影响公司。",
           ("区分业绩、政策、并购和订单等事件。", "核公司关联、可兑现环节及反证。"),
           ("已有新闻、事件证据与公司影响解释。",), ("参考仅声明日线依赖；文本时点和概率校准未证实。",)),
    _entry("rsi_reversal", "RSI超卖反转", 4, "reversal", "after_hours", "超卖、回升与企稳分别观察。",
           ("以RSI14观察超卖及后续回升。", "另核价格企稳，不能只凭超卖判断反转。"),
           ("现役技术维度已有RSI14及空头衰减。",), ("回升幅度、企稳定义与失效阈值待定。",)),
    _entry("shrink_pullback", "缩量回踩", 4, "reversal", "after_hours", "观察趋势中的缩量与支撑。",
           ("先确认上升趋势。", "观察缩量回到MA10或MA20附近后的支撑。"),
           ("已有潜伏观察池的试盘、缩量与确认规则。",), ("与潜伏池并非同一配方；窗口和容差仍须比较。",)),
    _entry("quality_value", "稳健价值", 4, "quality", "any", "核估值、流动性与活跃程度。",
           ("核对估值与流动性条件。", "观察成交活跃度是否适中。"),
           ("已有基本面和成交条件的分项观察。",), ("合理估值与适度活跃没有完整阈值定义。",)),
    _entry("wave_theory", "波浪理论", 4, "structure", "after_hours", "先明确浪型识别，再核可重复性。",
           ("区分推动与调整结构。", "记录浪型确认和失效的可重复条件。"),
           ("已有日线，不含获准的波浪识别器。",), ("分浪算法与歧义处理未确定，不生成目标价。",)),
    _entry("balanced_alpha", "均衡多因子", 4, "quality", "any", "比较多维组合及重复计权风险。",
           ("分别核估值、资金、动量与稳定性。", "明确权重、基准和重复信息后再比较。"),
           ("已有六维合成与研究因子库。",), ("不同于当前六维配方，不能直接沿用其效果。",)),
    _entry("box_first_board_breakout", "箱体首板突破", 3, "pattern", "after_hours", "核箱体、首板和放量是否同时成立。",
           ("参考箱体为T-20至T-1，振幅15%至25%。", "T日观察主板10%或科创、创业20%首板突破及放量。", "换手率需高于5%，另定义追高限制。"),
           ("已有价格规则、涨停与日线基础工具。",), ("放量阈值、追高界限及箱体边缘算法待定。",)),
    _entry("volume_breakout", "放量突破", 4, "trend", "after_hours", "把突破与成交确认分开核对。",
           ("观察价格是否突破前高。", "核对成交额相对基期是否放大。"),
           ("已有量能信号和成交额量比研究因子。",), ("前高窗口、有效突破和放量阈值未明确。",)),
    _entry("l1_l2_volume_resonance", "L1-L2量价共振", 3, "pattern", "after_hours", "研究有间隔的两次量价确认。",
           ("在30日内寻找L1放量信号。", "L1至L2至少间隔12个交易日。", "L2量参考前60日均量的3倍，并核下影线。"),
           ("已有量比工具；同类三倍量否决反例须保留。",), ("L1定义及长下影界限未完全给出；不继承其他战法结论。",)),
    _entry("capital_heat", "资金热度", 4, "theme_event", "any", "核资金活跃与价格变化是否一致。",
           ("核对资金与成交活跃程度。", "比较量价方向并定义过热限制。"),
           ("已有板块资金及个股资金分项观察。",), ("板块与个股资金口径不同；过热阈值待定。",)),
    _entry("one_yang_three_yin", "一阳夹三阴", 4, "pattern", "after_hours", "核阳线后连续三日的整理结构。",
           ("先识别一根大阳线。", "随后连续三根小阴线，并核成交量收缩。"),
           ("已有日线和量能工具，不含该完整形态规则。",), ("大阳、小阴、缩量与有效维持的阈值待定。",)),
    _entry("macd_golden_cross", "MACD金叉", 4, "trend", "after_hours", "核DIF、DEA交叉及柱线方向。",
           ("观察DIF向上穿越DEA。", "另核柱线转正及所处趋势。"),
           ("现役技术维度已有EMA12/26与DEA9及交叉信号。",), ("同类指标已存在，独立选股组合仍未验证。",)),
    _entry("chan_theory", "缠论", 4, "structure", "after_hours", "先定义分型、笔、线段与中枢。",
           ("明确包含关系、分型与笔的确认。", "明确线段、中枢及级别的可重算规则。"),
           ("已有日线，不含获准的缠论识别器。",), ("规则变体及确认时点未确定，不给自动买卖点。",)),
    _entry("w_bottom_breakout", "W底突破", 3, "reversal", "after_hours", "核两次低点、反弹及放量确认。",
           ("参考窗口为近60日的两次低点。", "第二低点企稳后，观察放量回到MA5、MA10、MA20上方。", "参考现价距20日低点不超过15%。"),
           ("已有日线与均线，不含完整W底确认器。",), ("低点确认、企稳容差和放量阈值仍须定义，禁止借未来拐点。",)),
    _entry("emotion_cycle", "情绪周期", 4, "theme_event", "after_hours", "对照换手、量价与市场环境。",
           ("观察换手与量价的变化。", "分别定义恐慌、过热及后续确认条件。"),
           ("已有市场情绪和量价分项观察。",), ("市场情绪不等于个股拐点；参考未给完整阶段判据。",)),
    _entry("bottom_volume", "底部放量", 4, "trend", "after_hours", "核下跌后的首次量价变化。",
           ("先定义此前的持续下跌。", "观察首次放量阳线及后续确认。"),
           ("已有日线、量比和反转分项。",), ("长期、首次及放量的窗口和阈值待定。",)),
    _entry("box_oscillation", "箱体震荡", 4, "reversal", "after_hours", "核区间边缘和失效条件。",
           ("定义箱体上下沿及确认窗口。", "观察价格在边缘的表现并记录突破失效。"),
           ("已有日线，不含该完整箱体规则。",), ("箱体算法、容差、成本和失效条件未确定。",)),
    _entry("low_volatility_quality", "低波质量", 4, "quality", "any", "分别核波动、回撤和估值。",
           ("比较同窗口波动与回撤。", "独立核对估值条件。"),
           ("研究因子库已有vola20及基本面读取。",), ("因子存在不等于已晋级；估值条件和组合权重仍缺。",)),
    _entry("expectation_repricing", "预期重估", 4, "theme_event", "any", "区分原预期、实际事件与修正。",
           ("固定事件发生前已可见的预期基准。", "比较业绩或政策新事实与该基准的差异。"),
           ("已有事件事实与公司关联解释。",), ("预期基准、首次可用时点和过热判据未取得，不能由日线补造。",)),
    _entry("auction_breakout_2to3", "二进三竞价爆量", 3, "pattern", "intraday", "仅研究真实竞价窗口的候选。",
           ("参考范围为沪深主板10%涨停制度，T-1为二连板。", "T日使用真实09:25集合竞价额，核放大关系。", "参考开盘涨幅为[1%, 3%)。"),
           ("已有交易时段和价格硬规则，不含此策略准入。",), ("竞价放大倍数待定；错过窗口不能回填，涨停身份与可执行性另核。",)),
)


def reference_catalog_entries() -> list[dict]:
    """Research execution is explicit; never add these to SPECS or collectors."""
    from app.picks._strategy_reference_rules import DEFINITION_DIGEST, reference_rule_metadata

    rules = reference_rule_metadata()
    rows = deepcopy(list(_ENTRIES))
    reused = {'hot_theme': 'themes', 'dragon_head': 'ladder', 'event_driven': 'events',
              'balanced_alpha': 'daily_picks', 'capital_heat': 'fundflow', 'emotion_cycle': 'market_regime'}
    existing_links = {'hot_theme': '/hunting?browse=hot', 'dragon_head': '/market',
        'event_driven': '/market?tab=events', 'balanced_alpha': '/hunting?sec=daily', 'capital_heat': '/hunting?browse=funds',
        'emotion_cycle': '/market'}
    for row in rows:
        code = row['candidate']['source_code']
        if code in rules:
            rule = rules[code]
            row['status'] = 'research_ready'
            row['candidate']['runnable'] = True
            row['candidate']['execution'] = {'mode': 'manual_research', 'version': rule['version'],
                'definition_digest': DEFINITION_DIGEST, 'production_enabled': False,
                'definition': rule['definition'], 'definition_supplement': rule['supplement']}
            row['candidate']['definition_supplement'] = rule['supplement']
            row['candidate']['limitations'] = ['仅启用手动研究筛选；匹配不等于正式入选、通知或模拟资格。',
                                              '历史点时宇宙、可执行成本与效果准入未完成，禁止改生产权重或风控。',
                                              '只用信号日及此前已完成日线，禁止借未来拐点。']
            row['discovery']['supports_saved_runs'] = True
            row['discovery']['session_label'] = '已完成日线研究'
        elif code in reused:
            row['candidate']['adoption'] = {'state': 'reuse_existing', 'consumer': reused[code],
                                          'reason': '已有真实消费者；保留来源和条件，不另造重复策略。',
                                          'href': existing_links[code]}
        elif code in {'wave_theory', 'chan_theory'}:
            row['candidate']['adoption'] = {'state': 'not_adopted', 'reason': '确认规则及未来重绘边界不明，无法可靠复现。'}
        else:
            row['candidate']['adoption'] = {'state': 'needs_contract_or_data',
                                          'reason': '；'.join(row['candidate']['limitations'][1:])}
    return rows
