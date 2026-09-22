from __future__ import annotations

import asyncio
import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core.bjtime import beijing_now  # S2-8 时区收敛
from app.core.freshness import Freshness
from app.data_quality.validator import mark_stale, validate_quote
from app.market import trade_calendar as tc
from app.market.indices import INDEX_MARKETS, INDEX_SUBSCRIPTIONS
from app.schemas.market import Quality, Quote, utcnow

log = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_MAX = 64
"""单个订阅者的出站队列上限（R24，2026-09-14）。

**为什么必须有界**：旧实现用无界 `asyncio.Queue()`，`put_nowait` 永不失败 ⇒
消费者一旦卡住（慢网络 / 半死连接 / writer 已成孤儿），积压**没有上限**
（2026-09-14 实测 1000+ 条），而投递侧完全察觉不到，内存随运行时长单调增长。

64 ≈ 1 分钟的 1Hz 推送量：足够吸收抖动；且队列只是"待发送帧"的缓冲区——
行情是**最新即正确**的快照型数据，积压的中间帧没有价值。
"""

_DROP_LOG_EVERY = 100
"""丢弃日志节流：首次丢必报，之后每 100 帧报一次（1Hz 下不至于刷屏）。"""


@dataclass
class Subscriber:
    """一个订阅连接的出站队列与它的账簿。

    `symbols is None` = 订阅全量。此前订阅集装在单元素列表 `cell` 里（为了
    "原地改写、绝不换队列"），改用显式字段后同一不变量仍在，且多了 `dropped`
    这个记账位置——丢弃是降级，必须能定位到是**哪个**连接在积压。
    """

    symbols: set[str] | None
    queue: asyncio.Queue
    dropped: int = 0
    created_at: datetime = field(default_factory=utcnow)


def offer(queue: asyncio.Queue, msg: dict) -> bool:
    """非阻塞投递到出站队列；队列满时**丢最旧、保最新**，返回本次是否发生了丢弃。

    为什么丢旧而不是阻塞或拒绝新：投递侧是 1Hz 广播循环与 WS reader，**都不能阻塞**
    （阻塞广播会拖垮所有订阅者）。行情是快照型数据，客户端真正需要的是"最新那一帧"
    ⇒ 丢最旧是语义损失最小的选择，同时把内存钉在上限内。
    返回值用于累计丢弃计数并告警（丢弃 = 降级，不能静默，见 `_broadcast`）。
    """
    try:
        queue.put_nowait(msg)
        return False
    except asyncio.QueueFull:
        pass
    # 满：挪出最旧的一条给新帧腾位置。单线程事件循环内 get_nowait 必成功，
    # 两个 except 分支只为防御"满队列居然取不出"这种不可能状态，不让它抛出。
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:  # pragma: no cover - 满队列不可能是空的
        return False
    try:
        queue.put_nowait(msg)
    except asyncio.QueueFull:  # pragma: no cover - 同上
        return False
    return True


async def _empty() -> list:
    """gather 分支占位：自选为空时无 quotes 请求（与旧串行行为一致）。"""
    return []


def _rejected_source_observation(q: Quote, prev: Quote | None = None) -> str | None:
    """返回“本条不能推进 current cache”的稳定原因；None 表示可接纳。

    审计行/日志仍保留上游返回事实，但 current value 只允许由时间不倒退、结构不非法的
    观测推进。`missing_price` 在 live 校验里虽只是 low，也不能用 None 覆盖已有正常价格。
    """
    reasons = set(q.quality_reasons or [])
    if q.quality is Quality.invalid:
        return "source_invalid_ignored"
    if "time_regress" in reasons:
        return "source_time_regress_ignored"
    if prev is not None and prev.data_timestamp is not None and q.data_timestamp is None:
        return "source_time_unknown_ignored"
    if q.price is None or "missing_price" in reasons:
        return "source_missing_price_ignored"
    return None


