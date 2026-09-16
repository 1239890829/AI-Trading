"""provider 能力注册表（OpenBB 调研采纳：能力注册 + 可查询，第 6 组）。

背景：4 个真实源（ths/tencent/eastmoney/sina）的可用能力散落在各 provider
方法里，"谁有什么"只存在于 docs/data-source-comparison.md 的手工实测文档。
本模块把那份实测矩阵变成**代码可查询**的结构，供运维端点、监控扩展
（ths_sentinel 式单点告警）与选源决策使用。

## 能力真值三档（核心设计——避免"方法存在"与"有真实数据"的混淆）

- SUPPORTED：真实现，返回真实数据；
- STUB：方法存在但恒返回空/None 的链上占位。composite._is_empty 会把空
  结果计入失败继续 fail over（所以占位不污染链），但每次调用浪费一跳并
  累计熔断计数——调用方（监控/选源）必须知道这些方法"名存实亡"；
- UNSUPPORTED：方法不存在，或显式 raise（如 sina.get_kline）。

## 分工边界

本表回答"**代码里**谁实现了什么"（静态真值）；运行时健康（熔断/延迟/
实时降级）由 GET /api/system/providers 负责。例：eastmoney.get_kline
代码完整但本机 push2his 被 WAF 拦——表中记 SUPPORTED 并在 note 标注实测
边界，运行时是否可用看 /api/system/providers 的熔断状态。

## 防腐化（tests/test_provider_capabilities.py 反射双向锚定）

① 声明为 SUPPORTED/STUB 的方法必须 hasattr(ProviderClass, method) 为真
  （防"provider 方法改名后注册表还在说有"）；
② CompositeProvider 的全部公共消费方法必须在每源条目中显式出现
  （防"链上加了新方法注册表不知道"——UNSUPPORTED 也要显式列）。

## 单点风险清单

single_point_methods() 列出只有一个 SUPPORTED 源的方法——已知单点：
交易日历/热股榜/集合竞价/复权事件/连板天梯/涨停原因 → ths，
当日分时分钟K → tencent，龙虎明细/资金流分属 eastmoney/sina。

⚠️ **逐笔（`get_trades`）已不再是单点**（2026-09-16 `IMP-038`）：eastmoney 仍是
链上唯一 SUPPORTED 源（故仍会出现在 `single_point_methods()` 里），但路由层
`/api/trades/{symbol}` 已有 **TDX 直连降级备源**（`app/market/tdx_tick.py`）。
本表只描述**链内**能力，不含路由层直连旁路 —— 读单点清单时必须带上这一句，
否则会把"已经有兜底"读成"挂了就没人管"。
"""
from __future__ import annotations

SUPPORTED = "supported"
STUB = "stub"
UNSUPPORTED = "unsupported"

LEVELS = {
    SUPPORTED: "真实现，返回真实数据",
    STUB: "方法存在但恒空/None（链上占位，fail over 跳过）",
    UNSUPPORTED: "方法不存在或显式 raise",
}


def _s(note: str = "") -> dict:
    return {"level": SUPPORTED, **({"note": note} if note else {})}


def _t(note: str = "") -> dict:
    return {"level": STUB, **({"note": note} if note else {})}


def _n(note: str = "") -> dict:
    return {"level": UNSUPPORTED, **({"note": note} if note else {})}


