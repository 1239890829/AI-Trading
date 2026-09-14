"""A 股交易日历。

## 为什么需要它

东财涨停池接口（push2ex / getTopicZTPool）有个致命行为：**传入非交易日时静默
返回最近一个交易日的数据，不报错、响应里没有日期字段**。于是 `date.today()` 与
`today - 1 day` 会取到同一份数据，"昨日涨停股今日表现"退化成"涨停股查自己涨停
那天的收盘价"，恒等于 +10%。2026-08-29（周六）线上把市场判成「高潮 / 置信度高」
就是这么来的。

`date.weekday()` 只能跳周末，遇法定节假日（春节、国庆、清明…）必然复现同一事故，
所以必须有一份真实交易日历。

## 日历来源（2026-08-29 修正：主源改为 ths 官方端点）

**主源：ths 官方 `GET /api/a-share/calendar/trading-days`**（近一年交易日）。
**备源：上证综指（sh000001）日 K 的日期集合**（腾讯 `fqkline` 推导）。

为什么必须换成官方端点（这是本次改动的原因）：

| 源 | 天数 | 范围 |
|---|---|---|
| ths 官方 | **242** | 2025-08-29 → 2026-08-28 |
| 腾讯日 K 推导 | 124 | 2026-03-03 → 2026-08-28 |

实测两者交集 124 天，**仅 ths 有 118 天、仅腾讯有 0 天**——官方端点完全覆盖推导口径，
且是交易所权威日历，不依赖 K 线接口可用性（东财 K 线在本机已实测不可用，
推导路径未来也可能踩到同样问题）。

⚠️ 首个版本只实现了腾讯推导，**绕开了早就存在且 main.py:75 / market.py:257 已在使用的
官方端点**，等于同一件事有两套实现且新的那套更差。现在改为官方优先、推导兜底。

推导路径的保留理由：指数永不停牌、交易日必然有 bar，作为无 Key 环境下的降级手段仍有价值。

代价是需要一次网络请求，因此进程内缓存（默认 12 小时），且失败时**显式降级**——
宁可让调用方看到"日历不可用"，也不要默默退回"只跳周末"的错误逻辑。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from app.core.bjtime import beijing_now, beijing_today  # S2-8 时区收敛

log = logging.getLogger(__name__)

INDEX_SYMBOL = "sh000001"  # 上证综指

#: 缓存**上限**时长（秒）。⚠️ 自 2026-09-14 起它不再是唯一的失效条件 —— 见
#: `_is_fresh()`：交易日历的真实时效语义是「**必须覆盖今天**」，而非「活了多少秒」。
_CACHE_TTL_SEC = 12 * 3600

#: 「覆盖重取」的最小间隔（秒）：缓存不满足覆盖判据、但距上次**尝试**不足此值时，
#: 仍先返回旧快照（不视为新鲜、但也不重取）。
#: 理由：节假日与源故障会让「不覆盖今天」长期为真，若无此下限，每个请求都会打一次
#: 上游 —— 从「一次陈旧」变成「持续重取风暴」，比原缺陷更差。
_REFRESH_MIN_INTERVAL_SEC = 5 * 60

#: 「今日快照」下界（09:30 = 连续竞价开始）。抓取时刻晚于当日该时刻 ⇒ 若今日是
#: 交易日，源头**必然**已把今日给出；此时列表仍不含今日，只可能是「今日确实休市」。
#: ⚠️ 不能用「抓取时刻在今天」做判据：凌晨抓的快照会带着不含今日的列表存活一整天，
#: 假休市窗口从分钟级扩大到**整个交易日**（2026-09-14 论证，见 implementation §5.4）。
_SESSION_OPEN = dt_time(9, 30)

_MIN_DAYS = 5  # 少于这个天数视为拉取失败，宁可报错也不用

#: 市场状态三态（KB 核心纪律「三态 > 二态」）。`unknown` 是**一等公民**：
#: 日历未覆盖今天时，既不能断言开市、也不能断言休市 —— 它是「未判定」。
MARKET_OPEN = "open"
MARKET_CLOSED = "closed"
MARKET_UNKNOWN = "unknown"

_lock = asyncio.Lock()
_cached_days: list[date] = []
_cached_at = 0.0
#: 快照落库时的**墙钟**（北京时间）。与 `_cached_at`（单调钟，只用于算年龄）分工：
#: 本字段回答「这份快照是在今天的什么时刻抓的」，是覆盖判据的输入。
_cached_at_wall: datetime | None = None
#: 上次**尝试**抓取的时刻（单调钟）。用于 `_REFRESH_MIN_INTERVAL_SEC` 限频；
#: 记「尝试」而非「成功」—— 源故障时成功时刻永远不更新，限频会失效。
_last_attempt_mono = 0.0


def _is_fresh(now_mono: float | None = None) -> bool:
    """缓存是否可**直接复用**（判据 = 未超上限 TTL **且** 覆盖今天）。

    为什么不能只看 TTL（2026-09-14 实测缺陷）：源头日历是**尾随窗口、不含未来日期**
    （09-13 22:28 抓 ⇒ 末日 09-11；09-14 10:28 抓 ⇒ 末日 09-14）。于是任何在午夜前
    填充的缓存**必然不含次日**，只要刷新边界落在开盘后，「假休市」窗口就每个交易日
    必然复发（实测 09:30–10:28 共 58 分钟，全站显示"休市 · 展示最近交易日数据"）。
    把判据改为「覆盖今天」，这类窗口降为 0。

    覆盖成立的两条路径（**缺一不可**，两个反例都更差）：
      ① `days[-1] >= today` —— 列表已含今天。**单独用它不够**：节假日（尾随源的末日
         停在上一交易日）会被判成"未覆盖"，进而被误当"开放"。
      ② 快照抓于「今天 09:30 之后」—— 已过开盘，源头若把今日算作交易日就必然给出。
         **单独用它也不够**：凌晨抓的快照会带着不含今日的列表活一整天（见 _SESSION_OPEN）。
    """
    if not _cached_days:
        return False
    if (now_mono if now_mono is not None else time.monotonic()) - _cached_at >= _CACHE_TTL_SEC:
        return False
    today = beijing_today()
    if _cached_days[-1] >= today:
        return True
    snap = _cached_at_wall
    return bool(snap and snap.date() == today and snap.time() >= _SESSION_OPEN)


def _should_refresh() -> bool:
    """是否**允许**发起重取（在 `_is_fresh()` 为假之后调用）。限频见 `_REFRESH_MIN_INTERVAL_SEC`。"""
    if not _cached_days:
        return True
    return time.monotonic() - _last_attempt_mono >= _REFRESH_MIN_INTERVAL_SEC


def _normalize(days: list[date]) -> list[date]:
    return sorted({d for d in days if isinstance(d, date)})


# ---- 技术债 #10：日历持久化兜底（data/trade_calendar.json）----

_PERSIST_PATH = Path(__file__).resolve().parents[2] / "data" / "trade_calendar.json"


def _persist(days: list[date], source: str) -> None:
    """成功抓取后落盘，供双源全挂时兜底。失败只告警（缓存是优化不是依赖）。"""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(json.dumps({
            "source": source,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "days": [d.isoformat() for d in days],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.warning("trading calendar persist failed", exc_info=True)


_PERSIST_MIN_RATIO = 0.8  # 新结果须 ≥ 现有持久化的 80% 才允许覆盖


def _persist_if_better(days: list[date], source: str) -> None:
    """持久化质量闸门（2026-09-07，修复 09-04 实测缺陷）。

    缺陷：`_persist` 无条件覆盖——ths 官方端点失败退 index-kline 备源时，
    ~120 自然日推导出的短日历（约 80 交易日）覆盖掉 243 天官方日历。
    两种失效：① 短日历成为兜底后 `nth_prev_trade_date(n>80)` 返回 None，
    复盘区间全废；② 兜底末日不含今天 → 今天被判非交易日（08-29 事故路径）。

    规则：已有持久化且新结果明显更短（<80%）→ 拒绝覆盖并告警。
    首次落盘（无 existing）与正常更新（官方≈官方、更长的备源）都放行。
    """
    existing = _load_persisted()
    if existing is not None and len(days) < len(existing) * _PERSIST_MIN_RATIO:
        log.warning(
            "trading calendar persist skipped: %d days from %s < %.0f%% of persisted "
            "%d days（劣质备源不覆盖官方日历，保留长历史兜底）",
            len(days), source, _PERSIST_MIN_RATIO * 100, len(existing),
        )
        return
    _persist(days, source)


# mtime 缓存（2026-09-08 P0-1，审查 §1.4.1 热路径）：_load_persisted 挂在
# in_trading_window → data_quality/validator.validate_quote 逐行情调用链上，
# 每次同步读盘+解析 JSON 是事件循环内的纯浪费。key 含 (mtime_ns, path)：
# 文件被重写（含 pytest 后还原）或测试 monkeypatch 换 _PERSIST_PATH 都会自动失效。
_persisted_cache: tuple[int, str, list[date] | None] | None = None


def _load_persisted() -> list[date] | None:
    global _persisted_cache
    try:
        st = _PERSIST_PATH.stat()
        key = (st.st_mtime_ns, str(_PERSIST_PATH))
    except OSError:
        return None  # 文件不存在（测试频繁建删，不缓存缺失态）
    cached = _persisted_cache
    if cached is not None and cached[0] == key[0] and cached[1] == key[1]:
        return cached[2]
    try:
        raw = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        days = _normalize([date.fromisoformat(s) for s in raw.get("days", [])])
        days = days if len(days) >= _MIN_DAYS else None
    except Exception:
        days = None
    _persisted_cache = (key[0], key[1], days)
    return days


def _iter_providers(provider):
    """展开 provider 链：CompositeProvider 取 .providers，裸 provider 取自身。"""
    if provider is None:
        return []
    inner = getattr(provider, "providers", None)
    if isinstance(inner, list) and inner:
        return list(inner)
    return [provider]


def _parse_official(raw) -> list[date]:
    """解析官方日历返回值（'YYYYMMDD' 字符串，也可能已是 date）。"""
    out: list[date] = []
    for it in raw or []:
        if isinstance(it, date):
            out.append(it)
            continue
        s = str(it).strip()
        if len(s) >= 8 and s[:8].isdigit():
            try:
                out.append(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
            except ValueError:
                continue
    return _normalize(out)


async def _official_days(provider) -> list[date]:
    """官方交易日历端点。返回空列表表示没有 provider 提供该能力。"""
    for p in _iter_providers(provider):
        fn = getattr(p, "get_trading_days", None)
        if fn is None:
            continue
        try:
            days = _parse_official(await fn())
        except Exception as exc:
            log.warning("trading calendar from %s failed: %s", getattr(p, "name", p), exc)
            continue
        if days:
            return days
    return []


async def _index_kline_days(provider, lookback_days: int) -> list[date]:
    """备源：上证综指日 K 的日期集合。指数永不停牌，交易日必然有 bar。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars = await provider.get_kline(INDEX_SYMBOL, "1d", start, end)
    return _normalize([b.ts.date() for b in bars])


