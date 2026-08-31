"""5 分钟涨速采样器（P1 扩展：指数/题材详情的"涨速"标签）。

口径（行业通行，非自创）：**涨速 = (当前价 − 5 分钟前价) / 5 分钟前价 × 100%**。
同花顺 / 东方财富 / 通达信行情列表的"涨速"列均为 5 分钟涨跌速口径；东财
push2 clist 的 f22 字段同源（2026-08-31 实测收盘值 0.4~0.6 量级吻合）。
ths 官方 API 无涨速数值字段（飙升榜/热股榜是热度排名），故自算。

真实数据支撑：价格**只**来自腾讯 qt.gtimg 批量快照（与个股行情同一条链），
绝不把当日涨跌幅或其他口径冒充涨速。

采样策略：**惰性采样**——涨速榜端点每次被调用时对目标成分批量快照并落
滚动缓存；前端 30s 轮询自然把历史攒到 5 分钟窗口。历史不足的标的返回
sampled=False（前端显示"采样中"），宁可空着也不臆造数值。
"""

from __future__ import annotations

import time
from collections import deque

WINDOW_SEC = 5 * 60          # 涨速口径窗口
BASE_TOLERANCE_SEC = 90      # 基准点允许的窗口偏移（4~6.5 分钟内的最近采样）
TTL_SEC = 20 * 60            # 单标的采样保留时长，超期清除
MAX_SYMBOLS = 600            # 缓存标的数上限（超限按最旧活动时间淘汰）
DEQUE_CAP = 40               # 每标的最多保留 40 个采样点（约 20 分钟）


class SpeedSampler:
    """按标的滚动的价格采样缓存。时间用 monotonic，测试可注入。"""

    def __init__(self) -> None:
        self._series: dict[str, deque[tuple[float, float]]] = {}
        self._last_seen: dict[str, float] = {}

    def record(self, prices: dict[str, float | None], now: float | None = None) -> int:
        """写入一批采样，返回记录的标的数。"""
        now = time.monotonic() if now is None else now
        self._gc(now)
        n = 0
        for sym, price in prices.items():
            if price is None or price <= 0:
                continue
            dq = self._series.get(sym)
            if dq is None:
                dq = self._series[sym] = deque(maxlen=DEQUE_CAP)
            # 采样去重：同一秒重复快照不追加（避免前端高频轮询灌爆队列）
            if dq and now - dq[-1][0] < 5:
                dq[-1] = (now, price)
            else:
                dq.append((now, price))
            self._last_seen[sym] = now
            n += 1
        return n

    def speed(self, symbol: str, now: float | None = None) -> tuple[float | None, float]:
        """返回 (涨速%, 采样跨度秒)。

        基准点取 [now−窗口−容差, now−窗口+容差] 内**最近**的一个采样；
        找不到足够历史（后端刚启动/标的刚进入关注列表）时涨速为 None，
        调用方必须显示"采样中"而不是拿别的数字顶上。
        """
        now = time.monotonic() if now is None else now
        dq = self._series.get(symbol)
        if not dq:
            return None, 0.0
        span = now - dq[0][0]
        latest = dq[-1][1]
        lo, hi = now - WINDOW_SEC - BASE_TOLERANCE_SEC, now - WINDOW_SEC + BASE_TOLERANCE_SEC
        base: float | None = None
        for ts, price in reversed(dq):
            if ts < lo:
                break
            if lo <= ts <= hi:
                base = price
                break
        if base is None or base <= 0 or latest is None or latest <= 0:
            return None, span
        return round((latest - base) / base * 100, 2), span

    def _gc(self, now: float) -> None:
        """超期标的清除 + 总量超限时淘汰最久不活跃的 10%。"""
        expired = [s for s, dq in self._series.items() if not dq or now - dq[-1][0] > TTL_SEC]
        for s in expired:
            self._series.pop(s, None)
            self._last_seen.pop(s, None)
        if len(self._series) > MAX_SYMBOLS:
            keep = sorted(self._series, key=lambda s: self._last_seen.get(s, 0), reverse=True)[: MAX_SYMBOLS // 10 * 9]
            drop = set(self._series) - set(keep)
            for s in drop:
                self._series.pop(s, None)
                self._last_seen.pop(s, None)