#: 真值矩阵（来源：docs/data-source-comparison.md 2026-08-29 四源实测 + 代码方法面）。
#: 键 = composite/直调消费的方法名；每源覆盖**全部**消费面（UNSUPPORTED 显式列出）。
CAPABILITIES: dict[str, dict[str, dict]] = {
    "ths": {
        "get_quotes": _s("快照（含涨停原因覆盖 100%——题材标签体系地基，无备源）"),
        "get_quote": _s("get_quotes 的单只便捷口径"),
        "get_indices": _s("指数快照"),
        "get_kline": _s("日线（prices-historical，P1-B parity 60/60 实证）；官方不覆盖分钟K/tick/L2"),
        "get_order_book": _t("fuyao 无五档盘口，恒 None；链上由腾讯提供"),
        "get_trades": _t("恒空；逐笔东财独有"),
        "get_limit_up_pool": _s("涨停池主源（含归因题材）"),
        "get_limit_down_pool": _n("无实现：跌停池东财 push2ex 独有"),
        "get_limit_break_pool": _s("炸板池"),
        "get_longhu_records": _s("龙虎榜主源"),
        "search": _n("ths 无搜索端点"),
        "get_minute_line": _n("官方不覆盖分钟K"),
        "get_board_rankings": _n(),
        "get_longhu_detail": _n(),
        "get_longhu_history": _n(),
        "get_capital_flow": _n(),
        "get_financials": _n(),
        "get_company_profile": _n(),
        "get_announcements": _n(),
        "get_news": _n(),
        "get_trading_days": _s("官方日历主源（含未来日期，供盘后调度与回测截窗）"),
        "get_hot_stock_list": _s("热股榜 ths 独有（人气先导 + D1 兑现验证）"),
        "get_hot_stock_list_history": _s("热股榜历史 ths 独有"),
        "get_skyrocket_list": _s("飙升榜 ths 独有（「正在变热」信号，排名逻辑与热股榜不同）"),
        "get_hot_rank_trend": _s("单股热榜排名走势 ths 独有（官方服务器即历史库；空集=从未上榜，允许空语义）"),
        "get_auction_snapshot": _s("集合竞价 ths 独有"),
        "get_auction_benchmark": _s("竞价基准 ths 独有"),
        "get_adjustment_events": _s("复权事件流（分钟回测前复权 + marketdb 因子推算地基）"),
        "get_limit_up_ladder": _s("连板天梯（seal_nextday 晋级率）；链外直调 ladder_check，不在 composite 面内"),
        "get_anomaly_list": _s("当日全市场异动原因 ths 独占（G5 验证环盘面证据源；空集=当日无记录，允许空语义）"),
        "get_anomaly_stock": _s("按代码批量查当日异动原因 ths 独占（1-50/批，支持 .BJ）"),
    },
    "tencent": {
        "get_quotes": _s("秒级快照主源（realtime_rank=1）"),
        "get_quote": _s("get_quotes 的单只便捷口径"),
        "get_indices": _s("指数快照主源"),
        "get_kline": _s("日线 + 分钟K（mkline 320 根上限、无翻页）——分钟K 唯一真源"),
        "get_order_book": _s("五档盘口主源"),
        "get_trades": _t("恒空：免费无稳定逐笔端点；逐笔走东财 details"),
        "get_limit_up_pool": _t("恒空占位：链上东财 push2ex 提供"),
        "get_limit_down_pool": _t("恒空占位：链上东财 push2ex 提供"),
        "get_limit_break_pool": _n("无实现：链上 ths/东财 push2ex 提供"),
        "get_longhu_records": _t("恒空占位：链上东财 datacenter 提供"),
        "search": _s("smartbox 联想搜索主源"),
        "get_minute_line": _s("当日 1 分钟分时——链上唯一实现者（单点）；降级备源=TDX 直连 m1（/api/minute-line 路由层，仅裸 6 位股票码，2026-09-03 实测全日 240 根/1.6s）"),
        "get_board_rankings": _n(),
        "get_longhu_detail": _n(),
        "get_longhu_history": _n(),
        "get_capital_flow": _n(),
        "get_financials": _n(),
        "get_company_profile": _n(),
        "get_announcements": _n(),
        "get_news": _n(),
        "get_trading_days": _n(),
        "get_hot_stock_list": _n(),
        "get_hot_stock_list_history": _n(),
        "get_skyrocket_list": _n(),
        "get_hot_rank_trend": _n(),
        "get_auction_snapshot": _n(),
        "get_auction_benchmark": _n(),
        "get_adjustment_events": _n(),
        "get_limit_up_ladder": _n(),
        "get_anomaly_list": _n(),
        "get_anomaly_stock": _n(),
    },
    "eastmoney": {
        "get_quotes": _s("秒级快照备源（realtime_rank=2，对冲并行）"),
        "get_quote": _s("get_quotes 的单只便捷口径"),
        "get_indices": _s("指数快照"),
        "get_kline": _s("push2his 本机被 WAF 拦（0.1s 快速失败）；家庭宽带通常可用——代码真，运行时看部署环境"),
        "get_order_book": _s("五档盘口备源"),
        "get_trades": _s("逐笔成交唯一真源（details）；**降级备源=TDX 直连逐笔**（`app/market/tdx_tick.py`，路由层接管，2026-09-16 IMP-038。口径为 3 秒快照聚合，非逐笔明细；实测本机 push2his 被 WAF 拦 3/3 ⇒ 该备源是逐笔当前**唯一可用**路径）"),
        "get_limit_up_pool": _s("push2ex 涨停池备源（ths 挂时兜底）"),
        "get_limit_down_pool": _s("push2ex getTopicDTPool（唯一实现者单点；2026-09-04 实测 p×1000）"),
        "get_limit_break_pool": _s("push2ex getTopicZBPool（P0-4 消除炸板率单点）"),
        "get_longhu_records": _s("datacenter 龙虎榜"),
        "search": _s("search 联想备源"),
        "get_minute_line": _n(),
        "get_board_rankings": _n(),
        "get_longhu_detail": _s("龙虎明细唯一实现者"),
        "get_longhu_history": _s("龙虎历史唯一实现者"),
        "get_capital_flow": _n(),
        "get_financials": _s("财务（ROE/毛利率）"),
        "get_company_profile": _s("公司资料"),
        "get_announcements": _s("公告"),
        "get_news": _s("新闻"),
        "get_trading_days": _n(),
        "get_hot_stock_list": _n(),
        "get_hot_stock_list_history": _n(),
        "get_skyrocket_list": _n(),
        "get_hot_rank_trend": _n(),
        "get_auction_snapshot": _n(),
        "get_auction_benchmark": _n(),
        "get_adjustment_events": _n(),
        "get_limit_up_ladder": _n(),
        "get_anomaly_list": _n(),
        "get_anomaly_stock": _n(),
    },
    "sina": {
        "get_quotes": _s("1Hz 并发组备源（P1-A：断腾讯自动兜底）"),
        "get_quote": _s("get_quotes 的单只便捷口径"),
        "get_indices": _s("指数快照"),
        "get_kline": _n("显式 raise ProviderError('sina kline not implemented')"),
        "get_order_book": _s("五档盘口备源"),
        "get_trades": _t("恒空占位"),
        "get_limit_up_pool": _t("恒空占位"),
        "get_limit_down_pool": _t("恒空占位"),
        "get_limit_break_pool": _n("无实现：链上 ths/东财 push2ex 提供"),
        "get_longhu_records": _t("恒空占位"),
        "search": _n(),
        "get_minute_line": _n(),
        "get_board_rankings": _s("新浪闪电排行（行业/概念）——唯一实现者（单点）"),
        "get_longhu_detail": _n(),
        "get_longhu_history": _n(),
        "get_capital_flow": _s("资金流——唯一实现者（单点）"),
        "get_financials": _n(),
        "get_company_profile": _n(),
        "get_announcements": _n(),
        "get_news": _n(),
        "get_trading_days": _n(),
        "get_hot_stock_list": _n(),
        "get_hot_stock_list_history": _n(),
        "get_skyrocket_list": _n(),
        "get_hot_rank_trend": _n(),
        "get_auction_snapshot": _n(),
        "get_auction_benchmark": _n(),
        "get_adjustment_events": _n(),
        "get_limit_up_ladder": _n(),
        "get_anomaly_list": _n(),
        "get_anomaly_stock": _n(),
    },
}


def capabilities_of(name: str) -> dict[str, dict]:
    """单源能力条目；未注册源（如 mock，测试桩无真实数据）返回空。"""
    return CAPABILITIES.get(name, {})


def providers_supporting(method: str) -> list[str]:
    """某方法的 SUPPORTED 源清单（STUB 不算数）。"""
    return [
        src for src, caps in CAPABILITIES.items()
        if caps.get(method, {}).get("level") == SUPPORTED
    ]


def single_point_methods() -> dict[str, str]:
    """方法 → 唯一 SUPPORTED 源。单点风险清单：该源挂了此方法即无人兜底。"""
    out: dict[str, str] = {}
    for method in sorted({m for caps in CAPABILITIES.values() for m in caps}):
        ps = providers_supporting(method)
        if len(ps) == 1:
            out[method] = ps[0]
    return out
