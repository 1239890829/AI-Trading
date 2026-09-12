"""RPS 相对强度（横截面）：个股 N 日涨幅在全市场的百分位（0-100）。

来源：trading 分组调研采纳（docs/archive/github-repo-audit-financial-api-sequoia-x.md）——
既有 tech_score 六/七维全是「个股自身 vs 自身历史」，缺「个股 vs 全市场」维度；
打板语境里「强者恒强」正是横截面信息（欧奈尔 RPS 口径）。

数据底座：本地 DuckDB marketdb（scripts/sync_marketdb.py 回补，ths 官方全市场
dump）。RPS 消费 **复权收盘（daily_k_adj）** 的 N 日涨幅——不复权会把除权股
（10送10 = 表观 -50%）错杀成最弱档，这是硬要求。

语义与降级（**三态**，缺一不可）：
- 一次 SQL 取「每股最新一根 + LAG(50)/LAG(120) 锚点涨幅」，分位在 Python 端算
  （全市场 ~5500 行；实测 1025 万行仓上首次 ~6s，缓存后亚毫秒）；并列组取组内
  最小百分位——保守口径；
- 仓未建 / 查询失败 / **数据陈旧** → 返回 `{}` 并 warning 一次，消费方
  （tech_score v3 rps 维）按缺证据记中性 0.5，绝不臆造分数；
- **陈旧态是 2026-09-10（P0-7）补上的第三态**：此前只覆盖「仓未建 / 查询失败」，
  于是**仓存在但停更 6 个交易日**时，它照旧用 09-03 的横截面算分位，
  `tech_score` 的 rps 维当今日数据静默使用——「拿旧横截面描述今天」，
  与已修掉的「拿昨天分位描述今天」同类（静默、看着正常）。判定见
  `app/market/marketdb_freshness.py`（内容日期 × 交易日滞后，非文件 mtime）；
- 截面按 (trade_date 或 latest) 缓存（TTLCache 约定：键空间有界、异常不缓存）——
  盘中数据不变，缓存 30 分钟足够；sync 落新数据后由 TTL 自然过期。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from app.core.ttl_cache import TTLCache
from app.market.marketdb_freshness import MAX_STALE_TRADE_DAYS, freshness as mdb_freshness
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛

log = logging.getLogger(__name__)

# app/picks/rps.py → parents[2] = backend/；仓在 backend/data/marketdb/（不入 git）
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

_SQL = """
WITH w AS (
    SELECT thscode, date_ms, close_adj,
           ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY date_ms) AS rn,
           COUNT(*)    OVER (PARTITION BY thscode) AS n_rows,
           LAG(close_adj, {n}) OVER (PARTITION BY thscode ORDER BY date_ms) AS c_prev
    FROM daily_k_adj
    WHERE date_ms <= ?
),
last_row AS (
    SELECT thscode, date_ms, close_adj / NULLIF(c_prev, 0) - 1.0 AS ret
    FROM w WHERE rn = n_rows AND c_prev IS NOT NULL AND c_prev > 0
)
SELECT thscode, ret FROM last_row WHERE ret IS NOT NULL
"""


def _trade_date_ms(trade_date: str | None) -> int:
    """"YYYYMMDD" → 上海零点毫秒；None = 不设上限（用仓内最新截面）。"""
    if not trade_date:
        return (1 << 62)  # 远未来，等价于无上限
    d = datetime.strptime(trade_date, "%Y%m%d")
    return int(datetime(d.year, d.month, d.day, tzinfo=BJ_TZ).timestamp() * 1000)


def _asof_date(trade_date: str | None) -> date | None:
    """"YYYYMMDD" → date（新鲜度判定的基准日）；None/非法 → None（= 今天）。"""
    if not trade_date:
        return None
    try:
        return datetime.strptime(trade_date, "%Y%m%d").date()
    except ValueError:
        return None


def _pct_rank_dedup(values: list[float]) -> list[int]:
    """百分位 0-100：并列组取组内最小百分位（保守）。n<=1 时全 50。"""
    n = len(values)
    if n <= 1:
        return [50] * n
    order = sorted(range(n), key=lambda i: values[i])
    out = [0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        rps = round(100.0 * i / (n - 1))
        for k in range(i, j + 1):
            out[order[k]] = rps
        i = j + 1
    return out


class RpsService:
    """全市场 RPS 截面服务（进程级单例；缓存挂在本实例上，键空间有界）。"""

    def __init__(self, db_path: str | Path | None = None, windows: tuple[int, ...] = (50, 120),
                 max_stale_days: int = MAX_STALE_TRADE_DAYS):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._windows = windows
        self._max_stale_days = max_stale_days
        self._cache = TTLCache("rps-snapshot", ttl=1800, maxsize=8)

    def available(self) -> bool:
        return self._db_path.exists()

    def freshness(self, trade_date: str | None = None) -> dict:
        """仓新鲜度（内容日期 × 交易日滞后）。显式 trade_date = 历史截面基准日。"""
        return mdb_freshness(self._db_path, asof=_asof_date(trade_date),
                             max_stale_days=self._max_stale_days)

    def snapshot(self, trade_date: str | None = None) -> dict[str, dict[str, int]]:
        """全市场 RPS 截面：{裸6位代码: {"rps50": .., "rps120": ..}}。

        :param trade_date: "YYYYMMDD" 历史截面（≤ 当日最新锚点，回测/复盘用）；
                           None = 仓内最新。
        缺仓 / **数据陈旧** / 查询失败 → {}（三态显式降级，一次性 warning）。
        """
        key = trade_date or "latest"
        hit, cached = self._cache.get(key)
        if hit:
            return cached
        if not self.available():
            self._warn_once(f"marketdb 不存在（{self._db_path}），RPS 降级为空——"
                            "先跑 scripts/sync_marketdb.py --full")
            return {}
        fresh = self.freshness(trade_date)
        if fresh["stale"]:
            # 陈旧横截面算出的分位是"旧数据描述今天"——宁缺毋滥，降级为缺证据
            self._warn_once(f"{fresh['reason']}——RPS 降级为空（陈旧横截面不得当今日数据用）")
            return {}
        try:
            out = self._query(trade_date)
        except Exception as exc:  # noqa: BLE001
            self._warn_once(f"RPS 查询失败，降级为空：{exc}")
            return {}
        self._cache.set(key, out)
        return out

    def get(self, symbol: str, trade_date: str | None = None) -> dict[str, int] | None:
        """单只便捷读取；无覆盖返回 None。"""
        return self.snapshot(trade_date).get(symbol)

    def _query(self, trade_date: str | None) -> dict[str, dict[str, int]]:
        import duckdb

        upper = _trade_date_ms(trade_date)
        per_window: dict[str, list[float]] = {}
        rows_by_window: dict[str, list[str]] = {}
        with duckdb.connect(str(self._db_path), read_only=True) as con:
            for n in self._windows:
                rows = con.execute(_SQL.format(n=n), [upper]).fetchall()
                rows_by_window[f"rps{n}"] = [r[0] for r in rows]
                per_window[f"rps{n}"] = [float(r[1]) for r in rows]

        merged: dict[str, dict[str, int]] = {}
        for field, rets in per_window.items():
            symbols = rows_by_window[field]
            ranks = _pct_rank_dedup(rets)
            for sym, rps in zip(symbols, ranks):
                bare = sym.split(".")[0]
                merged.setdefault(bare, {})[field] = rps
        return merged

    def _warn_once(self, msg: str) -> None:
        if getattr(self, "_warned", False):
            return
        self._warned = True
        log.warning("rps: %s", msg)


_default: RpsService | None = None


def get_rps_service() -> RpsService:
    """进程级单例（懒建；仓缺失时 available()=False，snapshot 降级空）。"""
    global _default
    if _default is None:
        _default = RpsService()
    return _default
