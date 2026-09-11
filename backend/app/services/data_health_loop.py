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

#: 涨跌停价探针标的：贵州茅台。主板、常年交易，只用来判断「限价字段能不能拿到」，
#: 与任何选股/交易逻辑无关（避免用会停牌/退市的标的做探针）。
_LIMIT_PROBE_SYMBOL = "600519"


async def limit_price_probe(app_state) -> str | None:
    """探针：此刻能否从行情链拿到涨跌停价。返回 issue 文案或 `None`（正常）。

    为什么必须有（S1-4 的可见性出口）：S1-4 把「缺限价」从**静默放行**改成
    **拒绝交易**——这是红线的正确严口径，但它把一次上游故障（补价源被封）
    放大成"全站下不了单"。没有探针时，这种放大只能等用户来投诉才发现，
    而系统自己会认为"一切正常、今天没有信号"（正是本文件开头那类静默事故）。

    文案刻意**不含变动的数值与错误消息**：哨兵按字符串去重，
    含变量会让它每 15 分钟推一次飞书。`state` 取自封闭集合，是安全的。
    """
    hub = getattr(app_state, "hub", None)
    provider = getattr(hub, "provider", None)
    if provider is None:
        return None  # 未装配行情链（精简启动/单测）不误报
    try:
        from app.services.quote_enrich import fill_limit_prices

        q = await provider.get_quote(_LIMIT_PROBE_SYMBOL)
        if q is None:
            return "涨跌停价探针取不到行情——模拟盘将按保守口径拒绝下单"
        q = await fill_limit_prices(provider, q)
        state = q.limit_prices_state()
        if state == "ready":
            return None
        return f"涨跌停价不可用（{state}）——模拟盘按保守口径拒绝下单，需查补价源"
    except Exception as exc:  # noqa: BLE001
        return f"涨跌停价探针失败：{type(exc).__name__}"


def scheduler_probe(app_state) -> str | None:
    """有调度器异常死亡（且未在重启）时返回一条 issue 文案，否则 None。

    为什么要有（S2-2 的可见性出口）：注册表 + `GET /api/system/schedulers` 解决的是
    "看得见"，但**总得有人去看**。而"任务静默死亡"的历史教训恰恰是没人会去看
    （否则就不叫静默了）。这里把它并进已有的数据健康哨兵：只要交易时段有任务
    死在异常上，就跟着异常卡一起推出去。

    文案只含任务名（稳定值），**不含数量、时间戳、错误摘要**——哨兵按字符串去重，
    含易变内容会退化成每 15 分钟推一次飞书。任务集合变化本身就是值得再推一次的事件。
    """
    reg = getattr(app_state, "schedulers", None)
    if reg is None:
        return None  # 未装配注册表（精简启动/单测）不误报
    dead = reg.dead_names()
    if not dead:
        return None
    return "调度器已死亡（未自动重启）：" + "、".join(sorted(dead))


async def data_health_loop(app_state, stop: asyncio.Event) -> None:
    """常驻循环：交易时段每 15 分钟一轮数据健康检查。startup 里 create_task。"""
    # 2026-09-09：修复双遗留 import 错误——①push_policy 在 app.services 不在 app.core
    # ②in_trading_window 在 trade_calendar 不在 trading_status。此前哨兵启动即崩
    # （先崩 ①，②被 ① 掩盖从未暴露），数据健康检查与飞书 ANOMALY 推送全部失效。
    from app.core.scheduler import wait_or_stop
    from app.market.trade_calendar import in_trading_window
    from app.market.trading_status import beijing_now
    from app.services.push_policy import AnomalyPushGuard, PolicyKind, feishu_allowed

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
                issues = list(out.get("issues") or [])
                # S1-4：涨跌停价可得性不并入 _collect_data_health（那是同步函数，
                # 探测需要发上游请求），在循环里单独补一条，与其余异常同一套去重/推送。
                probe_issue = await limit_price_probe(app_state)
                if probe_issue:
                    issues.append(probe_issue)
                # S2-2：调度器死亡也走同一条出口（同步、纯内存读）
                sched_issue = scheduler_probe(app_state)
                if sched_issue:
                    issues.append(sched_issue)
                if issues:
                    fresh = guard.filter_new(issues)
                    if fresh and feishu_allowed(PolicyKind.ANOMALY):
                        await _push_anomaly(now, fresh, app_state)
                else:
                    guard.filter_new([])  # 全部恢复 → 清空活跃集
            # S2-2 收尾（09-11）：原来裸 sleep 900s ⇒ 停机时收不到 stop，只能等满
            # 10s 宽限再被强制 cancel（实测日志「超过 10s 未退出，强制 cancel」）。
            # 改 wait_or_stop 后停机即时返回，不必靠 force cancel。
            if await wait_or_stop(stop, _INTERVAL_SECONDS):
                log.info("data-health loop stop requested")
                return
        except asyncio.CancelledError:
            log.info("data-health loop cancelled")
            return
        except Exception:  # noqa: BLE001  哨兵自身异常不拖垮系统
            log.exception("data-health sentinel iteration failed")
            if await wait_or_stop(stop, _INTERVAL_SECONDS):
                return


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
