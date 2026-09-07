"""akshare 扩展数据服务（2026-09-07 star 仓库评测整合，来源 akfamily/akshare v1.18.94）。

定位：**扩展面**，不进行情热链路——行情 K 线/快照仍走 ths→tencent→eastmoney→sina。
akshare 在本机网络的实测可用域（2026-09-07 电池测试）：

- 稳定可用：push2ex 三池（涨停/炸板/跌停，~150-250ms）、datacenter-web（龙虎榜明细/
  宏观 CPI/两融账户统计）、sina（ETF/可转债日 K）；
- 本机被墙：push2/push2his 系（个股快照/日 K/板块），与本机既有结论一致（见
  docs/data-source-comparison.md），这些面一律不接。

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


_service = AkshareExtService()


def get_akshare_ext() -> AkshareExtService:
    return _service
