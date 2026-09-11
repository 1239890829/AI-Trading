"""热点验证环（P1-6）：事件 → 关联板块事后验证 → 发酵四态。

补齐「事件驱动选股」的反馈闭环：EventCard 只有规则层的**方向判定**
（利好/利空/待判），缺一个「事后看，市场到底买不买账」的数据化验证。
本模块就是那一步——采样事件关联题材的当日涨停 + 板块主力资金，判定
事件是否在**发酵**。

设计取舍（2026-09-10）：
- **不落库、实时计算**：验证本质是时点快照（30/60min/收盘三次采样），
  落库反而要处理「同一事件多次采样」的历史与迁移；实时现算更准、零 schema 变更。
- **「事件后新涨停」不建基线快照表**：涨停池自带 `first_seal_time`，直接用
  「封板时间 ≥ 事件发布时间」判定，无需在事件入库时额外记录板块状态。
- **板块资金复用 L3 映射**：`theme_service.board_rows_for_names` 把 ths 题材名
  映射到东财板块（f62 口径），命中 board_flow 30s 缓存，零额外上游调用。

三态纪律：缺数据/窗口未到 → `unknown`（显式「未判定」），绝不臆造成
confirmed 或 faded；「有新涨停」与「资金净流入」是两套口径，分开采、分开说。
"""
from __future__ import annotations

import logging
from datetime import date, datetime

log = logging.getLogger(__name__)

#: 采样窗口：事件发布 < 30 分钟判「窗口未到」（unknown），避免过早下结论
MIN_AGE_MINUTES = 30

#: 事件后新涨停判定的封板时间解析（容忍 "09:35:00" / "09:35" / "935" 三种）
def _parse_hhmmss(s: str) -> tuple[int, int, int] | None:
    s = str(s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]), int(parts[1]), int(parts[2])
        if len(parts) == 2:
            return int(parts[0]), int(parts[1]), 0
        if len(parts) == 1 and len(parts[0]) in (3, 4):  # "935" / "0935"
            hhmm = parts[0].zfill(4)
            return int(hhmm[:2]), int(hhmm[2:]), 0
    except (TypeError, ValueError):
        return None
    return None


def _seal_after(published: datetime | None, seal: str | None, trade_date: date) -> bool:
    """封板时间是否晚于事件发布（即「事件后新涨停」）。"""
    if published is None or not seal:
        return False
    if published.date() < trade_date:
        return True  # 昨日事件，今日该题材所有涨停都算「事件后」
    t = _parse_hhmmss(seal)
    if t is None:
        return False
    return t >= (published.hour, published.minute, published.second)


def _theme_in_reason(theme: str, reason: str | None) -> bool:
    """涨停 reason（ths「+」分隔的题材串）是否含该题材。空题材/空 reason 不匹配。"""
    if not theme or not reason:
        return False
    return theme in reason