async def trading_days(provider, lookback_days: int = 120) -> list[date]:
    """返回升序的交易日列表。

    优先 ths 官方日历（权威、近一年、含节假日调整），失败后退到上证日 K 推导。
    两者都失败时抛 `RuntimeError` 而不是返回猜测值——猜测的日历比没有日历更危险。

    缓存判据见 `_is_fresh()`：**「覆盖今天」优先于「活了多少秒」**。
    """
    global _cached_days, _cached_at, _cached_at_wall, _last_attempt_mono
    now = time.monotonic()
    if _is_fresh(now):
        return list(_cached_days)

    async with _lock:
        # 双检：等锁期间可能已被别的协程填满
        if _is_fresh():
            return list(_cached_days)
        # 限频：不满足覆盖判据、但距上次尝试不足 `_REFRESH_MIN_INTERVAL_SEC` ⇒ 先返回
        # 旧快照。**刻意不返回空**——略旧的日历远好于「无日历」（调用方会降级成
        # 工作日推断，节假日误放行）。重取窗口由限频收敛，见 `_should_refresh()`。
        if not _should_refresh():
            return list(_cached_days)

        _last_attempt_mono = time.monotonic()
        days: list[date] = []
        source = ""
        try:
            days = await _official_days(provider)
            if days:
                source = "official"
        except Exception as exc:  # pragma: no cover - 防御，_official_days 内部已兜底
            log.warning("official trading calendar error: %s", exc)

        if len(days) < _MIN_DAYS:
            try:
                days = await _index_kline_days(provider, lookback_days)
                if days:
                    source = "index-kline"
            except Exception as exc:
                log.warning("index kline calendar failed: %s", exc)

        if len(days) < _MIN_DAYS:
            # 技术债 #10 兜底：双源都挂 → 读上次成功抓取的持久化日历（带时间戳的
            # 权威快照，不是猜测）。文件不存在才抛错——"拒绝猜测"语义保持不变。
            persisted = _load_persisted()
            if persisted is not None:
                days, source = persisted, "persisted"
                log.warning(
                    "trading calendar sources unavailable, using persisted calendar "
                    "(last fetched file)——建议检查 ths / 指数K线连通性"
                )
            else:
                raise RuntimeError(
                    f"交易日历拉取异常：仅得 {len(days)} 个交易日（<{_MIN_DAYS}），"
                    "且无持久化兜底文件，拒绝使用"
                )
        _cached_days = days
        _cached_at = time.monotonic()
        _cached_at_wall = beijing_now()
        if source != "persisted":
            _persist_if_better(days, source)
        log.info("trading calendar loaded: %s days from %s, last=%s",
                 len(days), source, days[-1])
        return list(days)


