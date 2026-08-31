"""统一进程内 TTL 缓存（P0-5 / 数据源 C3）。

收敛此前散落各处的自写缓存（sentiment/heatmap/boards/themes 60s、sparkline 5min、
announcements/news/digest 60s、trading-days 24h、screener 30min）：此前四套实现并存——
(time.time(), payload) 与 (monotonic(), ...) 两种元组、market.py/news.py 两份拷贝的
_route_cache/_ttl_hit 辅助对、screener 自带单飞——且键空间无界（公告/新闻按
(symbol, limit) 无限累积；题材看板与涨停归因每个交易日在 app.state 上挂一个新属性，
跨日慢性泄漏）。统一后的语义：

- 时间基准一律 monotonic()，不受系统时钟回拨影响；
- 容量有界（LRU 逐出）+ 逐出计数，键空间异常增长（"缓存键漂移"）从此可见；
- get_or_set 内建异步单飞：同 key 并发未命中只放一个协程回源，其余共享结果；
- 工厂抛异常不缓存（下次重试）；返回 None 默认不缓存（cache_none=True 可改）；
- 实例进入弱引用注册表，GET /api/system/caches 汇总命中率/容量/逐出。

缓存实例经 cache_on() 挂在进程级单例（app.state / hub / 服务实例）上而非模块全局：
测试为每个用例新建 app/服务实例，缓存随 holder 生命周期隔离，跨用例零污染。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import weakref
from collections import OrderedDict
from time import monotonic
from typing import Any, Callable

# 弱引用注册表：holder 被回收（如测试里的临时 app）后实例自动消失
_LIVE: "weakref.WeakSet[TTLCache]" = weakref.WeakSet()


class TTLCache:
    """有界 LRU + TTL + 异步单飞的进程内缓存。

    过期条目在读写时惰性清除；超过 maxsize 时逐出最久未使用的条目。
    字典操作由 threading.Lock 保护（set 可能来自 to_thread 的同步上下文）；
    锁本身不做 IO，开销可忽略。
    """

    def __init__(self, name: str, ttl: float, *, maxsize: int = 256):
        self.name = name
        self.ttl = float(ttl)
        self.maxsize = max(1, int(maxsize))
        self._data: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        self._locks: dict[Any, asyncio.Lock] = {}
        self._mu = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        _LIVE.add(self)

    # ---- 同步读写 ----

    def get(self, key: Any) -> tuple[bool, Any]:
        """返回 (是否命中, 值)；命中即刷新 LRU 顺序并计一次 hit。"""
        now = monotonic()
        with self._mu:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return False, None
            ts, value = entry
            if now - ts >= self.ttl:
                del self._data[key]
                self.misses += 1
                return False, None
            self._data.move_to_end(key)
            self.hits += 1
            return True, value

    def set(self, key: Any, value: Any) -> None:
        now = monotonic()
        with self._mu:
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1

    def invalidate(self, key: Any | None = None) -> None:
        """key=None 清空整表；否则只删该键。"""
        with self._mu:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key, None)

    def __len__(self) -> int:
        with self._mu:
            return len(self._data)

    def stats(self) -> dict:
        with self._mu:
            total = self.hits + self.misses
            return {
                "name": self.name,
                "ttl": self.ttl,
                "maxsize": self.maxsize,
                "size": len(self._data),
                "pending": len(self._locks),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None,
                "evictions": self.evictions,
            }

    # ---- 异步单飞 ----

    def _lock_for(self, key: Any) -> asyncio.Lock:
        with self._mu:
            lock = self._locks.get(key)
            if lock is None:
                # 锁登记表同样有界：只保留当前被持有的（持有中的锁不可能被误删，
                # 等待者早已持有同一对象引用，仍能正常会合）
                if len(self._locks) > max(self.maxsize * 2, 128):
                    self._locks = {k: v for k, v in self._locks.items() if v.locked()}
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def get_or_set(
        self,
        key: Any,
        factory: Callable[[], Any],
        *,
        cache_none: bool = False,
    ) -> tuple[bool, Any]:
        """命中直接返回 (True, 值)；未命中则单飞回源并缓存。

        返回 (hit, 值)：hit=True 仅表示"从缓存命中"；单飞中胜出回源的调用方
        （以及所有非命中路径）拿到 hit=False，调用方据此区分是否需要标注
        cached=True（如 screener 的 payload.model_copy）。
        factory 可为同步或异步可调用；抛异常时不缓存、异常原样上抛
        （CalendarUnavailable→503 这类错误路径不会被缓存）；返回 None 默认
        不缓存，便于"这次回源失败、下次请求重试"的语义。
        """
        hit, value = self.get(key)
        if hit:
            return True, value
        async with self._lock_for(key):
            hit, value = self.get(key)
            if hit:
                return True, value
            result = factory()
            if inspect.isawaitable(result):
                result = await result
            if result is not None or cache_none:
                self.set(key, result)
            return False, result


def cache_on(holder: Any, name: str, ttl: float, *, maxsize: int = 256) -> TTLCache:
    """取 holder（app.state / hub / 服务单例）上的命名缓存，不存在则创建挂载。"""
    attr = f"_ttl_cache_{name}"
    cache = getattr(holder, attr, None)
    if not isinstance(cache, TTLCache):
        cache = TTLCache(name, ttl, maxsize=maxsize)
        setattr(holder, attr, cache)
    return cache


def live_caches() -> list[dict]:
    """所有存活实例的统计快照（观测端点用；实例随 holder 回收自动退出）。"""
    return [c.stats() for c in sorted(_LIVE, key=lambda c: c.name)]
