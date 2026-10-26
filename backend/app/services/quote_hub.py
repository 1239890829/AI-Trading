from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.bjtime import BJ_TZ, beijing_now  # S2-8 时区收敛
from app.core.freshness import Freshness
from app.data_providers.composite import RecoveryRejection
from app.data_quality.validator import mark_stale, validate_quote
from app.market import trade_calendar as tc
from app.market.indices import INDEX_MARKETS, INDEX_SUBSCRIPTIONS
from app.schemas.market import Quality, Quote, QuoteDisplaySession, utcnow

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

_SOURCE_REJECTION_HISTORY_MAX = 64
MAX_SUBSCRIPTION_ID = 2**31 - 1


def validate_subscription_id(value: object) -> int:
    """Bound a connection-local scope generation; JSON booleans are not IDs."""
    if type(value) is not int or not 0 <= value <= MAX_SUBSCRIPTION_ID:
        raise ValueError("subscription_id must be an integer from 0 to 2147483647")
    return value


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
    subscription_id: int = 0


@dataclass
class RefreshDomain:
    """固定两条读取链的运行事实；源时间仍由 Quote 自身承载。"""

    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_type: str | None = None
    consecutive_failures: int = 0
    generation: int = 0
    succeeded: bool = False
    requested: bool = True
    requested_symbols: tuple[str, ...] = ()
    started_at: float | None = None  # 仅供本轮合批截止点，非源钟或 loop-lag 测量。


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


def _validate_source_quote(q: Quote, prev: Quote | None = None) -> Quote:
    """Add local checks without upgrading the provider's own quality verdict."""
    original_quality, original_reasons = q.quality, list(q.quality_reasons)
    validate_quote(q, prev)
    if original_quality != Quality.high:
        if original_quality == Quality.invalid or q.quality == Quality.invalid:
            q.quality = Quality.invalid
        elif original_quality == Quality.stale or q.quality == Quality.high:
            q.quality = original_quality
        q.quality_reasons = list(dict.fromkeys(original_reasons + q.quality_reasons))
    return q


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


def _mark_retained_rejected(
    q: Quote, reason: str, *, fresh_within: float, session: QuoteDisplaySession,
) -> None:
    """被拒包不改变原观测的年龄或暂停归因；拒绝仍另记覆盖率与摘要。"""
    stamp = q.data_timestamp
    if stamp is not None and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    normal_reasons = set(q.quality_reasons) <= {"market_closed", "off_session"}
    if (q.quality in {Quality.high, Quality.stale} and normal_reasons
            and (q.quality is Quality.high or bool(q.quality_reasons))
            and q.price is not None and q.price > 0 and stamp is not None
            and session.phase == "closed" and session.trade_day_result is not None
            and session.expected_source_date is not None and session.expected_source_after is not None
            and stamp.astimezone(BJ_TZ).date() == session.expected_source_date
            and session.expected_source_after <= stamp <= session.as_of):
        _mark_confirmed_closed(q)
        return
    if (q.quality is Quality.high and set(q.quality_reasons) <= {"off_session"}
            and q.price is not None and q.price > 0 and stamp is not None
            and 0 <= (session.as_of - stamp).total_seconds() <= fresh_within):
        return
    mark_stale(q, reason)