def is_trade_day(days: list[date], d: date) -> bool:
    """二分查找：d 是否为交易日。"""
    import bisect

    i = bisect.bisect_left(days, d)
    return i < len(days) and days[i] == d


def known_trade_days() -> list[date]:
    """**同步、零外呼**返回已知交易日：进程内缓存优先，退回持久化快照。

    用途：写路径（如 EOD 落盘闸门）需要一个便宜的「今天是不是交易日」判据，
    但 `trading_days()` 是 async 且可能触发网络 —— 写路径不宜为一次判据承担
    外呼失败风险。已知多少用多少，**判定不足时如实返回「未覆盖」**，
    由调用方按三态处理（见 `is_trade_day_on`）。
    """
    return list(_cached_days) or list(_load_persisted() or [])


def is_trade_day_on(d: date, days: list[date] | None = None) -> bool | None:
    """`d` 是否为交易日 —— **三态**：`True` / `False` / `None`（未判定）。

    | 返回 | 情形 | 可否据此拦截 |
    |---|---|---|
    | `False` | ① 周末；② 日历已覆盖 d 且 d 不是交易日（节假日） | ✅ 可以 |
    | `None`  | 日历未覆盖 d（尾随源 + 缓存过旧） | ❌ **不得**当非交易日用 |
    | `True`  | 日历覆盖且含 d | ✅ 可以 |

    为什么周末单独判而不等日历（2026-09-14）：尾随日历在周末**永远不覆盖今天**
    （末日停在周五），若只走「覆盖才判」就会把周末降级成 `None`，让 `unknown`
    白白丢掉一个本可确定的事实。三态的价值在于**只在真不确定时不确定**。

    `days` 参数（2026-09-14 F7）：调用方若已自行 `await trading_days()`（或单测
    注入了一份确定的列表），应**显式传入**——否则这里会去读进程缓存
    `known_trade_days()`，两者可能不是同一份，测试会隐式绑定真实日历
    （「一周只有几天是绿的守卫」）。不传则退回 `known_trade_days()`（同步、零外呼）。
    """
    if d.weekday() >= 5:
        return False
    days = known_trade_days() if days is None else days
    if not days or days[-1] < d:
        return None
    return is_trade_day(days, d)


