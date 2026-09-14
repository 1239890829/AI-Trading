"""盘中情绪监控（docs/sentiment.md「历史误判案例库」P2 #14，参考 WhiteWolf-js/daben-review）。

阻塞解除：推送通道已选飞书并落地 notifier（2026-09-02，8154e47），监控本体补齐。
三类**纯规则** P0 事件（不引入主观打分，全部可在盘后用池子数据复核）：

1. **高度板炸板**：昨日 ≥3 连板（今日封住即 ≥4 高度板）的股票今日盘中开板——
   高位承接转弱、梯队断层的前兆信号。离散事件，单拍即触发。
2. **炸板率破位**：炸板数/(涨停+炸板) ≥ 40% 且**连续 2 拍**确认——封板资金
   转弱。单拍噪声（瞬间开板回封）不触发；早盘薄池（合计 <20 只）不判定。
3. **指数急杀**：上证 15 分钟跌 ≥0.8% / 创业板指 ≥1.2%——系统性急跌。
   窗口本身天然平滑（15 分钟跨度），单拍即触发，冷却期内不重复。

判定纪律（沿用项目三态约定）：
- 休市/非交易时段不探测（push2ex 日期静默回退教训）；
- 昨日涨停池拉取失败 → 高度板检查**跳过**（unknown），绝不把空集冒充"确认无事件"；
- 池子/指数快照拉取失败只计错误不触发误报；
- 每类事件独立冷却（默认 60 分钟），避免同一态势刷屏。

告警走既有 AlertRule/AlertEvent + NotifierRegistry（in_app/log/feishu，
webhook 未配置时 feishu 通道显式跳过，不伪装成功）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.db import get_session_factory
from app.market.trade_calendar import in_trading_window, is_trade_day_on, trading_days
from app.models.alert import AlertRule
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository

log = logging.getLogger(__name__)

#: 监控专用系统规则名（get-or-create，与 __ths_reason_sentinel__ 同模式）
SENTIMENT_MONITOR_RULE_NAME = "__sentiment_monitor__"

from app.core.bjtime import beijing_now  # S2-8 时区收敛

#: 指数监控对象 → 15 分钟急杀阈值（%）。带市场前缀（裸 000001 是平安银行）。
INDEX_WATCH: dict[str, float] = {
    "sh000001": -0.8,  # 上证指数
    "sz399006": -1.2,  # 创业板指
}
INDEX_NAMES = {"sh000001": "上证指数", "sz399006": "创业板指"}


def _default_monitor_channels() -> str:
    """监控默认 channels（settings 逗号串）。"""
    import json

    return json.dumps([c.strip() for c in settings.sentiment_monitor_channels.split(",") if c.strip()])


def _ensure_rule(session_factory) -> AlertRule:
    """get-or-create 监控系统规则 + channels 跟随配置默认（系统规则由系统管理：
    2026-09-08 用户指令预警类不推飞书，配置默认去 feishu 后 DB 固化行自动收敛）。"""
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == SENTIMENT_MONITOR_RULE_NAME).one_or_none()
        default_channels = _default_monitor_channels()
        if row is None:
            row = AlertRule(
                name=SENTIMENT_MONITOR_RULE_NAME,
                enabled=1,
                condition_type="sentiment_monitor",
                scope="all",
                threshold=settings.sentiment_break_rate_threshold,
                channels=default_channels,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        elif row.channels != default_channels:
            row.channels = default_channels
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row


# ---------------------------------------------------------------- 纯判定函数（可回测）


def check_break_rate(
    limit_up_count: int,
    break_count: int,
    *,
    # 纯判定函数保留字面默认（零全局依赖可回测）；线上判定一律由
    # SentimentMonitor.break_rate_threshold（settings 配置化）显式传入。
    threshold: float = 0.40,
    min_pool: int = 20,
) -> float | None:
    """炸板率 = 炸板数/(涨停+炸板)。薄池（合计 < min_pool）返回 None 不判定。"""
    total = limit_up_count + break_count
    if total < min_pool:
        return None
    return break_count / total


def check_high_board_breaks(
    break_symbols: set[str],
    yst_boards: dict[str, int] | None,
    *,
    min_boards: int = 3,
) -> list[str] | None:
    """昨日 ≥min_boards 连板且今日盘中在炸板池 → 高度板炸板。

    yst_boards None（昨日池拉取失败）→ None = 判不出，绝不冒充"无事件"。
    """
    if yst_boards is None:
        return None
    return sorted(s for s, b in yst_boards.items() if b >= min_boards and s in break_symbols)


def check_index_plunge(
    series: deque | list,
    *,
    window_sec: float = 15 * 60,
    threshold_pct: float,
    now: float | None = None,
) -> float | None:
    """窗口急杀：(最新价/窗口首价 − 1)×100 ≤ threshold_pct 即触发。

    series 为 (ts, price) 序列（ts 秒）；窗口首点取「距 now ≥ window_sec 的
    最早点」，跨度不足（开盘头 15 分钟）返回 None。返回实际涨跌幅（%），未
    触发返回 None。
    """
    if len(series) < 2:
        return None
    now = time.time() if now is None else now
    cutoff = now - window_sec
    base_ts, base_price = None, None
    for ts, price in series:
        if ts <= cutoff:
            base_ts, base_price = ts, price  # 不断覆盖 → 取到窗口内最早的合格点
        else:
            break
    if base_ts is None:
        return None
    last_ts, last_price = series[-1]
    if last_price is None or base_price is None or base_price == 0:
        return None
    pct = (last_price / base_price - 1) * 100
    return pct if pct <= threshold_pct else None


def _sym_of(record: Any) -> str:
    return record.get("symbol") if isinstance(record, dict) else getattr(record, "symbol", "") or ""


def _boards_of(record: Any) -> int:
    v = record.get("consecutive_boards") if isinstance(record, dict) else getattr(record, "consecutive_boards", None)
    return int(v) if v else 0


def _name_of(record: Any) -> str:
    return (record.get("name") if isinstance(record, dict) else getattr(record, "name", None)) or ""


@dataclass
class SentimentMonitor:
    """状态机 + 单点 IO：probe_once 注入 now/trade_days/池数据即确定性单测。"""

    provider: Any  # composite（需 get_limit_up_pool / get_limit_break_pool / get_quotes）
    # 炸板率（阈值从 settings 读，2026-09-07 P0-3 配置化；default_factory 惰性
    # 取值，测试 monkeypatch settings.sentiment_break_rate_threshold 后新实例即生效）
    break_rate_threshold: float = field(
        default_factory=lambda: float(settings.sentiment_break_rate_threshold)
    )
    break_rate_confirm: int = 2          # 连续 N 拍确认
    break_rate_min_pool: int = 20        # 薄池不判定
    # 高度板
    high_board_min_boards: int = 3       # 昨日 ≥3 板（今日封住即 ≥4 高度板）
    # 指数急杀
    index_window_sec: float = 15 * 60
    index_thresholds: dict[str, float] = field(default_factory=lambda: dict(INDEX_WATCH))
    # 冷却
    alert_cooldown_seconds: float = 3600.0
    index_history_cap: int = 40          # 每指数最多保留点数（60s/拍 ≈ 40 分钟）

    # —— 运行状态（snapshot() 外露，随 /api/system/providers 一并可见）——
    state: str = "idle"
    last_probe_at: str | None = None
    last_break_rate: float | None = None
    break_rate_consecutive: int = 0
    last_high_board_breaks: list[str] | None = None
    last_index_pcts: dict = field(default_factory=dict)
    alerts_fired: int = 0
    last_message: str | None = None
    last_error: str | None = None
    _yst_cache: dict = field(default_factory=dict)      # date.iso → dict[symbol→boards] | None
    _index_hist: dict = field(default_factory=dict)     # symbol → deque[(ts, price)]
    _alert_epochs: dict = field(default_factory=dict)   # kind → epoch 上次告警

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "last_probe_at": self.last_probe_at,
            "last_break_rate": self.last_break_rate,
            "break_rate_consecutive": self.break_rate_consecutive,
            "last_high_board_breaks": self.last_high_board_breaks,
            "last_index_pcts": self.last_index_pcts,
            "alerts_fired": self.alerts_fired,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "config": {
                "break_rate_threshold": self.break_rate_threshold,
                "break_rate_confirm": self.break_rate_confirm,
                "high_board_min_boards": self.high_board_min_boards,
                "index_window_sec": self.index_window_sec,
                "index_thresholds": self.index_thresholds,
                "alert_cooldown_seconds": self.alert_cooldown_seconds,
            },
        }

    # ---------------------------------------------------------------- 探测

    async def probe_once(
        self,
        *,
        now: datetime | None = None,
        trade_days: list[date] | None = None,
        yst_pool: list | None = None,
    ) -> dict:
        """单拍探测。注入 now/trade_days/yst_pool 即确定性；默认真实时钟 + 日历。"""
        now = now or beijing_now()
        if self.provider is None:
            self.state = "idle"
            return self.snapshot()
        today = now.date()
        days = trade_days
        if days is None:
            try:
                days = await trading_days(self.provider, lookback_days=40)
            except Exception:
                days = None
        # ⚠️ 三态（F7，2026-09-14）：原写法 `(days and not is_trade_day(days, today))`
        # 在「日历未覆盖今天」时返回 False ⇒ 当成**确认休市** ⇒ 静默 idle，盘中
        # 情绪监控整段不工作。与 ths_sentinel 同源同修（同一反模式）。
        if not in_trading_window(now):
            self.state = "idle"
            return self.snapshot()
        day_state = is_trade_day_on(today, days)
        if day_state is False:
            self.state = "idle"
            return self.snapshot()
        if day_state is None:
            self.state = "calendar_unknown"
            self.last_error = "交易日历未覆盖今天或源不可用：未判定，本拍不探测"
            return self.snapshot()

        try:
            limit_up = await self.provider.get_limit_up_pool(today)
            break_pool = await self.provider.get_limit_break_pool(today)
        except Exception as exc:
            self.state = "probe_failed"
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self.last_probe_at = now.isoformat(timespec="seconds")
            return self.snapshot()

        self.last_error = None
        self.last_probe_at = now.isoformat(timespec="seconds")

        break_symbols = {_sym_of(r) for r in (break_pool or [])}

        # --- ① 高度板炸板（昨日池：缓存一次/日；失败 → unknown 跳过）---
        yst_boards = yst_pool
        if yst_boards is None:
            cached = self._yst_cache.get(today.isoformat(), "__miss__")
            if cached != "__miss__":
                yst_boards = cached
            else:
                try:
                    prev_day = await self._previous_trade_day(today, days)
                    yst_boards = (
                        {_sym_of(r): _boards_of(r) for r in await self.provider.get_limit_up_pool(prev_day)}
                        if prev_day
                        else None
                    )
                    if yst_boards is not None:
                        self._yst_cache = {today.isoformat(): yst_boards}  # 只留当日，防膨胀
                except Exception as exc:
                    log.warning("sentiment monitor: yst pool unavailable, high-board check skipped: %s", exc)
                    yst_boards = None
        self.last_high_board_breaks = check_high_board_breaks(
            break_symbols, yst_boards, min_boards=self.high_board_min_boards
        )
        if self.last_high_board_breaks:
            assert yst_boards is not None  # 非 None 才可能命中（check_high_board_breaks 契约）
            names = []
            for s in self.last_high_board_breaks:
                rec = next((r for r in (break_pool or []) if _sym_of(r) == s), None)
                names.append(f"{s} {_name_of(rec)}（昨 {yst_boards.get(s, '?')} 板）")
            await self._fire(
                "high_board_break",
                text=(
                    f"【情绪监控】高度板炸板：{'、'.join(names)}——高位承接转弱信号。\n"
                    "处置：警惕梯队断层与情绪退潮，谨慎接力高位股；关注炸板率与跌停家数是否跟随。"
                ),
                trigger_value=float(len(self.last_high_board_breaks)),
                threshold=1.0,
                now=now,
            )

        # --- ② 炸板率破位（连续 N 拍确认）---
        rate = check_break_rate(
            len(limit_up or []), len(break_pool or []),
            threshold=self.break_rate_threshold, min_pool=self.break_rate_min_pool,
        )
        self.last_break_rate = round(rate, 4) if rate is not None else None
        if rate is not None and rate >= self.break_rate_threshold:
            self.break_rate_consecutive += 1
            if self.break_rate_consecutive >= self.break_rate_confirm:
                await self._fire(
                    "break_rate",
                    text=(
                        f"【情绪监控】炸板率 {rate:.0%}（涨停 {len(limit_up or [])} / 炸板 {len(break_pool or [])}，"
                        f"阈值 {self.break_rate_threshold:.0%}）连续 {self.break_rate_consecutive} 拍破位——封板资金转弱。\n"
                        "处置：接力胜率显著下降，降仓或观望；留意首板晋级率是否同步走低。"
                    ),
                    trigger_value=rate,
                    threshold=self.break_rate_threshold,
                    now=now,
                )
        else:
            self.break_rate_consecutive = 0

        # --- ③ 指数急杀（窗口内首尾比较）---
        try:
            quotes = await self.provider.get_quotes(list(self.index_thresholds))
        except Exception as exc:
            log.warning("sentiment monitor: index quotes unavailable: %s", exc)
            quotes = []
        by_symbol: dict[str, Any] = {}
        for q in quotes:
            sym = getattr(q, "symbol", "") or ""
            by_symbol[sym] = q
            by_symbol.setdefault(sym[2:], q)  # 裸代码兜底匹配
        self.last_index_pcts = {}
        for sym, thr in self.index_thresholds.items():
            q = by_symbol.get(sym) or by_symbol.get(sym[2:])
            price = getattr(q, "price", None)
            if not price:
                continue
            epoch = time.time()
            hist = self._index_hist.setdefault(sym, deque(maxlen=self.index_history_cap))
            hist.append((epoch, float(price)))
            pct = check_index_plunge(hist, window_sec=self.index_window_sec, threshold_pct=thr, now=epoch)
            self.last_index_pcts[sym] = round(pct, 3) if pct is not None else None
            if pct is not None:
                await self._fire(
                    "index_plunge",
                    text=(
                        f"【情绪监控】{INDEX_NAMES.get(sym, sym)}15 分钟急杀 {pct:+.2f}%"
                        f"（阈值 {thr:.1f}%）——系统性急跌信号。\n"
                        "处置：暂停追高动作；已有持仓关注分时是否企稳，勿在急跌中补仓。"
                    ),
                    trigger_value=pct,
                    threshold=thr,
                    now=now,
                )

        self.state = "ok"
        return self.snapshot()

    async def _previous_trade_day(self, today: date, days: list[date] | None) -> date | None:
        """日历给昨日交易日；日历不可用时回退「工作日减一天」（宁可误报不算错方向）。"""
        if days:
            earlier = sorted(d for d in days if d < today)
            return earlier[-1] if earlier else None
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    async def _fire(self, kind: str, *, text: str, trigger_value: float, threshold: float, now: datetime) -> bool:
        """冷却去重 → record_trigger → NotifierRegistry 分发。返回是否真正发出。"""
        epoch = time.time()
        last = self._alert_epochs.get(kind)
        if last is not None and (epoch - last) < self.alert_cooldown_seconds:
            log.info("sentiment monitor: %s suppressed by cooldown (%.0fs left)", kind, self.alert_cooldown_seconds - (epoch - last))
            return False
        session_factory = get_session_factory()
        rule = _ensure_rule(session_factory)
        repo = AlertRepository(session_factory)
        event = repo.record_trigger(rule.id, "000000", trigger_value, threshold, snapshot={"kind": kind, "text": text})
        channels = await get_notifier_registry().dispatch(event, rule)
        repo.update_event_channels(event.id, channels)
        self._alert_epochs[kind] = epoch
        self.alerts_fired += 1
        self.last_message = text
        log.warning("[SENTIMENT-MONITOR] %s", text.splitlines()[0])
        return True


async def sentiment_monitor_loop(app, stop: asyncio.Event, interval: float | None = None) -> None:
    """lifespan 周期任务：交易时段每拍探测一次，单拍失败不终止循环。"""
    state = app.state if hasattr(app, "state") else app
    interval = max(30.0, float(interval if interval is not None else settings.sentiment_monitor_interval_seconds))
    monitor = getattr(state, "sentiment_monitor", None)
    if monitor is None:
        hub = getattr(state, "hub", None)
        monitor = SentimentMonitor(provider=hub.provider if hub is not None else None)
        state.sentiment_monitor = monitor
    while not stop.is_set():
        try:
            await monitor.probe_once()
        except Exception:
            log.exception("sentiment monitor probe failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
