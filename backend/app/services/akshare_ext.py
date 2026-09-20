"""akshare 扩展数据服务（2026-09-07 star 仓库评测整合，来源 akfamily/akshare v1.18.94）。

定位：**扩展面**，不进行情热链路——行情 K 线/快照仍走 ths→tencent→eastmoney→sina。
akshare 在本机网络的实测可用域（2026-09-07 电池测试）：

- 稳定可用：push2ex 三池（涨停/炸板/跌停，~150-250ms）、datacenter-web（龙虎榜明细/
  宏观 CPI/两融账户统计/中美国债收益率）、sina（ETF/可转债日 K、**美股指数日线**、**外汇日线**）；
- 本机被墙：push2/push2his 系（个股快照/日 K/板块）以及 **push2 域的 forex_hist_em**，
  与本机既有结论一致（见 docs/data/data-source-comparison.md），这些面一律不接。
  ⇒ 离岸人民币改走**新浪外汇日线**（`usdcnh_daily`，本模块内唯一的非 akshare 源，
    与 `_calendar_rows` 同属「扩展面直连 HTTP」，共用缓存/三态/代理卫生基建）。

工程约束：
- **懒加载**：akshare import 需 ~1-2s 且拖入 pandas，首次调用才导入；未安装时
  ``available=False``，消费方拿到显式 kind，绝不静默退化成"空数据"。
- **代理卫生**：akshare 内部用 requests（trust_env=True），macOS 会读到系统级死代理；
  模块导入时 ``NO_PROXY`` setdefault 为 ``*``（不覆盖用户显式配置），强制直连。
- **缓存**：一律走统一 TTLCache（cache_on 挂服务实例），工厂异常不缓存。
- **三态**：解析失败/数据源异常抛 ``AkshareExtError``，带 kind 与 hint。
"""
from __future__ import annotations

import asyncio
import os
from datetime import date as date_cls
from datetime import timedelta

from app.core.bjtime import beijing_today
from app.core.ttl_cache import cache_on

# requests(trust_env) 在 macOS 会读系统级代理配置；本机会话曾出现死系统代理。
# setdefault：用户显式配置了 NO_PROXY 时以用户为准。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")


class AkshareExtError(Exception):
    """akshare 扩展数据失败。kind: not_installed / source_error / empty_bad_input"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(f"[{kind}] {detail}")


def _norm_code(v) -> str | None:
    """东财返回的 代码 列常被 pandas 解析成 int（如 600519 → 600519；002 前导零丢失）。"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip().split(".")[0]
    return s.zfill(6) if s.isdigit() else (s or None)


def _clean(v):
    """NaN → None；numpy 标量 → Python 原生（JSON 安全）。"""
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return None
    return v


def _int_or_none(v) -> int | None:
    """'2' / 2 / 2.0 → 2；其余（None/NaN/非数字）→ None（三态：判不出不臆造 0）。"""
    v = _clean(v)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (OverflowError, ValueError):
            return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return None


