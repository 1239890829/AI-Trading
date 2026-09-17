"""Provider 链：主源失败自动切换备源（full.md §2.2 自动降级）。

- 逐方法 failover：行情走腾讯→新浪→东财，涨停池/龙虎榜走东财。
- 切换记录 switch_log（数据源切换日志），供 /api/health 展示。
- 秒级方法（REALTIME_METHODS）走"主源宽限 + 备源对冲"：主源超时未应答时
  备源并行起跑，故障周期不吃一整次主源超时（P1-A，realtime-broker §3.2 方案 B）。
- 全链失败抛 ProviderError → QuoteHub 标记 stale，绝不伪造实时数据。
- **请求级总预算（S2-3）**：failover 必须在 `REQUEST_BUDGET_SECONDS` 内收口。此前各源
  各自带 HTTP 超时（腾讯/东财/新浪 5s、ths 8s）却**无总预算**，全挂时单个请求悬停
  20~30s（= 各源超时之和），而 FastAPI 侧没有请求超时，前端只能干等、连接与事件循环被占住。
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import logging
from datetime import date, datetime

from app.data_providers.eastmoney import ProviderError

log = logging.getLogger(__name__)

#: 连续失败多少次后熔断（冷却期内不再请求该源的该方法）
FAILURE_THRESHOLD = 3
#: 熔断冷却时长（秒）
COOLDOWN_SECONDS = 60.0
#: 秒级方法主源宽限（秒）：正常应答 ~60ms 远小于此，主源超过宽限仍未归即
#: 并行打备源（先归者得，主源被放弃记一次失败——连续挂起 3 次同样进熔断）。
HEDGE_DELAY = 0.20

#: 请求级总预算（秒）：某方法一次调用**跨所有源加总**的时长上限（S2-3）。
#: 取值需容纳"最慢的正常源"（ths 单次 HTTP 超时 8s）再留一次换源的余量。
REQUEST_BUDGET_SECONDS = 12.0
#: 秒级方法的预算（秒）：Hub 以 1Hz 轮询这些方法，一次卡顿要吃掉好几个 tick，
#: 等不起总预算——正常情况下腾讯 ~60ms 应答，4s 已是极宽裕的上界。
REALTIME_BUDGET_SECONDS = 4.0


class BudgetExhausted(TimeoutError):
    """单源调用被**请求级预算**掐断（与"源自己超时"区分开，便于归因与记账）。"""


#: 秒级实时方法：Hub 以 1Hz 轮询这些方法，走 realtime_rank 排序——
#: 免费高频源（腾讯）优先，付费/慢源（ths 配额+8s 超时）不挡在秒级链路上。
#: 其余方法（K线/龙虎榜/财务等低频）维持构造顺序不变。
REALTIME_METHODS = frozenset(
    {"get_quotes", "get_indices", "get_quote", "get_order_book", "get_trades"}
)

_ROUTED = (
    "get_indices", "get_quotes", "get_quote", "get_kline", "get_order_book",
    "get_trades", "get_limit_up_pool", "get_longhu_records", "search", "get_board_rankings",
    "get_longhu_detail", "get_longhu_history", "get_capital_flow", "get_financials",
    "get_limit_break_pool", "get_trading_days", "get_announcements", "get_news", "get_company_profile",
)

#: 观测范围 = _ROUTED + 未列入 _ROUTED 但可路由的方法（get_minute_line、ths 专属）。
#: 仅用于 /api/system/providers 展示"每个源实现了哪些方法"，不参与路由选择。
_OBSERVABLE_METHODS = _ROUTED + (
    "get_limit_down_pool", "get_minute_line", "get_hot_stock_list", "get_hot_stock_list_history",
    "get_skyrocket_list", "get_hot_rank_trend",
    "get_auction_snapshot", "get_auction_benchmark", "get_adjustment_events",
    "get_anomaly_list", "get_anomaly_stock",
)


class CompositeProvider:
    realtime = True

    def __init__(self, providers: list):
        if not providers:
            raise ValueError("CompositeProvider needs at least one provider")
        self.providers = providers
        self.switch_log: list[str] = []
        self._last_good: dict[str, str] = {}
        self._failures: dict[tuple[str, str], int] = {}   # (method, provider) → 连续失败次数
        self._cooldown_until: dict[tuple[str, str], float] = {}  # (method, provider) → 解禁时间戳

    # ---- 熔断（circuit breaker）----

    def _in_cooldown(self, method: str, provider: str) -> bool:
        until = self._cooldown_until.get((method, provider))
        return until is not None and time.monotonic() < until

    def _cooldown_left(self, method: str, provider: str) -> float:
        until = self._cooldown_until.get((method, provider)) or 0.0
        return max(0.0, until - time.monotonic())

    def _record_failure(self, method: str, provider: str) -> None:
        key = (method, provider)
        n = self._failures.get(key, 0) + 1
        self._failures[key] = n
        if n >= FAILURE_THRESHOLD:
            self._cooldown_until[key] = time.monotonic() + COOLDOWN_SECONDS
            log.warning(
                "provider %s 的 %s 连续失败 %d 次 → 熔断 %.0fs", provider, method, n, COOLDOWN_SECONDS
            )

    def _record_success(self, method: str, provider: str) -> None:
        self._failures.pop((method, provider), None)
        self._cooldown_until.pop((method, provider), None)

    def breaker_state(self) -> dict:
        """熔断状态快照（供 /api/system 观测：哪些源被判死了）。"""
        return {
            f"{m}@{p}": {"failures": n, "cooldown_left": round(self._cooldown_left(m, p), 1)}
            for (m, p), n in self._failures.items()
        }

    def provider_health(self) -> dict:
        """provider 链全景快照（GET /api/system/providers，P0-A 可观测）。

        breaker_state() 只列出有失败记录的 (方法, 源)，且看不出"降级发生了没"——
        东财间歇断连、腾讯 WAF 封禁这类降级过去只能靠事后排查发现。这里补齐：
        - 每个源实现了哪些方法、秒级优先级（realtime_rank）
        - 熔断三态：open（冷却中，请求正在被跳过）/ watch（有失败未达阈值）/
          closed（无记录，键不存在）
        - 每个方法最后由哪个源成功服务（last_good）+ 最近切换记录
        """
        providers = [
            {
                "name": p.name,
                "realtime": bool(getattr(p, "realtime", False)),
                "realtime_rank": getattr(p, "realtime_rank", None),
                "methods": [m for m in _OBSERVABLE_METHODS if hasattr(p, m)],
            }
            for p in self.providers
        ]
        breakers = {}
        for (m, p), n in sorted(self._failures.items()):
            left = self._cooldown_left(m, p)
            breakers[f"{m}@{p}"] = {
                "failures": n,
                "cooldown_left": round(left, 1),
                "state": "open" if left > 0 else "watch",
            }
        return {
            "chain": self.name,
            "providers": providers,
            "breakers": breakers,
            "last_good": dict(self._last_good),
            "switch_log": list(self.switch_log[-20:]),
            # 预算值可观测（S2-3）：否则"为什么这个请求 4s 就失败了"只能靠读代码
            "budget_seconds": {
                "realtime": REALTIME_BUDGET_SECONDS,
                "default": REQUEST_BUDGET_SECONDS,
            },
        }

    @property
    def name(self) -> str:
        return "chain(" + "→".join(p.name for p in self.providers) + ")"

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()

    def budget_for(self, method: str) -> float:
        """该方法一次调用的**总**预算（秒）。秒级方法更紧（Hub 1Hz 轮询等不起）。"""
        return (
            REALTIME_BUDGET_SECONDS
            if method in REALTIME_METHODS
            else REQUEST_BUDGET_SECONDS
        )

    def _pick(self, method: str):
        picked = [p for p in self.providers if hasattr(p, method)]
        if method in REALTIME_METHODS:
            # 稳定排序：rank 相同保持构造顺序，因此非腾讯源之间的先后不变
            picked.sort(key=lambda p: getattr(p, "realtime_rank", 100))
        return picked

    async def _call(self, method: str, *args):
        errors: list[str] = []
        live: list = []
        for p in self._pick(method):
            # 熔断：该源在此方法上连续失败过多 → 冷却期内直接跳过，
            # 不再把请求打给一个已知挂掉的源（雪崩时尤其重要：
            # 2026-08-31 腾讯 WAF 封禁期间，每个 K 线请求都在往墙上撞）
            if self._in_cooldown(method, p.name):
                errors.append(f"{p.name}: 熔断冷却中（{self._cooldown_left(method, p.name):.0f}s）")
                continue
            live.append(p)
        if not live:
            raise ProviderError(f"all providers failed for {method}: " + "; ".join(errors))
        # S2-3：本次调用的硬上界——**跨源共享**（不是每源一份配额），
        # 否则 N 个源各花满 N 份预算，串行总时长照样是各源超时之和。
        deadline = time.monotonic() + self.budget_for(method)
        if method in REALTIME_METHODS and len(live) >= 2:
            return await self._call_hedged(method, args, live, errors, deadline)
        return await self._call_serial(method, args, live, errors, deadline)

    @staticmethod
    def _is_empty(result) -> bool:
        # 空结果同样计入失败：K 线/池子返回空往往是源已异常的前兆
        return result is None or (isinstance(result, (list, tuple)) and len(result) == 0)

    async def _attempt(
        self, method: str, p, args, deadline: float | None = None
    ) -> tuple[object, object, Exception | None]:
        """单源单次调用，绝不抛（异常作为第三元组项返回，便于并发收割）。

        `deadline`（S2-3）为本次调用的绝对时刻上界：源自己没超时也要被掐，
        并记 `BudgetExhausted`——它比普通异常多一层信息（"不是源的问题，是整体等不起"），
        且同样进熔断计数：被掐掉的源按**失败**记账，不会无限挂账。
        """
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                return p, None, BudgetExhausted("请求预算已耗尽（未发起）")
        else:
            left = None
        try:
            call = getattr(p, method)(*args)
            if left is None:
                return p, await call, None
            return p, await asyncio.wait_for(call, timeout=left), None
        except (asyncio.TimeoutError, TimeoutError):
            return p, None, BudgetExhausted(f"请求预算耗尽（剩余 {left:.2f}s 未应答）")
        except Exception as exc:
            log.warning("provider %s %s failed: %s", p.name, method, exc)
            return p, None, exc

    def _note_failure(self, method: str, name: str, exc: Exception | None, errors: list[str]) -> None:
        errors.append(f"{name}: {'empty' if exc is None else exc}")
        self._record_failure(method, name)

    def _settle_last_good(self, method: str, name: str) -> None:
        self._record_success(method, name)
        if self._last_good.get(method) != name:
            if method in self._last_good:
                msg = f"{method}: {self._last_good[method]} -> {name}"
                self.switch_log.append(msg)
                log.info("provider switched: %s", msg)
            self._last_good[method] = name

    async def _call_serial(
        self, method: str, args: tuple, live: list, errors: list[str], deadline: float | None
    ):
        """按序试源，全程受同一个 `deadline` 约束。

        `deadline` **刻意不给默认值**（S2-3）：本次实现时正是"参数加了默认值、
        却漏改一处调用点"⇒ 那条路径整条绕过预算（实时对冲回落的串行段静默跑满
        30s，测试才抓到）。必填参数让漏传在开发期就报 TypeError，而不是静默失效。
        """
        for p in live:
            if deadline is not None and time.monotonic() >= deadline:
                # 预算已光：剩下的源连问都不问，也**不记它们失败**——
                # 没发起过请求不是它们的责任，记了会误伤熔断统计。
                errors.append(f"{p.name}: 请求预算已耗尽，未发起")
                break
            _, result, exc = await self._attempt(method, p, args, deadline)
            if exc is None and not self._is_empty(result):
                self._settle_last_good(method, p.name)
                return result
            self._note_failure(method, p.name, exc, errors)
            if isinstance(exc, BudgetExhausted):
                # 本源被预算掐断 ⇒ 剩余额度已归零，继续循环只会得到一串"未发起"
                break
        raise ProviderError(f"all providers failed for {method}: " + "; ".join(errors))

    async def _call_hedged(
        self, method: str, args: tuple, live: list, errors: list[str], deadline: float | None
    ):
        """秒级方法对冲（P1-A）：主源宽限 HEDGE_DELAY，超时并行打备源，先归者得。

        - 主源宽限期内应答：成功用之（正常路径，备源零流量、行为与串行一致）；
          快速失败/空 → 记账后串行走剩余源（主源活着只是这把没给数据，无须并发）。
        - 主源超宽限：备源起跑，两路先归且可用者得；备源抢先时放弃主源
          （cancel + 记一次失败，连续挂起 3 次同样进熔断，不会无限挂账）。
        - 两路都受同一 `deadline` 约束（S2-3）：并发只缩短等待，不取消上界。
        """
        primary, backup = live[0], live[1]
        p_task = asyncio.create_task(self._attempt(method, primary, args, deadline))
        done, _ = await asyncio.wait({p_task}, timeout=HEDGE_DELAY)
        if done:
            _, result, exc = p_task.result()
            if exc is None and not self._is_empty(result):
                self._settle_last_good(method, primary.name)
                return result
            self._note_failure(method, primary.name, exc, errors)
            return await self._call_serial(method, args, live[1:], errors, deadline)

        b_task = asyncio.create_task(self._attempt(method, backup, args, deadline))
        tasks = {p_task: primary, b_task: backup}
        while tasks:
            done, _ = await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                p = tasks.pop(t)
                _, result, exc = t.result()
                if exc is None and not self._is_empty(result):
                    # 另一路仍在飞：若它是主源（被备源超车），放弃并记一次失败
                    # （连续挂起 3 次同样进熔断，不会无限挂账）；若是备源被超车，
                    # cancel 即可，不算它的失败。
                    other = p_task if t is b_task else b_task
                    if not other.done():
                        other.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await other
                        if other is p_task:
                            self._note_failure(
                                method, primary.name,
                                TimeoutError(f"超 {HEDGE_DELAY:.2f}s 未应答（备源已接手）"), errors,
                            )
                    self._settle_last_good(method, p.name)
                    return result
                self._note_failure(method, p.name, exc, errors)
            if tasks == {p_task: primary}:
                # 只剩挂起的主源：备源已败、主源远超宽限仍未归——放弃，串行下沉
                p_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await p_task
                self._note_failure(
                    method, primary.name,
                    TimeoutError(f"超 {HEDGE_DELAY:.2f}s 未应答（备源未接住）"), errors,
                )
                break
        # 两路都不可用：继续串行剩余源（共享同一 deadline）
        return await self._call_serial(method, args, live[2:], errors, deadline)

    async def get_indices(self) -> list:
        return await self._call("get_indices")

    async def get_quotes(self, symbols: list[str]) -> list:
        return await self._call("get_quotes", symbols)

    async def get_quote(self, symbol: str):
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_kline(self, symbol: str, timeframe: str, start: datetime | None = None, end: datetime | None = None) -> list:
        return await self._call("get_kline", symbol, timeframe, start, end)

    async def get_order_book(self, symbol: str):
        return await self._call("get_order_book", symbol)

    async def get_trades(self, symbol: str) -> list:
        return await self._call("get_trades", symbol)

    async def get_limit_up_pool(self, trade_date: date) -> list:
        return await self._call("get_limit_up_pool", trade_date)

    async def get_limit_down_pool(self, trade_date: date) -> list:
        """跌停池专用 failover（2026-09-09 修复「all providers failed: empty」误报）。

        语义与涨停池不同：**空池是合法状态**——强势日（如 09-08 糖业涨停潮 73 家
        涨停）跌停 0 家是真实行情，东财上游 rc:0 + tc:0 + pool:[] 即明确证据。
        通用 _call 的「空=失败累计」会把三源一致的合法空池误报为
        "all providers failed"，还会把源打进熔断。

        规则：未实现的方法不入链；源负责校验完整响应，只有列表（含 []）才算成功。
        多源仍取第一个非空，否则使用首个合法空池；全部失败或预算耗尽均抛错。
        与通用链共享 deadline、熔断与成功来源统计，合法空池也能恢复健康状态。
        """
        method = "get_limit_down_pool"
        deadline = time.monotonic() + self.budget_for(method)
        errors: list[str] = []
        empty_source = None
        for p in self._pick(method):
            if self._in_cooldown(method, p.name):
                errors.append(f"{p.name}: 熔断冷却中（{self._cooldown_left(method, p.name):.0f}s）")
                continue
            if time.monotonic() >= deadline:
                raise ProviderError(f"{method}: 请求预算已耗尽，未发起 {p.name}")
            _, rows, exc = await self._attempt(method, p, (trade_date,), deadline)
            if exc is None and isinstance(rows, list):
                self._record_success(method, p.name)
                if rows:
                    self._settle_last_good(method, p.name)
                    return rows
                empty_source = empty_source or p.name
                continue
            self._note_failure(method, p.name, exc or ProviderError("invalid pool result"), errors)
            if isinstance(exc, BudgetExhausted):
                raise ProviderError(f"{method}: {exc}")
        if empty_source is not None:
            self._settle_last_good(method, empty_source)
            return []
        raise ProviderError(f"all providers failed for {method}: {'; '.join(errors)}")

    async def get_longhu_records(self, trade_date: date) -> list:
        return await self._call("get_longhu_records", trade_date)

    async def search(self, query: str) -> list:
        """搜索专用 failover（空结果语义与通用 _call 不同）。

        suggest 类源对生僻词/拼音片段返回空是**正常语义**，不能像 K 线/池子那样
        把空当失败累计——否则中文 IME 逐字输入产生的垃圾查询（"xing"/"xingw"…）
        会把 tencent/eastmoney 以连续 3 次空结果打进 60s 熔断，用户上屏真词时
        两个真源都还在冷却期，任何搜索都秒回空（2026-09-02「星网锐捷搜不出」事故）。

        语义：有源返回非空 → 用它；有源明确"无匹配"（返回空，不计失败）→
        返回 []；全部异常/全部熔断 → 抛 ProviderError（路由转 502）。
        """
        errors: list[str] = []
        saw_no_match = False
        for p in self._pick("search"):
            if self._in_cooldown("search", p.name):
                errors.append(f"{p.name}: 熔断冷却中（{self._cooldown_left('search', p.name):.0f}s）")
                continue
            try:
                result = await p.search(query)
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
                log.warning("provider %s search failed: %s", p.name, exc)
                self._record_failure("search", p.name)
                continue
            if result:
                self._record_success("search", p.name)
                if self._last_good.get("search") != p.name:
                    if "search" in self._last_good:
                        msg = f"search: {self._last_good['search']} -> {p.name}"
                        self.switch_log.append(msg)
                        log.info("provider switched: %s", msg)
                    self._last_good["search"] = p.name
                return result
            saw_no_match = True  # 明确的"无匹配"：真实空结果，不计失败
        if saw_no_match:
            return []
        raise ProviderError("all search providers failed: " + "; ".join(errors))

    async def get_minute_line(self, symbol: str) -> list:
        return await self._call("get_minute_line", symbol)

    async def get_board_rankings(self, board_type: str = "hangye") -> list:
        return await self._call("get_board_rankings", board_type)

    async def get_longhu_detail(self, symbol: str, trade_date) -> dict:
        return await self._call("get_longhu_detail", symbol, trade_date)

    async def get_longhu_history(self, symbol: str, limit: int = 30) -> list:
        return await self._call("get_longhu_history", symbol, limit)

    async def get_capital_flow(self, symbol: str, days: int = 30) -> list:
        return await self._call("get_capital_flow", symbol, days)

    async def get_financials(self, symbol: str, periods: int = 8) -> list:
        return await self._call("get_financials", symbol, periods)

    async def get_limit_break_pool(self, trade_date) -> list:
        return await self._call("get_limit_break_pool", trade_date)

    async def get_trading_days(self) -> list:
        return await self._call("get_trading_days")

    async def get_company_profile(self, symbol: str) -> dict:
        return await self._call("get_company_profile", symbol)

    async def get_announcements(self, symbol: str, limit: int = 10) -> list:
        return await self._call("get_announcements", symbol, limit)

    async def get_news(self, symbol: str, limit: int = 10) -> list:
        return await self._call("get_news", symbol, limit)

    async def get_hot_stock_list(self, period: str = "day") -> list:
        """热股榜（仅 ths 实现；无备源，失败向上抛由调用方标 gap）。"""
        return await self._call("get_hot_stock_list", period)

    async def get_skyrocket_list(self, period: str = "day") -> list:
        """飙升榜（仅 ths 实现；排名逻辑与热股榜不同，「正在变热」信号）。"""
        return await self._call("get_skyrocket_list", period)

    async def get_hot_rank_trend(self, symbol: str, start: date, end: date) -> list:
        """单股热榜排名走势（仅 ths 实现；区间内未上榜日期正常缺失，空集合法）。"""
        return await self._call_allow_empty("get_hot_rank_trend", symbol, start, end)

    async def get_hot_stock_list_history(self, d: date) -> list:
        """历史热股榜（仅 ths 实现）。"""
        return await self._call("get_hot_stock_list_history", d)

    async def get_auction_snapshot(self, symbols: list[str], stage: str = "final") -> list:
        """集合竞价快照（仅 ths 实现）。"""
        return await self._call("get_auction_snapshot", symbols, stage)

    async def get_auction_benchmark(self, d: date) -> list:
        """短线风向标竞价基准（仅 ths 实现）。"""
        return await self._call("get_auction_benchmark", d)

    async def get_adjustment_events(self, symbol: str, start: date | None = None, end: date | None = None) -> list:
        """复权事件流（仅 ths 实现，单只；空事件流合法）。"""
        return await self._call("get_adjustment_events", symbol, start, end)

    async def _call_allow_empty(self, method: str, *args):
        """ths 独占 + 空集合法语义的调用（异动原因 today-only）。

        与 _call 的区别：空结果**不计失败**——异动在非交易日/无异动时段返回空是
        正常状态，若走 _call 会被连续累计打进熔断（同 search 的历史教训）。
        仅异常（网络/服务端错）才记失败；全挂抛 ProviderError。
        """
        errors: list[str] = []
        for p in self._pick(method):
            if self._in_cooldown(method, p.name):
                errors.append(f"{p.name}: 熔断冷却中（{self._cooldown_left(method, p.name):.0f}s）")
                continue
            try:
                result = await getattr(p, method)(*args)
            except Exception as exc:
                log.warning("provider %s %s failed: %s", p.name, method, exc)
                self._record_failure(method, p.name)
                errors.append(f"{p.name}: {exc}")
                continue
            self._settle_last_good(method, p.name)
            return result if result is not None else []
        raise ProviderError(f"all providers failed for {method}: " + "; ".join(errors))

    async def get_anomaly_list(self, tag_codes: list[str] | None = None) -> list:
        """当日全市场异动原因（仅 ths 实现；空集=当日无记录，属正常语义）。"""
        return await self._call_allow_empty("get_anomaly_list", tag_codes)

    async def get_anomaly_stock(self, symbols: list[str]) -> list:
        """按代码批量查当日异动原因（仅 ths 实现；无记录的代码不返回，属正常语义）。"""
        return await self._call_allow_empty("get_anomaly_stock", symbols)
