"""筹码分布引擎（CYQ 近似口径）——方向 2「主力行为识别」的数据基础。

ths 官方 59 端点无筹码分布数据（capability-map.md 已核对），故按经典 CYQ 算法
自算：marketdb 日K（daily_k: OHLCV+turnover）+ 换手率衰减模拟。

**口径声明（三态纪律，必须随输出透出）**：
- 流通股本不在仓内 → FREE_FLOAT_PROXY = mean(volume) / 0.01，
  即「假设窗口平均日换手率为 1%」。同一只票内所有换手率共同缩放，
  因此 **主峰位置 / 集中度 / 获利盘的相对形态稳健**；获利盘比例的绝对值
  受该假设影响，`approx=True` 显式标注，禁止当精确值解读。
- 每日新筹码按当日 [low, high] 三角分布铺开，峰在 VWAP=turnover/volume；
  一字板（high==low）全铺该价位；停牌/零成交日旧筹码不动。
- 衰减上限 0.85/日（保护极端换手把历史筹码一次清空）。

输出语义（CYQ 形态学）：
- profit_ratio  获利盘比例（< 现价的筹码占比）——高位高获利盘=派发风险区
- concentration 筹码集中度 (p95-p5)/(p95+p5)——越小越密集（CYQ 口径）
- main_peak     主密集峰（吸筹/派发成本区）
- support/resistance 现价下方/上方最大筹码峰（「上峰不移下跌不止，
  下峰锁定行情未尽」的量化对应）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.ttl_cache import TTLCache

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

# 算法常数（集中在表，便于复盘按 action_items 调参）
MAX_DAILY_TURNOVER = 0.85      # 单日最大换手比例（保护上限）
ASSUMED_AVG_TURNOVER = 0.01    # 自由流通盘代理的假设日均换手
DEFAULT_WINDOW = 250           # 参与分布的交易日窗口
GRID = 100                     # 价格网格档数
_BJ = timezone(timedelta(hours=8))
_MS = 1000


def simulate_chip_distribution(
    rows: list[dict[str, float | None]],
    grid: int = GRID,
) -> dict | None:
    """纯函数核心：按时间升序的 OHLCV 行 → 筹码分布与形态指标。

    :param rows: [{open, high, low, close, volume, turnover}]，date 升序；
                 各字段允许 None（该日跳过铺码；close 缺失不更新现价）。
    :returns: {grid_prices, dist, profit_ratio, concentration, main_peak,
               support, resistance, as_of_close}；有效行 <5 → None（样本不足显式降级）。
    """
    valid = [r for r in rows
             if r.get("high") is not None and r.get("low") is not None
             and r.get("volume") is not None and float(r["volume"]) > 0
             and float(r["high"]) > 0 and float(r["low"]) > 0]
    if len(valid) < 5:
        return None
    closes = [float(r["close"]) for r in valid if r.get("close") is not None]
    if not closes:
        return None
    as_of_close = closes[-1]

    lows = [float(r["low"]) for r in valid]
    highs = [float(r["high"]) for r in valid]
    g_min, g_max = min(lows), max(highs)
    if g_max <= g_min:
        g_max = g_min * 1.0001  # 全程一字（退市整理等极端）防零除
    step = (g_max - g_min) / grid
    grid_prices = [g_min + step * (i + 0.5) for i in range(grid)]

    # 自由流通盘代理（窗口内全部有效行参与，与铺码窗口解耦）
    volumes = [float(r["volume"]) for r in valid]
    ff_proxy = (sum(volumes) / len(volumes)) / ASSUMED_AVG_TURNOVER

    dist = [0.0] * grid
    for r in valid:
        if r.get("turnover") is None:
            continue  # 无成交额无法定峰位，跳过该日新筹码（旧筹码不动）
        vol = float(r["volume"])
        h = min(MAX_DAILY_TURNOVER, max(0.0, vol / ff_proxy))
        if h <= 0:
            continue
        dist = [x * (1.0 - h) for x in dist]
        low, high = float(r["low"]), float(r["high"])
        turnover = float(r["turnover"])
        vwap = turnover / vol if turnover > 0 else (low + high) / 2.0
        vwap = min(max(vwap, low), high)
        if high - low < 1e-9:  # 一字板：全铺该价位
            idx = min(grid - 1, max(0, int((vwap - g_min) / step)))
            dist[idx] += h
            continue
        half = max(vwap - low, high - vwap, 1e-9)
        weights = [max(0.0, 1.0 - abs(p - vwap) / half) for p in grid_prices]
        w_sum = sum(weights)
        if w_sum <= 0:
            continue
        for i, w in enumerate(weights):
            dist[i] += h * w / w_sum

    total = sum(dist)
    if total <= 0:
        return None
    dist = [x / total for x in dist]

    # —— 形态指标 ——
    profit_ratio = sum(d for d, p in zip(dist, grid_prices) if p < as_of_close)

    acc = 0.0
    cum: list[float] = []
    for d in dist:
        acc += d
        cum.append(acc)
    p5 = _quantile_price(cum, grid_prices, 0.05)
    p95 = _quantile_price(cum, grid_prices, 0.95)
    concentration = (p95 - p5) / (p95 + p5) if (p95 + p5) > 0 else None

    peak_idx = max(range(grid), key=lambda i: dist[i])
    peak_h = dist[peak_idx]
    half_window = [i for i in range(grid) if dist[i] >= peak_h * 0.5]
    below = [i for i in range(grid) if grid_prices[i] < as_of_close]
    above = [i for i in range(grid) if grid_prices[i] > as_of_close]

    return {
        "as_of_close": round(as_of_close, 3),
        "bars": len(valid),
        "grid_prices": [round(p, 3) for p in grid_prices],
        "dist": [round(d, 6) for d in dist],
        "profit_ratio": round(profit_ratio, 4),
        "concentration": round(concentration, 4) if concentration is not None else None,
        "main_peak": {
            "price": round(grid_prices[peak_idx], 3),
            "width_low": round(grid_prices[min(half_window)], 3),
            "width_high": round(grid_prices[max(half_window)], 3),
        },
        "support": _max_dist_price(below, dist, grid_prices),
        "resistance": _max_dist_price(above, dist, grid_prices),
    }


def _quantile_price(cum: list[float], grid_prices: list[float], q: float) -> float:
    for c, p in zip(cum, grid_prices):
        if c >= q:
            return p
    return grid_prices[-1]


def _max_dist_price(idxs: list[int], dist: list[float], grid_prices: list[float]) -> float | None:
    if not idxs:
        return None
    best = max(idxs, key=lambda i: dist[i])
    return round(grid_prices[best], 3) if dist[best] > 0 else None


class ChipService:
    """筹码分布服务：marketdb 只读查询 + TTLCache（键=symbol+window，有界）。"""

    def __init__(self, db_path: str | Path | None = None, window: int = DEFAULT_WINDOW):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._window = window
        self._cache = TTLCache("chip-dist", ttl=1800, maxsize=256)

    def available(self) -> bool:
        return self._db_path.exists()

    def distribution(self, symbol: str, full: bool = False) -> dict:
        """裸 6 位代码 → 筹码分布 + 形态指标 + 口径注记。

        :param full: True 时回传 grid_prices/dist 全网格（研究用；默认剔除减重）。
        缺仓 / 查询失败 / 样本不足 → {"available": False, "reason": ...}
        （三态显式降级，绝不静默返回空分布当「无筹码」）。
        """
        bare = symbol.split(".")[0]
        key = f"{bare}:{self._window}"
        hit, cached = self._cache.get(key)
        if hit:
            return cached if full else {k: v for k, v in cached.items()
                                        if k not in ("grid_prices", "dist")}
        out = self._query(bare)
        self._cache.set(key, out)
        return out if full else {k: v for k, v in out.items()
                                 if k not in ("grid_prices", "dist")}

    def _query(self, bare: str) -> dict:
        import duckdb

        if not self.available():
            return {"available": False,
                    "reason": "marketdb 不存在（先跑 scripts/sync_marketdb.py --full）"}
        try:
            with duckdb.connect(str(self._db_path), read_only=True) as con:
                rows = con.execute(
                    """
                    SELECT date_ms, open_price, high_price, low_price, close_price,
                           volume, turnover
                    FROM daily_k
                    WHERE thscode LIKE ?
                    ORDER BY date_ms
                    """,
                    [bare + ".%"],
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("chip: 查询失败 %s：%s", bare, exc)
            return {"available": False, "reason": f"marketdb 查询失败：{exc}"}

        if not rows:
            return {"available": False, "reason": f"{bare} 无日K覆盖"}

        sim = simulate_chip_distribution(
            [{"open": t[1], "high": t[2], "low": t[3], "close": t[4],
              "volume": t[5], "turnover": t[6]} for t in rows[-self._window:]]
        )
        if sim is None:
            return {"available": False, "reason": f"{bare} 有效日K不足（<5 根）"}

        sim["symbol"] = bare
        sim["available"] = True
        sim["approx"] = True
        sim["window"] = self._window
        sim["as_of"] = datetime.fromtimestamp(rows[-1][0] / _MS, tz=_BJ).strftime("%Y-%m-%d")
        sim["note"] = (
            "近似口径：流通盘=窗口均量/1%假设换手；形态（主峰/集中度/支撑压力）稳健，"
            "获利盘绝对值仅供参考"
        )
        sim["generated_at"] = datetime.now().isoformat(timespec="seconds")
        return sim


_default: ChipService | None = None


def get_chip_service() -> ChipService:
    """进程级单例。"""
    global _default
    if _default is None:
        _default = ChipService()
    return _default
