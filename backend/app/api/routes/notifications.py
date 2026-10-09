"""原选股时点、真实持仓风险与明确个股条件的站内消息。

IMP-086：普通全市场临板/开板事件留在原观察路径，不取得机会消息资格。
每日与盘中真实入选复用原结果，消息保留原时点；当前条件须查看选股页。
风险和明确配置的个股条件不要求先入选。新消息仅站内，不扩展外推或交易消费者。

⚠️ **`news_limit` / `news_min_score` 仅为旧客户端兼容保留、已不被消费**
（响应里的 ``news_min_score`` 同理，取值仍是配置默认）。`IMP-028` 收敛时留下的
``_daily_pick_item`` / ``_news_items`` / ``_classify_four_row`` 三个不再被引用的
生产者已于 `IMP-034` **删除**（连同守着一个够不到的落点的用例）——留着它们会给出
虚假的覆盖信心（同族教训见 `kb/09-verification-pitfalls.md`）。

session（盘前/盘中/盘后）**交易日历优先**（2026-09-13 用户报告的周末误标盘中修复）：
非交易日（周末/节假日）一律归**盘前**节拍（下一交易日开盘前消化的资讯）；
交易日按墙钟：<09:30 盘前；09:30–15:05 盘中（含午休——通知分类不需要午休粒度）；
其余盘后。日历不可用（trading_days 失败）回退墙钟——宁可放行不因日历故障误判
（与 in_trading_window 同哲学）。任何单一来源失败都显式降级（errors 字段）。

已读状态（2026-09-12 缺陷修复）也挂在本模块，但它**不是**聚合的一部分，而是
一份独立的持久化状态：

- ``GET  /api/notifications/read-state`` —— 读取权威已读状态；
- ``PUT  /api/notifications/read-state`` —— 提交本地状态，**服务端按单调规则合并**
  后落库并回传合并结果（合并纪律见 ``app/services/notification_read_state.py``）。

为什么必须落服务端：此前只存浏览器 localStorage，而它是**按 origin 命名空间**的，
换源（localhost ↔ 127.0.0.1）／换 profile／清站点数据都会让已读状态整体归零，
表现为「重启后全部已读变未读、徽标回到 65」（实测复现）。

## 空态可解释性（`BUG-016` 子项③，2026-09-16）

用户现场反馈「为什么消息通知一个也没有呢」取证后发现：**空响应体本身无法区分**
三种完全不同的处境 ——「真无机会（跑了但全被否）」「链路未跑」「上游空（盘前没选出）」，
三者都表现为 ``{"items": [], "count": 0}``。

因此 ``GET /api/notifications`` 在 **`items` 为空时**额外返回 ``data.diagnostics``
（`state` 四态 + 候选档位分布 + 逐股否决原因；实现与口径见
``app/picks/notification_diagnostics.py``）。⚠️ **非空态不返回该字段（恒 `null`）**，
且本项**只增加可诊断面、不改任何推送口径**（改档位门属交易信号口径变更，须用户拍板）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.core.config import settings
from app.repositories.alert_repo import AlertRepository
from app.core.bjtime import beijing_now, to_beijing
from app.market import trade_calendar as tc
from app.services import notification_read_state as read_state_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

BUY_POINT_RULE = "__picks_buy_point__"
# Ordinary watcher shapes remain in diagnostics, never in opportunity items.
WATCHER_RULE = "__picks_watcher__"
_NOTIF_KINDS = ("buy_point", "selection", "real_exit_alert")
_NOTIF_RULE_NAMES = (BUY_POINT_RULE, WATCHER_RULE, "__source_board_reopen__",
                     "__source_real_exit_alert__", "__selection_notifications__")
# The source window is per rule; ordinary monitors cannot evict selected results.
_NOTIF_FETCH_LIMIT = 500


def _session_of(bj: datetime, trading_dates: set | None = None) -> str:
    """北京时间 naive → 盘前/盘中/盘后。

    trading_dates（交易日 date 集合，日历唯一入口 trading_days 的产物）非 None 时
    **日历优先**：非交易日一律盘前节拍（周末/节假日的消息在下一交易日开盘前消化）；
    None（日历不可用）回退纯墙钟——回退是显式降级而非静默错误。
    """
    if trading_dates is not None and bj.date() not in trading_dates:
        return "pre_open"
    hm = bj.hour * 100 + bj.minute
    if 930 <= hm <= 1505:
        return "intraday"
    if hm < 930:
        return "pre_open"
    return "after_close"


def get_alert_repo(request: Request) -> AlertRepository:
    return request.app.state.alert_repo


def _json_dict(value) -> dict:
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _message_time(value) -> datetime:
    try:
        return to_beijing(datetime.fromisoformat(str(value)))
    except (ValueError, TypeError):
        return to_beijing(datetime.min)


def _explicit_symbol_rule(rule) -> bool:
    if getattr(rule, 'scope', None) != 'symbols' or getattr(rule, 'condition_type', None) not in {
        'price_above', 'price_below', 'change_pct_above', 'change_pct_below'}:
        return False
    try:
        channels = json.loads(rule.channels) if isinstance(rule.channels, str) else rule.channels
        symbols = json.loads(rule.symbols) if isinstance(rule.symbols, str) else rule.symbols
        return isinstance(symbols, list) and bool(symbols) and isinstance(channels, list) and 'in_app' in channels
    except (ValueError, TypeError, AttributeError):
        return False


def _original_reference(event, snapshot: dict) -> dict:
    """Expose only the persisted source; never reconstruct it from current picks."""
    evidence = snapshot.get('selection_evidence')
    if not isinstance(evidence, dict):
        evidence = {key: snapshot[key] for key in (
            'execution_ref', 'confidence', 'vetoes', 'invalidations', 'buy_range',
            'source', 'quality', 'quality_reasons', 'data_timestamp', 'received_at',
            'source_evidence',
        ) if key in snapshot}
    return {'event_id': event.id, 'kind': snapshot.get('kind') or 'explicit_condition',
        'trade_date': snapshot.get('trade_date'), 'selection_source': snapshot.get('selection_source'),
        'source_id': snapshot.get('source_id'), 'source_version': snapshot.get('source_version'),
        'source_as_of': snapshot.get('source_as_of') or snapshot.get('data_timestamp'),
        'recorded_at': event.triggered_at.isoformat(sep=' ') if event.triggered_at else None,
        'run_id': snapshot.get('run_id'), 'evidence': evidence}


def _alert_items(
    repo: AlertRepository, limit: int, trading_dates: set | None = None
) -> tuple[list[dict], dict[str, int]]:
    """Read immutable selection/buy-point facts; risk and explicit reminders stay independent."""
    from app.services.selection_notifications import OBSERVATION_VALIDITY, SELECTION_RULE, selection_dedup_key
    rules = {r.id: r for r in repo.list_rules()}
    allowed = {rid for rid, r in rules.items() if r.name in _NOTIF_RULE_NAMES or _explicit_symbol_rule(r)}
    if not allowed:
        return [], {}
    # Per-rule queries keep ordinary all-market events from exhausting the useful window.
    fetch_limit = max(limit, _NOTIF_FETCH_LIMIT)
    by_id = {}
    saturated = False
    for rid in sorted(allowed):
        rows = repo.list_events(limit=fetch_limit, rule_id=rid, real_symbol_only=True)
        if len(rows) >= fetch_limit:
            saturated = True
            log.warning('notifications: rule %s read window saturated (%s)', rid, fetch_limit)
        by_id.update((e.id, e) for e in rows if e.rule_id == rid)
    # Reattach at most the two original daily identities for already visible
    # stock/day groups. Newer events must not replace a first message's ID merely
    # because it fell beyond the per-rule window. Every row still passes the same
    # source/shape/ignore checks below; this does not revive filtered history.
    from app.picks.buy_point import _buy_point_dedup_key
    keys = set()
    for e in by_id.values():
        snap = _json_dict(e.snapshot)
        if not e.symbol or e.symbol == '000000':
            continue
        kind, rule_name = snap.get('kind'), rules[e.rule_id].name
        if (kind, rule_name) not in {('buy_point', BUY_POINT_RULE), ('selection', SELECTION_RULE)}:
            continue
        day = snap.get('trade_date') or (e.triggered_at.date().isoformat() if e.triggered_at else '')
        if isinstance(day, str) and day:
            keys.update((_buy_point_dedup_key(day, e.symbol), selection_dedup_key(day, e.symbol)))
    if keys:
        by_id.update((e.id, e) for e in repo.list_events_by_dedup_keys(list(keys)) if e.rule_id in allowed)
    events = list(by_id.values())
    ignored: set[int] = set()
    buy_ids = [e.id for e in events if _json_dict(e.snapshot).get('kind') == 'buy_point']
    if buy_ids:
        try:
            from sqlalchemy import select
            from app.core.db import get_session_factory
            from app.models.agent import AgentTriage
            sf = getattr(repo, '_session_factory', None) or get_session_factory()
            with sf() as db:
                ignored = set(db.scalars(select(AgentTriage.event_id).where(
                    AgentTriage.event_id.in_(buy_ids), AgentTriage.verdict == 'ignore')))
        except Exception:  # Historical opinion is optional; original facts remain identifiable.
            log.exception('notifications: historical triage read failed')
    seen: dict[str, int] = {'selection': 0, 'buy_point': 0, 'pre_limit': 0}
    if saturated:
        seen['read_window_saturated'] = 1
    items = []
    groups: dict[tuple[str, str], list[tuple]] = {}
    for e in events:
        rule = rules[e.rule_id]
        snap = _json_dict(e.snapshot)
        kind = snap.get('kind') or ''
        if kind:
            seen[kind] = seen.get(kind, 0) + 1
        if not e.symbol or e.symbol == '000000':
            continue
        name = str(snap.get('name') or '').strip()
        explicit = _explicit_symbol_rule(rule)
        if explicit:
            symbols = json.loads(rule.symbols) if isinstance(rule.symbols, str) else rule.symbols
            if e.symbol not in symbols:
                continue
            category, label, source = 'reminder', '自设条件提醒', '用户指定个股条件'
            condition = {'price_above': '价格高于', 'price_below': '价格低于', 'change_pct_above': '涨幅高于', 'change_pct_below': '涨幅低于'}[rule.condition_type]
            unit = '元' if rule.condition_type.startswith('price_') else '%'
            text = f'{rule.name}：{condition} {e.threshold:g}{unit}，触发值 {e.trigger_value:g}{unit}。仅为自设条件提醒，不构成选股或买卖建议。'
            validity = '仅触发时点满足用户明确配置的条件；请核对当前行情。'
        elif kind == 'real_exit_alert' and rule.name == '__source_real_exit_alert__':
            category, label, source = 'risk', '真实持仓风险', '真实持仓监护'
            text = snap.get('text') or '请核对持仓风险记录'
            validity = '需核对当前持仓、成本与价格；原风险信号可随条件变化失效'
        elif kind == 'selection' and rule.name == SELECTION_RULE:
            from app.services.selection_notifications import _time
            as_of = _time(snap.get('source_as_of'))
            source_kind = snap.get('selection_source')
            if not name or not as_of or not snap.get('source_id') or not snap.get('source_version') or not isinstance(snap.get('selection_evidence'), dict) or as_of.date().isoformat() != snap.get('trade_date'):
                continue
            if source_kind not in {'daily', 'intraday'} or (source_kind == 'intraday' and snap.get('run_id') != snap.get('source_id')):
                continue
            category, label = 'opportunity', '入选观察'
            source = '每日选股原结果' if source_kind == 'daily' else '盘中选股原结果'
            text, validity = snap.get('text') or '原入选依据缺项，请查看选股记录', OBSERVATION_VALIDITY
        elif kind == 'buy_point' and rule.name == BUY_POINT_RULE and name and e.id not in ignored:
            category, label, source = 'opportunity', '个股机会', '盘中买点判定'
            text = (snap.get('text') or '原买点条件时点记录') + '。' + OBSERVATION_VALIDITY
            validity = '仅触发时点满足原条件；当前资格须重新核验。' + OBSERVATION_VALIDITY
        else:
            continue
        bj = e.triggered_at
        display_ts = snap.get('source_as_of') if kind == 'selection' else bj.isoformat(sep=' ') if bj else None
        observed_at = _message_time(display_ts) if display_ts else bj
        item = {'id': f'alert-{e.id}', 'category': category, 'label': label,
            'session': _session_of(observed_at, trading_dates) if observed_at else 'intraday', 'ts': display_ts,
            'source_as_of': snap.get('source_as_of') or snap.get('data_timestamp'),
            'recorded_at': bj.isoformat(sep=' ') if bj else None,
            'references': [_original_reference(e, snap)],
            'title': f'【{label}】{e.symbol} {name}'.strip(), 'body': text, 'symbol': e.symbol,
            'url': None, 'score': None, 'source': source, 'validity': validity}
        if category == 'opportunity':
            day = snap.get('trade_date') or (bj.date().isoformat() if bj else '')
            groups.setdefault((day, e.symbol), []).append((e, item, kind))
        else:
            items.append(item)
    for rows in groups.values():
        # Anchor on the first event, independently of newer snapshots or current membership.
        rows.sort(key=lambda r: (r[0].triggered_at or datetime.min, r[0].id))
        anchor = rows[0][1]
        anchor['_event_ids'] = [r[0].id for r in rows]
        anchor['references'] = [ref for _, item, _ in rows for ref in item['references']]
        if len(rows) > 1:
            anchor['body'] += '\n' + '\n'.join(f'补充原时点 {r[1]["ts"]}（{r[1]["source"]}）：{r[1]["body"]}' for r in rows[1:])
        items.append(anchor)
    return items, seen


@router.get("/notifications")
async def notifications(
    request: Request,
    alert_limit: int = Query(default=50, ge=1, le=200),
    news_limit: int = Query(default=15, ge=1, le=50),
    news_min_score: float | None = Query(default=None, ge=0, le=100),
    repo: AlertRepository = Depends(get_alert_repo),
) -> dict:
    """返回具名个股机会与真实持仓风险；旧查询参数保留兼容。"""
    min_score = news_min_score if news_min_score is not None else settings.notifications_news_min_score
    now = beijing_now().replace(tzinfo=None)  # 事件 published_at 是北京 naive，同语义相减

    # 交易日历一次取（请求级）：session 分类的日历优先判定（§6.25 周末误标盘中修复）。
    # 失败 → None → 回退墙钟（宁可放行不因日历故障误判，与 in_trading_window 同哲学）。
    trading_dates: set | None = None
    try:
        provider = getattr(getattr(request.app.state, "hub", None), "provider", None)
        if provider is not None:
            days = await tc.trading_days(provider)
            trading_dates = set(days) if days else None
    except Exception:  # noqa: BLE001
        log.warning("notifications: trading calendar unavailable, session 回退墙钟", exc_info=True)
        trading_dates = None

    errors: dict[str, str] = {}
    alert_items: list[dict] = []
    shapes_seen: dict[str, int] = {}
    try:
        alert_items, shapes_seen = _alert_items(repo, alert_limit, trading_dates)
    except Exception as exc:  # noqa: BLE001
        log.exception("notifications: alert source failed")
        errors["alerts"] = type(exc).__name__

    items = alert_items
    items.sort(key=lambda x: _message_time(x["ts"]), reverse=True)
    # A saturated source window does not prove that older rows are eligible.
    # True is witnessed overflow; false is complete within the fetched window;
    # null exposes the remaining unknown rather than inventing an exact count.
    has_more = True if len(items) > alert_limit else None if shapes_seen.get('read_window_saturated') else False
    # 截断在**筛选之后**（读取窗口见 `_NOTIF_FETCH_LIMIT`）：先按形状挑出个股机会，
    # 再按时间倒序取前 `alert_limit` 条——而不是"先取最近 N 条再看有没有个股机会"。
    items = items[:alert_limit]
    # Live status is a persistent banner consumer, not a new stock opportunity
    # or repeat popup. The read clock and quote clock retain separate identities.
    from app.picks.exit_engine import position_monitor_state

    real_monitor = position_monitor_state()['real']
    read_status = real_monitor.get('state', 'unknown')
    monitor_status = ('uncompleted' if read_status == 'failed' else
                      'empty' if read_status == 'empty' else
                      real_monitor.get('evaluation_state', 'unknown') if read_status == 'ok' else 'unknown')
    monitor = {'state': monitor_status,
               'reason': real_monitor.get('reason') if read_status == 'failed' else real_monitor.get('evaluation_reason'),
               'uncompleted_symbols': real_monitor.get('uncompleted_symbols') or [],
               'evaluated_at': real_monitor.get('evaluated_at')}
    # 渠道回执与站内可见、浏览已读是三个事实。无 Outbox 行只能说没有记录，
    # 不能推出飞书已送达或用户已读；读取失败也不能隐藏已有站内事件。
    if items:
        try:
            outbox = getattr(repo, "outbox", None)
            event_ids = [eid for i in items for eid in i.get("_event_ids", [int(i["id"][6:])])]
            states = outbox.states_for_events(event_ids) if outbox else {}
            for item in items:
                item["channels"] = [state for eid in item.get("_event_ids", [int(item["id"][6:])]) for state in states.get(eid, [])]
        except Exception as exc:  # noqa: BLE001
            log.exception("notifications: channel status read failed")
            errors["channels"] = type(exc).__name__
            for item in items:
                item["channels"] = None
    for item in items:
        item.pop("_event_ids", None)
    # 空态诊断（`BUG-016` 子项③，2026-09-16）：**只在空态附加**。
    # 空响应体本身不含任何能区分「真无机会 / 链路未跑 / 上游空」的信息 ——
    # 三者都是 `{"items": [], "count": 0}`，这正是「不可解释」的根因。
    # ⚠️ 非空态**刻意不附加**：那是本子项的判据边界之外，且本端点是 30s 轮询热路径，
    #    不该为"用户不会问的场景"每拍多读两次库。
    diagnostics: dict | None = None
    if not items and not errors:
        try:
            from app.picks.notification_diagnostics import notification_diagnostics

            diagnostics = await asyncio.to_thread(notification_diagnostics)
        except Exception:  # noqa: BLE001  诊断失败不得拖垮通知端点本身
            log.exception("notifications: diagnostics failed")
            # ⚠️ 回 dict 而非 None：None 在本端点里**已被"非空态"占用**，
            #    两者同形就等于把"诊断坏了"伪装成"有通知"。异常一律显式降级。
            diagnostics = {
                "state": "unavailable",
                "note": "空态诊断不可用（内部错误）：**这不代表没有机会**，请查后端日志。",
            }
        if not isinstance(diagnostics, dict):
            # 同一纪律再兜一层：诊断函数若返回非 dict（契约破坏），也不能让
            # `data.diagnostics` 退化成 None —— 那正是本端点要消灭的"同形"。
            diagnostics = {
                "state": "unavailable",
                "note": "空态诊断返回了非预期结构：**这不代表没有机会**，请查后端日志。",
            }
        # 形状计数（2026-09-16）：空态下必须能区分「买点链没选出票」与
        # 「临板预警也没触发」——两者都表现为空列表。**只加可诊断面，不改推送口径**。
        #
        # ⚠️ 用**新 dict** 而不是 `diagnostics["shapes"] = ...` 就地改：
        #    诊断函数返回的对象属**调用方**（也属测试里的桩），就地 mutate 会把副作用
        #    留在那里 —— 守卫用例断言 `body["diagnostics"] == sentinel` 时，
        #    因两侧是同一对象而**恒真**，等于把"接线正确"这条判据悄悄掏空。
        diagnostics = {**diagnostics, "shapes": shapes_seen}
    return {
        "data": {
            "items": items,
            "count": len(items),
            "read_window": {"limit": alert_limit, "returned": len(items),
                            "has_more": has_more, "scope": "recent_eligible_messages"},
            "monitor": monitor,
            "generated_at": now.isoformat(sep=" "),
            "news_min_score": min_score,
            "policy": "stock_opportunities_only",
            "errors": errors or None,
            "diagnostics": diagnostics,
        },
        "meta": {},
    }


@router.get("/notifications/diagnostics")
async def get_notification_diagnostics() -> dict:
    """按需查看北京当日的判定事实；列表非空时也可解释其他候选为何未提醒。"""
    from app.picks.notification_diagnostics import notification_diagnostics

    return {"data": await asyncio.to_thread(notification_diagnostics), "meta": {}}


# ---------------------------------------------------------------- 已读状态（2026-09-12）


class ReadStateIn(BaseModel):
    """PUT 载荷。三个分量都**只增不减**，非法值由 pydantic 拦在入口。

    时间戳是 epoch 毫秒（正数）；`read_ids` 上界与服务层 ``READ_IDS_MAX`` 一致，
    防止异常客户端把单行 JSON 灌爆。
    """

    seen_before: int = Field(default=0, ge=0)
    read_ids: list[str] = Field(default_factory=list, max_length=read_state_service.READ_IDS_MAX)
    clear_before: int = Field(default=0, ge=0)


def _read_state_payload(state: dict) -> dict:
    return {
        "seen_before": state["seen_before"],
        "read_ids": state["read_ids"],
        "clear_before": state["clear_before"],
        "updated_at": state.get("updated_at"),
    }


@router.get("/notifications/read-state")
def get_read_state() -> dict:
    """权威已读状态。DB 不可用**不伪装成空状态**——已读归零正是本次要修的缺陷形态，
    所以这里让异常直接冒泡成 5xx，前端据此保留本地缓存值（宁可显示旧状态，不可假装未读）。"""
    return {"data": _read_state_payload(read_state_service.load_state()), "meta": {}}


@router.put("/notifications/read-state", dependencies=[Depends(require_write_token)])
def put_read_state(body: ReadStateIn) -> dict:
    """提交本地状态 → 服务端单调合并 → 落库并回传合并结果（前端以回传值为准）。"""
    merged = read_state_service.save_state(
        {"seen_before": body.seen_before, "read_ids": body.read_ids, "clear_before": body.clear_before}
    )
    return {"data": _read_state_payload(merged), "meta": {}}
