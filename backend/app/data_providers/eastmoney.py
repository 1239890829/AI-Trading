from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import httpx

from app.market import normalizer as nz
from app.schemas.market import (
    Kline,
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
        by_code = {r.get("f12"): r for r in rows}
        quotes: list[Quote] = []
        for sid, fallback_name in INDEX_SECIDS:
            raw = by_code.get(sid.split(".", 1)[1]) or {"f12": sid.split(".", 1)[1], "f14": fallback_name}
            quotes.append(nz.normalize_index(raw))
        return quotes

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

    # ---- 板块（概念/行业）行情与资金 ----
    # 注意：push2 主域在本机被 WAF 拦截（空回复），只有 push2delay 延迟域可用。
    # 详见 docs/data-sources.md §3.2。
    BOARD_HOST = "https://push2delay.eastmoney.com"
    BOARD_FIELDS = (
        "f2,f3,f6,f8,f12,f13,f14,f20,f62,f104,f105,f128,f140,f141,f184,"
        "f160,f109,f110,f24,f25"
    )

    async def get_board_metrics(self, kind: str = "concept") -> list[dict]:
        """板块行情+资金指标。kind: concept(概念) | industry(行业)。

        返回 dict 列表，字段见 docs/data-sources.md §3.2。
        注意 f160/f109/f110 的 3/5/10 日口径为字段序推断，**未经 K 线交叉验证**。

        坑：**单页上限 100 条**，概念板块 total=504 但 pz=600 也只回 100 条，
        必须按 total 翻页，否则会静默丢掉 80% 的板块。
        """
        fs = "m:90+t:3+f:!50" if kind == "concept" else "m:90+t:2+f:!50"
        url = f"{self.BOARD_HOST}/api/qt/clist/get"
        base_params = {
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": self.BOARD_FIELDS,
        }

        async def _page(pn: int, pz: int) -> dict:
            return await self._get_json(url, {**base_params, "pn": str(pn), "pz": str(pz)})

        first = await _page(1, 100)
        data = first.get("data") or {}
        total = int(data.get("total") or 0)
        diff = list(data.get("diff") or [])
        pages = max(1, (total + 99) // 100)
        if pages > 1:
            rest = await asyncio.gather(
                *[_page(pn, 100) for pn in range(2, pages + 1)], return_exceptions=True
            )
            for r in rest:
                if isinstance(r, dict):
                    diff += list((r.get("data") or {}).get("diff") or [])

        out: list[dict] = []
        for it in diff:
            name = it.get("f14")
            if not name:
                continue
            out.append(
                {
                    "board_code": it.get("f12"),
                    "name": str(name),
                    "kind": kind,
                    "price": _num(it.get("f2")),
                    "change_pct": _num(it.get("f3")),
                    "amount": _num(it.get("f6")),
                    "turnover_rate": _num(it.get("f8")),
                    "total_market_cap": _num(it.get("f20")),
                    "main_net_inflow": _num(it.get("f62")),
                    "main_net_ratio": _num(it.get("f184")),
                    "up_count": _int(it.get("f104")),
                    "down_count": _int(it.get("f105")),
                    "leader_name": it.get("f128"),
                    "leader_symbol": it.get("f140"),
                    # 多周期涨跌幅：字段序推断，待验证
                    "chg_3d": _num(it.get("f160")),
                    "chg_5d": _num(it.get("f109")),
                    "chg_10d": _num(it.get("f110")),
                    "chg_60d": _num(it.get("f24")),
                    "chg_ytd": _num(it.get("f25")),
                }
            )
        if not out:
            raise ProviderError(f"empty board metrics for kind={kind}")
        return out

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
        if text.startswith("cb("):
            text = text[3:-1]
        payload = _json.loads(text)
        arts = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
        out = [nz.normalize_news(r, symbol) for r in arts]
        return [r for r in out if r is not None]
