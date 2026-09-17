from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import httpx

from app.market import normalizer as nz
from app.schemas.market import (
    Kline,
    LimitDownRecord,
    LimitUpRecord,
    LongHuRecord,
    OrderBook,
    Quote,
    SymbolSearchItem,
    Trade,
)

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 指数 secid：沪 1.、深 0.
INDEX_SECIDS = [
    ("1.000001", "上证指数"),
    ("0.399001", "深证成指"),
    ("0.399006", "创业板指"),
    ("1.000688", "科创50"),
    ("1.000300", "沪深300"),
    ("1.000852", "中证1000"),
]

TIMEFRAME_KLT = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1d": 101, "1w": 102}

QUOTE_FIELDS = "f12,f13,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18,f124"
ORDERBOOK_FIELDS = (
    "f43,f57,f58,f86,"
    "f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,"
    "f11,f12,f13,f14,f15,f16,f17,f18,f19,f20"
)


class ProviderError(RuntimeError):
    """数据源请求失败。调用方必须停止将其当作实时数据使用。"""


def _num(v) -> float | None:
    """东财大量字段以 '-' 表示缺失，且数值/字符串混用。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def to_secid(symbol: str) -> str:
    s = symbol.strip()
    if not s:
        raise ProviderError("empty symbol")
    if s.startswith(("6", "9", "5")):
        return f"1.{s}"
    return f"0.{s}"


class EastmoneyProvider:
    """东方财富免费行情接口。免费接口无契约，字段可能漂移，失败必须降级处理。"""

    name = "eastmoney"
    realtime = True
    #: 秒级链位次（见 composite.REALTIME_METHODS）：腾讯(0)→新浪(1)→东财(2)，
    #: 与 composite 模块头"行情走腾讯→新浪→东财"的既有意图对齐——
    #: 此前无显式 rank，构造顺序让东财排在新浪前，属注释与行为的静默漂移。
    realtime_rank = 2

    def __init__(self, timeout: float = 5.0):
        self._client = httpx.AsyncClient(
            trust_env=False,  # 行情源均为国内站，直连，不走用户系统代理
            timeout=timeout,
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str, params: dict) -> dict:
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code} from {url}")
        body = resp.content.strip()
        if not body:
            raise ProviderError(f"empty reply from {url} (可能被限流)")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from {url}") from exc

    async def _ulist(self, secids: list[str], fields: str) -> list[dict]:
        payload = await self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {"fltt": "2", "invt": "2", "fields": fields, "secids": ",".join(secids)},
        )
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        return diff

    async def get_indices(self) -> list[Quote]:
        secids = [sid for sid, _ in INDEX_SECIDS]
        rows = await self._ulist(secids, "f12,f13,f14,f2,f3,f4,f6,f124")
        if not isinstance(rows, list):
            raise ProviderError("invalid index rows")
        by_secid = {}
        for raw in rows:
            if not isinstance(raw, dict):
                raise ProviderError("invalid index row")
            sid = f"{raw.get('f13')}.{raw.get('f12')}"
            if sid not in secids:
                continue
            if sid in by_secid:
                raise ProviderError(f"duplicate index identity: {sid}")
            by_secid[sid] = raw
        # 缺项保持缺席，由 Hub 保留旧值并标 stale；不能造空报价覆盖旧缓存。
        return [nz.normalize_index(by_secid[sid]) for sid in secids if sid in by_secid]

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        rows = await self._ulist([to_secid(s) for s in symbols], QUOTE_FIELDS)
        return [nz.normalize_quote(r) for r in rows if r.get("f12")]

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]:
        klt = TIMEFRAME_KLT.get(timeframe)
        if klt is None:
            raise ProviderError(f"unsupported timeframe: {timeframe}")
        payload = await self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": to_secid(symbol),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": str(klt),
                "fqt": "1",  # 前复权
                "end": "20500101",
                "lmt": "1000",
            },
        )
        klines_data = (payload.get("data") or {}).get("klines") or []
        bars = [nz.normalize_kline_row(symbol, timeframe, row) for row in klines_data]
        bars = [b for b in bars if b is not None]
        if start is not None:
            bars = [b for b in bars if b.ts >= start]
        if end is not None:
            bars = [b for b in bars if b.ts <= end]
        return bars

    async def get_order_book(self, symbol: str) -> OrderBook | None:
        payload = await self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/get",
            {"fltt": "2", "invt": "2", "fields": ORDERBOOK_FIELDS, "secid": to_secid(symbol)},
        )
        raw = payload.get("data")
        if not raw:
            return None
        return nz.normalize_order_book(symbol, raw)

    async def get_trades(self, symbol: str) -> list[Trade]:
        payload = await self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/details/get",
            {
                "secid": to_secid(symbol),
                "fields1": "f1,f2,f3,f4,f5",
                "fields2": "f51,f52,f53,f54,f55",
                "pos": "-100",
            },
        )
        details = (payload.get("data") or {}).get("details") or []
        trades = [nz.normalize_trade(symbol, row) for row in details]
        return [t for t in trades if t is not None]

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]:
        payload = await self._get_json(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "500",
                "sort": "fbt:asc",
                "date": trade_date.strftime("%Y%m%d"),
            },
        )
        pool = (payload.get("data") or {}).get("pool") or []
        records = [nz.normalize_limit_up(r, trade_date) for r in pool]
        return [r for r in records if r is not None]

    async def get_limit_down_pool(self, trade_date: date) -> list[LimitDownRecord]:
        """跌停池（2026-09-04 新增，市场页跌停入口联动）。

        实测：不带 date 参数返回 rc:102 data:null（与 ZT/ZB 池不同），date 必带；
        仅业务成功且 tc 与完整 pool 一致才返回；缺失/损坏/截断不得冒充合法空池。
        qdate 不作为请求日期证据（上游可能返回最近交易日，见 data-sources §3.1）。
        """
        payload = await self._get_json(
            "https://push2ex.eastmoney.com/getTopicDTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "500",
                "sort": "fund:asc",
                "date": trade_date.strftime("%Y%m%d"),
            },
        )
        if not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0:
            raise ProviderError("eastmoney limit-down business response failed")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("pool"), list):
            raise ProviderError("eastmoney limit-down pool missing or malformed")
        pool = data["pool"]
        if type(data.get("tc")) is not int or data["tc"] != len(pool):
            raise ProviderError("eastmoney limit-down pool incomplete")
        symbols = [str(r.get("c") or "") if isinstance(r, dict) else "" for r in pool]
        if (any(len(s) != 6 or not s.isascii() or not s.isdigit() for s in symbols)
                or len(set(symbols)) != len(symbols)):
            raise ProviderError("eastmoney limit-down pool has invalid or duplicate symbols")
        records = [nz.normalize_limit_down(r, trade_date) for r in pool]
        if any(r is None for r in records):
            raise ProviderError("eastmoney limit-down pool normalization failed")
        return records

    @staticmethod
    def _fmt_hhmmss(v) -> str | None:
        """push2ex 的时间为整数 92501 ↔ 09:25:01。"""
        n = _int(v)
        if n is None or n < 0 or n > 235959:
            return None
        return f"{n:06d}"[:2] + ":" + f"{n:06d}"[2:4] + ":" + f"{n:06d}"[4:6]

    async def get_limit_break_pool(self, trade_date: date) -> list[LimitUpRecord]:
        """炸板池备源（数据源方案 B5）：push2ex getTopicZBPool，消除炸板率单点。

        ⚠️ 字段缩放与涨停池（getTopicZTPool）**不同**，不能复用 normalize_limit_up：
        - p / ztp 为 **×1000**：600103 p=3850 ↔ 实际收盘 3.85（已与 TDX 日K交叉验证，
          而 ZT 池是 ×100）
        - zdp 已是百分数（0.785 ≈ +0.79%）
        - zbc=炸板次数、hs=换手率%、amount=成交额(元)、ltsz/tshare=市值(元)、fbt=首次封板时间

        池子很小（8/28 全市场 16 只），pagesize=500 一页足够；
        若未来 tc > 500 需翻页，此处以日志暴露而不是静默截断。
        """
        payload = await self._get_json(
            "https://push2ex.eastmoney.com/getTopicZBPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "500",
                "sort": "fbt:asc",
                "date": trade_date.strftime("%Y%m%d"),
            },
        )
        data = payload.get("data") or {}
        pool = data.get("pool") or []
        total = _int(data.get("tc")) or 0
        if total > len(pool):
            log.warning("limit-break pool truncated: tc=%s got=%d", total, len(pool))

        out: list[LimitUpRecord] = []
        for r in pool:
            symbol = str(r.get("c") or "")
            if not symbol:
                continue
            raw_price = _num(r.get("p"))
            out.append(
                LimitUpRecord(
                    symbol=symbol,
                    name=r.get("n"),
                    trade_date=trade_date,
                    price=raw_price / 1000 if raw_price is not None else None,
                    change_pct=_num(r.get("zdp")),
                    first_seal_time=self._fmt_hhmmss(r.get("fbt")),
                    break_count=_int(r.get("zbc")),
                    turnover_rate=_num(r.get("hs")),
                    amount=_num(r.get("amount")),
                    float_market_cap=_num(r.get("ltsz")),
                    total_market_cap=_num(r.get("tshare")),
                    industry_board=r.get("hybk") or None,
                    source="eastmoney",
                )
            )
        return out

    # ---- 板块（概念/行业）行情与资金 ----
    # get_board_metrics 已删除（2026-09-07 健康度审查 P0-1）：板块级数据统一
    # 走 app/market/board_flow.get_board_list 唯一入口（summary/architecture-design.md §2
    # 硬规则），f109/f110/f160 等字段序推断涨跌幅随之弃用（P1-6 已实证不可信）。
    # 注意：push2 主域在本机被 WAF 拦截（空回复），只有 push2delay 延迟域可用。

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]:
        payload = await self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "ALL",
                "filter": f"(TRADE_DATE='{trade_date.isoformat()}')",
                "pagesize": "500",
                "pageno": "1",
                "sort": "BILLBOARD_NET_AMT",
                "order": "desc",
                "source": "WEB",
                "client": "WEB",
            },
        )
        result = payload.get("result") or {}
        rows = result.get("data") or []
        records = [nz.normalize_longhu(r) for r in rows]
        return [r for r in records if r is not None]

    async def search(self, query: str) -> list[SymbolSearchItem]:
        payload = await self._get_json(
            "https://searchapi.eastmoney.com/api/suggest/get",
            {"input": query, "type": "14", "token": "D43BF722C8E33BDC906FB84D85E326E8", "count": "10"},
        )
        table = (payload.get("QuotationCodeTable") or {})
        items = []
        for raw in table.get("Data") or []:
            parsed = nz.normalize_search(raw)
            if parsed is None:
                continue
            code, name, market = parsed
            items.append(SymbolSearchItem(symbol=code, name=name, market=market, source=self.name))
        return items

    async def get_longhu_detail(self, symbol: str, trade_date) -> dict:
        """个股龙虎榜席位明细（买5/卖5 + 席位类型识别）。非交易日/未上榜返回空 dict。"""
        async def _side(report: str) -> list:
            payload = await self._get_json(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                {
                    "reportName": report,
                    "columns": "ALL",
                    "filter": f"(TRADE_DATE='{trade_date.isoformat()}')(SECURITY_CODE=\"{symbol}\")",
                    "pagesize": "10",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            rows = (payload.get("result") or {}).get("data") or []
            out: list = []
            seen: set = set()
            for r in rows:
                seat = nz.normalize_longhu_seat(r, "buy" if "BUY" in report else "sell")
                if not seat:
                    continue
                # 东财按上榜原因等多维度可能返回同席位重复行
                sig = (seat["seat"], seat["buy"], seat["sell"], seat["net"])
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(seat)
            return out

        buy, sell = await asyncio.gather(
            _side("RPT_BILLBOARD_DAILYDETAILSBUY"), _side("RPT_BILLBOARD_DAILYDETAILSSELL")
        )
        if not buy and not sell:
            raise ProviderError(f"{symbol} {trade_date} 无龙虎榜席位数据")
        return {"symbol": symbol, "trade_date": trade_date.isoformat(), "buy_seats": buy, "sell_seats": sell}

    async def get_longhu_history(self, symbol: str, limit: int = 30) -> list[dict]:
        payload = await self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "ALL",
                "filter": f"(SECURITY_CODE=\"{symbol}\")",
                "pagesize": str(limit),
                "sort": "TRADE_DATE",
                "order": "desc",
                "source": "WEB",
                "client": "WEB",
            },
        )
        rows = (payload.get("result") or {}).get("data") or []
        out = [nz.normalize_longhu_history(r) for r in rows]
        return [r for r in out if r is not None]

    async def get_financials(self, symbol: str, periods: int = 8) -> list[dict]:
        payload = await self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "ALL",
                "filter": f"(SECURITY_CODE=\"{symbol}\")",
                "pagesize": str(periods),
                "sort": "REPORTDATE",
                "order": "desc",
                "source": "WEB",
                "client": "WEB",
            },
        )
        rows = (payload.get("result") or {}).get("data") or []
        out = [nz.normalize_financial(r) for r in rows]
        out = [r for r in out if r is not None]
        dedup: dict = {}
        for r in out:  # 同一报告期可能有预告/正式两行，保留 API 顺序中的首行
            dedup.setdefault(r["report_date"], r)
        return sorted(dedup.values(), key=lambda r: r["report_date"], reverse=True)

    async def get_company_profile(self, symbol: str) -> dict:
        secucode = f"{symbol}.SH" if symbol.startswith(("6", "9", "5")) else f"{symbol}.SZ"
        payload = await self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_F10_BASIC_ORGINFO",
                "columns": "ALL",
                "filter": f"(SECUCODE=\"{secucode}\")",
                "source": "HSF10",
                "client": "PC",
            },
        )
        rows = (payload.get("result") or {}).get("data") or []
        profile = nz.normalize_company_profile(rows[0]) if rows else None
        if profile is None:
            raise ProviderError(f"{symbol} 无公司资料")
        # 所属板块/概念（emweb CoreConception：ssbk=行业/地域/风格/概念混合标签，hxtc=核心题材文字）
        try:
            cc = await self._client.get(
                "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax",
                params={"code": secucode},
                headers={"Referer": "https://emweb.securities.eastmoney.com/"},
            )
            if cc.status_code == 200:
                cc_data = cc.json()
                ssbk_rows = [b for b in cc_data.get("ssbk") or [] if b.get("BOARD_NAME")]
                # boards 保留全量混合标签（不丢数据），board_groups 提供 行业/地域/概念/风格指数 分类
                profile["boards"] = [b.get("BOARD_NAME") for b in ssbk_rows]
                profile["board_groups"] = nz.classify_boards(ssbk_rows)
                # 板块名 → 东财板块代码：调用方（P1-4 所属板块资金）按 code 直取，免名字匹配
                # 歧义（行业三级名带罗马数字后缀，如「白酒Ⅱ」vs 板块榜的「白酒」）。
                # ⚠️ F10 ssbk 的 BOARD_CODE 是**纯数字 ID**（茅台白酒Ⅱ=1277、平安银行Ⅱ=475），
                # 板块榜是 `BK`+**4 位补零**（BK1277 / BK0475）——2026-09-10 逐项实测确认。
                profile["board_codes"] = {
                    str(b.get("BOARD_NAME")): nz.board_code_norm(b.get("BOARD_CODE"))
                    for b in ssbk_rows
                    if b.get("BOARD_NAME") and b.get("BOARD_CODE")
                }
                profile["core_themes"] = [t for t in (x.get("KEY_THEME") or x.get("BOARD_NAME") for x in cc_data.get("hxtc") or []) if t]
        except Exception as exc:
            log.warning("conception fetch failed for %s: %s", symbol, exc)
        return profile

    async def get_announcements(self, symbol: str, limit: int = 10) -> list[dict]:
        payload = await self._get_json(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            {
                "sr": "-1", "page_size": str(limit), "page_index": "1",
                "ann_type": "A", "client_source": "web",
                "stock_list": symbol, "f_node": "0", "s_node": "0",
            },
        )
        items = (payload.get("data") or {}).get("list") or []
        out = [nz.normalize_announcement(r, symbol) for r in items]
        return [r for r in out if r is not None]

    async def get_news(self, symbol: str, limit: int = 10) -> list[dict]:
        import json as _json

        param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                            "pageIndex": 1, "pageSize": limit,
                                            "preTag": "", "postTag": ""}},
        }
        resp = await self._client.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "cb", "param": _json.dumps(param, ensure_ascii=False)},
        )
        if resp.status_code != 200:
            raise ProviderError(f"news HTTP {resp.status_code}")
        text = resp.text.strip()
        if not text:
            # 该接口反爬时返回 HTTP 200 空 body（实测无 UA 恒空、高频访问间歇空），
            # 必须显式失败而不是让 json.loads 抛裸异常——错误口径与 _get_json 一致
            raise ProviderError("empty reply from search-api (可能被限流)")
        if text.startswith("cb("):
            text = text[3:-1]
        try:
            payload = _json.loads(text)
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from search-api: {exc}") from exc
        arts = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
        out = [nz.normalize_news(r, symbol) for r in arts]
        return [r for r in out if r is not None]