def is_today_trade_day() -> bool | None:
    """今天是否交易日（`beijing_today()` 口径，三态同 `is_trade_day_on`）。"""
    return is_trade_day_on(beijing_today())


# ---- 交易时段判定（2026-09-01：盘前空数据不再误标"可疑"，validator/QuoteHub 共用）----

def market_open_state(days: list[date] | None, now: datetime | None = None) -> str:
    """市场状态**三态**裁决 —— 唯一实现（`open` / `closed` / `unknown`）。

    为什么要有这个函数（2026-09-14 实测缺陷，见 implementation §5.4）：
    同一情形曾有两处**相反**的判定 —— `in_trading_window`（读持久化文件、**有**
    `days[-1] >= today` 守卫 ⇒ 放行）与 `QuoteHub._refresh_closed_state`（读进程内
    缓存、**无**守卫 ⇒ 判休市）。于是「日历未覆盖今天」这一情形被两处分别塌缩成
    「开放」与「确认休市」，违背核心纪律「三态 > 二态」；后者导致全站「休市」误标，
    且**每个交易日开盘后必然复发**（缓存相位落在 09:30 之后的那些进程）。

    `closed` **只在有确定依据时**返回（三条互斥路径）：
      ① 周末；② 日历覆盖今天且今天不是交易日（节假日）；③ 当前不在宽窗口内
      （盘前/盘后/夜间 —— 「此刻没有实时行情」本身是确定的，与今天是否交易日无关）。
    `unknown` **只在一种情形**返回：处于可能的交易时段内，但日历未覆盖今天
      —— 此时既无日历背书开市、也不能因源没更新就断言休市。

    调用方纪律：`unknown` **不得**被任何一方塌缩 —— 既不标 `market_closed`，
    也不标 `ready`，改由数据质量（validator / freshness）如实定级。
    """
    now = now or beijing_now()
    if now.weekday() >= 5:
        return MARKET_CLOSED
    covered = bool(days) and days[-1] >= now.date()
    if covered and not is_trade_day(days, now.date()):
        return MARKET_CLOSED  # 日历明确今天休市（节假日）
    if not in_wide_market_window(now):
        return MARKET_CLOSED  # 时段外：此刻不存在实时行情，与交易日归属无关
    return MARKET_OPEN if covered else MARKET_UNKNOWN


