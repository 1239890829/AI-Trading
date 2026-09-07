"""LLM 网关健康探针（2026-09-06）。

为什么要有这个模块：网关故障在系统里只有**一种**表现——「LLM 不可用 →
降级到 rules」，界面和复盘里都看不出原因。但两类故障的处置完全不同：

- 额度耗尽（`QUOTA`）→ 用户动作：给网关充值
- 网关报错（`GATEWAY_ERROR`/`TIMEOUT`）→ 运维动作：等它恢复

2026-09-04 全天复盘/摘要静默降级就是这么被漏掉的，事后还得人肉翻日志。
本模块用最小调用定期体检，把失败落到 `LLMFailure` 分类上，经
`GET /api/system/providers` 的 `llm_gateway` 字段外露。

三条自律：
1. **探针付真金白银**（单次约 $0.0006）→ 有 TTL 缓存、可关闭、默认半小时一拍。
2. **成功长缓存 / 失败短缓存**——失败不能永久缓存（否则故障被掩盖），
   但也不能不缓存（否则 health 端点每拍都真调一次）。
3. **只体检，不参与业务调用**——探针自身失败绝不影响任何真实功能；
   真实调用的失败分类走 `LLMError.kind`，两者共用同一套 `LLMFailure`。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.llm_client import LLMError, LLMFailure, chat_completion_via_cli, hint_for
from app.models.alert import AlertRule
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository

log = logging.getLogger(__name__)

_BJT = timezone(timedelta(hours=8))

# 最小体检请求：三件套裁剪后约 120 input tokens，输出被提示词压到几个字符
_PROBE_SYSTEM = "你是一个健康检查探针。只回复一个单词 OK，不要任何解释、标点或多余字符。"
_PROBE_USER = "ping"

def _now() -> datetime:
    return datetime.now(_BJT)


def resolve_target() -> tuple[str, str, str]:
    """解析探针目标 (model, cli_path, provider)。

    取 review 的 model（复盘是 LLM 的主消费方），回退 news。三者任一为空
    → 返回空 model，调用方据此判定"未启用"，**绝不拿默认值去调**。
    """
    provider = (settings.llm_provider or "openai").strip() or "openai"
    model = (settings.review_llm_model or settings.news_llm_model or "").strip()
    return model, (settings.llm_cli_path or "").strip(), provider


def run_probe_call(model: str, cli_path: str, timeout: float) -> str:
    """真实发一次最小调用。只支持 claude_cli——openai 路径不配凭据时必然
    NOT_CONFIGURED，为它单独探针没有意义（业务调用失败时已经带 kind）。"""
    return chat_completion_via_cli(
        model,
        [{"role": "system", "content": _PROBE_SYSTEM}, {"role": "user", "content": _PROBE_USER}],
        cli_path=cli_path,
        timeout=timeout,
    )


@dataclass
class LlmProbe:
    """网关体检状态机。纯状态 + 单点 IO，runner 可注入做确定性单测。"""

    ok_ttl: float = 1800.0      # 成功结果缓存 30min（省额度）
    fail_ttl: float = 120.0     # 失败结果缓存 2min（防抖，但不长期掩盖故障）
    probe_timeout: float = 60.0
    # 告警：连续失败几次才发、冷却多久。None = 跟随 settings（生产默认）
    alert_after: int | None = None
    alert_cooldown: float | None = None
    alert_enabled: bool = True

    # —— 运行态（snapshot() 全量外露）——
    state: str = "idle"             # idle | ok | failed | disabled
    last_probe_at: str | None = None
    last_ok_at: str | None = None
    last_failure_kind: str | None = None
    last_error: str | None = None
    latency_ms: int | None = None
    consecutive_failures: int = 0
    probes: int = 0
    model: str = ""
    alerts_fired: int = 0
    last_alert_at: str | None = None
    _last_epoch: float = field(default=0.0, repr=False)
    _last_ok: bool | None = field(default=None, repr=False)
    _last_alert_epoch: float = field(default=0.0, repr=False)
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    def _lock_of(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def snapshot(self, *, cached: bool = False) -> dict:
        return {
            "state": self.state,
            "provider": (settings.llm_provider or "openai"),
            "model": self.model or None,
            "last_probe_at": self.last_probe_at,
            "last_ok_at": self.last_ok_at,
            "last_failure_kind": self.last_failure_kind,
            "last_failure_hint": hint_for(self.last_failure_kind),
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
            "consecutive_failures": self.consecutive_failures,
            "probes": self.probes,
            "alerts_fired": self.alerts_fired,
            "last_alert_at": self.last_alert_at,
            "cached": cached,
            "config": {
                "probe_interval_seconds": settings.llm_probe_interval_seconds,
                "ok_ttl": self.ok_ttl,
                "fail_ttl": self.fail_ttl,
                "alert_after": (
                    self.alert_after if self.alert_after is not None
                    else settings.llm_probe_alert_after
                ),
                "alert_cooldown_seconds": (
                    self.alert_cooldown if self.alert_cooldown is not None
                    else settings.llm_probe_alert_cooldown_seconds
                ),
            },
        }

    def _fresh(self) -> bool:
        """上次结果是否仍在有效期内——成功与失败用不同 TTL。"""
        if self._last_ok is None or not self._last_epoch:
            return False
        ttl = self.ok_ttl if self._last_ok else self.fail_ttl
        return (time.monotonic() - self._last_epoch) < ttl

    async def probe_once(
        self,
        *,
        force: bool = False,
        runner: Callable[[str, str, float], str] | None = None,
    ) -> dict:
        """单次体检。TTL 命中且非 force → 直接回快照（cached=True）。

        `runner` 供测试注入；生产路径用 `run_probe_call`。任何异常都落进
        state 而不外抛——探针不是业务链路，它挂了不该影响调用方。
        """
        async with self._lock_of():
            if not force and self._fresh():
                return self.snapshot(cached=True)
            model, cli_path, provider = resolve_target()
            self.model = model
            if not model or provider != "claude_cli":
                # 未配置 / 非 CLI 后端：标 disabled 并保持，不消耗额度
                self.state = "disabled"
                self.last_failure_kind = LLMFailure.NOT_CONFIGURED.value
                self.last_error = (
                    "未配置 review/news 的 *_llm_model"
                    if not model else f"provider={provider} 不在探针覆盖范围"
                )
                self._last_epoch = time.monotonic()
                self._last_ok = False
                return self.snapshot()

            call = runner or run_probe_call
            started = time.monotonic()
            self.probes += 1
            self.last_probe_at = _now().isoformat(timespec="seconds")
            try:
                reply = await asyncio.to_thread(call, model, cli_path, self.probe_timeout)
            except LLMError as exc:
                self._fail(exc.kind.value, str(exc)[:200])
                await self._maybe_alert()
                return self.snapshot()
            except Exception as exc:  # noqa: BLE001  探针兜底：未知异常也算网关失败
                self._fail(LLMFailure.GATEWAY_ERROR.value, f"{type(exc).__name__}: {exc}"[:200])
                await self._maybe_alert()
                return self.snapshot()

            self.latency_ms = int((time.monotonic() - started) * 1000)
            if not isinstance(reply, str) or not reply.strip():
                self._fail(LLMFailure.EMPTY.value, "探针收到空回复")
                await self._maybe_alert()
                return self.snapshot()
            self.state = "ok"
            self.last_ok_at = self.last_probe_at
            self.last_failure_kind = None
            self.last_error = None
            self.consecutive_failures = 0
            self._last_epoch = time.monotonic()
            self._last_ok = True
            log.info("[LLM-PROBE] ok model=%s latency=%sms", model, self.latency_ms)
            return self.snapshot()

    def _fail(self, kind: str, detail: str) -> None:
        self.state = "failed"
        self.last_failure_kind = kind
        self.last_error = detail
        self.consecutive_failures += 1
        self._last_epoch = time.monotonic()
        self._last_ok = False
        log.warning("[LLM-PROBE] failed kind=%s detail=%s", kind, detail)

    async def _maybe_alert(self) -> None:
        """连续失败达阈值 → 落告警事件（带冷却，避免同一故障刷屏）。

        冷却是硬要求：2026-09-04 复盘定案的缺陷之一就是"预警规则无去重/冷却，
        3.5h 连发 7 条雷同事件"。探针是定时跑的，不冷却必然刷屏。
        """
        after = (
            self.alert_after if self.alert_after is not None
            else settings.llm_probe_alert_after
        )
        cooldown = (
            self.alert_cooldown if self.alert_cooldown is not None
            else settings.llm_probe_alert_cooldown_seconds
        )
        if not self.alert_enabled or after <= 0:
            return
        if self.consecutive_failures < after:
            return
        epoch = time.monotonic()
        if self._last_alert_epoch and (epoch - self._last_alert_epoch) < cooldown:
            log.info(
                "llm probe alert suppressed by cooldown (%.0fs left)",
                cooldown - (epoch - self._last_alert_epoch),
            )
            return
        kind = self.last_failure_kind or LLMFailure.GATEWAY_ERROR.value
        sent = await fire_llm_alert(
            kind=kind,
            hint=hint_for(kind),
            detail=self.last_error,
            consecutive=self.consecutive_failures,
            threshold=after,
        )
        if sent:
            self._last_alert_epoch = epoch
            self.alerts_fired += 1
            self.last_alert_at = _now().isoformat(timespec="seconds")


#: 探针专用系统规则名（get-or-create，与 __sentiment_monitor__ 同模式）
LLM_PROBE_RULE_NAME = "__llm_gateway_probe__"


def _default_probe_channels() -> str:
    import json

    return json.dumps([c.strip() for c in settings.llm_probe_channels.split(",") if c.strip()])


def _ensure_rule(session_factory) -> Any:
    """get-or-create 探针告警规则。channels 取当前配置默认（新建时固化）。"""
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == LLM_PROBE_RULE_NAME).one_or_none()
        if row is None:
            row = AlertRule(
                name=LLM_PROBE_RULE_NAME,
                enabled=1,
                condition_type="llm_gateway_probe",
                scope="all",
                threshold=float(settings.llm_probe_alert_after),
                channels=_default_probe_channels(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row


async def fire_llm_alert(
    *,
    kind: str,
    hint: str | None,
    detail: str | None,
    consecutive: int,
    threshold: int,
) -> bool:
    """落 AlertEvent 并经 NotifierRegistry 分发。

    失败**不外抛**——告警是旁路的旁路，它挂了绝不能拖垮探针（探针挂了也
    不影响业务，但会让人失去眼睛）。返回是否真正发出（供计数与冷却判定）。
    """
    text = f"[LLM 网关] 连续 {consecutive} 次体检失败：{hint or kind}（kind={kind}）"
    if detail:
        text = f"{text}\n{detail}"
    try:
        session_factory = get_session_factory()
        rule = _ensure_rule(session_factory)
        repo = AlertRepository(session_factory)
        event = repo.record_trigger(
            rule.id,
            "000000",
            float(consecutive),
            float(threshold),
            snapshot={"kind": kind, "consecutive": consecutive, "text": text},
        )
        channels = await get_notifier_registry().dispatch(event, rule)
        repo.update_event_channels(event.id, channels)
    except Exception:  # noqa: BLE001
        log.exception("llm probe alert dispatch failed")
        return False
    log.warning("[LLM-PROBE-ALERT] %s", text.splitlines()[0])
    return True


async def probe_loop(app: Any, stop: asyncio.Event, interval: float | None = None) -> None:
    """lifespan 周期任务：每拍体检一次，单拍失败不终止循环。"""
    state = app.state if hasattr(app, "state") else app
    interval = max(60.0, float(interval if interval is not None else settings.llm_probe_interval_seconds))
    probe = getattr(state, "llm_probe", None)
    if probe is None:
        probe = LlmProbe()
        state.llm_probe = probe
    while not stop.is_set():
        try:
            await probe.probe_once()
        except Exception:  # noqa: BLE001
            log.exception("llm probe crashed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
