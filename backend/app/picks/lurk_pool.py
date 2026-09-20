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
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from app.market import price_rules
from app.market.marketdb_freshness import MAX_STALE_TRADE_DAYS, trading_day_lag

log = logging.getLogger(__name__)

#: 上海时区。`date_ms` 是 UTC+8 零点毫秒，必须按该时区还原日期，
#: 否则跨零点会差一天（与 marketdb_freshness._to_date 同口径）。
from app.core.bjtime import BJ_TZ, beijing_now  # S2-8 时区收敛

DB = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"
FETCH_DAYS = 55      # 拉取最近 55 个实际交易日（不是 55 个自然日）
LOOKBACK_DAYS = 5    # 确认距最新数据 <= N 个交易日才算「观察中」
TOP_N = 30
MIN_VOLUME_HISTORY = 40
LOGIC_VERSION = "lurk-point-in-time-v2"


def _limit_pct(code: str, *, asof: date | None = None) -> float:
    """潜伏形态涨幅上限（小数），按试盘日委托统一 price_rules。

    `asof` 用于可由代码段和制度生效日确定的历史板块规则。marketdb 无历史名称，
    因而 2026-07-06 前主板 ST 5% 特例仍无法可靠还原；调用方不得把这条未知边界
    写成已 point-in-time 完整覆盖。
    """
    return price_rules.limit_pct(str(code).split(".")[0], asof=asof) / 100.0


def _scan(bars: list[tuple]) -> dict | None:
    """单股形态扫描：只在完整历史上判断，并返回**最近**一次试盘/确认时点。"""
    n = len(bars)
    if n < MIN_VOLUME_HISTORY + 2:
        return None

    # point-in-time 研究宁可弃权也不“修”坏输入：重复/乱序日期和 NaN 会让窗口
    # 被压缩或比较失真，不能继续算成一个看似正常的形态。
    prev_ms: int | float | None = None
    for bar in bars:
        if len(bar) < 7:
            return None
        try:
            ts = float(bar[1])
            values = [float(x) for x in bar[2:7]]
        except (TypeError, ValueError):
            return None
        if not math.isfinite(ts) or any(not math.isfinite(x) for x in values):
            return None
        if prev_ms is not None and ts <= prev_ms:
            return None
        prev_ms = ts

    code = bars[0][0] if isinstance(bars[0][0], str) else "600000"
    last_probe = None
    last_conf = None
    for i in range(MIN_VOLUME_HISTORY, n):
        # 潜伏前提：30 根旧量能 + 10 根近量能是硬前史，禁止负索引借用未来尾部。
        wc = [bars[k][5] for k in range(i - 20, i)]
        if min(wc) <= 0:
            continue
        amp = (max(wc) - min(wc)) / min(wc)
        if amp > 0.30:
            continue
        v_recent = sum(bars[k][6] for k in range(i - 10, i)) / 10
        v_prior = sum(bars[k][6] for k in range(i - 40, i - 10)) / 30
        if v_prior <= 0 or v_recent > v_prior * 1.2:
            continue

        o, h, c, v = bars[i][2], bars[i][3], bars[i][5], bars[i][6]
        pc = bars[i - 1][5]
        if pc <= 0:
            continue
        probe_date = datetime.fromtimestamp(bars[i][1] / 1000.0, tz=BJ_TZ).date()
        cap = _limit_pct(code, asof=probe_date)
        chg = c / pc - 1
        if not (0.02 <= chg < cap - 0.01):
            continue
        v5 = sum(bars[k][6] for k in range(i - 5, i)) / 5
        if v5 <= 0 or v < v5 * 1.3:
            continue
        body = max(o, c) - min(o, c)
        if body <= 0 or (h - max(o, c)) / body < 0.4:
            continue
        floor = min(bars[k][5] for k in range(i - 5, i))

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
            # 不在第一次命中后退出：文档语义是“最近确认”，后面出现新的合法形态
            # 应覆盖旧确认；未来数据只能形成**未来的新确认**，不能改写既有前缀判断。
            last_probe = bars[i][1]
            last_conf = bars[j][1]
            break

    if last_conf is None:
        return None
    return {"probe_ms": last_probe, "confirm_ms": last_conf}