def _mark_retained_rejected(q: Quote, reason: str) -> None:
    """保留最后可信值，但把“本轮没有可信刷新”作为 stale 暴露给消费者。"""
    mark_stale(q, reason)


class QuoteHub:
    """行情缓存与广播中心。

    - 轮询 Provider → Normalizer（Provider 内部）→ Quality Validator → 缓存 → WebSocket
    - 刷新失败时：停止伪造实时数据，把缓存数据标记为 stale 并记录原因
      （2026-09-01 秒级化修订：数据年龄未超 stale_after 的瞬时失败不标不广播——
      1Hz 节奏下单次网络抖动不该让前端闪"数据过期"）
    - 订阅者通过 asyncio.Queue 接收增量推送，消息带自增 seq
    """

    def __init__(
        self,
        provider,
        poll_interval: float,
        get_watchlist: Callable[[], list[str]] | None = None,
        history_len: int = 600,
        stale_after: float = 10.0,
    ):
        self.provider = provider
        self.poll_interval = max(0.5, poll_interval)
        self.stale_after = max(self.poll_interval, stale_after)
        self.get_watchlist = get_watchlist or (lambda: [])
        self.indices: dict[str, Quote] = {}
        self.quotes: dict[str, Quote] = {}
        self.quote_history: deque[tuple[str, datetime, float | None]] = deque(maxlen=history_len)
        # 每个元素是一个 Subscriber（符号集 + 出站队列 + 丢弃计数）。订阅集**原地改写**，
        # 绝不换队列：writer 正 parked 在队列 get() 上，换队列会让它永远等在孤儿队列。
        self._subscribers: list[Subscriber] = []
        # 推送链路**累计**丢弃帧数（R24）：只记在连接上会随断开一起消失，
        # 而"卡住的客户端断开"恰恰是最需要看到这个数字的时刻。
        self.dropped_frames = 0
        self._seq = 0
        self.last_success_refresh: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        # 批量请求的**返回覆盖率**（R18，2026-09-14）：成功按"请求完成"记账是不够的
        # ——源可能返回 A 却漏 B，此时 B 的旧缓存会被当成实时广播。
        # 这两个字段是该轮的取证面（缺失清单 + 覆盖率），也是 freshness 降级的依据。
        self.last_missing_symbols: list[str] = []
        self.last_batch_coverage: float | None = None
        self.last_quote_rejections: dict[str, str] = {}
        # 以共同请求目录为分母；首次缺席也计数，但不臆造报价。
        self.last_missing_indices: list[str] = []
        self.last_index_coverage: float | None = None
        self.last_index_rejections: dict[str, str] = {}
        # 休市状态沿触发（红线 2：休市日数据不得冒充实时）
        self._closed_marked = False

    # ---------- 刷新 ----------

    async def refresh(self) -> None:
        self.last_attempt = utcnow()
        try:
            # 指数与自选并行拉取（2026-09-01 秒级化）：串行两次 HTTP 会把
            # 1s 固定节奏的实际周期拉长到 ~1.6s，并行后单周期 ≈ 最慢一路
            watchlist = self._poll_symbols()
            new_indices, new_quotes = await asyncio.gather(
                self.provider.get_indices(),
                self.provider.get_quotes(watchlist) if watchlist else _empty(),
            )
        except Exception as exc:  # ProviderError、网络错误等一律降级
            self.consecutive_failures += 1
            self.last_error = str(exc)
            age = (
                (utcnow() - self.last_success_refresh).total_seconds()
                if self.last_success_refresh is not None
                else float("inf")
            )
            # 数据年龄超过 stale_after 才标 stale 并广播：瞬时失败（单次网络
            # 抖动、源 5xx 一两轮）期间缓存数据仍远新于阈值，保持 live 语义；
            # 启动后从未成功（age=inf）立即标，绝不无中生有（红线 2）。
            if age >= self.stale_after:
                log.warning(
                    "quote refresh failed (%s): %s — data age %.0fs >= stale_after, marking stale",
                    type(exc).__name__, exc, age,
                )
                self._mark_all_stale()
            else:
                log.warning("quote refresh failed (%s): %s — keeping last good data", type(exc).__name__, exc)
            return
        # 裸代码只有在市场也匹配且身份唯一时才进入校验；校验通过“接纳门”后才推进 current cache。
        # 上游晚到/非法观测不能覆盖最近可信值，否则 received-order 会反过来污染 source-time。
        counts = Counter((q.symbol, q.market) for q in new_indices)
        accepted_indices: list[Quote] = []
        rejected_indices: dict[str, str] = {}
        candidate_indices: list[Quote] = []
        for q in new_indices:
            expected_market = INDEX_MARKETS.get(q.symbol)
            if expected_market is None:
                rejected_indices[f"{q.market or '?'}:{q.symbol}"] = "source_identity_unexpected_ignored"
                continue
            if q.market != expected_market:
                rejected_indices[q.symbol] = "source_identity_market_mismatch_ignored"
                continue
            if counts[q.symbol, q.market] != 1:
                rejected_indices[q.symbol] = "source_identity_duplicate_ignored"
                continue
            candidate_indices.append(q)
        for q in candidate_indices:
            prev = self.indices.get(q.symbol)
            validate_quote(q, prev)
            rejected = _rejected_source_observation(q, prev)
            if rejected is not None:
                rejected_indices[q.symbol] = rejected
                continue
            self.indices[q.symbol] = q
            accepted_indices.append(q)
        self._mark_index_gaps(accepted_indices, rejected_indices)

        quote_counts = Counter(q.symbol for q in new_quotes)
        returned_by_symbol = {q.symbol: q for q in new_quotes}
        accepted_by_symbol: dict[str, Quote] = {}
        rejected_quotes: dict[str, str] = {
            q.symbol: "source_identity_unrequested_ignored"
            for q in new_quotes if q.symbol not in watchlist
        }
        for symbol in watchlist:
            if quote_counts.get(symbol, 0) > 1:
                rejected_quotes[symbol] = "source_identity_duplicate_ignored"
                continue
            q = returned_by_symbol.get(symbol)
            if q is None:
                continue
            prev = self.quotes.get(symbol)
            validate_quote(q, prev)
            rejected = _rejected_source_observation(q, prev)
            if rejected is not None:
                rejected_quotes[symbol] = rejected
                continue
            self.quotes[symbol] = q
            accepted_by_symbol[symbol] = q
            if q.price is not None and q.data_timestamp is not None:
                self.quote_history.append((q.symbol, q.data_timestamp, q.price))
        self._mark_batch_gaps(watchlist, accepted_by_symbol, rejected_quotes)
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_refresh = utcnow()
        await self._refresh_closed_state()
        # 休市：_refresh_closed_state 已广播 stale（market_closed），不再补发
        # quotes 消息——此前每周期 stale+quotes 连发两条，且 quotes 类型会把
        # 前端状态从"休市"冲回"实时推送"（状态闪烁）。
        if not self._closed_marked:
            self._broadcast("quotes")

    async def _refresh_closed_state(self) -> None:
        """休市判定（红线 2）—— **只在「确定休市」时**把缓存标 stale。

        三态单点裁决（2026-09-14 修复，见 implementation §5.4）：
        归属判定改走 `tc.market_open_state`，它区分 `closed` 与 `unknown`。
        原实现是 `verdict = tc.is_trade_day(days, now.date()) and _in_market_hours(now)`，
        **缺「日历是否覆盖今天」的守卫** ⇒ 进程内缓存日历跨日陈旧（不含今天）时
        `is_trade_day` 返回 False，被当成「**确认休市**」⇒
        `_mark_all_stale("market_closed")` ⇒ 前端显示「休市 · 展示最近交易日数据」。
        这不是一次性事件：源头日历是**尾随窗口、不含未来日期**，任何午夜前填充的缓存
        必然不含次日 ⇒ **每个交易日开盘后都会复现**（实测 09:30–10:28 共 58 分钟）。

        三种状态各自的处置（`unknown` **不得**被塌缩）：
        - `closed`（周末 / 节假日 / 时段外）⇒ 标 `stale("market_closed")`；
        - `open`   ⇒ 不干预，数据质量由 validator 定级；
        - `unknown`（处于交易时段内、但日历未覆盖今天）⇒ **不标休市**：既不能冒充休市、
          也不能冒充实时 —— 交给数据质量如实定级（KB 核心纪律「三态 > 二态」）。
        日历不可用 / 为空 → 返回不干预（未知不判，保持原行为）。
        """
        try:
            now = beijing_now()
            days = await tc.trading_days(self.provider)
            if not days:
                return
            state = tc.market_open_state(days, now)
        except Exception as exc:
            log.debug("market-open check unavailable: %s", exc)
            return
        if state == tc.MARKET_CLOSED:
            # 每轮重标（2026-09-01 修复：原沿触发只在首轮标 stale，之后 refresh()
            # 又把 validator 判定的新数据存回缓存——盘前质量在 stale/low/invalid
            # 之间震荡，出现"可疑/非法"误标）。休市态稳定为 stale("market_closed")。
            if not self._closed_marked:
                self._closed_marked = True
                log.info("market closed: cached quotes marked stale")
            self._mark_all_stale(reason="market_closed")
        elif self._closed_marked:
            # 非确定休市（`open` 或 `unknown`）：清标记以恢复 quotes 广播。
            # `unknown` 必须走这里 —— 继续带着 `_closed_marked` 会让前端停在"休市"
            # 且不广播（见 `refresh()` 的 `if not self._closed_marked`）。
            self._closed_marked = False

    def _mark_index_gaps(
        self, returned: list[Quote], rejected: dict[str, str] | None = None,
    ) -> None:
        """核对可接纳结果；拒绝观测也算本轮缺失，但保留旧值并标明拒绝原因。"""
        rejected = rejected or {}
        self.last_index_rejections = dict(sorted(rejected.items()))
        missing = sorted(INDEX_MARKETS.keys() - {q.symbol for q in returned})
        for symbol in missing:
            cached = self.indices.get(symbol)
            if cached is not None:
                reason = rejected.get(symbol)
                if reason:
                    _mark_retained_rejected(cached, reason)
                else:
                    mark_stale(cached, "index_batch_missing")
        if missing != self.last_missing_indices:
            if missing:
                log.warning("指数行情缺失（已有缓存标 stale，无缓存保持缺席）：%s", ", ".join(missing))
            else:
                log.info("此前缺失的指数已全部返回")
        self.last_missing_indices = missing
        self.last_index_coverage = (len(INDEX_MARKETS) - len(missing)) / len(INDEX_MARKETS)

    def index_batch(self) -> dict:
        """最近完成的指数批次完整性；None 表示尚未取得批次，失败不改写旧证据。"""
        return {
            "expected_count": len(INDEX_MARKETS),
            "coverage": self.last_index_coverage,
            "missing_symbols": list(self.last_missing_indices),
        }

    def source_rejections(self) -> dict:
        """最近一个**已完成批次**中，源观测被接纳门拒绝的紧凑摘要。

        不把完整股票清单塞进每个 API 信封；只暴露数量与稳定原因分布。指数的
        缺失代码已有 `index_batch.missing_symbols`。Provider 整体请求失败时本摘要与
        `index_batch` 一样保留上一已完成批次，另由 `last_error/freshness` 表达新失败。
        """
        def summary(rows: dict[str, str]) -> dict:
            return {
                "count": len(rows),
                "reasons": dict(sorted(Counter(rows.values()).items())),
            }

        return {
            "quotes": summary(self.last_quote_rejections),
            "indices": summary(self.last_index_rejections),
        }

    def _mark_batch_gaps(
        self, watchlist: list[str], by_symbol: dict[str, Quote],
        rejected: dict[str, str] | None = None,
    ) -> None:
        """批量**部分成功**时，未返回的标的必须降级——不能拿旧缓存冒充实时（红线 2）。

        缺陷（R18，2026-09-14）：原实现只对「返回了的符号」写缓存。缺失符号既没被
        更新、也没被标记，``quality`` 仍是上一次的 ``high``，而 Hub 已记成功并
        ``_broadcast("quotes")`` ⇒ 合成场景下 B 是**前一天的报价**却随 quotes 广播，
        消费方只看 ``quality`` 或只看 Hub 状态都会被误导。根因是成功按"请求完成"
        记账，没有按**请求集**核对返回覆盖率与逐标的年龄。

        修法：逐标的 freshness 为权威。缺失但**有旧缓存**的标 ``stale("batch_missing")``
        —— 保留旧值不丢数据，但明确它已不是本轮的实时数据；无缓存的保持缺席
        （调用方按 missing 处理，不臆造）。休市时 ``_refresh_closed_state`` 会用
        ``market_closed`` 覆盖（更贴近成因），故本方法必须先于它执行。
        """
        rejected = rejected or {}
        self.last_quote_rejections = dict(sorted(rejected.items()))
        missing = [s for s in watchlist if s not in by_symbol]
        coverage = (len(watchlist) - len(missing)) / len(watchlist) if watchlist else None
        prev_missing = self.last_missing_symbols
        self.last_missing_symbols = missing
        self.last_batch_coverage = coverage

        for symbol in missing:
            cached = self.quotes.get(symbol)
            if cached is not None:
                reason = rejected.get(symbol)
                if reason:
                    _mark_retained_rejected(cached, reason)
                else:
                    mark_stale(cached, "batch_missing")

        # 只在**缺失集变化**时留痕，避免 1Hz 轮询把日志刷成噪声
        if missing != prev_missing:
            if missing:
                log.warning(
                    "批量行情部分缺失：%d/%d 只未返回（覆盖率 %.0f%%），已标 stale：%s%s",
                    len(missing), len(watchlist), (coverage or 0.0) * 100,
                    ", ".join(missing[:10]), " …" if len(missing) > 10 else "",
                )
            elif prev_missing:
                log.info("批量行情覆盖率已恢复 100%%（%d 只）", len(watchlist))

    def _safe_watchlist(self) -> list[str]:
        try:
            return self.get_watchlist()
        except Exception:
            log.exception("watchlist lookup failed; keeping previous watchlist")
            return list(self.quotes.keys())

    def _poll_symbols(self) -> list[str]:
        """轮询池 = 自选 ∪ 各订阅者订阅集中的裸 6 位代码。

        2026-09-02 用户反馈"详情页分时/K线不及时更新"的根因：此前只轮询自选，
        详情面板看非自选股时该股永远不进缓存 → WS 推送与 REST 轮询都取不到，
        唯一的 30s 估值补源又被"保留 WS 最新价"的合并逻辑封死更新——图表只剩
        60s REST 校准一条慢通道。订阅者要什么就拉什么：详情页多看的标的并入
        1Hz 轮询池（详情页通常只看一只，增量可忽略）。指数（sh000001 等带
        前缀形态）不进池——指数由 get_indices 单独维护，get_quotes 里有
        前缀回退逻辑兜底。订阅集每轮从 _subscribers 现算，零额外状态；
        订阅者断开（unsubscribe）后自动移出。"""
        symbols = set(self._safe_watchlist())
        for sub in self._subscribers:
            if sub.symbols:
                symbols.update(s for s in sub.symbols if len(s) == 6 and s.isdigit())
        return sorted(symbols)

    def _mark_all_stale(self, reason: str = "refresh_failed") -> None:
        for q in self.indices.values():
            mark_stale(q, reason)
        for q in self.quotes.values():
            mark_stale(q, reason)
        self._broadcast("stale")

    def freshness(self) -> Freshness:
        """行情链新鲜度（S2-1 契约的 **QuoteHub 样板**）。

        `is_stale()` 是这套判定的"二态年代"替身：它把三种**成因完全不同**的情况
        ——①从未成功刷新 ②已标记休市 ③超过 `stale_after`——压成一个布尔，
        调用方无从区分"该显示占位符"还是"该显示昨收并标注"。本方法保留三种成因，
        `is_stale()` 改为它的派生布尔（判定逻辑单点在此，不再各写一份）。
        """
        source = getattr(self.provider, "name", None)
        if self.last_success_refresh is None:
            return Freshness.unavailable(reason="行情链尚未成功刷新过", source=source)
        f = Freshness.from_age(
            as_of=self.last_success_refresh, fresh_within=self.stale_after,
            source=source, missing_reason="无成功刷新时间，无法判定新鲜度",
        )
        if self._closed_marked and f.state == "ready":
            # 休市：数据是最近交易日的，绝不冒充实时（红线 2）
            return Freshness.stale(
                as_of=f.as_of, age_seconds=f.age_seconds, source=source,
                reason="已标记休市：当前为最近交易日数据，不冒充实时",
            )
        return f

    def is_stale(self) -> bool:
        return not self.freshness().is_fresh()

    # ---------- 读取 ----------

    def _visible_quote(self, quote: Quote) -> Quote:
        """返回消费侧视图；缓存未被继续轮询时也不能无限保持 high。

        current cache 保存最后被接纳的事实，不因为时间过去就原地篡改。
        但 REST/WS 读取必须同时反映 source event time 的年龄；超过
        stale_after 时返回 stale 副本，直到下一条可信观测推进 current。
        """
        if quote.quality in (Quality.stale, Quality.invalid):
            return quote
        if quote.freshness(fresh_within=self.stale_after).state != "stale":
            return quote
        visible = quote.model_copy(deep=True)
        mark_stale(visible, "quote_age_exceeded")
        return visible

    def get_indices(self) -> list[Quote]:
        return [self._visible_quote(q) for q in self.indices.values()]

    def get_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        # 指数兜底：带前缀查询（sh000001，指数详情链路的规范形态）归一化成裸代码查
        # indices，并以查询形态返回（model_copy 不变异共享缓存对象）。
        # ⚠️ 裸 6 位代码**绝不**回退 indices（2026-09-01 P0 修复）：000001 平安银行
        # 与上证指数、000688 国城矿业与科创50 撞码——裸代码查询曾直接命中
        # indices 返回指数数据冒充股票行情（实测 000001 返回上证指数 3979.88）。
        # 项目纪律：裸代码=股票，指数必须带前缀（CONTEXT.md）。
        if symbols:
            out = []
            for s in symbols:
                q = self.quotes.get(s)
                if q is None and len(s) >= 3 and s[:2].lower() in ("sh", "sz", "bj"):
                    q = self.indices.get(s[2:])
                    q = (q.model_copy(update={"symbol": s})
                         if q is not None and q.market == s[:2].upper() else None)
                if q is not None:
                    out.append(self._visible_quote(q))
            return out
        return [self._visible_quote(q) for q in self.quotes.values()]

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---------- 订阅 ----------

    def subscribe(self, symbols: set[str] | None = None) -> asyncio.Queue:
        """注册订阅者。返回的队列终身复用——**改订阅集用 update_symbols，
        绝不 unsubscribe+subscribe 换新队列**：writer 正 parked 在旧队列的
        get() 上，换队列后 writer 永远等在孤儿队列（2026-09-01 实测事故：
        前端 loadBase 触发 subscribe → 推送静默死亡 → 前端 32s 自愈重连 →
        用户体感"约 30 秒才更新一次"）。符号集存单元格以便原地更新。

        队列**有界**（`SUBSCRIBER_QUEUE_MAX`，R24）：旧实现无界，消费者卡住时
        积压无上限且投递侧无法察觉。有界后投递走 `offer()`（丢最旧、保最新）。
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.append(Subscriber(symbols=symbols, queue=queue))
        return queue

    def update_symbols(self, queue: asyncio.Queue, symbols: set[str] | None) -> bool:
        """原地更新订阅集（同一队列，writer 无感）。队列不存在返回 False。"""
        for sub in self._subscribers:
            if sub.queue is queue:
                sub.symbols = symbols
                return True
        return False

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [s for s in self._subscribers if s.queue is not queue]

    def subscriber_stats(self) -> dict:
        """推送链路的取证面（R24）：连接数、队列上限、累计丢弃帧数。

        丢弃只可能发生在**出站队列**满时——即客户端（或已成孤儿的 writer）
        停止消费而服务端仍在按 1Hz 生产。这个数字是"推送正在丢帧"的唯一
        系统级读数；`dropped_frames` 刻意做成**累计**而非当前值，
        因为最该看到它的时刻正是那个连接断开之后。
        """
        return {
            "subscribers": len(self._subscribers),
            "queue_max": SUBSCRIBER_QUEUE_MAX,
            "dropped_frames": self.dropped_frames,
        }

    def _broadcast(self, msg_type: str) -> None:
        seq = self.next_seq()
        ts = utcnow().isoformat()
        for sub in self._subscribers:
            symbols = sub.symbols
            payload = self.get_quotes(sorted(symbols) if symbols is not None else None)
            index_subscribed = symbols is None or any(s.lower() in INDEX_SUBSCRIPTIONS for s in symbols)
            has_rejection = bool(self.last_quote_rejections or self.last_index_rejections)
            if not payload and not (index_subscribed and self.last_missing_indices) and not has_rejection:
                continue
            dropped = offer(
                sub.queue,
                {
                    "type": msg_type,
                    "seq": seq,
                    "ts": ts,
                    "data": [q.model_dump(mode="json") for q in payload],
                    "meta": {
                        "index_batch": self.index_batch(),
                        "source_rejections": self.source_rejections(),
                    },
                },
            )
            if dropped:
                sub.dropped += 1
                self.dropped_frames += 1
                if sub.dropped == 1 or sub.dropped % _DROP_LOG_EVERY == 0:
                    log.warning(
                        "订阅出站队列已满（maxsize=%d），丢弃最旧帧保最新——该连接可能已卡住："
                        "本连接累计丢 %d 帧，全局累计 %d 帧",
                        SUBSCRIBER_QUEUE_MAX,
                        sub.dropped,
                        self.dropped_frames,
                    )

    # ---------- 后台循环 ----------

    async def run(self) -> None:
        while True:
            started = utcnow()
            await self.refresh()
            elapsed = (utcnow() - started).total_seconds()
            delay = self.poll_interval
            if self.consecutive_failures > 0:
                delay = min(self.poll_interval * (2 ** min(self.consecutive_failures, 4)), 60.0)
                log.info("provider degraded, next refresh in %.0fs", delay)
            elif self._closed_marked:
                # 休市数据静止：降频到 5s 保活（省 Provider 配额/流量），开盘
                # 恢复检测延迟 ≤5s；交易时段（含竞价/午间）保持秒级节奏
                delay = max(delay, 5.0)
            # 固定节奏：扣除本轮刷新耗时，保证推送周期 = poll_interval 而非
            # poll_interval + 网络耗时（1s 档位下串行耗时的稀释不可忽略）
            await asyncio.sleep(max(0.0, delay - elapsed))
