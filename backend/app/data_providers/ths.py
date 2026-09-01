"""同花顺金融数据服务 Provider（官方 fuyao API，https://fuyao.aicubes.cn）。

- 认证：X-api-key（key 只放 .env，禁止入库入前端）
- thscode 格式：600519.SH / 000001.SZ
- 能力：行情快照、交易日历、涨停/跌停/炸板池、连板天梯、龙虎榜（含概念标签）、
  财务三表、估值、集合竞价、异动、热榜、全市场导出
- 边界：不含 L2 十档/逐笔/分钟K（官方 capability-map 声明）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from app.data_providers.eastmoney import ProviderError
from app.schemas.market import Kline, LimitUpRecord, LongHuRecord, Quote

log = logging.getLogger(__name__)

SOURCE = "ths"
_TZ_SH = timezone(timedelta(hours=8))


def to_thscode(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9", "5")):
        return f"{s}.SH"
    return f"{s}.SZ"


def from_thscode(thscode: str) -> str:
    return thscode.split(".")[0]


def _as_shanghai(dt: datetime) -> datetime:
    """naive datetime 统一按上海时区解释（与腾讯 K 线的 _as_aware 同款处理）。"""
    return dt.replace(tzinfo=_TZ_SH) if dt.tzinfo is None else dt.astimezone(_TZ_SH)


def date_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_TZ_SH).timestamp() * 1000)


class ThsFuyaoProvider:
    name = "ths"
    realtime = True

    def __init__(self, api_key: str, base_url: str = "https://fuyao.aicubes.cn", timeout: float = 8.0):
        if not api_key:
            raise ProviderError("ths api_key missing")
        self._client = httpx.AsyncClient(
            trust_env=False, timeout=timeout,
            headers={"X-api-key": api_key, "Accept": "application/json"},
        )
        self._base = base_url.rstrip("/")
        self._names: dict[str, str] = {}
        self._names_ts: float = 0.0

    async def _ensure_names(self, symbols: list[str]) -> None:
        """官方代码表 → {code: name} 映射，24h 缓存；失败不阻塞行情。"""
        import time as _time

        if self._names and _time.time() - self._names_ts < 86400:
            return
        if all(s in self._names for s in symbols):
            return
        try:
            names: dict[str, str] = {}
            offset = 0
            while True:
                data = await self._get(
                    "/api/meta/tickers/list",
                    {"exchange": "SH,SZ", "asset_type": "a-share", "limit": 10000, "offset": offset},
                )
                items = data.get("item") or []
                for it in items:
                    code = from_thscode(str(it.get("thscode") or ""))
                    if len(code) == 6 and it.get("name"):
                        names[code] = it["name"]
                if len(items) < 10000:
                    break
                offset += 10000
            if names:
                self._names = names
                self._names_ts = _time.time()
        except Exception as exc:
            log.warning("ths tickers/list failed: %s", exc)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        resp = await self._client.get(f"{self._base}{path}", params=params)
        if resp.status_code != 200:
            raise ProviderError(f"ths {path} HTTP {resp.status_code}")
        payload = resp.json()
        if payload.get("code") != 0:
            raise ProviderError(f"ths {path} code={payload.get('code')} {payload.get('message')}")
        return payload.get("data") or {}

    # ---- 行情 ----

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        data = await self._get("/api/a-share/prices/snapshot", {"thscodes": ",".join(to_thscode(s) for s in symbols)})
        items = data.get("item") or []
        await self._ensure_names(symbols)
        quotes = [self._parse_quote(it) for it in items]
        quotes = [q for q in quotes if q is not None]
        for q in quotes:
            if q.name is None:
                q.name = self._names.get(q.symbol)
        if not quotes:
            raise ProviderError("ths snapshot empty")
        return quotes

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    def _parse_quote(self, it: dict) -> Quote | None:
        code = from_thscode(str(it.get("thscode") or ""))
        if len(code) != 6:
            return None
        num = lambda v: float(v) if v is not None else None  # noqa: E731
        ts_ms = num(it.get("timestamp")) if "timestamp" in it else None
        return Quote(
            symbol=code,
            name=it.get("name"),
            market="SH" if it.get("thscode", "").endswith(".SH") else "SZ",
            price=num(it.get("last_price")),
            open=num(it.get("open_price")),
            high=num(it.get("high_price")),
            low=num(it.get("low_price")),
            prev_close=num(it.get("prev_price")),
            change=num(it.get("price_change")),
            change_pct=round(num(it.get("price_change_ratio_pct")) or 0, 2),
            volume=num(it.get("volume")),
            amount=num(it.get("turnover")),
            data_timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None,
            source=SOURCE,
        )

    INDEX_NAMES = {
        "000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指",
        "000688.SH": "科创50", "000300.SH": "沪深300", "000852.SH": "中证1000",
    }

    async def get_indices(self) -> list[Quote]:
        data = await self._get(
            "/api/a-share/prices/snapshot",
            {"thscodes": ",".join(self.INDEX_NAMES)},
        )
        quotes = []
        for it in data.get("item") or []:
            q = self._parse_quote(it)
            if q is None:
                continue
            q.name = self.INDEX_NAMES.get(it.get("thscode"), q.name)
            quotes.append(q)
        if not quotes:
            raise ProviderError("ths indices empty")
        return quotes

    # ---- 特殊数据 ----

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]:
        data = await self._get(
            "/api/a-share/special-data/limit-up-pool",
            {"date_ms": date_ms(trade_date), "page": 1, "size": 200, "sort_field": "continue_day_cnt"},
        )
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            out.append(
                LimitUpRecord(
                    symbol=code,
                    name=it.get("name"),
                    trade_date=trade_date,
                    price=num(it.get("last_price")),
                    change_pct=num(it.get("price_change_ratio_pct")),
                    first_seal_time=str(it.get("limit_up_time") or "") or None,
                    seal_amount=num(it.get("seal_money")),
                    turnover_rate=num(it.get("turnover_rate")),
                    consecutive_boards=int(num(it.get("continue_day_cnt")) or 1),
                    boards_stat=str(it.get("continue_day_text") or "") or None,
                    reason=it.get("limit_up_reason"),
                    source=SOURCE,
                )
            )
        if not out:
            raise ProviderError(f"ths limit-up-pool empty for {trade_date}")
        return out

    async def get_limit_up_ladder(self) -> list[dict]:
        """连板天梯近 30 交易日矩阵，展平为行 [{date, tier, board_num, symbol, name, seal_nextday}]。

        B4 交叉验证数据面：seal_nextday 即源方算好的"次日是否封板"。
        文档写 string|null、每梯队最多 4 只——2026-08-31 实抓两者均不符：
        实际是布尔 true/false（最近交易日为 null），梯队只数无上限（二板 10+ 只）；
        sign_level 实测恒为整数 0，无信息量，丢弃。
        """
        data = await self._get("/api/a-share/special-data/limit-up-ladder", {})
        out: list[dict] = []
        for day in data.get("item") or []:
            d = day.get("date")
            for tier, members in (day.get("boards") or {}).items():
                for it in members or []:
                    code = from_thscode(str(it.get("thscode") or ""))
                    if len(code) != 6:
                        continue
                    out.append({
                        "date": d,
                        "tier": tier,
                        "board_num": int(it.get("board_num") or 0),
                        "symbol": code,
                        "name": it.get("name"),
                        "seal_nextday": it.get("seal_nextday"),  # bool | None（最近交易日无次日参考）
                        "source": SOURCE,
                    })
        if not out:
            raise ProviderError("ths limit-up ladder empty")
        return out

    async def get_limit_break_pool(self, trade_date: date) -> list[LimitUpRecord]:
        """炸板池（涨停后开板未回封）。"""
        data = await self._get(
            "/api/a-share/special-data/limit-break-pool",
            {"date_ms": date_ms(trade_date), "page": 1, "size": 200},
        )
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            out.append(
                LimitUpRecord(
                    symbol=code, name=it.get("name"), trade_date=trade_date,
                    price=num(it.get("last_price")), change_pct=num(it.get("price_change_ratio_pct")),
                    break_count=int(num(it.get("open_times")) or 0) if it.get("open_times") is not None else None,
                    turnover_rate=num(it.get("turnover_ratio_pct")),
                    amount=num(it.get("turnover")),
                    source=SOURCE,
                )
            )
        return out

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]:
        """龙虎榜个股明细。

        ⚠️ 2026-09-02 修复（字段名必须按官方文档，不能猜）：
        原实现用的是凭猜测写的字段名——price_change_ratio_pct / buy_amount /
        sell_amount / net_buy / last_price / turnover ——**没有一个是响应里真实存在的**
        （真实字段见 skills/hithink-finance/docs/api/endpoints-special-data.md
        §dragon-tiger-list，stock_items[] 共 14 个字段）。
        后果：所有数值字段恒为 None，前端龙虎榜看起来"数据全空"，
        而接口其实返回了 68 条记录——**静默失败，不报错**。

        请求参数同样订正：文档只支持 `date`(YYYY-MM-DD) 与 `board_type`，
        原先传的 `date_ms` + `size` 都不是本接口的参数。

        另：原 `reason = it.get("reason") or concepts` 会在真实原因缺失时
        拿 concept_list 冒充"上榜原因"，导致界面显示"玉米,粮食概念,乳业"。
        已改为只用真实的 limit_reason；概念标签另存 concept_tags。

        ⚠️ 2026-09-02 二次修复（range_days 是统计区间，不是"上榜天数"）：
        同一只股票可能同时上「当日榜」与「三日榜」，交易所分别披露，
        stock_items[] 里就是**两条独立记录**，靠 `range_days` 区分（1 / 3）。
        两者的 buy/sell/net 是不同区间的累计值，**相加或互相替代都是错的**。
        此前未解析该字段，下游 `{r.symbol: r}` 建 dict 静默覆盖 → 取到哪条由服务端
        返回顺序决定。实测 2026-08-31：76 条 / 70 只股票；002396 星网锐捷
        日榜净额 -9783 万、三日榜 +7252 万**符号相反**，直接导致"游资净买入"
        证据方向随机反转。故此处必须原样透出 range_days。
        """
        data = await self._get(
            "/api/a-share/special-data/dragon-tiger-list",
            {"date": trade_date.isoformat(), "board_type": "all"},
        )
        out = []
        for it in data.get("stock_items") or []:
            # ticker 是纯代码（文档字段），thscode 需转换，两者都兜底
            code = str(it.get("ticker") or "") or from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            # ths 的 change 是**小数比例**（实测：0.0997 = 9.97%、0.2002 = 20.02% 创业板涨停），
            # 而本项目 change_pct 统一为**百分数**。不换算会让涨停股在界面显示成 0.1%。
            raw_change = num(it.get("change"))
            change_pct = raw_change * 100 if raw_change is not None else None
            concepts = ",".join(c.get("name", "") for c in (it.get("concept_list") or [])[:5]) or None
            out.append(
                LongHuRecord(
                    symbol=code,
                    name=it.get("name"),
                    trade_date=trade_date,
                    # stock_items[] **没有收盘价与成交额**字段（hot_money_items[].rows[] 才有 amount）。
                    # 取不到就置 None，绝不拿其他字段顶替。
                    close=None,
                    change_pct=change_pct,
                    amount=None,
                    net_buy=num(it.get("net_value")),
                    buy_amount=num(it.get("buy_value")),
                    sell_amount=num(it.get("sell_value")),
                    reason=it.get("limit_reason"),
                    concept_tags=concepts,
                    # 统计区间：1=当日榜、3=三日榜。同一股票两榜并存时靠它区分。
                    range_days=int(num(it.get("range_days"))) if it.get("range_days") is not None else None,
                    net_rate=num(it.get("net_rate")),
                    # org_net_value / hot_money_net_value **允许缺失**：
                    # 缺失表示"该榜单没有机构/游资席位参与"，与"参与但净额为 0"语义不同，
                    # 故保持 None，绝不用 0 填充。
                    org_net_value=num(it.get("org_net_value")),
                    hot_money_net_value=num(it.get("hot_money_net_value")),
                    hot_rank=int(num(it.get("hot_rank"))) if it.get("hot_rank") is not None else None,
                    source=SOURCE,
                )
            )
        if not out:
            raise ProviderError(f"ths dragon-tiger empty for {trade_date}")
        return out

    async def get_trading_days(self) -> list[str]:
        """近一年交易日（YYYYMMDD 升序）。"""
        data = await self._get("/api/a-share/calendar/trading-days")
        return [str(it.get("date")) for it in data.get("item") or []]

    async def get_hot_stock_list(self, period: str = "day") -> list[dict]:
        """当前热股榜（period=day 24小时榜 / hour）。[{rank, symbol, name, heat, rank_change}]。"""
        data = await self._get("/api/a-share/special-data/hot-stock-list", {"period": period})
        ts = data.get("timestamp")
        ts_iso = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            out.append({
                "rank": int(it.get("rank") or 0),
                "symbol": code,
                "name": it.get("name"),
                "heat": num(it.get("heat")),
                "rank_change": num(it.get("rank_change")),
                "ts": ts_iso,
                "source": SOURCE,
            })
        if not out:
            raise ProviderError("ths hot stock list empty")
        return out

    async def get_hot_stock_list_history(self, d: date) -> list[dict]:
        """指定自然日的历史热股排名（D1 收盘验证用）。字段同 get_hot_stock_list 无 heat。"""
        data = await self._get(
            "/api/a-share/special-data/hot-stock-list-history", {"date": d.strftime("%Y-%m-%d")}
        )
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            out.append({
                "rank": int(it.get("rank") or 0),
                "symbol": code,
                "name": it.get("name"),
                "heat": None,
                "rank_change": None,
                "ts": None,
                "source": SOURCE,
            })
        if not out:
            raise ProviderError(f"ths hot stock list history empty for {d}")
        return out

    async def get_auction_snapshot(self, symbols: list[str], stage: str = "final") -> list[dict]:
        """集合竞价快照（stage=final 终态 / live 实时）。单次 ≤100 只。

        返回 [{symbol, name, auction_price, auction_pct, auction_volume(手), auction_amount,
        auction_volume_ratio, auction_unmatched, data_status}]。data_status 表示就绪状态
        （ready/final/suspended/not_ready），非就绪条目照常返回、由调用方决定是否标 gap。
        """
        if not symbols:
            return []
        codes = ",".join(to_thscode(s) for s in symbols[:100])
        data = await self._get("/api/a-share/auction/snapshot", {"thscodes": codes, "stage": stage})
        num = lambda v: float(v) if v is not None else None  # noqa: E731
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            out.append({
                "symbol": code,
                "name": it.get("name"),
                "auction_price": num(it.get("auction_price")),
                "auction_pct": num(it.get("auction_pct")),
                "auction_volume": num(it.get("auction_volume")),
                "auction_amount": num(it.get("auction_amount")),
                "auction_volume_ratio": num(it.get("auction_volume_ratio")),
                "auction_unmatched": num(it.get("auction_unmatched")),
                "pre_close_price": num(it.get("pre_close_price")),
                "data_status": it.get("data_status"),
                "source": SOURCE,
            })
        if not out:
            raise ProviderError("ths auction snapshot empty")
        return out

    async def get_auction_benchmark(self, d: date) -> list[dict]:
        """短线风向标竞价基准（按日）：[{symbol, name, auction_pct, tags[]}]。"""
        data = await self._get(
            "/api/a-share/auction/short-term-benchmark", {"date": d.strftime("%Y-%m-%d")}
        )
        num = lambda v: float(v) if v is not None else None  # noqa: E731
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            out.append({
                "symbol": code,
                "name": it.get("name"),
                "auction_pct": num(it.get("auction_pct")),
                "tags": list(it.get("tags") or []),
                "source": SOURCE,
            })
        if not out:
            raise ProviderError(f"ths auction benchmark empty for {d}")
        return out

    async def get_adjustment_events(self, symbol: str, start: date | None = None, end: date | None = None) -> list[dict]:
        """复权事件流（现金分红/送股，单只）：[{ex_date, dividend, bonus}]，ex_date 降序。

        官方只给原始事件，复权因子由调用方推导（避免误当服务端已算好的每日因子）。
        """
        params: dict = {"thscode": to_thscode(symbol)}
        if start:
            params["from"] = start.strftime("%Y-%m-%d")
        if end:
            params["to"] = end.strftime("%Y-%m-%d")
        data = await self._get("/api/a-share/corporate-actions/adjustment-factors", params)
        num = lambda v: float(v) if v is not None else 0.0  # noqa: E731
        out = []
        for it in data.get("item") or []:
            ex_ms = it.get("ex_date_ms")
            if not ex_ms:
                continue
            out.append({
                "ex_date": datetime.fromtimestamp(ex_ms / 1000, tz=_TZ_SH).date(),
                "dividend": num(it.get("dividend_per_share")),
                "bonus": num(it.get("per_share_bonus")),
                "source": SOURCE,
            })
        return out  # 事件可为空（无分红送股），不抛错

    # ---- 协议其余方法：链上由其他 Provider 负责 ----

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]:
        """A 股历史日 K（fuyao `/api/a-share/prices/historical`）。

        为什么补它：K 线此前只有腾讯一条可用通路，2026-08-31 腾讯 WAF 封禁
        后全链路中断（东财同时不可用），技术面评分与详情页 K 线图一起降级。
        ths 是官方源且不受腾讯 WAF 影响，是天然的第三源。

        官方限制（见 skills/hithink-finance/docs/api/endpoints-prices.md）：
        - 每次请求**仅一个 thscode**，不接受逗号
        - 仅支持 `interval=1d`（日线）→ 分钟/周线直接抛错交由链上下沉
        - 时间窗口 ≤ 10 年，超出返回 code=1003
        - `adjust` 默认 forward（前复权），与腾讯口径一致
        """
        if timeframe != "1d":
            raise ProviderError(f"ths kline 仅支持日线 1d，收到 {timeframe}")
        end_dt = _as_shanghai(end) if end is not None else datetime.now(_TZ_SH)
        start_dt = _as_shanghai(start) if start is not None else end_dt - timedelta(days=730)
        if start_dt > end_dt:
            raise ProviderError(f"ths kline 时间区间非法：{start_dt} > {end_dt}")
        if (end_dt - start_dt).days > 3650:
            raise ProviderError("ths kline 时间窗口 ≤10 年（code=1003）")

        data = await self._get(
            "/api/a-share/prices/historical",
            {
                "thscode": to_thscode(symbol),
                "interval": "1d",
                "start": int(start_dt.timestamp() * 1000),
                "end": int(end_dt.timestamp() * 1000),
                "adjust": "forward",
            },
        )
        bars: list[Kline] = []
        prev_close: float | None = None
        for it in data.get("item") or []:
            ms = it.get("date_ms")
            if not ms:
                continue
            close = it.get("close_price")
            change_pct = None
            if close is not None and prev_close:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            bars.append(
                Kline(
                    symbol=symbol,
                    timeframe="1d",
                    ts=datetime.fromtimestamp(ms / 1000, tz=_TZ_SH),
                    open=it.get("open_price"),
                    high=it.get("high_price"),
                    low=it.get("low_price"),
                    close=close,
                    volume=it.get("volume"),
                    amount=it.get("turnover"),
                    change_pct=change_pct,
                    source=SOURCE,
                )
            )
            if close is not None:
                prev_close = close
        if not bars:
            raise ProviderError(f"ths kline empty for {symbol}")
        return bars

    async def get_order_book(self, symbol: str):
        return None  # fuyao 无五档盘口，链上由腾讯提供

    async def get_trades(self, symbol: str) -> list:
        return []

    async def search(self, query: str) -> list:
        return []
