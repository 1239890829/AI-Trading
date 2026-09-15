"""智能体与复盘工具：参数变更、任务、市场气候、复盘、晨报。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations


from .core import (
    ToolContext,
    _clip,
    _fmt_rows,
    _rec,
    _resolve_date,
    log,
)
async def _t_param_changes(ctx: ToolContext, **kw) -> str:
    """参数变更单（审计留痕，P2-28① 批量）。

    白名单之外不可改；每次应用/回滚都有留痕。取不到时说明，不编造变更记录。
    """
    if ctx.session_factory is None:
        return "参数变更：无数据源（session_factory 未提供）"
    try:
        from sqlalchemy import select

        from app.models.agent import AgentParamChange

        with ctx.session_factory() as db:
            rows = db.execute(
                select(AgentParamChange).order_by(AgentParamChange.id.desc()).limit(20)
            ).scalars().all()
            recs = [{"id": r.id, "key": r.key, "before": r.before, "after": r.after,
                     "status": r.status,
                     "at": r.created_at.isoformat() if r.created_at else ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        return f"参数变更：读取失败（{exc}）"
    if not recs:
        return "参数变更：暂无变更记录"
    return _fmt_rows("参数变更（最近 20 条）", recs, [
        ("id", ""), ("key", "参数"), ("before", "原值"), ("after", "新值"),
        ("status", "状态"), ("at", "时间"),
    ], total=len(recs))


async def _t_agent_tasks(ctx: ToolContext, **kw) -> str:
    """任务中心：最近任务的类型/状态/风险等级（P2-28① 批量）。"""
    if ctx.session_factory is None:
        return "任务中心：无数据源（session_factory 未提供）"
    try:
        from sqlalchemy import select

        from app.models.agent import AgentTask

        with ctx.session_factory() as db:
            rows = db.execute(
                select(AgentTask).order_by(AgentTask.id.desc()).limit(20)
            ).scalars().all()
            recs = [{"id": r.id, "type": r.type, "status": r.status,
                     "risk_level": r.risk_level or "",
                     "at": r.created_at.isoformat() if r.created_at else ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        return f"任务中心：读取失败（{exc}）"
    if not recs:
        return "任务中心：暂无任务"
    return _fmt_rows("任务中心（最近 20 条）", recs, [
        ("id", ""), ("type", "类型"), ("status", "状态"), ("risk_level", "风险"), ("at", "时间"),
    ], total=len(recs))


async def _t_review(ctx: ToolContext, **kw) -> str:
    from app.review.storage import get_report

    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    rep = get_report(ctx.session_factory, d.isoformat())
    if rep is None:
        return f"{d} 没有复盘报告"
    summary = getattr(rep, "summary", None) or {}
    lines = [f"【复盘报告 {d}】"]
    if isinstance(summary, dict):
        for k in ("headline", "market_summary", "conclusion"):
            if summary.get(k):
                lines.append(f"- {k}：{summary[k]}")
    items = list(getattr(rep, "action_items", None) or [])
    if items:
        lines.append(f"- 改进项 {len(items)} 条：")
        for it in items[:10]:
            dd = _rec(it)
            lines.append(f"  · [{dd.get('status', '—')}] {dd.get('title', '')[:40]}")
    return _clip("\n".join(lines))


async def _t_brief(ctx: ToolContext, **kw) -> str:
    from app.picks.morning_brief import brief_for_today

    target, payload = brief_for_today()
    if payload is None:
        return f"{target} 尚无盘前简报"
    lines = [f"【盘前简报 {target}】"]
    for d in (payload.get("directions") or [])[:5]:
        lines.append(
            f"- {d.get('direction', '')}｜{d.get('entry_mode', '—')}"
            f"｜触发：{'; '.join((d.get('trigger_conditions') or [])[:2]) or '—'}"
        )
    lines.append(f"- 提醒：{len(payload.get('alerts') or [])} 条")
    return _clip("\n".join(lines))


async def _t_climate(ctx: ToolContext, **kw) -> str:
    """气候一阶相位（ENSO/ONI）→ 板块前瞻线索（P1-32，2026-09-11）。

    ⚠️ **必须同时输出两段声明**：①`timing_note`（ONI 滞后、无领先性）
    ②`empirical_verdict`（人工传导链**未获数据支持**，基础化工方向甚至相反）。
    少任何一段，助手都可能把它转述成「厄尔尼诺来了，买化肥」（红线 3）。
    """
    from app.market.climate import collect as _collect_climate

    try:
        payload = await _collect_climate()
    except Exception as exc:  # noqa: BLE001  取不到如实说，不编造
        log.warning("climate tool failed: %s", exc)
        return "气候指数（NOAA ONI）取数失败——请如实说明取不到，不要编造相位"
    if not payload:
        return "气候指数暂不可用（NOAA ONI 不可达）——如实说明无数据即可"

    lines = ["【气候一阶相位（NOAA ONI，重叠三月季）】"]
    state = payload.get("state")
    note = payload.get("unjudged_reason")
    if state is None:
        lines.append(f"- 本次未判定：{note or '数据不足'}")
    else:
        label = {"el_nino": "厄尔尼诺", "la_nina": "拉尼娜", "neutral": "中性"}.get(state, state)
        strength = payload.get("strength")
        band = {"weak": "弱", "moderate": "中等", "strong": "强", "very_strong": "超强"}
        seg = f"（{band.get(strength, strength)}）" if strength else ""
        alert = payload.get("alert") or ""
        lines.append(
            f"- 当前相位：**{label}**{seg}；同向连续 {payload.get('consecutive')} 个季"
            + (f"；行业上 ONI 阈值 ±{payload.get('threshold')}，"
               f"连续 {payload.get('persist_seasons')} 季才算「确立」" if alert else "")
        )
        if alert:
            lines.append(
                f"- **预警态**：已连续越线 {payload.get('consecutive')} 季但未满 "
                f"{payload.get('persist_seasons')} 季 ⇒ 尚未构成官方「{alert} 确立」"
            )
        latest = payload.get("latest") or {}
        if latest:
            lines.append(
                f"- 最新季：{latest.get('season')} {latest.get('year')}"
                f"（季末 {latest.get('end_date')}）ANOM={latest.get('anom'):+.2f}"
            )
        series = payload.get("series") or []
        if series:
            lines.append(
                "- 近几季：" + "、".join(f"{s['season']} {s['anom']:+.2f}" for s in series)
            )
        links = payload.get("candidate_links") or []
        if links:
            lines.append(
                "- 候选题材（**人工映射，未获数据支持，仅线索**）："
                + "、".join(f"{r['target']}（强度{r['strength']}）" for r in links)
            )

    lines.append(f"- 时点结构（重要）：{payload.get('timing_note')}")
    lines.append(f"- 实证判读（重要）：{payload.get('empirical_verdict')}")
    lines.append(f"- 数据时间 {payload.get('as_of') or '—'}；{payload.get('disclaimer')}")
    return _clip("\n".join(lines))
