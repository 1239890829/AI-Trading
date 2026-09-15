"""工具注册表：TOOL_SPECS（白名单）/ TOOL_LABELS / 清单渲染。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations


from .account import (
    _t_paper,
    _t_positions,
    _t_watchlist,
)
from .agent import (
    _t_agent_tasks,
    _t_brief,
    _t_climate,
    _t_param_changes,
    _t_review,
)
from .core import (
    MAX_CALLS_PER_TURN,
    MAX_TOOL_ROUNDS,
    ToolSpec,
)
from .events import (
    _t_alert_events,
    _t_chain,
    _t_events,
    _t_news,
    _t_sentiment,
)
from .market import (
    _t_auction,
    _t_basics,
    _t_board_flow,
    _t_boards,
    _t_capital_flow,
    _t_commodity,
    _t_kline,
    _t_limit_break,
    _t_limit_down,
    _t_limit_up,
    _t_longhu,
    _t_market_overview,
    _t_minute,
    _t_orderbook,
    _t_quotes,
    _t_trades,
)
from .picks import (
    _t_anomaly,
    _t_backtest,
    _t_factor_profile,
    _t_minute_decisions,
    _t_picks,
)
from .themes import (
    _t_hot,
    _t_theme_members,
    _t_themes,
)
TOOL_SPECS: dict[str, ToolSpec] = {
    # P2-5（2026-09-12）：事件→板块传导链的**反向检索**（关键词 → 链）。
    # 与 events 工具的分工：events 给「发生了什么」，本工具给「它可能传到哪些板块」。
    "chain": ToolSpec(
        "chain",
        "事件→板块传导链检索（给关键词，返回关联的链条与目标板块；"
        "映射索引非已验证规律，强度值未经数据验证）",
        "keyword=事件关键词（如 厄尔尼诺 / 非农 / 加息 / OpenAI）",
        _t_chain,
    ),
    "quotes": ToolSpec("quotes", "批量实时行情快照（指数需带前缀，如 sh000001）", "symbols=600519,000001（≤6 只）", _t_quotes),
    "limit_up": ToolSpec("limit_up", "某交易日涨停池", "date=YYYY-MM-DD（可省略=最近交易日）", _t_limit_up),
    "limit_down": ToolSpec("limit_down", "某交易日跌停池", "date=YYYY-MM-DD（可省略）", _t_limit_down),
    "limit_break": ToolSpec("limit_break", "某交易日炸板池", "date=YYYY-MM-DD（可省略）", _t_limit_break),
    "longhu": ToolSpec("longhu", "龙虎榜：当日全市场榜；给 symbols 则查个股席位明细", "date=YYYY-MM-DD（可省略）｜symbols=600519（可选，个股维度）", _t_longhu),
    # P2-28①（2026-09-11）：题材成分明细——此前只有「题材梯队」没有「成分」，
    # 助手被问「某题材有哪些票」时只能凭印象作答。
    "theme_members": ToolSpec("theme_members", "官方题材成分明细（给题材名，返回成分股列表）",
                              "keyword=题材名（如 代糖 / 创新药）", _t_theme_members),
    # P2-28① 批量（2026-09-11）：盘口 / 逐笔 / 集合竞价——三个都是 provider 直连，
    # 一次性登记，避免逐个加、每次都动一遍能力清单守卫（KB-ENG-49）。
    "orderbook": ToolSpec("orderbook", "盘口五档（L1，非 L2）",
                          "symbols=单只代码", _t_orderbook),
    "trades": ToolSpec("trades", "逐笔成交明细",
                       "symbols=单只代码", _t_trades),
    "auction": ToolSpec("auction", "集合竞价快照（仅 ths 一源，取不到属正常）",
                        "symbols=逗号分隔代码（≤5）｜stage=final（默认）", _t_auction),
    # P2-28① 第三批（2026-09-11）：因子档案 / 参数变更单 / 任务中心——
    # 均用既有数据源（factors.report 纯函数 + 已注入的 session_factory），
    # 一次登记，避免逐个动能力清单守卫。
    "factor_profile": ToolSpec("factor_profile",
                               "因子档案：本地全历史 IC/ICIR 评估（样本内，未做样本外验证）",
                               "无参数", _t_factor_profile),
    "param_changes": ToolSpec("param_changes", "参数变更单（审计留痕，最近 20 条）",
                              "无参数", _t_param_changes),
    "agent_tasks": ToolSpec("agent_tasks", "任务中心：最近任务与状态（最近 20 条）",
                            "无参数", _t_agent_tasks),
    # P2-28① 最后一项（2026-09-11）：做T决策库。惰性结算与 /api/market/minute-decisions 同口径。
    "minute_decisions": ToolSpec("minute_decisions",
                                 "做T决策库（记录→结算→错误归因；open=未到结算窗口，非失败）",
                                 "symbol=可选，按标的代码过滤｜limit=条数（1~50，默认 20）",
                                 _t_minute_decisions),
    # P2-28① 清单外补登记（2026-09-11）：预警触发记录。走 AlertRepository，不自己拼 SQL。
    "alert_events": ToolSpec("alert_events", "预警触发记录（时间为北京时间）",
                             "symbol=可选｜limit=条数（1~50，默认 20）", _t_alert_events),
    "boards": ToolSpec("boards", "板块排行榜", "board_type=hangye|gainian（默认 hangye）", _t_boards),
    "hot": ToolSpec("hot", "人气热股榜", "period=day|week|month（默认 day）", _t_hot),
    "anomaly": ToolSpec("anomaly", "当日异动原因（可按代码查为什么异动）", "symbols=可选，逗号分隔≤6只；缺省=全市场榜", _t_anomaly),
    "review": ToolSpec("review", "某交易日复盘报告要点", "date=YYYY-MM-DD（可省略）", _t_review),
    "brief": ToolSpec("brief", "今日盘前简报", "无参数", _t_brief),
    # AI 大脑 P1 扩容（2026-09-08）：持仓/精选/情绪/事件——系统数据资产对助手开放
    "picks": ToolSpec("picks", "最近一次每日精选组合（含置信档）", "无参数", _t_picks),
    "positions": ToolSpec("positions", "当前持仓与浮动盈亏", "无参数", _t_positions),
    "sentiment": ToolSpec("sentiment", "近 5 日情绪相位", "无参数", _t_sentiment),
    "events": ToolSpec("events", "今日 watcher 异动/确认/证伪事件（自选池监控）", "无参数", _t_events),
    "news": ToolSpec(
        "news", "全网资讯/快讯事件流（今日热点消息 + 利好利空方向推断）",
        "limit=10（3~30）",
        _t_news,
    ),
    # P1-3（2026-09-10）：板块资金流——回答「今天资金在堆哪个方向」
    # 参数说明刻意不用 `|`（那是工具调用的分段符，写进去会被解析成无 `=` 的段而丢弃）
    "board_flow": ToolSpec(
        "board_flow", "板块主力净额排行（东财 f62 口径）",
        "kind=concept（默认）｜range=intraday（默认）｜可选值 industry / 5d / 10d",
        _t_board_flow,
    ),
    # P1-33（2026-09-11）：大宗商品一阶价格 → 板块传导线索（实测标定，无隔夜领先性）
    # 参数说明同样刻意不用 `|`（工具调用分段符）
    "commodity": ToolSpec(
        "commodity", "大宗商品异动与板块传导线索（含「无领先性」时点声明）",
        "keyword=可选，按商品名或行业名过滤（如 原油 / 钢铁 / 有色）",
        _t_commodity,
    ),
    # P1-32（2026-09-11）：气候一阶相位（ENSO/ONI）——把「厄尔尼诺」从新闻关键词
    # 升级为一阶指数；输出强制携带「滞后无领先性」+「人工链未获支持」双声明
    "climate": ToolSpec(
        "climate", "气候相位（厄尔尼诺/拉尼娜/中性）与候选传导链（含实证判读声明）",
        "无参数",
        _t_climate,
    ),
    # ---- 个股与大盘数据面扩容（2026-09-11，用户实测「系统明明有、助手说没有」）----
    "kline": ToolSpec(
        "kline", "个股 K 线（日线/分钟线）区间涨跌与最近若干根明细",
        "symbol=600519｜timeframe=1d（默认），可选 1w/60m/30m/15m/5m/1m｜limit=10（3~30）",
        _t_kline,
    ),
    "minute": ToolSpec(
        "minute", "当日分时走势摘要（开/高/低/振幅/关键时点/末段变化）",
        "symbol=600519",
        _t_minute,
    ),
    "capital_flow": ToolSpec(
        "capital_flow", "个股资金流（主力=超大单+大单，连续净流入天数）",
        "symbol=600519｜days=5（3~20）",
        _t_capital_flow,
    ),
    "basics": ToolSpec(
        "basics", "个股基本面与消息面：公司资料 + 财务摘要 + 最近公告 + 相关新闻",
        "symbol=600519",
        _t_basics,
    ),
    "market_overview": ToolSpec(
        "market_overview", "大盘概览：指数快照 + 涨跌家数宽度 + 两市成交额",
        "无参数",
        _t_market_overview,
    ),
    "themes": ToolSpec(
        "themes", "题材梯队看板（强度/阶段/健康度，涨停池按题材重组）",
        "date=YYYY-MM-DD（可省略）｜limit=10（3~30）",
        _t_themes,
    ),
    "watchlist": ToolSpec(
        "watchlist", "自选股清单与实时行情（按分组）",
        "无参数",
        _t_watchlist,
    ),
    "paper": ToolSpec(
        "paper", "模拟交易账户：资产汇总与持仓浮盈（只读）",
        "无参数",
        _t_paper,
    ),
    # P2-28① 收尾（2026-09-12）：回测。生产侧**无结果表可读**（端点同步计算、不持久化），
    # 故登记为「现算不留存」的执行型工具；耗时实测 0.9s 冷启，明细见 `_t_backtest` 头注。
    "backtest": ToolSpec(
        "backtest",
        "单标的日线策略回测（历史统计事实·样本内·未做参数优化，不构成买卖建议）",
        "symbol=单只代码（6 位）｜strategy=ma_cross/ma_breakout｜"
        "bars=回看日K根数（100~500，默认 250）",
        _t_backtest,
    ),
}


TOOL_LABELS: dict[str, str] = {
    "chain": "传导链",
    "quotes": "实时行情",
    "limit_up": "涨停池",
    "limit_down": "跌停池",
    "limit_break": "炸板池",
    "longhu": "龙虎榜",
    "theme_members": "题材成分",
    "orderbook": "盘口",
    "trades": "逐笔",
    "auction": "集合竞价",
    "factor_profile": "因子档案",
    "param_changes": "参数变更",
    "agent_tasks": "任务中心",
    "minute_decisions": "做T决策",
    "alert_events": "预警记录",
    "boards": "板块排行",
    "hot": "人气热榜",
    "anomaly": "异动原因",
    "review": "复盘报告",
    "brief": "盘前简报",
    "picks": "每日精选",
    "positions": "持仓",
    "sentiment": "情绪相位",
    "events": "盘中事件",
    "news": "资讯快讯",
    "board_flow": "板块资金流",
    "commodity": "大宗商品",
    "climate": "气候相位",
    "kline": "K线",
    "minute": "分时走势",
    "capital_flow": "个股资金流",
    "basics": "公司资料与公告",
    "market_overview": "大盘概览",
    "themes": "题材梯队",
    "watchlist": "自选股",
    "paper": "模拟账户",
    "backtest": "策略回测",
}


def tool_label(name: str) -> str:
    """工具名 → 中文短标签（进度提示与回执展示用；前端不再自己维护一份映射）。"""
    return TOOL_LABELS.get(name) or name


def tool_manifest() -> str:
    """给提示词的工具清单（只读 + 受限，明确边界）。"""
    lines = [
        "## 可用工具（只读，受限）",
        "需要真实数据时，在回答**开头单独一行**写 {{tool:名称|参数=值}}，",
        "系统会取数后把结果回填给你，你再继续回答（标记行不会显示给用户）。",
        f"可以先取一批数、看过结果**再取第二批**（最多 {MAX_TOOL_ROUNDS} 轮，"
        f"每轮最多 {MAX_CALLS_PER_TURN} 次）；不在下表的名称/参数会被拒绝；取不到就如实说没有，绝不编造。",
        "**常见误判**：下表覆盖了行情/K线/分时/资金流/龙虎榜/公告财务/资讯快讯/"
        "指数宽度/题材/精选/持仓/事件等绝大部分数据需求——**先取数，再下结论**，"
        "不要凭印象回答「我没有这项数据」。",
    ]
    for spec in TOOL_SPECS.values():
        lines.append(f"- {{{{tool:{spec.name}|{spec.params}}}}} → {spec.desc}")
    return "\n".join(lines)
