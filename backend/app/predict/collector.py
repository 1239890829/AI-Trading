"""预判模块的通用取数助手。

⚠️ 预判「生产入口」（采集→评分→落库）已随 run_prediction 于 2026-09-13
死代码清理移除（全仓无调用方），原证据采集器 collect_predict_evidence
一并移除（其唯一消费方就是 run_prediction）。本模块现仅保留三个
可独立测试的纯助手：
- guess_context：运行时点语境（weekend / holiday / pre_market / intraday / evening）
- next_weekday_after：目标交易日（严格晚于 d 的下一个工作日，节假日不校准）
- pick_daily_board：龙虎榜「当日榜 vs 三日榜」选榜口径（同股双记录不可互相覆盖）
若将来重启预判生产，从 git 历史找回。
"""
from __future__ import annotations

from datetime import date, datetime

from app.schemas.market import LongHuRecord


def guess_context(now_cst: datetime) -> str:
    """运行时点语境：weekend / holiday / pre_market / intraday / evening。"""
    wd = now_cst.weekday()  # 0=Mon
    t = now_cst.hour * 100 + now_cst.minute
    if wd >= 5:
        return "weekend"
    if t < 915:
        return "pre_market"
    if 915 <= t <= 1505:
        return "intraday"
    return "evening"


def next_weekday_after(d: date) -> date:
    """目标交易日：严格晚于 d 的下一个周一至周五（节假日不校准，报告内声明）。"""
    from datetime import timedelta

    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def pick_daily_board(records: list[LongHuRecord]) -> dict[str, LongHuRecord]:
    """从龙虎榜记录中按 symbol 选出**当日榜**（range_days == 1）。

    ⚠️ 为什么不能简单写 `{r.symbol: r for r in records}`：
    交易所对同一只股票可同时披露「当日榜」（如日涨幅偏离值达 7%）与
    「三日榜」（如连续三日涨幅偏离值累计达 20%），两者是**两条独立记录**、
    buy/sell/net 是不同区间的累计值，相加或互相替代都是错的。
    用 dict 建映射会静默覆盖，保留哪条取决于服务端返回顺序——不报错、
    不可复现，是典型的静默失效。

    实测 2026-08-31：002396 星网锐捷 日榜净额 −9783 万 / 三日榜 +7252 万，
    **符号相反**。若取到三日榜，"游资净买入"证据会给出与事实相反的方向。

    选榜口径：优先 range_days == 1（当日榜，"当日游资净买入"才是资金验证语义）；
    只有当日榜缺席时才回退到其他区间，调用方须用返回记录的 `.range_days`
    标注口径，不得把三日榜数字当当日数读。
    """
    daily: dict[str, LongHuRecord] = {}
    fallback: dict[str, LongHuRecord] = {}
    for r in records:
        if not r.symbol:
            continue
        if r.range_days == 1:
            daily[r.symbol] = r
        else:
            # range_days 缺失（老数据源/东财口径）时按当日榜处理：
            # 东财 datacenter 本身就是日榜口径，且该分支下不存在多榜并存。
            fallback.setdefault(r.symbol, r)
    for sym, rec in fallback.items():
        daily.setdefault(sym, rec)
    return daily
