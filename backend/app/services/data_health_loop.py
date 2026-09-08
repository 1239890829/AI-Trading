"""数据健康哨兵盘中循环（push_policy ② ANOMALY 类的接入点）。

交易时段每 15 分钟跑一轮 `_collect_data_health`（与进化议程第六路同源）；
**新出现**的异常经 FeishuNotifier 推一张摘要卡（状态变化才推，重复异常不刷屏）。
非交易时段静默（收盘后的健康检查由 15:45 议程证据承担）。

红线：只推系统级异常（数据管道/告警管道），不推任何股票信号；
全中文；推送失败只记日志。
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_INTERVAL_SECONDS = 15 * 60


async def data_health_loop(app_state, stop: asyncio.Event) -> None:
    """常驻循环：交易时段每 15 分钟一轮数据健康检查。startup 里 create_task。"""
    from app.core.push_policy import PolicyKind, feishu_allowed
    from app.market.trading_status import beijing_now, in_trading_window
    from app.services.push_policy import AnomalyPushGuard

    guard = AnomalyPushGuard()
    log.info("data-health sentinel loop started (interval %ds, trading hours only)", _INTERVAL_SECONDS)
    while True:
        try:
            if stop.is_set():
                log.info("data-health loop stop requested")
                return
            now = beijing_now()
            if in_trading_window(now):
                from app.core.db import get_session_factory
                from app.services.evolution import _collect_data_health

                out = _collect_data_health(get_session_factory())
                issues = out.get("issues") or []
                if issues:
                    fresh = guard.filter_new(issues)
                    if fresh and feishu_allowed(PolicyKind.ANOMALY):
                        await _push_anomaly(now, fresh, app_state)
                else:
                    guard.filter_new([])  # 全部恢复 → 清空活跃集
            await asyncio.sleep(_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            log.info("data-health loop cancelled")
            return
        except Exception:  # noqa: BLE001  哨兵自身异常不拖垮系统
            log.exception("data-health sentinel iteration failed")
            await asyncio.sleep(_INTERVAL_SECONDS)


async def _push_anomaly(now, fresh_issues: list[str], app_state) -> None:
    """推送系统异常摘要卡（ANOMALY）。"""
    from app.notifiers import get_notifier_registry
    from app.services.push_policy import PolicyKind, feishu_allowed

    if not feishu_allowed(PolicyKind.ANOMALY):
        return
    notifier = get_notifier_registry().get("feishu")
    if notifier is None or getattr(notifier, "send_interactive", None) is None:
        log.warning("anomaly detected but feishu notifier unavailable: %s", fresh_issues)
        return
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red",
                   "title": {"tag": "plain_text", "content": f"系统异常 · 数据健康哨兵 {now:%H:%M}"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md",
                     "content": "\n".join(f"⚠️ {i}" for i in fresh_issues[:5])}}],
    }
    try:
        ok = await notifier.send_interactive(card)
        log.info("anomaly card %s", "sent" if ok else "failed")
    except Exception:  # noqa: BLE001
        log.exception("anomaly card send failed")