def in_trading_window(now: datetime | None = None) -> bool:
    """同步判定当前是否处于**连续竞价**时段（交易日 09:30–11:30 / 13:00–15:00，北京时间）。

    完整性罚分（missing_price/price 越界等）只应在连续竞价生效：集合竞价
    （09:15–09:25）与开盘前形态一样不完整——价格在撮合、high/low 未建立，
    2026-09-01 09:21 实测竞价时段再次全体误标"可疑/非法"，故窗口缩到连续竞价。
    交易日判断用持久化日历兜底（trading_days() 成功抓取后落盘的
    data/trade_calendar.json）；日历缺失或未覆盖今天时为 `unknown` ⇒ **放行**
    ——节假日少量误放行可接受，宁可放行也不因日历故障把盘中误判成休市。
    **归属裁决走单点 `market_open_state`**（2026-09-14 收口，此前与 QuoteHub 口径相反）。
    """
    now = now or beijing_now()
    if market_open_state(_load_persisted(), now) == MARKET_CLOSED:
        return False  # 周末 / 节假日 / 时段外 —— 三种确定的 NOT open
    hhmm = now.hour * 100 + now.minute  # 模块级 import time 遮蔽 datetime.time，用 hhmm 整数比较
    return (930 <= hhmm <= 1130) or (1300 <= hhmm <= 1500)


def in_wide_market_window(now: datetime | None = None) -> bool:
    """含集合竞价与收盘定价的**宽松**交易窗口（09:15–15:05，仅时刻判定）。

    与 in_trading_window（连续竞价严窗）各司其职：本函数管「数据新鲜度/
    缓存节奏」类判定（QuoteHub stale 标记、board_flow 缓存 TTL），不判
    交易日归属（历史上两处消费方都只判时刻，交易日由调用方自理）——
    2026-09-07 R2 收口：QuoteHub._in_market_hours 与 board_flow._in_session
    的同口径时刻判定合并到此处，时段窗口单点。
    """
    now = now or beijing_now()
    hhmm = now.hour * 100 + now.minute
    return 915 <= hhmm <= 1505


def last_trade_date(days: list[date], asof: date | None = None) -> date | None:
    """返回 <= asof 的最后一个交易日。asof 默认为今天。"""
    import bisect

    if not days:
        return None
    asof = asof or beijing_today()
    i = bisect.bisect_right(days, asof)
    return days[i - 1] if i > 0 else None


def prev_trade_date(days: list[date], d: date) -> date | None:
    """返回 d 之前的**上一个**交易日（严格早于 d，不含 d 本身）。"""
    import bisect

    if not days:
        return None
    i = bisect.bisect_left(days, d)
    return days[i - 1] if i > 0 else None


def nth_prev_trade_date(days: list[date], anchor: date, n: int) -> date | None:
    """以 anchor 为起点（须为交易日）往前数第 n 个交易日。n=1 即前一交易日。"""
    import bisect

    if not days or n < 0:
        return None
    i = bisect.bisect_left(days, anchor)
    j = i - n
    return days[j] if j >= 0 else None


def recent_trade_dates(days: list[date], anchor: date, count: int) -> list[date]:
    """返回以 anchor 结尾（含）的连续 count 个交易日，**由近及远**。"""
    import bisect

    if not days or count <= 0:
        return []
    i = bisect.bisect_left(days, anchor)
    end = i + 1  # 若 anchor 不是交易日，bisect_left 指向其后一个，正好截断到它之前
    if i >= len(days) or days[i] != anchor:
        end = i
    start = max(0, end - count)
    return list(reversed(days[start:end]))


def invalidate_cache() -> None:
    """测试用：清空进程内缓存（含覆盖判据的墙钟与限频位）。"""
    global _cached_days, _cached_at, _cached_at_wall, _last_attempt_mono
    _cached_days = []
    _cached_at = 0.0
    _cached_at_wall = None
    _last_attempt_mono = 0.0
