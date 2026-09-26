"""事件与资讯工具：事件池、告警事件、情绪、资讯、传导链。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import asyncio
from app.core.bjtime import beijing_now

from .core import (
    ToolContext,
    _clip,
    _fmt_rows,
    _int_arg,
    _rec,
)
async def _t_alert_events(ctx: ToolContext, **kw) -> str:
    """预警触发记录（P2-28① 清单外补登记）。

    数据源是 `alert_event` 表，走 `AlertRepository.list_events`（不自己拼 SQL——
    仓库层已封装口径）。

    ⚠️ `triggered_at` 是**北京时间**（该列用 `beijing_now_naive`），与 agent 域的
    UTC naive **不同**（见结转表 #6）——直接展示即可，**不得再 +8h**。
    """
    if ctx.session_factory is None:
        return "预警记录：无数据源（session_factory 未提供）"
    try:
        limit = int(kw.get("limit") or 20)
    except (TypeError, ValueError):
        return "参数不合法：limit 必须是整数"
    limit = max(1, min(limit, 50))
    sym = (kw.get("symbol") or "").strip() or None

    try:
        from app.repositories.alert_repo import AlertRepository

        # 2026-09-12 实测判定**不搬线程**：`AlertRepository.list_events(limit≤50)` 中位 **0.36ms**
        # （对照 `EventStore.list_events` 7.2~85ms）⇒ 毫秒级，与 `ensure_system_rule` 同族。
        events = AlertRepository(ctx.session_factory).list_events(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return f"预警记录：读取失败（{exc}）"

    rows = [
        {"id": e.id, "rule_id": e.rule_id, "symbol": e.symbol,
         "trigger_value": getattr(e, "trigger_value", None),
         "threshold": getattr(e, "threshold", None),
         "at": e.triggered_at.isoformat() if getattr(e, "triggered_at", None) else ""}
        for e in (events or [])
        if not sym or str(getattr(e, "symbol", "")) == sym
    ]
    if not rows:
        return f"预警记录：暂无触发记录（symbol={sym or '全部'}）"
    return _fmt_rows(
        f"预警触发记录（{sym or '全部'}，最近 {len(rows)} 条 · 时间为北京时间）",
        rows,
        [("id", ""), ("symbol", ""), ("trigger_value", "触发值"), ("threshold", "阈值"),
         ("at", "时间")],
        total=len(rows),
    )


async def _t_sentiment(ctx: ToolContext, **kw) -> str:
    """近几日情绪相位（AI 大脑 P1：助手读得到市场温度）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    from app.market.sentiment_history import get_history

    import asyncio as _asyncio

    rows = await _asyncio.to_thread(get_history, ctx.session_factory, 5)
    if not rows:
        return "尚无情绪历史存档"
    lines = ["【近 5 日情绪相位】"]
    for r in rows[:5]:
        d = _rec(r)
        lines.append(
            f"- {d.get('trade_date', '—')}：{d.get('phase', '—')}"
            f"｜温度 {d.get('temperature') if d.get('temperature') is not None else '—'}"
            f"｜置信 {d.get('confidence') or '—'}"
            f"{'（相位不可靠）' if d.get('phase_unreliable') else ''}"
        )
    return _clip("\n".join(lines))


