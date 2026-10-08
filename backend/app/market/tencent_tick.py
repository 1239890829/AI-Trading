"""有界腾讯HTTP分笔后备：仅当现有链和TDX无结果时调用，不采集全天。

页0是最早成交，指数倍增+二分定位尾页；最多24请求/4秒。
分笔约3秒聚合，量为手；日期取自同证券快照，不以本机今天补造。
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone, timedelta

import httpx

from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import cache_on
from app.data_providers.eastmoney import ProviderError
from app.data_providers.tencent import to_tencent_symbol, _ROW_RE
from app.schemas.market import Quality, Trade

_URL = "https://stock.gtimg.cn/data/index.php"
_PAGE_SIZE = 70
_MAX_PAGE = 255  # 超范围直接失败，不返回旧尾部冒充最新
_SIDE = {"B": "buy", "S": "sell", "M": "neutral"}


def parse_tick_page(text: str, code: str, page: int) -> list[dict]:
    if not text.strip():
        return []
    match = re.fullmatch(rf'v_detail_data_{re.escape(code)}=\[(\d+),"([^"]*)"\];?', text.strip())
    if not match or int(match[1]) != page:
        raise ProviderError("tencent ticks unexpected symbol/page/format")
    rows = []
    try:
        for item in match[2].split("|") if match[2] else []:
            seq, clock, price, change, volume, amount, side = item.split("/")
            datetime.strptime(clock, "%H:%M:%S")
            nums = list(map(float, (price, change, volume, amount)))
            if not all(math.isfinite(n) for n in nums) or nums[0] <= 0 or nums[2] < 0 or nums[3] < 0 or side not in _SIDE:
                raise ValueError("invalid price/volume/amount/side")
            rows.append({"seq": int(seq), "clock": clock, "price": nums[0], "volume": nums[2], "side": _SIDE[side]})
    except ValueError as exc:
        raise ProviderError("tencent ticks invalid record") from exc
    if (rows and rows[0]['seq'] != page * _PAGE_SIZE) or len(rows) > _PAGE_SIZE or any(b['seq'] != a['seq'] + 1 or b['clock'] < a['clock'] for a, b in zip(rows, rows[1:])):
        raise ProviderError("tencent ticks sequence/time gap")
    return rows


class TencentTickFallback:
    def __init__(self):
        self._cache = cache_on(self, "tencent_tick.tail", 3, maxsize=64)

    async def fetch(self, symbol: str, limit: int = 50) -> list[Trade]:
        code = to_tencent_symbol(symbol)
        if not re.fullmatch(r'(sh|sz)\d{6}', code) or code.startswith(('sh000', 'sz399')):
            raise ProviderError("tencent ticks unsupported security")
        if not 1 <= limit <= 200:
            raise ProviderError("tencent ticks limit must be 1..200")
        # UTC日+源日校验，缓存返回副本，保留原received_at；不刷新旧数据时间。
        key = (code, limit, datetime.now(BJ_TZ).date())
        _, rows = await self._cache.get_or_set(key, lambda: self._load(symbol, code, limit))
        return [r.model_copy(deep=True) for r in rows]

    async def _load(self, symbol: str, code: str, limit: int) -> list[Trade]:
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(trust_env=False, timeout=1.5, headers={"User-Agent": "Mozilla/5.0"}) as client:
                requests = 0

                async def read(url, params=None):
                    nonlocal requests
                    requests += 1
                    if requests > 24:
                        raise ProviderError("tencent ticks request budget exceeded")
                    async with client.stream('GET', url, params=params) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 192 * 1024:
                                raise ProviderError('tencent ticks response size budget exceeded')
                    return data.decode('gbk', errors='strict')

                async def stamp():
                    raw = await read('https://qt.gtimg.cn/q=' + code)
                    matched = [(p+c, f) for p, c, f in _ROW_RE.findall(raw) if p+c == code]
                    if len(matched) != 1:
                        raise ProviderError("tencent ticks snapshot identity mismatch")
                    fields = matched[0][1].split('~')
                    try:
                        stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=BJ_TZ)
                        if stamp.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(seconds=120):
                            raise ValueError('future source timestamp')
                        volume = float(fields[36])
                        if not math.isfinite(volume) or volume < 0:
                            raise ValueError('invalid snapshot volume')
                        return stamp, volume
                    except (ValueError, IndexError) as exc:
                        raise ProviderError("tencent ticks missing/invalid source timestamp or volume") from exc

                pages = {}

                async def page(n, *, refresh=False):
                    if refresh or n not in pages:
                        pages[n] = parse_tick_page(await read(_URL, {'appn': 'detail', 'action': 'data', 'c': code, 'p': n}), code, n)
                    return pages[n]

                before, volume = await stamp()
                if not await page(0):
                    if volume > 0:
                        raise ProviderError('tencent ticks empty despite positive source volume')
                    return []
                low, high = 0, 1
                while await page(high):
                    low = high
                    if high == _MAX_PAGE:
                        raise ProviderError("tencent ticks tail exceeds page budget")
                    high = min(high * 2, _MAX_PAGE)
                while high - low > 1:
                    mid = (high + low) // 2
                    if await page(mid):
                        low = mid
                    else:
                        high = mid
                # 尾页可能在查询时新增成交；刷新并追一页，否则误把发现时旧尾当最新。
                tail = await page(low, refresh=True)
                next_tail = await page(low + 1, refresh=True)
                if next_tail:
                    low += 1
                    tail = next_tail
                    if await page(low + 1, refresh=True):
                        raise ProviderError("tencent ticks tail changed too quickly")
                collected = tail
                n = low - 1
                while len(collected) < limit and n >= 0:
                    collected = await page(n) + collected
                    n -= 1
                if any(b['seq'] != a['seq'] + 1 or b['clock'] < a['clock'] for a, b in zip(collected, collected[1:])):
                    raise ProviderError("tencent ticks tail discontinuity")
                after, _ = await stamp()
                if before.date() != after.date():
                    raise ProviderError("tencent ticks crossed source day")
                now = datetime.now(timezone.utc)
                out = []
                for r in collected[-limit:]:
                    ts = datetime.combine(after.date(), datetime.strptime(r['clock'], '%H:%M:%S').time(), BJ_TZ).astimezone(timezone.utc)
                    if ts > after.astimezone(timezone.utc):
                        raise ProviderError("tencent ticks exceed source timestamp")
                    stale = (now - ts).total_seconds() > 120
                    out.append(Trade(symbol=symbol, ts=ts, price=r['price'], volume=r['volume'], side=r['side'], source='tencent', quality=Quality.stale if stale else Quality.medium, quality_reasons=['HTTP分笔聚合，非L2；全天完整性未验证'] + (['成交时间超过120秒'] if stale else [])))
                return out


_fallback = TencentTickFallback()


async def fetch_tencent_trades(symbol: str, limit: int = 50) -> list[Trade]:
    return await _fallback.fetch(symbol, limit)
