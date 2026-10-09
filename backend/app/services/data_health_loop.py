"""数据健康哨兵盘中循环（push_policy ② ANOMALY 类的接入点）。

交易时段每 15 分钟跑一轮 `_collect_data_health`（与进化议程第六路同源）；
**新出现**的异常经 FeishuNotifier 推一张摘要卡（状态变化才推，重复异常不刷屏）。
非交易时段静默（收盘后的健康检查由 15:45 议程证据承担）。

红线：只推系统级异常（数据管道/告警管道），不推任何股票信号；
全中文；事件与外发意图同库提交，未知受理结果不自动重发。
"""
from __future__ import annotations

import asyncio
import logging
from app.core.bjtime import beijing_now

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


def scheduler_failing_probe(app_state) -> str | None:
    """有调度器**连续单拍异常**（但循环仍在跑）时返回一条 issue 文案，否则 None。

    为什么要有（O-1，2026-09-12 评审）：`scheduler_probe` 只问「死了没」，而更隐蔽
    的一种失效是**任务活着、每拍都抛异常**——`last_tick` 照旧刷新、累计 `failures`
    照旧为 0，在 `/api/system/schedulers` 上就是一条**健康的记录**。历史教训
    （情绪指标库静默停更 6 个交易日、调度传字符串日历导致 TypeError 被 except 吞）
    正是这一形态：循环在转、产出为零、没有任何一处会报出来。

    与 `scheduler_probe` 报成**两条** issue（而不是并成一条）是刻意的：两者的
    恢复条件与处置动作不同（"死了"要查为何退出，"持续失败"要看错误摘要），
    并成一条后任务名集合一变就会互相打穿 `AnomalyPushGuard` 的字符串去重，
    把已经恢复的那条又推一次。

    文案只含任务名（稳定值）——同 `scheduler_probe`，理由见该函数 docstring。
    """
    reg = getattr(app_state, "schedulers", None)
    if reg is None:
        return None  # 未装配注册表（精简启动/单测）不误报
    failing = reg.failing_names()
    if not failing:
        return None
    return "调度器持续失败（循环仍在跑但每拍异常）：" + "、".join(sorted(failing))


async def data_health_loop(app_state, stop: asyncio.Event) -> None:
    """常驻循环：交易时段每 15 分钟一轮数据健康检查。startup 里 create_task。"""
    # 2026-09-09：修复双遗留 import 错误——①push_policy 在 app.services 不在 app.core
    # ②in_trading_window 在 trade_calendar 不在 trading_status。此前哨兵启动即崩
    # （先崩 ①，②被 ① 掩盖从未暴露），数据健康检查与飞书 ANOMALY 推送全部失效。
    from app.core.scheduler import wait_or_stop
    from app.market.trade_calendar import in_trading_window
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

                # ⚠️ 必须 to_thread：_collect_data_health 是**同步**函数，内含
                # `duckdb.connect(market.duckdb)` 的 `MAX(date_ms)`（1027 万行库）
                # + 多次 SQLite 全表读 + 文件读。本循环 startup 里 create_task
                # 常驻事件循环，且**只在交易时段**跑 ⇒ 直接调用会卡住 QuoteHub
                # 的 1s 行情节奏，正是最不能卡的时候。2026-09-11 与 evolution 的
                # conclude_due / collect_inputs 同批收口（同属"同步函数被 async
                # 调度器直接调用"，P2-1~15 那一类）。
                out = await asyncio.to_thread(_collect_data_health, get_session_factory())
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
                # O-1：连续单拍失败（循环仍在跑）同样走这条出口。与上一条是
                # **两条独立 issue**，理由见 scheduler_failing_probe docstring。
                failing_issue = scheduler_failing_probe(app_state)
                if failing_issue:
                    issues.append(failing_issue)
                # The durable source receipt, not an in-memory filter, owns
                # active episodes. Recovery must also reconcile an empty set.
                await _push_anomaly(now, issues, app_state)
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


