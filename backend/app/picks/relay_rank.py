"""P1-6 接力质量排序（2026-09-09 落地，基于 P1-3 池内条件 IC 验证）

背景：kmid2/max20 在「昨日涨停池 ∩ T-1 因子」可执行口径下 T+5 RankIC 稳定为正
（kmid2 +0.060/65% 日为正、max20 +0.052/61%，1466+ 交易日样本）。语义：
- kmid2（当日实体/全距，[-1,1]）越高 = 封板越实/方向越确定——池内"收得越实
  次日越强"；
- max20（20 日最高价/现价）越接近 1 = 越接近 20 日新高——池内"回撤越浅越强"。

用途：今日涨停池收盘后按二者排序 → 次日接力候选的**顺序参考**（不是买卖信号，
只回答"同是涨停、谁的接力质量统计上更高"）。红线合规：输出排序+依据，无买卖建议。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

log = logging.getLogger(__name__)

TOP_N = 15

# 池内逐只日 K 的并发上限（P0-3，2026-09-11）。原实现是**串行 await**：
# 单请求耗时 = 池大小 × RTT，涨停 60+ 只时可达数十秒。8 路并发把耗时压到
# O(RTT × 池/8)，同时不至于把腾讯源打出限流（该源在四源链里承担日 K 主职）。
# 用信号量而非无界 gather：池子大时无界并发等于自我 DDoS。
MAX_CONCURRENCY = 8


async def _bars_last_n(provider, symbol: str, n: int, upto: date) -> list[dict] | None:
    """腾讯日 K qfq 末 n 根（≤ upto）。失败 None（单只降级，不拖垮整池）。"""
    try:
        bars = await provider.get_kline(symbol, "1d")
    except Exception as exc:  # noqa: BLE001  单只失败降级
        log.warning("relay-rank: kline %s failed: %s", symbol, exc)
        return None
    rows = [b for b in bars if b.ts.date() <= upto]
    if len(rows) < 2:
        return None
    return rows[-n:]


def _factors(rows: list[dict]) -> dict[str, float | None]:
    """末根当日 kmid2/max20（与 factor library 同式；max20 用 close 相对前 20 日高）。"""
    bar = rows[-1]
    hi, lo, op, cl = bar.high, bar.low, bar.open, bar.close
    kmid2 = (cl - op) / (hi - lo) if (hi is not None and lo is not None and hi > lo) else None
    prior_high = max(b.high for b in rows[:-1] if b.high is not None) if len(rows) > 1 else None
    max20 = (prior_high / cl) if (prior_high and cl) else None
    return {"kmid2": kmid2, "max20": max20}


async def compute_relay_rank(provider, trade_date: date, pool: list | None = None) -> list[dict]:
    """今日涨停池 → 接力质量排序。pool 可注入（测试）；缺省拉当日池。

    P0-3（2026-09-11）：池内逐只取 K 由串行改**有界并发**（`MAX_CONCURRENCY`）。
    单只失败仍只降级自己（`_bars_last_n` 返回 None → 该只不进榜），不拖垮整池。
    """
    if pool is None:
        pool = await provider.get_limit_up_pool(trade_date)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(rec) -> dict | None:
        symbol = getattr(rec, "symbol", None)
        if not symbol:
            return None
        async with sem:
            bars = await _bars_last_n(provider, symbol, 21, trade_date)
        if not bars:
            return None
        fx = _factors(bars)
        return {
            "symbol": symbol,
            "name": getattr(rec, "name", "") or "",
            "boards": getattr(rec, "consecutive_boards", 1) or 1,
            "reason": (getattr(rec, "reason", "") or "").strip(),
            "kmid2": round(fx["kmid2"], 4) if fx["kmid2"] is not None else None,
            "max20": round(fx["max20"], 4) if fx["max20"] is not None else None,
        }

    gathered = await asyncio.gather(*(one(rec) for rec in pool))
    out: list[dict] = [r for r in gathered if r is not None]
    # 主排序 kmid2 降序（封得实），辅 max20 升序（近新高）；因子缺失垫底不臆造
    out.sort(key=lambda x: (
        -(x["kmid2"] if x["kmid2"] is not None else -9),
        x["max20"] if x["max20"] is not None else 99,
    ))
    return out[:TOP_N]
