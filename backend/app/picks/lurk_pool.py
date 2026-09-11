"""P1-5 潜伏观察池（2026-09-09 落地，判据同 P1-2 验证口径2）

KB-STOCK-24 系统化：主力悄然潜伏 = 长期缩量横盘（抛压真空）→ 放量试盘 →
缩量回踩不破位。P1-2 实证（scripts/validate_slow_patterns.py）：该形态确认后
20 日内涨停 26.4% vs 基线 15.9%（1.67×）——慢信号（5 日仅 1.21×），故定位为
**中线观察池**（供临板雷达/人工次日留意），非短线买入信号。

数据：marketdb daily_k（原始 OHLCV，覆盖至最近 sync 日）。盘后运行；同步滞后
交易日由 sync_marketdb 补。名称缺（daily_k 无名称列）→ 只给代码，不臆造。

🔴 陈旧披露（2026-09-11）：本池的「最近确认日 ≤ LOOKBACK_DAYS 交易日」是**相对库内
最新 K 线**判定的。marketdb 一旦停更，池子会看起来完全正常，实际却基于若干交易日前的
K 线（实测曾用 6 个交易日前的数据静默出池）。故输出额外带 `as_of`（池实际基于的 K 线
日期）、`stale_days`（相对今天滞后的交易日数）与 `stale`——**保持「可用但可见」**：
本池是中线观察池、不进任何评分权重，故停更时**不降级、不隐藏**，只把口径摊开给人看。

判据（与验证脚本同源）：
- 潜伏前提：T 前 20 根 close 振幅 ≤30% 且 近 10 日均量 ≤ 前 30 日均量 ×1.2
- 试盘 T：涨幅∈[2%, 涨停线-1%)、量>前5日均量×1.3、上影/实体>0.4
- 确认 C（T+1..T+6 首个）：close<close_T、量≤volume_T×0.7、期间低不破前5日平台
输出最近确认日距今 ≤ LOOKBACK_DAYS 的「观察中」票。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb

from app.market import price_rules
from app.market.marketdb_freshness import MAX_STALE_TRADE_DAYS, trading_day_lag

log = logging.getLogger(__name__)

#: 上海时区。`date_ms` 是 UTC+8 零点毫秒，必须按该时区还原日期，
#: 否则跨零点会差一天（与 marketdb_freshness._to_date 同口径）。
from app.core.bjtime import BJ_TZ, beijing_now  # S2-8 时区收敛

DB = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"
FETCH_DAYS = 55      # 拉取交易日跨度（窗口+确认扫描余量）
LOOKBACK_DAYS = 5    # 确认日在最近 N 交易日内的才算「观察中」
TOP_N = 30


def _limit_pct(code: str) -> float:
    """潜伏形态的涨幅上限（小数）：委托单点 price_rules.limit_pct。

    2026-09-11 收口：原为本模块第三份硬编码实现（且无 ST 分支）。marketdb 的
    daily_k 无名称列 → 传空 name；并轨后主板 ST 亦为 10%，与无名称兜底一致，
    口径不再依赖名称，故此委托不引入偏差。
    """
    return price_rules.limit_pct(str(code).split(".")[0]) / 100.0


def _scan(bars: list[tuple]) -> dict | None:
    """单股形态扫描：返回最近确认日（date_ms）若命中；None 否则。"""
    n = len(bars)
    if n < 26:
        return None
    cap = _limit_pct(bars[0][0] if isinstance(bars[0][0], str) else "600000")
    last_conf = None
    for i in range(25, n):  # 留 20 根潜伏窗
        # 潜伏前提
        wc = [bars[k][5] for k in range(i - 20, i)]  # close
        if not wc or min(wc) <= 0:
            continue
        amp = (max(wc) - min(wc)) / min(wc)
        if amp > 0.30:
            continue
        v_recent = sum(bars[k][6] for k in range(i - 10, i)) / 10
        v_prior = sum(bars[k][6] for k in range(i - 40, i - 10)) / 30
        if v_prior <= 0 or v_recent > v_prior * 1.2:
            continue
        # 试盘
        o, h, c, v = bars[i][2], bars[i][3], bars[i][5], bars[i][6]
        pc = bars[i - 1][5]
        if pc <= 0:
            continue
        chg = c / pc - 1
        if not (0.02 <= chg < cap - 0.01):
            continue
        v5 = sum(bars[k][6] for k in range(max(0, i - 5), i)) / 5
        if v5 <= 0 or v < v5 * 1.3:
            continue
        body = max(o, c) - min(o, c)
        if body <= 0 or (h - max(o, c)) / body < 0.4:  # noqa: E501
            continue
        floor = min(bars[k][5] for k in range(max(0, i - 5), i))
        for k in range(1, 7):
            j = i + k
            if j >= n:
                break
            cj = bars[j]
            if cj[5] >= c:
                continue
            if cj[6] > v * 0.7:
                continue
            if min(bars[x][4] for x in range(i, j + 1)) < floor:
                break
            last_conf = bars[j][1]  # date_ms
            break
        if last_conf is not None:
            break
    return {"confirm_ms": last_conf} if last_conf else None


def scan_lurk_pool(db_path: Path | None = None, *, asof: date | None = None) -> dict:
    """全市场扫描 → 潜伏观察池。

    返回 `{trade_date, as_of, stale_days, stale, stale_note, items:[{symbol, confirm_ms}]}`。
    `as_of` = 池实际依据的 K 线日期（= 库内 `MAX(date_ms)`），`stale_days` = 该日期相对
    `asof`（默认今天，上海）滞后的**交易日数**——停更时消费方据此自行判断可信度，
    本函数不因停更而丢弃结果（中线观察池，不进评分权重）。

    :param asof: 陈旧判定基准日；None = 今天。测试/回放可显式传入以获得确定结果。
    """
    db = str(db_path or DB)
    con = duckdb.connect(db, read_only=True)
    mx = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    lo = mx - FETCH_DAYS * 86_400_000
    rows = con.execute(
        "select thscode, date_ms, open_price, high_price, low_price, close_price, volume "
        "from daily_k where date_ms >= ? order by thscode, date_ms", [lo]
    ).fetchall()
    con.close()
    by_code: dict[str, list] = defaultdict(list)
    for code, t, o, h, l, c, v in rows:
        if None in (o, h, l, c, v):
            continue
        by_code[code].append((code, t, o, h, l, c, v))
    items: list[dict] = []
    day_ms = 86_400_000
    for code, bars in by_code.items():
        hit = _scan(bars)
        if not hit or (mx - hit["confirm_ms"]) > LOOKBACK_DAYS * day_ms:
            continue
        # 上证/深/创等统一去后缀为系统内部 code（600118.SH → 600118）；北交所 8/4/920 保留数字
        items.append({"symbol": code.split(".")[0], "confirm_ms": hit["confirm_ms"]})
    items.sort(key=lambda x: x["confirm_ms"], reverse=True)

    data_date = datetime.fromtimestamp(mx / 1000.0, tz=BJ_TZ).date()
    stale_days = trading_day_lag(data_date, asof or beijing_now().date())
    stale = stale_days > MAX_STALE_TRADE_DAYS
    return {
        "trade_date": data_date.isoformat(),
        "as_of": data_date.isoformat(),
        "stale_days": stale_days,
        "stale": stale,
        "stale_note": (
            f"池基于 {data_date.isoformat()} 的 K 线，滞后 {stale_days} 个交易日"
            f"（阈值 {MAX_STALE_TRADE_DAYS}，含阈值内属正常）——"
            "marketdb 停更时先跑 scripts/sync_marketdb.py"
        ) if stale else "",
        "items": items[:TOP_N],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(scan_lurk_pool(), ensure_ascii=False, indent=1))