def scan_lurk_pool(db_path: Path | None = None, *, asof: date | None = None) -> dict:
    """全市场扫描 → 潜伏观察池，历史回放按 `asof` 真正截断。

    `as_of` 是本次扫描实际可见的最新 K 线日；`requested_asof` 是调用方请求时点。
    历史 `asof` 会同时参与 SQL 上界、形态窗口和试盘日涨跌停制度，绝不只影响陈旧说明。
    """

    db = str(db_path or DB)
    requested_asof = asof or beijing_now().date()

    def unavailable(note: str) -> dict:
        return {
            "trade_date": None,
            "as_of": None,
            "requested_asof": requested_asof.isoformat(),
            "stale_days": None,
            "stale": True,
            "stale_note": note,
            "logic_version": LOGIC_VERSION,
            "items": [],
        }

    if not Path(db).exists():
        return unavailable(
            f"marketdb 仓不存在（{db}）——先跑 scripts/sync_marketdb.py 回补"
        )

    cutoff_ms = int(
        (
            datetime(
                requested_asof.year,
                requested_asof.month,
                requested_asof.day,
                tzinfo=BJ_TZ,
            )
            + timedelta(days=1)
        ).timestamp() * 1000
    ) - 1
    try:
        con = duckdb.connect(db, read_only=True)
        # live 路径也按 requested_asof 截断：即使上游误写未来日期，当前 API
        # 也不能把“未来 K 线”当作今天已知事实。历史回放与实时路径共用同一上界。
        mx = con.execute(
            "select max(date_ms) from daily_k where date_ms <= ?", [cutoff_ms]
        ).fetchone()[0]
        if mx is None:
            con.close()
            return unavailable(
                f"marketdb 截至 {requested_asof.isoformat()} 无可用 K 线——不以空池冒充无候选"
            )

        # FETCH_DAYS 是**交易日条数**。先取最近 N 个市场日期，再据此拉全市场，
        # 避免按 55 个自然日截窗在长假附近凑不够 40 根前史。
        trade_ms = [
            row[0]
            for row in con.execute(
                "select distinct date_ms from daily_k where date_ms <= ? "
                "order by date_ms desc limit ?",
                [mx, FETCH_DAYS],
            ).fetchall()
        ]
        trade_ms.reverse()
        if not trade_ms:
            con.close()
            return unavailable(
                f"marketdb 截至 {requested_asof.isoformat()} 无可用交易日"
            )
        lo = trade_ms[0]
        rows = con.execute(
            "select thscode, date_ms, open_price, high_price, low_price, "
            "close_price, volume from daily_k "
            "where date_ms >= ? and date_ms <= ? order by thscode, date_ms",
            [lo, mx],
        ).fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001
        try:
            con.close()
        except Exception:
            pass
        log.warning("lurk_pool: marketdb 读取失败：%s", exc)
        return unavailable(f"marketdb daily_k 不可读：{exc}")

    by_code: dict[str, list] = defaultdict(list)
    for code, t, o, h, l, c, v in rows:
        # 不在装配层丢坏行；交给 _scan 对整只股票 fail-closed，避免删掉一根后
        # 让窗口“自动补齐”成另一段历史。
        by_code[code].append((code, t, o, h, l, c, v))

    trade_pos = {ms: idx for idx, ms in enumerate(trade_ms)}
    latest_pos = len(trade_ms) - 1
    items: list[dict] = []
    for code, bars in by_code.items():
        hit = _scan(bars)
        if not hit:
            continue
        conf_pos = trade_pos.get(hit["confirm_ms"])
        if conf_pos is None:
            continue
        lag_sessions = latest_pos - conf_pos
        if lag_sessions > LOOKBACK_DAYS:
            continue
        items.append(
            {
                "symbol": code.split(".")[0],
                "probe_ms": hit["probe_ms"],
                "confirm_ms": hit["confirm_ms"],
                "confirm_lag_sessions": lag_sessions,
            }
        )
    items.sort(key=lambda x: x["confirm_ms"], reverse=True)

    data_date = datetime.fromtimestamp(mx / 1000.0, tz=BJ_TZ).date()
    stale_days = trading_day_lag(data_date, requested_asof)
    stale = stale_days > MAX_STALE_TRADE_DAYS
    return {
        "trade_date": data_date.isoformat(),
        "as_of": data_date.isoformat(),
        "requested_asof": requested_asof.isoformat(),
        "stale_days": stale_days,
        "stale": stale,
        "stale_note": (
            f"池基于 {data_date.isoformat()} 的 K 线，滞后 {stale_days} 个交易日"
            f"（阈值 {MAX_STALE_TRADE_DAYS}，含阈值内属正常）——"
            "marketdb 停更时先跑 scripts/sync_marketdb.py"
        ) if stale else "",
        "logic_version": LOGIC_VERSION,
        "rule_note": (
            "试盘涨幅上限按试盘日板块制度；marketdb 无历史名称，"
            "2026-07-06 前主板 ST 5% 特例仍不可可靠还原"
        ),
        "items": items[:TOP_N],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(scan_lurk_pool(), ensure_ascii=False, indent=1))