async def _t_events(ctx: ToolContext, **kw) -> str:
    """今日 watcher 异动/确认/证伪事件（AI 大脑 P1：助手读得到盘中事件流）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    from datetime import datetime

    from sqlalchemy import select

    from app.models.alert import AlertEvent, AlertRule
    from app.picks.watcher import WATCHER_RULE_NAME
    def _q():
        with ctx.session_factory() as db:  # type: ignore[misc]
            rule_ids = db.execute(
                select(AlertRule.id).where(AlertRule.name == WATCHER_RULE_NAME)
            ).scalars().all()
            if not rule_ids:
                return []
            rows = db.execute(
                select(AlertEvent)
                .where(AlertEvent.rule_id.in_(rule_ids))
                .order_by(AlertEvent.id.desc())
                .limit(40)
            ).scalars().all()
            return [
                {"triggered_at": str(r.triggered_at), "snapshot": r.snapshot}
                for r in rows
            ]

    import asyncio as _asyncio
    import json as _json

    rows = await _asyncio.to_thread(_q)
    today = beijing_now().strftime("%Y-%m-%d")
    today_rows = []
    for r in rows:
        # triggered_at 已是**北京 naive**（2026-09-09 口径统一）→ 直接比对，
        # 不再 +8h（旧注释「UTC naive」是统一前的残留，双重偏移会让 16:00
        # 之后的告警落到次日而被过滤掉）。
        try:
            dt = datetime.fromisoformat(r["triggered_at"])
        except Exception:  # noqa: BLE001
            continue
        if dt.strftime("%Y-%m-%d") == today:
            today_rows.append(r)
    if not today_rows:
        return "今日暂无 watcher 事件（含确认/证伪/大单异动）"
    lines = [f"【今日 watcher 事件 {len(today_rows)} 条】"]
    for r in today_rows[:10]:
        snap = r["snapshot"]
        if isinstance(snap, str):
            try:
                snap = _json.loads(snap)
            except Exception:  # noqa: BLE001
                snap = {}
        lines.append(f"- {snap.get('text') or snap.get('kind') or '（无文本）'}")
    return _clip("\n".join(lines))


async def _t_news(ctx: ToolContext, **kw) -> str:
    """全网资讯/快讯事件流（热点消息 + 利好利空方向映射）。

    2026-09-11 补：用户实测助手答「我今天没有全网新闻/资讯类的实时数据源，无法直接列
    今日热点新闻」——**这是错的**，系统有完整的新闻事件管道（`app/news/flash.py` 双域
    快讯采集 → `EventStore` → `GET /api/events*`，即市场页「事件面板」的数据源），
    只是助手侧没登记工具。与个股消息面（公告/新闻标题）不同，这里给的是**全市场**视角。

    ⚠️ 输出必须保留方向行的 `basis`（依据）——事件→题材的方向映射是**系统推断**，
    不是官方结论（红线 3：只给关联 + 依据 + 失效条件，不得转述成买卖建议）。
    """
    store = ctx.event_store
    if store is None:
        return "资讯事件流不可用：事件库未初始化（如实说明取不到即可，不要编造新闻）"
    limit = _int_arg(kw.get("limit"), 10, 3, 30)
    try:
        rows = list(await asyncio.to_thread(store.list_events, active_only=True, limit=limit) or [])
    except Exception as exc:  # noqa: BLE001
        return f"资讯事件流读取失败：{type(exc).__name__}: {exc}"
    if not rows:
        return "当前无活跃资讯事件（非交易时段/快讯采集未产出，属正常空态）"

    lines = [
        f"【活跃资讯事件 {len(rows)} 条（来源：系统快讯事件流，与市场页事件面板同源）】",
        "- 口径：事件与方向映射由系统规则/LLM 从公开快讯抽取，"
        "**方向是推断不是官方结论**，只作线索；不构成买卖建议。",
    ]
    for r in rows:
        rec = _rec(r)
        when = str(rec.get("published_at") or "")[:16]
        lines.append(
            f"- [{rec.get('source') or '—'}｜{when}] {str(rec.get('title') or '')[:60]}"
        )
        ref = rec.get("interpretation_ref")
        if isinstance(ref, dict) and ref.get("version_id") is not None and ref.get("available_at"):
            lines.append(
                f"  · 解释版本：{ref['version_id']}｜可见：{ref['available_at']}"
                f"｜状态：{ref.get('state') or 'unknown'}"
            )
        else:
            lines.append("  · 解释版本：未知（旧卡无可追溯版本；方向仅供线索）")
        summary = rec.get("summary")
        if summary:
            lines.append(f"  · 摘要：{str(summary)[:80]}")
        dirs = rec.get("directions") or []
        for d in dirs[:3]:
            dd = _rec(d)
            lines.append(
                f"  · 方向推断：{dd.get('target') or '—'}"
                f"（{dd.get('direction') or '—'}，强度 {dd.get('strength') or '—'}）"
                f"｜依据：{str(dd.get('basis') or '—')[:50]}"
                + ("｜状态：待验证假设" if dd.get("matched_by") == "llm_aux" else "")
            )
    return _clip("\n".join(lines))


async def _t_chain(ctx: ToolContext, **kw) -> str:  # noqa: ARG001 — 纯函数工具，不需要 ctx
    """事件→板块传导链检索（P2-5，`chain|keyword=`）。

    需求形态的澄清（账本 P2-5 曾把两件事混为一件）：
    - `events|hot` **已无必要**——「热榜」由 `hot` 工具覆盖 ⇒ 该子项销账；
    - `chain|keyword` 是**真的缺**：`chains.py` 原有的是 `match_chains(title)`——
      **给定一条新闻标题、返回它命中哪些链**（正向，抽取时用）；而需求是
      **给定一个关键词、返回相关链**（反向索引，检索时用）。两者语义不同，
      故新增 `find_chains()` 而非改 `match_chains`。

    三条纪律（测试锁住）：
    1. **必须随结论给口径**——本表是**人工维护的映射索引，不是已验证的行情规律**；
       其中强度/弹性数字源自单日单案例观察（KB-DEC-019 反固化条款）。只报
       "厄尔尼诺→化肥" 而不带这句，模型会把它当选股依据讲给用户。
    2. **方向不固定的链不给方向**——非农/利率的 direction 取决于事件正文里的
       意外差/主导矛盾，这里无从判定，如实说"待判"，绝不填个默认值。
    3. 未命中时给**触发词示例**帮模型换个说法，而不是只说"没找到"。
    """
    from app.events.chains import chain_keywords, find_chains

    keyword = (kw.get("keyword") or "").strip()
    if not keyword:
        return ("参数缺失：keyword（事件关键词，如 厄尔尼诺 / 非农 / 加息 / OpenAI）。"
                f"当前支持的触发词：{'、'.join(chain_keywords())}")

    hits = find_chains(keyword)
    if not hits:
        return (f"未找到与「{keyword}」相关的传导链。传导链按**触发词**建索引，"
                f"当前支持的触发词：{'、'.join(chain_keywords())}")

    lines = [f"【传导链检索：{keyword}】命中 {len(hits)} 条"]
    for g in hits:
        lines.append(f"## {g['label']}（命中触发词「{g['matched_keyword']}」）")
        if g["targets"]:
            for t in g["targets"]:
                lines.append(f"- {t['target']}（类型 {t['target_type']}，"
                             f"方向 {'+' if t['direction'] > 0 else ''}{t['direction']}，"
                             f"强度 {t['strength']}）")
        else:
            lines.append("- 该链的目标板块随事件正文动态确定，此处不预置")
        lines.append(f"- 方向口径：{g['direction_note']}")
    lines.append(
        "- ⚠️ 口径：本表是**人工维护的映射索引（关键词 → 题材），不是已验证的行情规律**；"
        "强度/弹性数字源自单日单案例观察、**未经数据验证**，只作线索与可解释性参考，"
        "不得当结论引用、不得据此跨题材外推。"
    )
    lines.append("- ⚠️ 不构成买卖建议。")
    return _clip("\n".join(lines))
