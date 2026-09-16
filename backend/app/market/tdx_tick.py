"""TDX 逐笔成交适配层（easy_tdx `MacClient.get_transactions`，MAC 协议 TCP 7709）。

## 为什么有这一层

`get_trades` 曾是**单点**：链上 4 源里 ths / tencent / sina 都是 `return []` 占位，
只有东财 `push2his` 真实现（`provider_capabilities.py` 自述「唯一真源」）。2026-09-16
实测该源在本机 **3/3 抛 ProviderError**（`Server disconnected without sending a
response.`，0.12–0.22s 快速失败 = WAF 特征），线上 `GET /api/trades/600519` 恒 **502**
—— 不是"通而空"，是"完全不可用"。本模块提供**降级备源**实现，把单点补成双源
（为什么不是主源：见 `fetch_trades_with_tdx_fallback` 的 docstring）。

## 三个必须记住的实测结论（2026-09-16 取证，`IMP-038`）

① **`count > 1000` 的自动分页是错的**。`get_transactions` 文档写"自动分页"，但
   `_TRANSACTION_PAGE_SIZE = 1000`（`easy_tdx/mac/client.py`）把「最新 1000 笔」放前面、
   更早的直接 `extend` 在后 ⇒ **页序拼反**：`count=2000` 实测
   `head=13:47:48 / tail=13:47:45`（首尾同分钟）、`mono_inc=False`；`count=8000` 只回 3867 行。
   ⇒ 本模块**手动分页**（每页 ≤1000、后取的页 `insert(0, ...)`），且快路径根本不分页（见②）。

② **`start` 以「最新」为原点、段内升序** ⇒ `count=N, start=0` 直接给出**最新 N 笔且升序**。
   实测 `count=5` 返回 `15:06:33 … 15:24:55`（当日最后 5 笔）。这是本模块的**快路径**：
   取最新 50 笔 = **一次** IPC（median 20.1ms），无需翻全天（4 页 150–300ms）。

③ **`bs_flag` 语义与东财相反**：`0=买入 / 1=卖出 / 2=中性 / 5=盘后`（`easy_tdx/mac/models.py`），
   东财是 `1=买 / 2=卖 / 4=中性`。方向交叉实证：价格上行段 `bs_flag=0` 占 121/138。
   把东财的映射搬过来会让**买卖完全反色**。

## 连接：必须复用（28× 差异）

每次 `with MacClient()` 新建连接的 median 是 **616.9ms**（132–1099ms），复用是 **21.8ms**。
`tdx_kline.py` 那种"每次新建"的写法在逐笔场景（前端 10s 轮询）不可接受 ⇒ 本模块用
**模块级单例 + 可重入锁**。`MacClient(auto_reconnect=True, heartbeat_interval=15.0)`
自带重连与心跳，单例持有即可；协议级错误仍会丢弃单例重建（见 `fetch_tdx_trades`）。

## 时间戳口径：**真 UTC**（aware）

`time` 列是北京时间墙钟，减 `BJ_OFFSET` 后标 UTC。这与 `Quote.data_timestamp`
（ths 用 `datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc)`）和
`minute_backfill.tdx_row_to_points` 同源；前端 `timeText()` 按浏览器本地时区渲染，
UTC+8 下正好还原北京时间。

⚠️ **不要**照抄 `normalizer.normalize_trade`（东财）的 `wall.replace(tzinfo=utc)`
——那是"伪 UTC"：同一时刻在 UTC+8 浏览器里会显示成 **17:30**（8 小时偏差）。
该偏差此前不可见，只因逐笔路径一直是 502（从未有人看到过一行数据）。

## 已知边界（诚实标注，勿当已解决）

- **`time` 列不含日期**：非交易日/盘前请求，TDX 回的是**最近交易日**的尾段，本模块按
  `beijing_today()` 贴日期 ⇒ 盘前（<09:25）取到的可能是上一交易日的逐笔，**日期标签差一天**
  （时分秒仍正确，当前 UI 只展示 `HH:MM:SS`，故无可见影响）。需精确日期时显式传 `date=`。
- **北交所覆盖不全**：920819 有数据；430047 / 830799 实测返 **0 行**（非报错）。
- **判错市场不报错、只返空**（实测 600519 传 SZ 返 0 行）⇒ `tdx_market()` 必须判准，
  **不能靠"有没有抛异常"发现映射错误**。
- **盘中实时性未验证**：取证时已盘后（20:20+），盘中刷新延迟需下一交易日复测。
- **直连旁路**：不走 composite 的熔断 / 请求预算 / health 视图（与
  `scripts/sync_marketdb.py` 直连 `prices/snapshot` 同族的治理缺口，见 `IMP-037`）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from datetime import date, datetime, time, timezone
from typing import Awaitable, Callable

from app.core.bjtime import BJ_OFFSET, beijing_today
from app.core.config import settings
from app.schemas.market import Trade

log = logging.getLogger(__name__)

#: `Trade.source` 取值。前端 `lib/format.ts` 的 `SOURCE_LABELS` 必须有同名键。
TDX_SOURCE = "tdx"

#: 单页上限 = easy_tdx `_TRANSACTION_PAGE_SIZE`。**超过它就会触发库内错误分页**（见 docstring ①）。
PAGE_SIZE = 1000

#: 翻页上界（防御性）：20 × 1000 = 2 万笔/日；实测 600519 全日 3867 笔。
MAX_PAGES = 20

#: 建连默认 socket 超时。**单例复用后以首次建立的值为准**（`MacClient` 把 timeout
#: 固化在 `TdxConnection` 上，per-call 传参只在新建连接时生效）。
DEFAULT_TIMEOUT = 8.0

#: 北交所代码段 —— 与 `app/market/price_rules.limit_pct` 同源口径，勿各自维护。
_BJ_PREFIXES = ("43", "83", "87", "88", "92")

#: `bs_flag` → `Trade.side`。**与东财（1=买/2=卖/4=中性）相反**，勿照搬（见 docstring ③）。
_BS_FLAG_SIDE = {0: "buy", 1: "sell", 2: "neutral", 5: "neutral"}

#: easy-tdx `Market` 枚举值（SZ=0 / SH=1 / BJ=2）。后缀显式给出时**后缀优先**。
_MARKET_BY_SUFFIX = {"SZ": 0, "SH": 1, "BJ": 2}

_SYMBOL_RE = re.compile(r"^(?P<code>\d{6})(?:\.(?P<mkt>SH|SZ|BJ))?$", re.IGNORECASE)

#: 连接生命周期 + IPC 串行化的**可重入**锁（`fetch_tdx_trades` 会在持锁状态下
#: 调 `close_tdx_client()`，普通 Lock 会自锁死）。
_lock = threading.RLock()
_client = None
_client_pid: int | None = None


def _split_symbol(symbol: str) -> tuple[str, str | None]:
    """`'600519'` / `'600519.SH'` → `('600519', 'SH'|'SZ'|'BJ'|None)`。

    带后缀时**后缀优先**：`000001.SH` 是上证指数、`000001.SZ` 是平安银行 ——
    仅凭首位数字判市场必错（`tdx_minute_line_fallback` 的 docstring 已记该坑）。
    """
    m = _SYMBOL_RE.match(str(symbol or "").strip())
    if m is None:
        raise ValueError(
            f"tdx 逐笔仅支持 6 位裸码或带 .SH/.SZ/.BJ 后缀的代码，收到 {symbol!r}"
        )
    suffix = m.group("mkt")
    return m.group("code"), (suffix.upper() if suffix else None)


def tdx_market(symbol: str) -> int:
    """symbol → easy-tdx `Market` 枚举值（SZ=0 / SH=1 / BJ=2）。

    ⚠️ 判错市场**不报错、只返回空**（实测 2026-09-16：600519 传 SZ 返 0 行）——
    无法用"有没有抛异常"发现映射错误，故此处判准是硬要求。

    `tdx_kline.py` 的 `Market.SH if symbol[0] in "69" else Market.SZ` **不含北交所**
    （920819 会被判成 SH ⇒ 恒空），本模块不复用该写法。
    """
    code, suffix = _split_symbol(symbol)
    if suffix is not None:
        return _MARKET_BY_SUFFIX[suffix]
    if code.startswith(_BJ_PREFIXES):
        return _MARKET_BY_SUFFIX["BJ"]
    return _MARKET_BY_SUFFIX["SH"] if code[0] in "69" else _MARKET_BY_SUFFIX["SZ"]


def _resolve_day(date_yyyymmdd: int | None) -> date:
    """交易日：显式 `date=` 优先，否则取**北京今天**（边界见模块 docstring）。"""
    if date_yyyymmdd is None:
        return beijing_today()
    return datetime.strptime(str(date_yyyymmdd), "%Y%m%d").date()


def _as_float(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _row_ts(raw, day: date) -> datetime | None:
    """`time` 列（`datetime.time` 或 `'HH:MM:SS'` 串）+ 交易日 → **真 UTC** aware。"""
    if isinstance(raw, datetime):
        t = raw.time()
    elif isinstance(raw, time):
        t = raw
    else:
        try:
            t = datetime.strptime(str(raw or "").strip(), "%H:%M:%S").time()
        except ValueError:
            return None
    return (datetime.combine(day, t.replace(tzinfo=None)) - BJ_OFFSET).replace(
        tzinfo=timezone.utc
    )


def row_to_trade(symbol: str, row: dict, day: date) -> Trade | None:
    """TDX 逐笔行 → `Trade`（纯函数，可单测）。

    量纲 = **手**（与东财 details 同口径）：实测 600519 全日 Σvol 26,243 手，
    对照 fuyao 日线 26,235.24 手 + 盘后 8 笔。
    行不可解析（时间/价格/量任一缺失）返回 None —— 由调用方丢弃，不伪造 0。
    """
    ts = _row_ts(row.get("time"), day)
    price = _as_float(row.get("price"))
    volume = _as_float(row.get("vol"))
    if ts is None or price is None or volume is None:
        return None
    flag = _as_float(row.get("bs_flag"))
    side = _BS_FLAG_SIDE.get(None if flag is None else int(flag), "neutral")
    return Trade(
        symbol=symbol, ts=ts, price=price, volume=volume, side=side, source=TDX_SOURCE
    )


def _safe_close(client) -> None:
    """尽力关闭，**不吞掉**原始错误（关闭失败只记 debug）。"""
    try:
        client.close()
    except Exception:  # noqa: BLE001
        log.debug("tdx client close failed", exc_info=True)


def _get_client(timeout: float | None = None):
    """模块级长连接单例（复用 21.8ms vs 每次新建 616.9ms）。

    **调用方必须持有 `_lock`**（本函数不做加锁，避免嵌套语义含糊）。
    带 pid 判据：uvicorn worker fork 或测试子进程不得沿用父进程的 socket。
    """
    global _client, _client_pid
    from easy_tdx import MacClient

    pid = os.getpid()
    if _client is not None and _client_pid == pid:
        return _client
    if _client is not None:  # 跨进程残留：先尽力关掉再建新的
        _safe_close(_client)
        _client = None
    try:
        client = MacClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
    except Exception:
        _client, _client_pid = None, None  # 建连失败不留半成品单例，下次重试
        raise
    _client, _client_pid = client, pid
    return client


def close_tdx_client() -> None:
    """关闭单例连接（应用 shutdown / 测试收尾）。幂等。"""
    global _client, _client_pid
    with _lock:
        if _client is not None:
            _safe_close(_client)
        _client, _client_pid = None, None


def _fetch_frames(client, market: int, code: str, *, want: int | None, date_yyyymmdd: int | None) -> list:
    """手动分页（见模块 docstring ①：库内自动分页在 >1000 时**页序拼反**）。

    `start` 以「最新」为原点、段内升序 ⇒ 后取的页更早 ⇒ `insert(0, ...)` 插到前面。
    `want=None` 表示取到当日首笔为止；**每次请求量恒 ≤ `PAGE_SIZE`**。
    """
    frames: list = []
    got = 0
    start = 0
    for _ in range(MAX_PAGES):
        size_want = PAGE_SIZE if want is None else min(PAGE_SIZE, want - got)
        if size_want <= 0:
            break
        frame = client.get_transactions(
            market, code, count=size_want, start=start, date=date_yyyymmdd
        )
        got_now = 0 if frame is None else len(frame)
        if got_now == 0:
            break  # 空页 = 已到当日首笔（或该标的无逐笔）
        frames.insert(0, frame)
        got += got_now
        if got_now < size_want:
            break  # 不足一页 ⇒ 已到当日首笔
        start += got_now
    return frames


def fetch_tdx_trades(
    symbol: str,
    *,
    limit: int | None = None,
    date: int | None = None,
    timeout: float | None = None,
) -> list[Trade]:
    """TDX 逐笔 → `list[Trade]`（**升序**，时间从早到晚）。**同步阻塞**。

    - `limit`：取**最新** N 笔（None = 当日全部，走手动翻页）。
      `limit ≤ PAGE_SIZE` 时是单次 IPC 快路径（实测 median 20.1ms）。
    - `date`：`YYYYMMDD` 历史日；None = 当日（边界见模块 docstring）。
    - `timeout`：仅在**新建**连接时生效（单例复用后以首次建立的值为准）。

    **失败抛异常**（调用方决定降级）；**空列表是合法结果**（北交所部分标的实测恒空），
    二者必须区分开。事件循环调用方一律走 `fetch_trades_with_fallback`（内含 `to_thread`）。
    """
    code, _ = _split_symbol(symbol)
    market = tdx_market(symbol)
    day = _resolve_day(date)
    with _lock:
        client = _get_client(timeout)
        try:
            frames = _fetch_frames(client, market, code, want=limit, date_yyyymmdd=date)
        except Exception:
            # 协议级错误 auto_reconnect 兜不住（它只管 socket 断开）：丢弃单例，
            # 下次调用重建连接，避免一次坏连接让后续请求全部陪葬。
            close_tdx_client()
            raise
    rows = [r for frame in frames for r in frame.to_dict("records")]
    if limit is not None:
        rows = rows[-limit:]
    out: list[Trade] = []
    for row in rows:
        trade = row_to_trade(symbol, row, day)
        if trade is not None:
            out.append(trade)
    return out


async def fetch_trades_with_tdx_fallback(
    primary: Callable[[str], Awaitable[list[Trade]]],
    symbol: str,
    *,
    limit: int = 50,
    timeout: float | None = None,
) -> tuple[list[Trade], str, str]:
    """`primary`（provider 链）→ **TDX 直连备源**。**不抛异常**。

    返回 `(trades, source, detail)`：

    - `(rows, "<链上源名>" | "tdx", "")` —— 取到数据，`source` 供前端标注口径；
    - `([], "none", "chain: …; tdx: …")` —— 两源都没给出数据。`detail` 是各自的
      失败/空原因，调用方据此决定「502 报错」还是「空列表」——**"都失败"与"都为空"
      必须可分辨**，否则会把数据源故障说成"这只票没有逐笔"。

    ## 为什么是「链主源 + TDX 备源」而不是反过来（2026-09-16 落地时的取舍）

    账本里 `IMP-038` 的原方案是「TDX 接主源」。实施时实测到一条硬约束**否决了它**：
    测试环境 `ASHARE_DATA_PROVIDER=mock`，若 TDX 排在链前，则
    `tests/test_api.py::test_order_book_and_trades` 这类用例会**绕过 mock 直连真实
    TDX 服务器**（实测确实建连并返回 10 行真数据）⇒ 单测变成"有网才过、结果随行情变"。

    另有两点同向：① 与仓内 TDX 的既有角色一致（`tdx_minute_line_fallback` 也是**备源**，
    `provider_capabilities` 记为「TDX 直连作为路由层降级备源」）；② TDX 是**直连旁路**，
    不进 composite 的熔断 / 请求预算 / health 视图（治理缺口与 `scripts/sync_marketdb.py`
    同族，见 `IMP-037`）——把链留在前面，主路径仍在治理之内。

    代价可接受且**会自收敛**：链上 3 个空占位（ths/tencent/sina）会在 3 次失败后进
    60s 熔断冷却，稳态下每次请求只多一跳东财失败（实测延迟 1.01s → 0.186 → **0.023s**）。

    ⚠️ **但"链在前"只保证了链**命中**时**不触网**：链返回空/异常时仍会走 TDX ⇒
    测试里必须再用 `settings.trades_tdx_fallback_enabled=false` 关掉备源
    （`tests/conftest.py` 已置，理由见该字段注释）。两者缺一，单测都可能真的连上 TDX。
    """
    detail: list[str] = []
    try:
        rows = list(await primary(symbol) or [])
        if rows:
            return rows, _row_source(rows[0]), ""
        detail.append("chain: empty")
    except Exception as exc:  # noqa: BLE001  降级链必须吞掉主源异常
        detail.append(f"chain: {exc}")
        log.warning("chain trades failed for %s: %s", symbol, exc)
    if not settings.trades_tdx_fallback_enabled:
        detail.append("tdx: disabled")
        return [], "none", "; ".join(detail)
    try:
        rows = await asyncio.to_thread(fetch_tdx_trades, symbol, limit=limit, timeout=timeout)
        if rows:
            return rows, TDX_SOURCE, ""
        detail.append("tdx: empty")
    except Exception as exc:  # noqa: BLE001
        detail.append(f"tdx: {exc}")
        log.warning("tdx trades failed for %s: %s", symbol, exc)
    return [], "none", "; ".join(detail)


# 降级链 `detail` 中表示**非故障**的终态片段（生产者见上）。其余片段一律视为**真实故障**。
#
# 为什么要有这张表：调用方最常见的写法是 `if detail:` 判"取数失败"，而
# `"chain: empty; tdx: disabled"` 也是**非空字符串** ⇒ 会把"两源都没数据 / 备源未启用"
# 说成"数据源故障"（红线 3 同族：口径不许想当然）。判据与生产者放在一起、由两个消费方
# （`api/routes/market_quotes.trades` 与 `assistant/tools/market._t_trades`）共用一份，
# 避免各自复写后走样。
_NON_FAILURE_SEGMENTS = frozenset({"chain: empty", "tdx: empty", "tdx: disabled"})


def trades_failure_detail(detail: str) -> str:
    """从降级链 `detail` 中挑出**真实故障**片段；无故障时返回**空串**。

    `"chain: empty; tdx: disabled"` ⇒ `""`（两源都没给出数据，非故障）
    `"chain: WAF blocked; tdx: empty"` ⇒ `"chain: WAF blocked"`（链真失败）

    ⚠️ 判据是**白名单**（`_NON_FAILURE_SEGMENTS`）而不是"含 Error/timeout 字样"：
    异常文本不可控（`ProviderError` 的消息是 `Server disconnected without sending
    a response.`，不含任何关键字），按关键字判会把真故障漏成"没数据"。
    """
    segments = [s.strip() for s in str(detail or "").split(";")]
    return "; ".join(s for s in segments if s and s not in _NON_FAILURE_SEGMENTS)


def _row_source(row) -> str:
    """行的来源标识。**dict 与模型两种形态都要容**——测试桩常直接给 dict
    （`tests/test_depth_tools.py` 的 `_Prov`），只写 `row.source` 会在那里抛
    `AttributeError`，而异常会被降级链吞掉 ⇒ **表现成"链失败"而非"代码有 bug"**
    （2026-09-16 实测踩到：两条用例因此静默走到 TDX 并拿到真数据）。"""
    if isinstance(row, dict):
        return str(row.get("source") or "chain")
    return str(getattr(row, "source", None) or "chain")