def _health_card(now, issues: list[str]) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red",
                   "title": {"tag": "plain_text", "content": f"系统异常 · 数据健康哨兵 {now:%H:%M}"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md",
                     "content": "\n".join(f"⚠️ {i}" for i in issues[:5])}}],
    }


def _reconcile_anomalies(now, issues: list[str], session_factory, channel_available: bool) -> dict:
    """Persist observation and enqueue atomically in the existing source outbox.

    Source evidence stays immutable. ``health_resolution`` and its card are
    current projections: partial recovery drops obsolete lines before send.
    Accepted, rejected, expired or unknown episodes remain covered while active;
    only observed recovery permits a later recurrence to create a new episode.
    """
    import json
    from uuid import uuid4
    from sqlalchemy import select, update
    from app.models.alert import AlertEvent, AlertRule
    from app.models.notification_outbox import NotificationOutbox
    from app.picks.source_events import record_source_event
    from app.services.push_policy import PolicyKind, feishu_allowed

    current = set(str(i) for i in issues if i)
    kind = 'system_health_anomaly'
    with session_factory() as db:
        # Serializes concurrent probes/recovery and first episode creation.
        db.execute(update(AlertRule).where(AlertRule.name == f'__source_{kind}__').values(
            last_triggered_at=AlertRule.last_triggered_at))
        rows = db.scalars(select(AlertEvent).join(AlertRule).where(
            AlertRule.name == f'__source_{kind}__',
        )).all()
        covered: set[str] = set()
        for row in rows:
            snap = json.loads(row.snapshot or '{}')
            resolution = snap.get('health_resolution') or {}
            if resolution.get('state') != 'active':
                continue
            active = set(resolution.get('active_issues') or []).intersection(current)
            covered.update(active)
            resolution = {**resolution, 'active_issues': sorted(active), 'observed_at': now.isoformat(),
                          'state': 'active' if active else 'recovered',
                          'resolved_at': None if active else now.isoformat()}
            snap['health_resolution'] = resolution
            snap['card'] = _health_card(now, sorted(active))
            row.snapshot = json.dumps(snap, ensure_ascii=False)
        fresh = sorted(current - covered)
        state, event_id = 'unchanged', None
        if fresh and feishu_allowed(PolicyKind.ANOMALY) and channel_available:
            episode = uuid4().hex
            event_id, _, _ = record_source_event(kind, episode, symbol='000000', name='',
                text='系统异常：' + '；'.join(fresh), source_id=episode, source_version=episode,
                source_as_of=now.isoformat(), trade_date=now.date().isoformat(), direction='系统异常',
                source_evidence={'issues': fresh}, card=_health_card(now, fresh),
                session_factory=session_factory, db=db)
            row = db.get(AlertEvent, event_id)
            snap = json.loads(row.snapshot)
            snap['health_resolution'] = {'state': 'active', 'active_issues': fresh,
                                         'observed_at': now.isoformat(), 'resolved_at': None}
            row.snapshot = json.dumps(snap, ensure_ascii=False)
            state = 'queued' if db.scalar(select(NotificationOutbox.id).where(
                NotificationOutbox.event_id == event_id).limit(1)) is not None else 'recorded'
        elif fresh:
            state = 'channel_unavailable' if not channel_available else 'policy_disabled'
        db.commit()
    return {'state': state, 'event_id': event_id, 'new_issues': fresh}


async def _push_anomaly(now, issues: list[str], app_state) -> dict:
    """Reconcile the full current set; AlertEngine later sends typed receipts."""
    from app.core.db import get_session_factory
    from app.core.bjtime import to_beijing
    from app.notifiers import get_notifier_registry

    notifier = get_notifier_registry().get('feishu')
    available = notifier is not None and bool(notifier.delivery_target())
    try:
        return await asyncio.to_thread(_reconcile_anomalies, to_beijing(now), issues,
                                       get_session_factory(), available)
    except Exception:
        log.exception('health event/intent transaction failed; episode remains retryable')
        return {'state': 'failed', 'event_id': None}