def verify_event(
    *,
    published_at: datetime | None,
    theme_targets: list[str],
    limit_up_pool: list[dict],
    board_fund: dict | None,
    trade_date: date,
    now: datetime | None = None,
) -> dict:
    """事件发酵四态判定（纯函数）。

    参数
    ----
    published_at: 事件发布时间（北京 naive）；None → 无法判窗口，按 unknown。
    theme_targets: 事件关联题材（EventDirection target，非空才可判）。
    limit_up_pool: 当日涨停池（每项含 `reason` 与 `first_seal_time`）。
    board_fund: 关联板块资金行（`board_rows_for_names` 的 value，含 `main_net_yi`）；
        None = 板块映射不到，资金维度按缺失处理。
    trade_date: 当日（判定「昨日事件」用）。
    now: 当前时间（北京 naive），默认取系统时间。

    返回
    ----
    {status, basis, new_limit_ups, net_inflow_yi, age_minutes}
    status ∈ fermenting / confirmed / faded / unknown；basis 说明判定依据。
    """
    now = now or datetime.now()
    age_min = None
    if published_at is not None:
        age_min = round((now - published_at).total_seconds() / 60, 1)

    # ① 窗口未到 / 无题材 / 无发布时间 → unknown（显式「未判定」，不臆造）
    if not theme_targets:
        return {"status": "unknown", "basis": "事件无关联题材", "new_limit_ups": 0,
                "net_inflow_yi": None, "age_minutes": age_min}
    if published_at is None or age_min is None or age_min < MIN_AGE_MINUTES:
        return {"status": "unknown", "basis": "采样窗口未到（事件发布 <30 分钟）",
                "new_limit_ups": 0, "net_inflow_yi": None, "age_minutes": age_min}

    # ② 事件后新涨停：reason 含关联题材 且 封板 ≥ 事件发布
    new_limit_ups = 0
    for r in limit_up_pool:
        reason = str(r.get("reason") or "")
        seal = r.get("first_seal_time")
        if any(_theme_in_reason(t, reason) for t in theme_targets) and _seal_after(published_at, seal, trade_date):
            new_limit_ups += 1

    # ③ 关联板块主力净流入（f62，亿；None=映射不到）
    net = board_fund.get("main_net_yi") if board_fund else None

    # ④ 四态判定
    has_new = new_limit_ups > 0
    inflow = net is not None and net > 0
    outflow = net is not None and net < 0

    if has_new and inflow:
        status, basis = "confirmed", f"事件后新涨停 {new_limit_ups} 家 且 关联板块主力净流入 {net:+.2f} 亿"
    elif has_new:
        status, basis = "fermenting", f"事件后新涨停 {new_limit_ups} 家，但板块资金未净流入"
    elif inflow:
        status, basis = "fermenting", f"关联板块主力净流入 {net:+.2f} 亿，但尚无事件后新涨停"
    elif outflow:
        status, basis = "faded", f"事件后无新涨停 且 关联板块主力净流出 {net:+.2f} 亿"
    elif net == 0:
        status, basis = "faded", "事件后无新涨停 且 关联板块主力净额为 0（未获资金认可）"
    else:
        status, basis = "unknown", "事件后无新涨停，板块资金映射不到（数据不足，不臆造）"

    return {
        "status": status,
        "basis": basis,
        "new_limit_ups": new_limit_ups,
        "net_inflow_yi": net,
        "age_minutes": age_min,
    }


async def verify_active_events(
    store,
    hub,
    limit_up_pool: list[dict],
    trade_date: date,
    *,
    limit: int = 30,
) -> list[dict]:
    """编排：活跃事件 → 批量验证（实时，不落库）。

    涨停池由调用方传入（避免本函数自行拉取——端点/调度可复用已加载的池，
    也便于测试注入受控桩）；板块资金经 `board_rows_for_names` 一次性批取。
    """
    events = store.list_events(active_only=True, limit=limit)
    if not events:
        return []

    # 所有事件的题材 target 去重 → 一次批取板块资金（L3 映射，命中 30s 缓存）
    all_targets: list[str] = []
    for e in events:
        for d in e.directions:
            if d.target_type == "theme" and d.target not in all_targets:
                all_targets.append(d.target)

    from app.services.theme_service import board_rows_for_names

    board_rows = await board_rows_for_names(all_targets) if all_targets else {}

    out: list[dict] = []
    for e in events:
        themes = [d.target for d in e.directions if d.target_type == "theme"]
        # 关联板块资金：取匹配到的板块里净额绝对值最大的（「资金最想说的那个板块」）
        matched = [board_rows[t] for t in themes if t in board_rows]
        fund = None
        if matched:
            fund = max(
                (r for r in matched if r.get("main_net_yi") is not None),
                key=lambda r: abs(r.get("main_net_yi") or 0),
                default=matched[0],
            )
        verdict = verify_event(
            published_at=e.published_at,
            theme_targets=themes,
            limit_up_pool=limit_up_pool,
            board_fund=fund,
            trade_date=trade_date,
        )
        out.append({
            "event_id": e.id,
            "title": e.title,
            "published_at": e.published_at.isoformat() if e.published_at else None,
            "themes": themes,
            "board_name": fund.get("name") if fund else None,
            **verdict,
        })
    return out
