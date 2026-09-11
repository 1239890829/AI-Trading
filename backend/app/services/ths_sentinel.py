"""ths 涨停原因单点依赖哨兵（P0-B）。

背景：涨停原因/题材标签体系 100% 建在 ths 官方 limit_reason 字段上，
东财同字段覆盖率 0%、全市场**无备源**（docs/data-source-comparison.md §2.9）。
ths 一旦改字段契约/降级/断供，题材标签、热力图、选股信号会在**无报错**的
情况下静默退化成"无题材"——这类故障不抛异常、界面照常渲染，只能靠主动探测抓。

探测逻辑（交易时段内周期执行）：
- 覆盖率告警：当日涨停池 reason 非空率 < 阈值，连续 N 次触发；
- 不可达告警：涨停池连续 M 次拉取失败（全链 ProviderError）触发；
- 池子样本 < min_records 时不判定（空池 ≠ 空原因，早盘薄池不误报）；
- 休市/非交易时段不探测（避免 push2ex 类"日期静默回退"拿残留数据）。

告警走既有 AlertRule/AlertEvent + NotifierRegistry 通道，文案固定含
"题材标签可能失效"处置提示；运行状态随 GET /api/system/providers 一并可见。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.config import settings
from app.core.db import get_session_factory
from app.market.trade_calendar import in_trading_window, is_trade_day, trading_days
from app.models.alert import AlertRule
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository

log = logging.getLogger(__name__)

#: 哨兵专用系统规则名（record_trigger 外键要求 rule 存在；get-or-create）
SENTINEL_RULE_NAME = "__ths_reason_sentinel__"

from app.core.bjtime import beijing_now  # S2-8 时区收敛


def _ensure_rule(session_factory) -> AlertRule:
    """get-or-create 哨兵系统规则（与 watcher.ensure_system_rule 同模式）。"""
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == SENTINEL_RULE_NAME).one_or_none()
        if row is None:
            row = AlertRule(
                name=SENTINEL_RULE_NAME,
                enabled=1,
                condition_type="ths_reason_sentinel",
                scope="all",
                threshold=0.5,
                channels='["in_app", "log"]',
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row


def _reason_of(record: Any) -> str:
    """兼容 pydantic 模型与 dict 两种形态取 reason。"""
    if isinstance(record, dict):
        return (record.get("reason") or "").strip()
    return (getattr(record, "reason", None) or "").strip()


@dataclass
class ThsReasonSentinel:
    """纯状态机 + 单点 IO：probe_once 可注入 now/trade_days 做确定性单测。"""

    provider: Any  # composite（或任何实现 get_limit_up_pool 的对象）
    min_records: int = 5
    alarm_coverage: float = 0.5
    bad_required: int = 2
    err_required: int = 3
    alert_cooldown_seconds: float = 4 * 3600.0

    # —— 运行状态（snapshot() 全量外露）——
    state: str = "idle"  # idle | ok | no_data | degraded | alert | probe_failed
    consecutive_bad: int = 0
    consecutive_err: int = 0
    last_probe_at: str | None = None
    last_records: int | None = None
    last_coverage: float | None = None
    last_error: str | None = None
    last_alert_at: str | None = None
    last_message: str | None = None
    alerts_fired: int = 0
    _last_alert_epoch: float | None = field(default=None, repr=False)

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "last_probe_at": self.last_probe_at,
            "records": self.last_records,
            "coverage": self.last_coverage,
            "consecutive_bad": self.consecutive_bad,
            "consecutive_err": self.consecutive_err,
            "alerts_fired": self.alerts_fired,
            "last_alert_at": self.last_alert_at,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "config": {
                "min_records": self.min_records,
                "alarm_coverage": self.alarm_coverage,
                "bad_required": self.bad_required,
                "err_required": self.err_required,
                "alert_cooldown_seconds": self.alert_cooldown_seconds,
            },
        }

    # ---------------------------------------------------------------- 探测

    async def probe_once(self, *, now: datetime | None = None, trade_days: list[date] | None = None) -> dict:
        """单次探测。now/trade_days 注入即确定性（测试用）；默认真实时钟 + 日历。"""
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
        # 门禁：休市/非交易时段不探测——拿到的是回退或残留数据（日期回退教训），
        # 判定毫无意义还可能把昨日原因误判成今日缺失。
        if (days and not is_trade_day(days, today)) or not in_trading_window(now):
            self.state = "idle"
            return self.snapshot()

        try:
            records = await self.provider.get_limit_up_pool(today)
        except Exception as exc:
            self.consecutive_err += 1
            self.state = "probe_failed"
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self._touch(now)
            if self.consecutive_err >= self.err_required:
                fired = await self._fire_alert(
                    kind="pool_unreachable",
                    text=(
                        f"【数据源哨兵】涨停池连续 {self.consecutive_err} 次拉取失败"
                        f"（ths 涨停原因无备源），题材标签可能失效（无数据可比对）。\n"
                        "处置：打开 GET /api/system/providers 查看熔断状态与最近错误；"
                        "确认异常期间暂停依赖题材标签的信号。"
                    ),
                    trigger_value=float(self.consecutive_err),
                    threshold=float(self.err_required),
                )
                if fired:
                    self.state = "alert"
            return self.snapshot()

        self.consecutive_err = 0
        self.last_error = None
        self._touch(now)
        total = len(records)
        self.last_records = total
        if total < self.min_records:
            # 早盘薄池/空池：样本不足不判定（空池 ≠ 空原因）
            self.consecutive_bad = 0
            self.state = "no_data"
            return self.snapshot()

        with_reason = sum(1 for r in records if _reason_of(r))
        coverage = with_reason / total
        self.last_coverage = round(coverage, 3)
        if coverage < self.alarm_coverage:
            self.consecutive_bad += 1
            if self.consecutive_bad >= self.bad_required:
                self.state = "alert"
                await self._fire_alert(
                    kind="low_coverage",
                    text=(
                        f"【数据源哨兵】ths 涨停原因非空率 {coverage:.1%}"
                        f"（{with_reason}/{total}，阈值 {self.alarm_coverage:.0%}），题材标签可能失效。\n"
                        "处置：题材标签/热力图/选股信号依赖 ths 官方 reason 字段且无备源；"
                        "打开 GET /api/system/providers 查看 ths 熔断与降级记录；"
                        "确认异常期间暂停依赖题材标签的信号。"
                    ),
                    trigger_value=coverage,
                    threshold=self.alarm_coverage,
                )
            else:
                self.state = "degraded"
        else:
            if self.consecutive_bad >= self.bad_required or self.state == "alert":
                log.info(
                    "ths reason sentinel recovered: coverage %.1f%% (%d/%d)",
                    coverage * 100, with_reason, total,
                )
            self.consecutive_bad = 0
            self.state = "ok"
        return self.snapshot()

    def _touch(self, now: datetime) -> None:
        self.last_probe_at = now.isoformat(timespec="seconds")

    async def _fire_alert(self, *, kind: str, text: str, trigger_value: float, threshold: float) -> bool:
        """冷却去重 → record_trigger → NotifierRegistry 分发。返回是否真正发出。"""
        now_epoch = time.time()
        if self._last_alert_epoch is not None and (now_epoch - self._last_alert_epoch) < self.alert_cooldown_seconds:
            log.info(
                "ths reason sentinel: alert suppressed by cooldown (%.0fs left)",
                self.alert_cooldown_seconds - (now_epoch - self._last_alert_epoch),
            )
            return False
        session_factory = get_session_factory()
        rule = _ensure_rule(session_factory)
        repo = AlertRepository(session_factory)
        event = repo.record_trigger(
            rule.id,
            "000000",
            trigger_value,
            threshold,
            snapshot={"kind": kind, "text": text},
        )
        channels = await get_notifier_registry().dispatch(event, rule)
        repo.update_event_channels(event.id, channels)
        self._last_alert_epoch = now_epoch
        self.last_alert_at = beijing_now().isoformat(timespec="seconds")
        self.last_message = text
        self.alerts_fired += 1
        log.warning("[THS-SENTINEL] %s", text.splitlines()[0])
        return True


async def sentinel_loop(app, stop: asyncio.Event, interval: float | None = None) -> None:
    """lifespan 周期任务：交易时段每拍探测一次，单拍失败不终止循环。"""
    state = app.state if hasattr(app, "state") else app
    interval = max(60.0, float(interval if interval is not None else settings.ths_sentinel_interval_seconds))
    sent = getattr(state, "ths_sentinel", None)
    if sent is None:
        hub = getattr(state, "hub", None)
        sent = ThsReasonSentinel(provider=hub.provider if hub is not None else None)
        state.ths_sentinel = sent
    while not stop.is_set():
        try:
            await sent.probe_once()
        except Exception:
            log.exception("ths reason sentinel probe failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