def _df_records(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        rec = {}
        for col in df.columns:
            rec[str(col)] = _clean(row[col])
        out.append(rec)
    return out


def _ymd(d: date_cls) -> str:
    return d.strftime("%Y%m%d")


class AkshareExtService:
    """akshare 懒加载封装：可用性探测 + 三池 + 宏观/两融。全部方法线程池执行。"""

    def __init__(self) -> None:
        self._mod = None
        self._import_tried = False

    # ---- 懒加载 ----
    def _akshare(self):
        if self._mod is not None:
            return self._mod
        if self._import_tried:  # 已试过且失败：不反复 import（import 本身 ~1-2s）
            raise AkshareExtError("not_installed", "akshare 未安装或导入失败（见 /api/ext/akshare/status）")
        self._import_tried = True
        try:
            import akshare as ak  # noqa: PLC0415 —— 刻意懒加载

            self._mod = ak
            return ak
        except Exception as exc:  # ImportError 及导入期副作用异常
            raise AkshareExtError("not_installed", f"akshare 导入失败：{exc}") from exc

    def status(self) -> dict:
        try:
            ak = self._akshare()
            return {"available": True, "version": getattr(ak, "__version__", None)}
        except AkshareExtError as exc:
            return {"available": False, "version": None, "kind": exc.kind, "detail": exc.detail}

    # ---- 内部：统一线程池 + 缓存 ----
    async def _cached(self, cache_name: str, ttl: float, key, factory):
        cache = cache_on(self, cache_name, ttl)
        _hit, value = await cache.get_or_set(key, lambda: asyncio.to_thread(factory))
        return value

    async def _pool(self, kind: str, trade_date: date_cls) -> list[dict]:
        """涨停/炸板/跌停三池（push2ex 域，实测稳定）。kind: zt|zb|dt"""
        fn_map = {
            "zt": ("stock_zt_pool_em", "pool-crosscheck-zt"),
            "zb": ("stock_zt_pool_zbgc_em", "pool-crosscheck-zb"),
            "dt": ("stock_zt_pool_dtgc_em", "pool-crosscheck-dt"),
        }
        ak_name, cache_name = fn_map[kind]
        ak = self._akshare()
        fn = getattr(ak, ak_name, None)
        if fn is None:
            raise AkshareExtError("source_error", f"akshare 无 {ak_name} 接口（版本变动？）")

        def _fetch() -> list[dict]:
            try:
                return _df_records(fn(date=_ymd(trade_date)))
            except AkshareExtError:
                raise
            except Exception as exc:
                raise AkshareExtError("source_error", f"{ak_name}({_ymd(trade_date)}) 失败：{exc}") from exc

        # TTL 30min：池子盘后定稿，盘中给分钟级刷新足够
        return await self._cached(cache_name, 1800.0, _ymd(trade_date), _fetch)

    # ---- 对外能力 ----
    async def limit_up_pool(self, trade_date: date_cls) -> list[dict]:
        """涨停池（东财口径）。返回字段：symbol/name/price/change_pct/consecutive_boards/..."""
        records = await self._pool("zt", trade_date)
        out = []
        for r in records:
            out.append(
                {
                    "symbol": _norm_code(r.get("代码")),
                    "name": r.get("名称"),
                    "price": _clean(r.get("最新价")),
                    "change_pct": _clean(r.get("涨跌幅")),
                    "consecutive_boards": _clean(r.get("连板数")),
                    "boards_stat": r.get("涨停统计"),
                    "amount": _clean(r.get("成交额")),
                    "float_market_cap": _clean(r.get("流通市值")),
                    "total_market_cap": _clean(r.get("总市值")),
                }
            )
        return [x for x in out if x["symbol"]]

    async def limit_down_pool(self, trade_date: date_cls) -> list[dict]:
        """跌停池（东财 push2ex 口径，与自建 DTPool 同源可交叉校验）。"""
        records = await self._pool("dt", trade_date)
        out = []
        for r in records:
            out.append(
                {
                    "symbol": _norm_code(r.get("代码")),
                    "name": r.get("名称"),
                    "price": _clean(r.get("最新价")),
                    "change_pct": _clean(r.get("涨跌幅")),
                    "consecutive_days": _clean(r.get("连续跌停天数")),
                    "open_count": _clean(r.get("开板次数")),
                    "seal_amount": _clean(r.get("封单资金")),
                }
            )
        return [x for x in out if x["symbol"]]

    async def macro_cpi(self) -> list[dict]:
        """宏观 CPI 月度序列（datacenter 域，实测稳定）。本栈此前无宏观面。"""
        ak = self._akshare()
        fn = getattr(ak, "macro_china_cpi", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 macro_china_cpi 接口")

        def _fetch() -> list[dict]:
            try:
                return _df_records(fn())
            except Exception as exc:
                raise AkshareExtError("source_error", f"macro_china_cpi 失败：{exc}") from exc

        # TTL 12h：月度数据
        return await self._cached("ext-macro-cpi", 43200.0, "all", _fetch)

    async def margin_account(self) -> list[dict]:
        """融资融券账户统计（datacenter 域）。"""
        ak = self._akshare()
        fn = getattr(ak, "stock_margin_account_info", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 stock_margin_account_info 接口")

        def _fetch() -> list[dict]:
            try:
                return _df_records(fn())
            except Exception as exc:
                raise AkshareExtError("source_error", f"stock_margin_account_info 失败：{exc}") from exc

        # TTL 12h：日频披露
        return await self._cached("ext-margin-acct", 43200.0, "all", _fetch)


    async def zt_pool_previous(self, trade_date: date_cls) -> list[dict]:
        """昨日涨停今日表现（东财 push2ex 口径，实测稳定）。

        字段含 昨日封板时间/昨日连板数/涨速/振幅——复盘「昨涨停溢价」的交叉校验源
        （主口径 = 本系统从快照+涨停池推导；本接口为第二口径，两口径不一致 → 显式呈现，不静默择一）。
        """
        ak = self._akshare()
        fn = getattr(ak, "stock_zt_pool_previous_em", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 stock_zt_pool_previous_em 接口")

        def _fetch() -> list[dict]:
            try:
                return _df_records(fn(date=_ymd(trade_date)))
            except Exception as exc:
                raise AkshareExtError("source_error",
                                      f"stock_zt_pool_previous_em({_ymd(trade_date)}) 失败：{exc}") from exc

        # TTL 30min：盘中动态、盘后定稿
        records = await self._cached("ext-zt-prev", 1800.0, _ymd(trade_date), _fetch)
        out = []
        for r in records:
            out.append(
                {
                    "symbol": _norm_code(r.get("代码")),
                    "name": r.get("名称"),
                    "price": _clean(r.get("最新价")),
                    "change_pct": _clean(r.get("涨跌幅")),
                    "prev_seal_time": _clean(r.get("昨日封板时间")),
                    "prev_boards": _clean(r.get("昨日连板数")),
                    "turnover": _clean(r.get("成交额")),
                    "turnover_ratio_pct": _clean(r.get("换手率")),
                }
            )
        return [x for x in out if x["symbol"]]

    # ---- 财经日历（P1-8 残余，2026-09-10） ----
    _BAIDU_CAL_URL = "https://finance.pae.baidu.com/sapi/v1/financecalendar"
    _BAIDU_CAL_HEADERS = {
        "accept": "application/vnd.finance-web.v1+json",
        "origin": "https://finance.baidu.com",
        "referer": "https://finance.baidu.com/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
    }

    def _calendar_cookie(self, headers: dict) -> str | None:
        """复用 akshare 的 cookie 获取（两步：首页拿 BAIDUID → hm.js 拿 HMACCOUNT）。

        取不到返回 None 由调用方抛 source_error —— 不静默退化成空列表。
        """
        try:
            from akshare.news.news_baidu import _get_baidu_cookie  # noqa: PLC0415
        except Exception:
            return None
        try:
            return _get_baidu_cookie(headers.copy())
        except Exception:
            return None

    def _calendar_rows(self, trade_date: date_cls) -> list[dict]:
        """百度股市通财经日历全量行（自建分页）。

        ⚠️ **为什么不直接调 ``ak.news_economic_baidu``**（2026-09-10 实测定位）：
        akshare 1.18.94 的 ``_baidu_finance_calendar`` 首页请求带 ``impersonate="chrome110"``，
        但**翻页请求（news_baidu.py:162）漏传该参数**；百度对无浏览器 TLS 指纹的请求直接
        403 ⇒ **事件数 >100 的日期必然抛 HTTPError**，而 <=100 条的日期正常。
        症状极具误导性：09-08（96 条）、09-09（67 条）可用，09-10（109 条）稳定 403，
        看起来像「数据源随机不可用」，实为分页缺陷。高价值日（CPI/非农）恰恰是事件最多的日子。
        本方法每页都带 impersonate，实测 09-10 全量 109 条可取。
        """
        try:
            from curl_cffi import requests as ccr  # noqa: PLC0415 —— akshare 传递依赖
        except Exception as exc:
            raise AkshareExtError("not_installed", f"curl_cffi 不可用：{exc}") from exc

        headers = dict(self._BAIDU_CAL_HEADERS)
        cookie = self._calendar_cookie(headers)
        if not cookie:
            raise AkshareExtError("source_error", "百度财经日历 cookie 获取失败（反爬拦截）")
        headers["cookie"] = cookie

        ymd = _ymd(trade_date)
        formatted = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        rows: list[dict] = []
        total = None
        for page in range(5):  # 5 页 × 100 = 500 条，实测单日上限 109，足够
            params = {
                "start_date": formatted,
                "end_date": formatted,
                "pn": str(page),
                "rn": "100",
                "cate": "economic_data",
                "finClientType": "pc",
            }
            try:
                resp = ccr.get(
                    self._BAIDU_CAL_URL,
                    params=params,
                    headers=headers,
                    impersonate="chrome110",
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                raise AkshareExtError(
                    "source_error", f"financecalendar({formatted}) 第 {page} 页失败：{exc}"
                ) from exc
            page_rows: list[dict] = []
            info = (payload.get("Result") or {}).get("calendarInfo") or []
            for item in info:
                if item.get("date") == formatted:
                    total = item.get("total")
                    page_rows = item.get("list") or []
            rows += page_rows
            if len(page_rows) < 100 or (total is not None and len(rows) >= total):
                break
        return rows

    async def macro_calendar(self, trade_date: date_cls) -> list[dict]:
        """财经日历·当日经济数据。

        返回字段：date / time / region / event / actual / forecast / previous / importance。
        ``actual`` 源占位文案「未公布」原样保留，由消费方按缺失处理（本层不做语义改写）。
        """

        def _fetch() -> list[dict]:
            out = []
            for r in self._calendar_rows(trade_date):
                out.append(
                    {
                        "date": r.get("date"),
                        "time": (r.get("time") or "").strip() or None,
                        "region": (r.get("region") or "").strip() or None,
                        "event": (r.get("title") or "").strip() or None,
                        "actual": _clean(r.get("pubVal")),
                        "forecast": _clean(r.get("indicateVal")),
                        "previous": _clean(r.get("formerVal")),
                        "importance": _int_or_none(r.get("star")),
                    }
                )
            return [x for x in out if x["event"]]

        # TTL 30min：公布值盘中会刷新（如 16:00 社融），盘前一次取足够
        return await self._cached("ext-macro-calendar", 1800.0, _ymd(trade_date), _fetch)

    # ---- 隔夜海外一阶输入（P1-34，2026-09-11）----
    # 统一返回 [{"date": "YYYY-MM-DD", "close": 数值}]，**变化率由消费方自算**
    # （不采信数据源自带的涨跌幅字段——口径必须单点收口到 app/market/overnight_bias.py）。

    async def us_index_daily(self, symbol: str) -> list[dict]:
        """新浪美股指数日线。symbol: ``.IXIC``（纳斯达克）/ ``.SOX``（费城半导体）/ ``.DJI``。

        实测 2026-09-11：~0.2s 返回全量历史（5707 行），本方法只回最近 12 个交易日。
        """
        ak = self._akshare()
        fn = getattr(ak, "index_us_stock_sina", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 index_us_stock_sina 接口")
        key = symbol.strip().lstrip(".")

        def _fetch() -> list[dict]:
            try:
                df = fn(symbol=symbol)
            except Exception as exc:
                raise AkshareExtError("source_error", f"index_us_stock_sina({symbol}) 失败：{exc}") from exc
            out = []
            for r in _df_records(df)[-12:]:
                d = r.get("date")
                close = _clean(r.get("close"))
                if d is None or close is None:
                    continue
                out.append({"date": str(d)[:10], "close": close})
            return out

        # TTL 1h：日频数据，盘前取一次足够
        return await self._cached(f"ext-us-idx-{key}", 3600.0, "tail", _fetch)

    async def us_treasury_10y_daily(self) -> list[dict]:
        """中美国债收益率（datacenter 域）中的**美国 10Y**，最近 45 天窗口。

        ⚠️ 该源的美债行常有滞后（当日为 NaN、隔日补值），故此处 **dropna 后取最近点**；
        相邻有效点间隔由消费方（``overnight_bias``）按 ``MAX_GAP_DAYS`` 判「数据滞后」。
        """
        ak = self._akshare()
        fn = getattr(ak, "bond_zh_us_rate", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 bond_zh_us_rate 接口")

        # 日期归属走权威时钟：`date_cls.today()` 按进程时区取日，非 CST 机器差一天
        start = (beijing_today() - timedelta(days=45)).strftime("%Y%m%d")

        def _fetch() -> list[dict]:
            try:
                df = fn(start_date=start)
            except Exception as exc:
                raise AkshareExtError("source_error", f"bond_zh_us_rate({start}) 失败：{exc}") from exc
            out = []
            for r in _df_records(df):
                d = r.get("日期")
                v = _clean(r.get("美国国债收益率10年"))
                if d is None or v is None:
                    continue
                out.append({"date": str(d)[:10], "close": v})
            return out

        # TTL 1h：日频
        return await self._cached("ext-us10y", 3600.0, start, _fetch)

    async def usdcnh_daily(self) -> list[dict]:
        """离岸人民币 USDCNH 日线（**非 akshare 源**）。

        ⚠️ 为什么破例放在本模块：akshare 的 ``forex_hist_em`` 走 push2 域（本机被墙），
        而新浪外汇日线可用且与本模块既有的 ``_calendar_rows`` 同属「扩展面直连 HTTP」，
        故与缓存/三态/代理卫生基建共用一套。日线布局：``日期,今开,最低,最高,收盘``。
        """
        try:
            from curl_cffi import requests as ccr  # noqa: PLC0415 —— akshare 传递依赖
        except Exception as exc:
            raise AkshareExtError("not_installed", f"curl_cffi 不可用：{exc}") from exc

        url = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_fx_susdcnh=/"
               "NewForexService.getDayKLine?symbol=fx_susdcnh")
        headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}

        def _fetch() -> list[dict]:
            try:
                resp = ccr.get(url, headers=headers, impersonate="chrome110", timeout=20)
                resp.raise_for_status()
                text = resp.text
                body = text[text.index('("') + 2: text.rindex('")')]
            except Exception as exc:
                raise AkshareExtError("source_error", f"新浪外汇日线(USDCNH) 失败：{exc}") from exc
            out = []
            for chunk in body.split("|"):
                parts = chunk.split(",")
                if len(parts) < 5 or not parts[4].strip():
                    continue
                try:
                    out.append({"date": parts[0][:10], "close": float(parts[4])})
                except ValueError:
                    continue
            return out[-12:]

        # TTL 1h：日频（离岸 24h 交易，日线在次日凌晨定稿）
        return await self._cached("ext-usdcnh", 3600.0, "tail", _fetch)

    # ---- 大宗商品一阶价格（P1-33，2026-09-11）----------------------------------
    # 为什么用新浪期货：东财 push2 域本机被墙（eastmoney 系行情不可达），而
    # `futures_zh_daily_sina` 直连新浪、长历史（螺纹 4000+ 行 / 原油 2000+ 行）、
    # 且与 A 股同时区（15:00 收盘），对齐成本最低。返回**最近 30 根**已够用
    # （消费方只需最后两根算日变化 + 判滞后）。

    async def commodity_daily(self, code: str) -> list[dict]:
        """商品期货连续合约日线（新浪）。code 形如 ``SC0``（原油）/``CU0``（沪铜）。

        ⚠️ ``*0`` 是**主连**（主力连续）合成价，跨月换月处存在跳空；
        这是本项数据源的**已知口径代价**——换月缺口会被计成一次大变化。
        消费方（``market/commodity_chain.py``）用「自身日变化中位数」作死区，
        对偶发换月跳空的容忍度由此而来；若要彻底消除需改用单合约拼接，本期不做。
        """
        ak = self._akshare()
        fn = getattr(ak, "futures_zh_daily_sina", None)
        if fn is None:
            raise AkshareExtError("source_error", "akshare 无 futures_zh_daily_sina 接口")
        key = code.strip().upper()

        def _fetch() -> list[dict]:
            try:
                df = fn(symbol=key)
            except Exception as exc:
                raise AkshareExtError("source_error", f"futures_zh_daily_sina({key}) 失败：{exc}") from exc
            out = []
            for r in _df_records(df)[-30:]:
                d = r.get("date")
                close = _clean(r.get("close"))
                if d is None or close is None:
                    continue
                out.append({"date": str(d)[:10], "close": close})
            return out

        # TTL 30min：日频，但盘中拉当日 bar 是未完成 bar —— 消费方用 date < asof 守卫
        return await self._cached(f"ext-commodity-{key}", 1800.0, "tail", _fetch)


_service = AkshareExtService()


def get_akshare_ext() -> AkshareExtService:
    return _service