def _mark_confirmed_closed(q: Quote) -> None:
    """仅给正常行情落闭市上下文，不抹真实质量原因；缺源钟显式待核。"""
    if q.quality is not Quality.high or any(r != "off_session" for r in q.quality_reasons):
        return
    if q.data_timestamp is None:
        mark_stale(q, "source_time_unknown")
        return
    mark_stale(q, "market_closed")


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
        self._source_rejection_events: deque[dict] = deque(maxlen=_SOURCE_REJECTION_HISTORY_MAX)
        # 以共同请求目录为分母；首次缺席也计数，但不臆造报价。
        self.last_missing_indices: list[str] = []
        self.last_index_coverage: float | None = None
        self.last_index_rejections: dict[str, str] = {}
        # 休市状态沿触发（红线 2：休市日数据不得冒充实时）
        self._closed_marked = False
        self._domains = {"indices": RefreshDomain(), "quotes": RefreshDomain(requested=False)}
        self._domain_tasks: dict[str, asyncio.Task] = {}
        self._complete_generations = {"indices": 0, "quotes": 0}
        self._pending_domains: set[str] = set()
        self._publish_task: asyncio.Task | None = None
        self._publish_timer: asyncio.Handle | None = None
        self._publish_deadline: float | None = None
        self._running = False
        self._stopping = False
        self._last_publish_at: float | None = None

    # ---------- 刷新 ----------

    async def refresh(self) -> None:
        """启动填充与手工刷新共用在飞读取；取消一个等待者不取消共享读取。"""
        await asyncio.gather(self._read_domain("indices"), self._read_domain("quotes"))
        await self._flush_pending()

    async def _read_domain(self, domain: str) -> None:
        task = self._domain_tasks.get(domain)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh_domain(domain), name=f"quote-hub-{domain}")
            self._domain_tasks[domain] = task
        await asyncio.shield(task)

    async def _refresh_domain(self, domain: str) -> None:
        state = self._domains[domain]
        state.last_attempt = self.last_attempt = utcnow()
        state.started_at = asyncio.get_running_loop().time()
        watchlist = await self._poll_symbols_for_refresh() if domain == "quotes" else []
        state.requested = domain == "indices" or bool(watchlist)
        state.requested_symbols = tuple(INDEX_MARKETS) if domain == "indices" else tuple(watchlist)
        try:
            read_started = time.monotonic()
            if domain == "indices":
                rows = await self.provider.get_indices()
                self._accept_indices(rows)
            else:
                rows = await self.provider.get_quotes(watchlist) if watchlist else []
                self._accept_quotes(watchlist, rows)
            # A slow recovery must not hold already returned normal source rows.
            # Rejections retain the original value/clock; its own age and
            # confirmed pause determine quality independently of this packet.
            self._pending_domains.add(domain)
            if self._running:
                self._schedule_publish()
            recovered = await self._recover_regressions(domain, rows, state.requested_symbols, read_started)
            if recovered:
                if domain == "indices":
                    self._accept_indices(recovered, recovery=True)
                else:
                    self._accept_quotes(watchlist, recovered, recovery=True)
        except Exception as exc:  # ProviderError、网络错误等一律降级
            state.consecutive_failures += 1
            state.last_error, state.last_error_type = str(exc), type(exc).__name__
            state.succeeded = False
            age = (
                (utcnow() - state.last_success).total_seconds()
                if state.last_success is not None
                else float("inf")
            )
            if age >= self.stale_after:
                log.warning(
                    "quote %s refresh failed (%s): %s — data age %.0fs >= stale_after, marking stale",
                    domain, type(exc).__name__, exc, age,
                )
                cached = self.indices if domain == "indices" else self.quotes
                for q in cached.values():
                    mark_stale(q, "refresh_failed")
                self._pending_domains.add(domain)
            else:
                log.warning("quote %s refresh failed (%s): %s — keeping last good data", domain, type(exc).__name__, exc)
        else:
            state.last_success = utcnow()
            state.last_error = state.last_error_type = None
            state.consecutive_failures = 0
            state.succeeded = True
            self._pending_domains.add(domain)
        state.generation += 1
        self._sync_complete_state()
        if self._running:
            self._schedule_publish()

    async def _recover_regressions(
        self, domain: str, rows: list[Quote], requested: tuple[str, ...], read_started: float,
    ) -> list[Quote]:
        recover = getattr(self.provider, "recover_quote_regressions", None)
        if not callable(recover):
            return []
        method = "get_indices" if domain == "indices" else "get_quotes"
        current = self.indices if domain == "indices" else self.quotes
        rejections = self.last_index_rejections if domain == "indices" else self.last_quote_rejections
        rejected = {}
        for q in rows:
            if rejections.get(q.symbol) != "source_time_regress_ignored":
                continue
            prev = current.get(q.symbol)
            if (q.symbol not in requested or prev is None
                    or not q.market or q.market != prev.market
                    or (domain == "indices" and q.market != INDEX_MARKETS.get(q.symbol))):
                continue
            checked = _validate_source_quote(q.model_copy(deep=True), prev)
            if _rejected_source_observation(checked, prev) == "source_time_regress_ignored":
                rejected[q.symbol, q.market] = q.source
        if not rejected:
            return []

        def validate_recovery(q: Quote, key: tuple[str, str]) -> Quote | RecoveryRejection | None:
            # Do not let validate_quote's recomputation launder a stale provider cache.
            if q.quality in {Quality.invalid, Quality.stale} or q.data_timestamp is None:
                return None
            prev = current[key[0]]
            if q.market != prev.market:
                return None
            checked = _validate_source_quote(q.model_copy(deep=True), prev)
            rejection = _rejected_source_observation(checked, prev)
            if checked.price is None or rejection not in {None, "source_time_regress_ignored"}:
                return None
            now = utcnow()
            previous_time = prev.data_timestamp
            if previous_time.tzinfo is None:
                previous_time = previous_time.replace(tzinfo=timezone.utc)
            source_time = checked.data_timestamp
            if (source_time > now
                    or source_time.astimezone(BJ_TZ).date() != now.astimezone(BJ_TZ).date()
                    or checked.freshness(fresh_within=self.stale_after).state not in {"ready", "degraded"}):
                return None
            if source_time <= previous_time:
                return RecoveryRejection.not_newer
            return checked

        try:
            method_budget = self.provider.budget_for(method)
            replacements = await recover(
                method, rejected, deadline=read_started + min(method_budget, self.poll_interval),
                idle_limited=self.poll_interval < method_budget, validate=validate_recovery,
            )
        except Exception as exc:
            log.warning("source clock regression recovery unavailable (%s)", type(exc).__name__)
            return []
        # Another trusted update may have advanced current while a source was in
        # flight. Recheck now and never replay the normal rows or their history.
        return [checked for key, row in replacements.items()
                if key in rejected and isinstance(row, Quote)
                and isinstance(checked := validate_recovery(row, key), Quote)]

    def _sync_complete_state(self) -> None:
        self.consecutive_failures = max(s.consecutive_failures for s in self._domains.values())
        self.last_error = next((s.last_error for s in self._domains.values() if s.last_error), None)
        # 保留完整轮成功钟：两域都在上一完整轮之后成功才可推进，部分成功不背书整链。
        if all(s.succeeded and s.generation > self._complete_generations[d]
               for d, s in self._domains.items()):
            self.last_success_refresh = min(s.last_success for s in self._domains.values())
            self._complete_generations = {d: s.generation for d, s in self._domains.items()}

    def _schedule_publish(self) -> None:
        if self._stopping or not self._pending_domains:
            return
        if self._publish_task is not None and not self._publish_task.done():
            return
        loop = asyncio.get_running_loop()
        due = self._publish_due_at()
        if self._publish_timer is not None:
            # 另域已归或已失败：把等待同轮的 timer 提前，仍遵守原出版间隔。
            # uvloop may return an opaque Handle for an immediate call_at;
            # only its cancellation contract is shared with TimerHandle.
            if self._publish_deadline is not None and self._publish_deadline <= due:
                return
            self._publish_timer.cancel()
        self._publish_deadline = max(loop.time(), due)
        self._publish_timer = loop.call_at(self._publish_deadline, self._start_publish)

    def _publish_due_at(self) -> float:
        loop = asyncio.get_running_loop()
        interval = max(self.poll_interval, 5.0) if self._closed_marked else self.poll_interval
        due = self._last_publish_at + interval if self._last_publish_at is not None else loop.time()
        for domain, state in self._domains.items():
            task = self._domain_tasks.get(domain)
            if domain not in self._pending_domains and task is not None and not state.consecutive_failures:
                # 健康异步源的小幅抖动要同轮合批；未知悬挂最多等到原周期结束。
                # 已失败域不等待，不延长 Provider 预算，也不让其拖慢正常域轮询。
                if not task.done() and state.started_at is not None:
                    bound = state.started_at + self.poll_interval
                else:
                    # 另一内部循环的 tick 可能晚一个事件循环 turn 才开始。
                    # 仍用已归域本轮起点，不能为每次观察反复续期。
                    starts = [self._domains[d].started_at for d in self._pending_domains
                              if self._domains[d].started_at is not None]
                    bound = min(starts) + self.poll_interval if starts else loop.time()
                due = max(due, bound)
        return due

    def _start_publish(self, *, force: bool = False) -> None:
        self._publish_timer = None
        self._publish_deadline = None
        if self._stopping:
            return
        if self._pending_domains and (self._publish_task is None or self._publish_task.done()):
            if not force and self._publish_due_at() > asyncio.get_running_loop().time():
                self._schedule_publish()
                return
            self._publish_task = asyncio.create_task(self._publish_pending(), name="quote-hub-publish")

    async def _flush_pending(self) -> None:
        if self._publish_timer is not None:
            self._publish_timer.cancel()
            self._publish_timer = None
        self._publish_deadline = None
        self._start_publish(force=True)
        if self._publish_task is not None:
            await asyncio.shield(self._publish_task)

    async def _publish_pending(self) -> None:
        changed, self._pending_domains = self._pending_domains, set()
        try:
            await self._refresh_closed_state()
            if not self._closed_marked:
                self._broadcast("quotes", changed_domains=changed)
            self._last_publish_at = asyncio.get_running_loop().time()
        finally:
            self._publish_task = None
            if self._running:
                self._schedule_publish()

    def _accept_indices(self, new_indices: list[Quote], *, recovery: bool = False) -> None:
        closed = tc.market_open_state(tc.known_trade_days(), beijing_now()) == tc.MARKET_CLOSED
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
                self._record_source_rejection("indices", q, "source_identity_unexpected_ignored")
                continue
            if q.market != expected_market:
                rejected_indices[q.symbol] = "source_identity_market_mismatch_ignored"
                self._record_source_rejection("indices", q, "source_identity_market_mismatch_ignored")
                continue
            if counts[q.symbol, q.market] != 1:
                rejected_indices[q.symbol] = "source_identity_duplicate_ignored"
                self._record_source_rejection("indices", q, "source_identity_duplicate_ignored")
                continue
            candidate_indices.append(q)
        for q in candidate_indices:
            prev = self.indices.get(q.symbol)
            _validate_source_quote(q, prev)
            rejected = _rejected_source_observation(q, prev)
            if rejected is not None:
                rejected_indices[q.symbol] = rejected
                self._record_source_rejection("indices", q, rejected)
                continue
            if closed:
                _mark_confirmed_closed(q)
            self.indices[q.symbol] = q
            accepted_indices.append(q)
        if recovery:
            self._resolve_recovery_gaps("indices", {q.symbol for q in accepted_indices}, len(INDEX_MARKETS))
        else:
            self._mark_index_gaps(accepted_indices, rejected_indices)

    def _accept_quotes(self, watchlist: list[str], new_quotes: list[Quote], *, recovery: bool = False) -> None:
        closed = tc.market_open_state(tc.known_trade_days(), beijing_now()) == tc.MARKET_CLOSED
        quote_counts = Counter(q.symbol for q in new_quotes)
        returned_by_symbol = {q.symbol: q for q in new_quotes}
        accepted_by_symbol: dict[str, Quote] = {}
        rejected_quotes: dict[str, str] = {}
        for q in new_quotes:
            if q.symbol not in watchlist:
                rejected_quotes[q.symbol] = "source_identity_unrequested_ignored"
                self._record_source_rejection("quotes", q, "source_identity_unrequested_ignored")
            elif quote_counts[q.symbol] > 1:
                rejected_quotes[q.symbol] = "source_identity_duplicate_ignored"
                self._record_source_rejection("quotes", q, "source_identity_duplicate_ignored")
        for symbol in watchlist:
            if symbol in rejected_quotes:
                continue
            q = returned_by_symbol.get(symbol)
            if q is None:
                continue
            prev = self.quotes.get(symbol)
            _validate_source_quote(q, prev)
            rejected = _rejected_source_observation(q, prev)
            if rejected is not None:
                rejected_quotes[symbol] = rejected
                self._record_source_rejection("quotes", q, rejected)
                continue
            if closed:
                _mark_confirmed_closed(q)
            self.quotes[symbol] = q
            accepted_by_symbol[symbol] = q
            if q.price is not None and q.data_timestamp is not None:
                self.quote_history.append((q.symbol, q.data_timestamp, q.price))
        if recovery:
            self._resolve_recovery_gaps("quotes", set(accepted_by_symbol), len(watchlist))
        else:
            self._mark_batch_gaps(watchlist, accepted_by_symbol, rejected_quotes)

    def _resolve_recovery_gaps(self, domain: str, accepted: set[str], expected_count: int) -> None:
        """Only successful target updates can resolve the preceding batch's gaps."""
        if not accepted:
            return
        if domain == "indices":
            self.last_index_rejections = {s: reason for s, reason in self.last_index_rejections.items() if s not in accepted}
            self.last_missing_indices = [s for s in self.last_missing_indices if s not in accepted]
            self.last_index_coverage = (expected_count - len(self.last_missing_indices)) / expected_count
        else:
            self.last_quote_rejections = {s: reason for s, reason in self.last_quote_rejections.items() if s not in accepted}
            self.last_missing_symbols = [s for s in self.last_missing_symbols if s not in accepted]
            self.last_batch_coverage = (expected_count - len(self.last_missing_symbols)) / expected_count if expected_count else None

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
        日历不可用 / 为空 → 复用可信日历缓存；未覆盖今日仍是 unknown。
        """
        try:
            days = await tc.trading_days(self.provider)
        except Exception as exc:
            log.debug("market-open check unavailable: %s", exc)
            days = None
        now = beijing_now()  # 等待日历可跨竞价/午休边界，必须用完成时刻裁决。
        # 空/异常日历仍可读取既有可信缓存；不覆盖今日时保留 unknown。
        # 不能沿用旧 closed 跳过广播又清 pending；阶段须按当次时刻重新裁决。
        state = tc.market_open_state(days or tc.known_trade_days(), now)
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
        """拒绝仍算本轮缺失；保留值按自己的源钟/阶段定质，原因另记摘要。"""
        rejected = rejected or {}
        session = self._display_session()
        self.last_index_rejections = dict(sorted(rejected.items()))
        missing = sorted(INDEX_MARKETS.keys() - {q.symbol for q in returned})
        for symbol in missing:
            cached = self.indices.get(symbol)
            if cached is not None:
                reason = rejected.get(symbol)
                if reason:
                    _mark_retained_rejected(cached, reason, fresh_within=self.stale_after, session=session)
                else:
                    mark_stale(cached, "index_batch_missing")
        if missing != self.last_missing_indices:
            if missing:
                log.warning("指数本轮可接纳行情缺失（保留缓存，按原源钟/阶段定质）：%s", ", ".join(missing))
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

    def _record_source_rejection(self, domain: str, quote: Quote, reason: str) -> None:
        """Keep only rejected Hub admission metadata, separate from current facts."""
        current = (self.indices if domain == "indices" else self.quotes).get(quote.symbol)
        self._source_rejection_events.append({
            "domain": domain, "identity": {"symbol": quote.symbol, "market": quote.market},
            "source": quote.source,
            "observed_source_at": quote.data_timestamp.isoformat() if quote.data_timestamp is not None else None,
            "current_source_at": current.data_timestamp.isoformat() if current is not None and current.data_timestamp is not None else None,
            "received_at": quote.received_at.isoformat() if quote.received_at is not None else None,
            "reason": reason, "observed_at": utcnow().isoformat(),
        })

    def source_rejection_observations(self) -> dict:
        """Maintenance-only newest-first snapshots; transport/recovery prefilters are outside this scope."""
        return {"scope": "hub_admission", "limit": _SOURCE_REJECTION_HISTORY_MAX,
                "events": deepcopy(list(reversed(self._source_rejection_events)))}

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

        已返回但被拒的包同样记缺项；其原current自身仍新鲜或满足暂停尾点时，
        保留原状态。拒绝原因另记摘要，不借新包接收钟延续旧值。
        """
        rejected = rejected or {}
        session = self._display_session()
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
                    _mark_retained_rejected(cached, reason, fresh_within=self.stale_after, session=session)
                else:
                    mark_stale(cached, "batch_missing")

        # 只在**缺失集变化**时留痕，避免 1Hz 轮询把日志刷成噪声
        if missing != prev_missing:
            if missing:
                log.warning(
                    "批量行情部分缺失：%d/%d 只未接纳（覆盖率 %.0f%%），保留缓存与逐项源钟状态：%s%s",
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

    async def _poll_symbols_for_refresh(self) -> list[str]:
        # The repository owns its Session. Only its blocking read enters a worker;
        # fallback cache access and current subscriptions stay on the event loop.
        # The existing quotes-domain singleflight owns this read until it drains.
        read = asyncio.create_task(asyncio.to_thread(self.get_watchlist))
        try:
            watchlist = await asyncio.shield(read)
        except asyncio.CancelledError:
            while not read.done():
                try:
                    await asyncio.shield(read)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not read.cancelled():
                read.exception()
            raise
        except Exception:
            log.exception("watchlist lookup failed; keeping previous watchlist")
            watchlist = list(self.quotes.keys())
        return self._merge_poll_symbols(watchlist)

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
        return self._merge_poll_symbols(self._safe_watchlist())

    def _merge_poll_symbols(self, watchlist: list[str]) -> list[str]:
        symbols = set(watchlist)
        for sub in self._subscribers:
            if sub.symbols:
                symbols.update(s for s in sub.symbols if len(s) == 6 and s.isdigit())
        return sorted(symbols)

    def _mark_all_stale(self, reason: str = "refresh_failed") -> None:
        for cached in (self.indices, self.quotes):
            for q in cached.values():
                if reason == "market_closed":
                    _mark_confirmed_closed(q)
                else:
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
            partial = [s.last_success for s in self._domains.values() if s.last_success is not None]
            if partial:
                return Freshness.degraded(
                    reason="行情链仅部分读取域成功，完整轮尚未就绪", as_of=min(partial), source=source,
                )
            return Freshness.unavailable(reason="行情链尚未成功刷新过", source=source)
        f = Freshness.from_age(
            as_of=self.last_success_refresh, fresh_within=self.stale_after,
            source=source, missing_reason="无成功刷新时间，无法判定新鲜度",
        )
        if self._closed_marked and f.state == "ready":
            # 休市标记不证明源日期就是最近交易日，更不能冒充实时（红线 2）。
            return Freshness.stale(
                as_of=f.as_of, age_seconds=f.age_seconds, source=source,
                reason="已标记休市：保留源报价，源交易日需单独核对，不冒充实时",
            )
        return f

    def refresh_domains(self) -> dict:
        """纯读两域诊断：完成钟、源钟、覆盖率与在飞状态分开，不冒充 HTTP 次数。"""
        result = {}
        for domain, state in self._domains.items():
            cached = self.indices if domain == "indices" else self.quotes
            rows = [cached[s] for s in state.requested_symbols if s in cached]
            stamps = [q.data_timestamp for q in rows if q.data_timestamp is not None]
            source_clock = min(stamps) if stamps else None
            source_states = Counter(q.freshness(fresh_within=self.stale_after).state for q in rows)
            if state.last_success is None:
                fresh = Freshness.unavailable(reason="该行情读取域尚未成功", source=getattr(self.provider, "name", None))
            else:
                fresh = Freshness.from_age(as_of=state.last_success, fresh_within=self.stale_after,
                                           source=getattr(self.provider, "name", None))
            task = self._domain_tasks.get(domain)
            result[domain] = {
                "requested": state.requested,
                "in_flight": task is not None and not task.done(),
                "last_attempt": state.last_attempt.isoformat() if state.last_attempt else None,
                "last_success_refresh": state.last_success.isoformat() if state.last_success else None,
                "last_error": state.last_error,
                "last_error_type": state.last_error_type,
                "consecutive_failures": state.consecutive_failures,
                "freshness": fresh.model_dump(mode="json"),
                "source_oldest_as_of": source_clock.isoformat() if source_clock else None,
                "source_stale_count": source_states["stale"],
                "source_degraded_count": source_states["degraded"],
                "source_time_unknown_count": sum(q.data_timestamp is None for q in rows),
                "missing_count": len(self.last_missing_indices if domain == "indices" else self.last_missing_symbols),
                "rejected_count": len(self.last_index_rejections if domain == "indices" else self.last_quote_rejections),
                "coverage": self.last_index_coverage if domain == "indices" else self.last_batch_coverage,
            }
        return result

    def is_stale(self) -> bool:
        return not self.freshness().is_fresh()

    # ---------- 读取 ----------

    def _display_session(self) -> QuoteDisplaySession:
        """现有日历与北京时刻的只读展示上下文；不取数、不影响交易资格。"""
        now = beijing_now()
        days = tc.known_trade_days()
        trade_day = tc.is_trade_day_on(now.date(), days)
        minute = now.hour * 60 + now.minute
        if trade_day is None:
            phase = "unknown"
        elif not trade_day:
            phase = "closed"
        elif (9 * 60 + 15 <= minute < 9 * 60 + 25
              or 14 * 60 + 57 <= minute < 15 * 60):
            phase = "auction"
        elif 9 * 60 + 25 <= minute < 9 * 60 + 30:
            phase = "pre_open_gap"
        elif (9 * 60 + 30 <= minute <= 11 * 60 + 30
              or 13 * 60 <= minute < 14 * 60 + 57):
            phase = "continuous"
        else:
            phase = "closed"
        # 尾随日历在周末通常只到周五。只有周五确实在已知日历中才补足周末覆盖；
        # 更早的旧日历（包括无法确认的假期）保持未知，不能随便取最后一天当“最新”。
        weekend_covered = bool(now.weekday() >= 5 and days and
                               days[-1] == now.date() - timedelta(days=now.weekday() - 4))
        covered = bool(days and (days[-1] >= now.date() or weekend_covered))
        expected = None
        if covered and trade_day is not None:
            expected = (now.date() if trade_day and minute >= 9 * 60 + 15
                        else tc.prev_trade_date(days, now.date()))
        tail = None
        if expected is not None and phase == "closed":
            # 日期相同仍可能是上午就停止更新；沿用原 stale_after 容差核对暂停前尾点。
            hour = 11 if trade_day and 11 * 60 + 30 < minute < 13 * 60 else 15
            end_minute = 30 if hour == 11 else 0
            tail = now.replace(year=expected.year, month=expected.month, day=expected.day,
                               hour=hour, minute=end_minute, second=0, microsecond=0)
            tail -= timedelta(seconds=self.stale_after)
        return QuoteDisplaySession(phase=phase, trade_date=now.date(), as_of=now,
                                   trade_day_result=trade_day, expected_source_date=expected,
                                   expected_source_after=tail)

    def _visible_quote(self, quote: Quote, session: QuoteDisplaySession | None = None) -> Quote:
        """返回消费侧视图；缓存未被继续轮询时也不能无限保持 high。

        current cache 保存最后被接纳的事实，不因为时间过去就原地篡改。
        但 REST/WS 读取必须同时反映 source event time 的年龄；超过
        stale_after 时返回 stale 副本，直到下一条可信观测推进 current。
        """
        # Quote 的行情数值与源时点均为标量/不可变值；仅复制可变展示成员，
        # 避免每秒为每条正常行情递归深复制全部字段。
        visible = quote.model_copy(update={
            "quality_reasons": list(quote.quality_reasons),
            "display_session": (session or self._display_session()).model_copy(),
        })
        if (quote.quality not in (Quality.stale, Quality.invalid)
                and quote.freshness(fresh_within=self.stale_after).state == "stale"):
            if quote.quality in (Quality.low, Quality.medium):
                source_reason = "source_quality_low" if quote.quality == Quality.low else "source_quality_medium"
                visible.quality = Quality.stale
                visible.quality_reasons = list(dict.fromkeys([
                    *visible.quality_reasons, source_reason, "quote_age_exceeded",
                ]))
            else:
                mark_stale(visible, "quote_age_exceeded")
        return visible

    def get_indices(self) -> list[Quote]:
        session = self._display_session()
        return [self._visible_quote(q, session) for q in self.indices.values()]

    def present_quote(self, quote: Quote, symbol: str) -> Quote:
        """校验单次读取身份并复用展示上下文；不写入Hub或刷新健康事实。"""
        requested = symbol.lower()
        prefixed = len(requested) == 8 and requested[:2] in {"sh", "sz", "bj"}
        code = requested[2:] if prefixed else requested
        if len(code) != 6 or not code.isdigit():
            raise ValueError("invalid requested quote identity")
        market = (requested[:2].upper() if prefixed else
                  "BJ" if code.startswith(("4", "8", "920")) else
                  "SH" if code.startswith(("6", "9", "5")) else "SZ")
        if quote.symbol.lower() not in {code, requested} or quote.market != market:
            raise ValueError("source quote identity mismatch")
        previous = (self.indices.get(code) if requested in INDEX_SUBSCRIPTIONS
                    else self.quotes.get(code))
        if previous is not None and previous.market != market:
            previous = None
        # validator验证裸代码；不能把指数前缀当成非法股票代码，也不能改源对象。
        row = quote.model_copy(update={"symbol": code, "quality_reasons": list(quote.quality_reasons)})
        original_quality, original_reasons = row.quality, list(row.quality_reasons)
        validate_quote(row, previous)
        if original_quality != Quality.high:
            if original_quality == Quality.invalid or row.quality == Quality.invalid:
                row.quality = Quality.invalid
            elif original_quality == Quality.stale or row.quality == Quality.high:
                row.quality = original_quality
            row.quality_reasons = list(dict.fromkeys(original_reasons + row.quality_reasons))
        rejection = _rejected_source_observation(row, previous)
        session = self._display_session()
        if previous is not None and rejection is not None:
            row = previous.model_copy(update={"quality_reasons": list(previous.quality_reasons)})
            _mark_retained_rejected(row, rejection, fresh_within=self.stale_after, session=session)
            if original_quality != Quality.high and row.quality_reasons == [rejection]:
                row.quality_reasons = list(dict.fromkeys(row.quality_reasons + original_reasons))
        if session.phase == "closed" and session.trade_day_result is not None:
            _mark_confirmed_closed(row)
        if row.quality == Quality.high and row.data_timestamp is None:
            mark_stale(row, "source_time_unknown")
        return self._visible_quote(row.model_copy(update={"symbol": symbol}), session)

    def get_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        session = self._display_session()
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
                    out.append(self._visible_quote(q, session))
            return out
        return [self._visible_quote(q, session) for q in self.quotes.values()]

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---------- 订阅 ----------

    def subscribe(self, symbols: set[str] | None = None, *, subscription_id: int = 0) -> asyncio.Queue:
        """注册订阅者。返回的队列终身复用——**改订阅集用 update_symbols，
        绝不 unsubscribe+subscribe 换新队列**：writer 正 parked 在旧队列的
        get() 上，换队列后 writer 永远等在孤儿队列（2026-09-01 实测事故：
        前端 loadBase 触发 subscribe → 推送静默死亡 → 前端 32s 自愈重连 →
        用户体感"约 30 秒才更新一次"）。符号集存单元格以便原地更新。

        队列**有界**（`SUBSCRIBER_QUEUE_MAX`，R24）：旧实现无界，消费者卡住时
        积压无上限且投递侧无法察觉。有界后投递走 `offer()`（丢最旧、保最新）。
        """
        subscription_id = validate_subscription_id(subscription_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.append(Subscriber(symbols=symbols, queue=queue, subscription_id=subscription_id))
        return queue

    def update_symbols(self, queue: asyncio.Queue, symbols: set[str] | None, *, subscription_id: int = 0) -> bool:
        """原地更新订阅集（同一队列，writer 无感）。队列不存在返回 False。"""
        subscription_id = validate_subscription_id(subscription_id)
        for sub in self._subscribers:
            if sub.queue is queue:
                sub.symbols = symbols
                sub.subscription_id = subscription_id
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

    def _broadcast(self, msg_type: str, *, changed_domains: set[str] | None = None) -> None:
        seq = self.next_seq()
        ts = utcnow().isoformat()
        domain_details = self.refresh_domains()
        for sub in self._subscribers:
            symbols = sub.symbols
            index_subscribed = symbols is None or any(s.lower() in INDEX_SUBSCRIPTIONS for s in symbols)
            quotes_subscribed = symbols is None or any(len(s) == 6 and s.isdigit() for s in symbols)
            if changed_domains is not None and not (
                (index_subscribed and "indices" in changed_domains)
                or (quotes_subscribed and "quotes" in changed_domains)
            ):
                continue
            # 帧仍包含该订阅的完整缓存；前端替换快照，不能仅投递成功域的增量。
            payload = self.get_quotes(sorted(symbols) if symbols is not None else None)
            has_rejection = bool((quotes_subscribed and self.last_quote_rejections)
                                 or (index_subscribed and self.last_index_rejections))
            incomplete_index = index_subscribed and bool(self.last_missing_indices)
            incomplete_quotes = quotes_subscribed and bool(
                self.last_missing_symbols if symbols is None else symbols.intersection(self.last_missing_symbols)
            )
            unavailable = any(
                wanted and state.requested and state.last_attempt is not None and state.last_success is None
                for wanted, state in ((index_subscribed, self._domains["indices"]),
                                      (quotes_subscribed, self._domains["quotes"]))
            )
            if not payload and not (incomplete_index or incomplete_quotes or unavailable or has_rejection):
                continue
            visible_stale = any(q.quality in (Quality.stale, Quality.invalid) for q in payload)
            frame_type = "stale" if visible_stale or incomplete_index or incomplete_quotes or unavailable else msg_type
            dropped = offer(
                sub.queue,
                {
                    "type": frame_type,
                    "subscription_id": sub.subscription_id,
                    "seq": seq,
                    "ts": ts,
                    "data": [q.model_dump(mode="json") for q in payload],
                    "meta": {
                        "index_batch": self.index_batch(),
                        "source_rejections": self.source_rejections(),
                        "refresh_domains": domain_details,
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

    async def _run_domain(self, domain: str) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            await self._read_domain(domain)
            elapsed = asyncio.get_running_loop().time() - started
            delay = self.poll_interval
            failures = self._domains[domain].consecutive_failures
            if failures > 0:
                delay = min(self.poll_interval * (2 ** min(failures, 4)), 60.0)
                log.info("provider %s degraded, next refresh in %.0fs", domain, delay)
            elif self._closed_marked:
                # 休市/午休数据静止：降频到 5s 保活（省 Provider 配额/流量），
                # 恢复检测延迟 ≤5s；竞价与连续竞价保持秒级节奏。
                delay = max(delay, 5.0)
            # 固定节奏：扣除本轮刷新耗时，保证推送周期 = poll_interval 而非
            # poll_interval + 网络耗时（1s 档位下串行耗时的稀释不可忽略）
            await asyncio.sleep(max(0.0, delay - elapsed))

    async def run(self) -> None:
        """注册表仍只有一个 Hub owner；两域独立轮询，共用原轮频、退避与广播批次。"""
        if self._running:
            raise RuntimeError("QuoteHub poller already running")
        self._running = True
        self._stopping = False
        loops = [asyncio.create_task(self._run_domain(d), name=f"quote-hub-loop-{d}")
                 for d in ("indices", "quotes")]
        try:
            await asyncio.gather(*loops)
        finally:
            # Keep the owner occupied while its worker drains, but do not let
            # a cancelled publisher's finally create a new unowned publisher.
            self._stopping = True
            if self._publish_timer is not None:
                self._publish_timer.cancel()
                self._publish_timer = None
            self._publish_deadline = None
            owned = set(loops) | set(self._domain_tasks.values())
            if self._publish_task is not None:
                owned.add(self._publish_task)
            for task in owned:
                task.cancel()
            drained = asyncio.gather(*owned, return_exceptions=True)
            while not drained.done():
                try:
                    await asyncio.shield(drained)
                except asyncio.CancelledError:
                    continue
            drained.result()
            self._domain_tasks.clear()
            self._publish_task = None
            self._pending_domains.clear()
            self._last_publish_at = None
            self._running = False
            self._stopping = False
