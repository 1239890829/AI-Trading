"""P2-3 层1：pending 事件 LLM 辅助判定（攒批模式）。

规则引擎判不出方向的事件（direction=0 / 无方向行，且未超半衰期）——
攒够 `min_batch` 条后一次 LLM 调用批量判定（摊薄 claude CLI 冷启动），
命中（direction≠0）写 EventDirection 行（matched_by=llm_aux，basis 注明
判定源=LLM 辅助）；**无论命中与否，全批都记 llm_judged_at**——防重复
调用烧钱（每条事件一生至多让 LLM 判一次，判中性也是"已判过"）。

安全与纪律（对照 docs/summary/ai-evolution.md §7 层1）：
- 判定源标 llm_aux，不伪装规则命中（与 alert_triage llm_fallback 同纪律）；
- theme 必须匹配官方题材目录名（子串/词干），不臆造新题材；
- 调用失败整批跳过（不写任何状态），下轮自然重试；超时/无输出不抛致命错；
- 默认关闭（settings.event_llm_aux_enabled=False），显式开才跑。
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.event import EventCard, EventDirection
from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)

# 题材目录名词干（与 extract._NAME_SUFFIX 同口径；短词干不参与防误命中）
_NAME_SUFFIX = re.compile(r"(概念|板块|产业|指数)$")


def _stem(name: str) -> str:
    s = _NAME_SUFFIX.sub("", name or "").strip()
    return s if len(s) >= 2 else ""


def _match_theme(theme: str | None, theme_names: list[str]) -> str | None:
    """LLM 给的题材名 → 目录内最佳匹配（整名 > 词干 > 包含）；无匹配返回 None。"""
    if not theme:
        return None
    t = str(theme).strip()
    if t in theme_names:
        return t
    stem = _stem(t)
    for name in theme_names:
        if _stem(name) == stem and len(stem) >= 2:
            return name
    for name in theme_names:  # LLM 常给泛词（如「半导体」对「半导体概念」）→ 词干包含兜底
        if len(stem) >= 2 and stem in _stem(name):
            return name
    return None


def _pending_candidates(sf, *, theme_names: list[str], max_batch: int,
                        age_max_h: float) -> list[EventCard]:
    """候选：active、无 direction≠0 行、llm_judged_at IS NULL、近 age_max_h 小时发布。
    仅收「官方目录里找得到题材名」的事件——LLM 连题材归属都判不出的事件
    （纯数据罗列/无题材驱动）不值得花钱判，直接在扫描层排除。
    """
    now = beijing_now_naive()
    rows = []
    with sf() as db:
        stmt = (
            select(EventCard)
            .where(EventCard.status == "active")
            .where(EventCard.llm_judged_at.is_(None))
            .order_by(EventCard.published_at.desc())
            .limit(max_batch * 4)  # 放大取数：下面还要过滤有方向行/超龄
        )
        for row in db.execute(stmt).scalars():
            if row.published_at is None:
                continue
            age_h = (now - row.published_at).total_seconds() / 3600
            if age_h < 0 or age_h > age_max_h:
                continue
            has_dir = any(d.direction != 0 for d in row.directions)
            if has_dir:
                continue
            rows.append(row)
            if len(rows) >= max_batch:
                break
    return rows


def _mark_judged(sf, rows: list[EventCard]) -> None:
    """整批记 llm_judged_at（无论命中与否）。"""
    now = beijing_now_naive()
    with sf() as db:
        for r in rows:
            row = db.get(EventCard, r.id)
            if row is not None:
                row.llm_judged_at = now
        db.commit()


def _insert_directions(sf, event_id: int, hits: list[dict]) -> int:
    """命中结果 → EventDirection 行（matched_by=llm_aux）。返回写入行数。"""
    with sf() as db:
        row = db.get(EventCard, event_id)
        if row is None:
            return 0
        existing = {(d.target_type, d.target) for d in row.directions}
        n = 0
        for h in hits:
            key = ("theme", h["target"])
            if key in existing:
                continue
            db.add(EventDirection(
                event_id=event_id,
                target_type="theme",
                target=h["target"],
                direction=int(h["direction"]),
                strength=1,
                chain=h.get("chain") or "",
                basis=h.get("basis") or "LLM 辅助判定（规则未命中方向词）",
                matched_by="llm_aux",
            ))
            existing.add(key)
            n += 1
        db.commit()
        return n


def _deepseek_items(cands: list[EventCard]) -> tuple[list[dict] | None, str | None]:
    """让当前 DeepSeek 对指定事件做题材/方向判定；不写库。"""
    from app.core.config import settings
    from app.core.llm_client import LLMError, chat_completion, extract_json_object

    titles = [c.title for c in cands]
    prompt = (
        "你是 A 股消息面判定器。对以下新闻批量判断其对 A 股题材的方向影响。\n"
        "只输出 JSON，不要解释。格式：{\"items\":[{\"direction\":-1|0|1,"
        "\"theme\":\"受影响题材名(无则null)\",\"chain\":\"传导一句话(无则空)\","
        "\"reason\":\"一句话依据\"}]}\n"
        "direction: 1=利好相关题材/标的, -1=利空, 0=中性(纯数据罗列/常规澄清/"
        "无题材驱动)。items 数量必须与输入条数一致，顺序对应。\n"
        "注意：龙虎榜/成交量/资金流向/限售解禁数据类、公司常规澄清通常为 0；"
        "政策定调/产业事件/海外映射等明确方向才非 0。"
    )
    try:
        raw = chat_completion(
            base_url=settings.review_llm_base_url,
            api_key=settings.review_llm_api_key,
            model=settings.review_llm_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": __import__("json").dumps(titles, ensure_ascii=False)},
            ],
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=180.0,
        )
    except LLMError as exc:
        return None, f"LLM 调用失败：{exc}"
    except Exception as exc:
        return None, f"异常：{exc}"
    try:
        parsed = extract_json_object(raw) or {}
        items = parsed.get("items") or []
        if not isinstance(items, list) or len(items) != len(titles):
            return None, f"输出条数不符（{len(items)}/{len(titles)}）"
        return items, None
    except Exception as exc:
        return None, f"解析失败：{exc}"


def _jev_actionability(cands: list[EventCard]) -> dict[int, float] | None:
    """批量判断事件是否值得继续做非零 A 股题材方向判定。

    只输出每条事件的 Noul 概率，不生成题材名、不写库。失败返回 None；
    shadow/cascade 如何消费由 judge_pending_batch 决定。
    """
    from app.core.config import settings
    from app.core.jev_client import evaluate

    mode = str(getattr(settings, "jev_event_aux_mode", "off") or "off").strip().lower()
    if mode not in {"shadow", "cascade"} or not cands:
        return None
    ids = [c.id for c in cands]
    if any(event_id is None for event_id in ids) or len(set(ids)) != len(ids):
        # pending candidates must be persisted EventCard rows; ambiguous identity makes
        # answer-to-event alignment unsafe, so do not spend an API call.
        log.warning("jev event prefilter skipped: candidate ids must be unique and non-null")
        return None

    events = [
        {
            "title": c.title,
            "summary": str(c.summary or "")[:1200],
            "source": c.source,
            "source_tier": c.source_tier,
        }
        for c in cands
    ]
    questions: dict[str, dict] = {}
    for idx in range(len(cands)):
        questions[f"event_{idx}"] = {
            "type": "noul",
            "instructions": {
                "task": f"判断 events[{idx}] 是否存在足够直接、可解释的 A 股题材方向催化，值得后续赋非零方向。",
                "rules": [
                    "纯行情、龙虎榜/成交量/资金流向等交易统计通常不是题材催化。",
                    "常规澄清、无明确传导的海外公司消息、信息不足通常为否。",
                    "明确产业政策、供需变化、重大签约中标、量产突破、制裁/限制及可解释海外映射可为是。",
                    "这里只判断是否值得继续做题材方向判定，不预测股票涨跌。",
                ],
            },
            "criteria": {
                "true": "存在直接的产业/政策/公司事件及可解释传导，值得继续判断受影响题材与方向。",
                "false": "缺乏直接题材催化，或主要是纯数据/行情/常规澄清/弱关联。",
            },
        }

    result = evaluate({"events": events}, questions, purpose="event_llm_aux")
    if not result.get("ok"):
        return None
    answers = result.get("answers") or {}
    out: dict[int, float] = {}
    for idx, cand in enumerate(cands):
        answer = answers.get(f"event_{idx}")
        if not isinstance(answer, dict):
            return None
        p = answer.get("noul")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0.0 <= float(p) <= 1.0:
            return None
        out[cand.id] = float(p)
    return out


def judge_pending_batch(sf=None, *, theme_names: list[str] | None = None,
                        min_batch: int | None = None, max_batch: int | None = None,
                        age_max_h: float | None = None) -> dict:
    """攒批 LLM 判定一轮。返回统计（纯同步实现，LLM 调用阻塞——由调用方放线程池）。

    theme_names 为空 → 目录未同步 → 跳过（不臆造题材）。
    调用失败/解析不出 → 整批不写任何状态，返回 skipped=True（下轮重试）。
    """
    from app.core.config import settings

    if not settings.event_llm_aux_enabled:
        return {"skipped": True, "reason": "event_llm_aux_enabled=False（默认关）"}
    sf = sf or get_session_factory()
    theme_names = theme_names or []
    if not theme_names:
        return {"skipped": True, "reason": "题材目录未同步（theme_names 空）"}

    mn = min_batch if min_batch is not None else settings.event_llm_aux_min_batch
    mx = max_batch if max_batch is not None else settings.event_llm_aux_max_batch
    am = age_max_h if age_max_h is not None else settings.event_llm_aux_age_max_h

    cands = _pending_candidates(sf, theme_names=theme_names, max_batch=mx, age_max_h=am)
    if len(cands) < mn:
        return {"skipped": True, "reason": f"候选 {len(cands)} < 攒批下限 {mn}"}

    from app.core.jev_client import record_comparison

    mode = str(getattr(settings, "jev_event_aux_mode", "off") or "off").strip().lower()
    jev_probs = _jev_actionability(cands) if mode in {"shadow", "cascade"} else None

    # cascade 只允许“极高把握为中性/弱关联”的事件跳过 DeepSeek。
    # 正向/利空事件仍交 DeepSeek 产出官方题材归属；Jev 不自由生成题材。
    pre_neutral: set[int] = set()
    deepseek_cands = list(cands)
    if mode == "cascade" and jev_probs:
        neutral_max = float(getattr(settings, "jev_event_aux_neutral_max_noul", 0.05))
        pre_neutral = {
            cand.id for cand in cands
            if float(jev_probs.get(cand.id, 1.0)) <= neutral_max
        }
        deepseek_cands = [cand for cand in cands if cand.id not in pre_neutral]

    items_by_id: dict[int, dict] = {
        event_id: {"direction": 0, "theme": None, "chain": "", "reason": "Jev高置信中性前置"}
        for event_id in pre_neutral
    }
    if deepseek_cands:
        deepseek_items, error = _deepseek_items(deepseek_cands)
        if deepseek_items is None:
            # 保持原有整批语义：只要还有 DeepSeek 子集失败，本轮一个事件都不标记，
            # 包括已被 Jev 判中性的行，避免半批状态。
            log.warning("llm_aux judge failed (batch skipped, retry next round): %s", error)
            return {"skipped": True, "reason": error or "LLM 判定失败"}
        items_by_id.update({
            cand.id: item for cand, item in zip(deepseek_cands, deepseek_items)
        })

    hit_count = mark_count = 0
    for cand in cands:
        item = items_by_id.get(cand.id)
        if not isinstance(item, dict):
            continue
        try:
            direction = int(item.get("direction") or 0)
        except (TypeError, ValueError):
            continue
        theme = _match_theme(item.get("theme"), theme_names)
        actionable = direction != 0 and theme is not None

        # shadow/cascade 中仍进 DeepSeek 的事件都有参考结论；Noul 没有独立
        # confidence，因此只记一致/分歧，不伪造 avg_confidence。
        if jev_probs is not None and cand.id not in pre_neutral:
            record_comparison(
                "event_llm_aux",
                "actionable" if float(jev_probs[cand.id]) >= 0.5 else "neutral",
                "actionable" if actionable else "neutral",
                confidence=None,
            )

        if not actionable:
            continue  # 中性/题材对不上 → 不落行（但该事件已试过，下面统一标记）
        n = _insert_directions(sf, cand.id, [{
            "target": theme,
            "direction": direction,
            "chain": str(item.get("chain") or "")[:256],
            "basis": f"LLM 辅助判定：{str(item.get('reason') or '')[:120]}"[:256],
        }])
        hit_count += n
        mark_count += 1

    # 无论命中与否，整批都标记「已判过」——防每轮重复烧钱
    _mark_judged(sf, cands)
    return {
        "skipped": False,
        "candidates": len(cands),
        "hit_events": mark_count,
        "directions_written": hit_count,
        "jev_mode": mode,
        "jev_prefiltered_neutral": len(pre_neutral),
        "deepseek_candidates": len(deepseek_cands),
    }


async def llm_aux_loop(app, *, stop: asyncio.Event) -> None:
    """常驻低频轮询（P2-3 层1 自动调度者）：交易时段每 interval 秒攒批判定一轮。

    何时跑：开关开启 + 交易时段内（盘外不判——盘后事件本就要到次日盘中
    才有题材效应，且避免盘后无人值守时花钱）。启动先等 5 分钟让目录同步/
    快讯首轮完成。失败/攒批不足 → 静默跳过下轮（judge_pending_batch 自身
    返回 skipped 不抛）。开关默认关 → 每轮快速跳过（仅目录读取开销，可控）。
    """
    from app.core.config import settings
    from app.core.scheduler import wait_or_stop

    interval = settings.event_llm_aux_loop_interval
    log.info("llm_aux loop started (interval=%.0fs, enabled=%s)",
             interval, settings.event_llm_aux_enabled)
    # 启动让目录/快讯首轮就绪。S2-2 收尾（09-11）：改 wait_or_stop——裸 sleep 300s
    # 时停机只是「恰好醒着」才退，否则白烧一个宽限窗口再被强制 cancel。
    if await wait_or_stop(stop, 300):
        return
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        if not settings.event_llm_aux_enabled:
            continue
        # 盘外跳过（判定价值在盘中；避免无人值守花钱）
        try:
            from app.market.trade_calendar import in_trading_window

            if not in_trading_window():
                continue
        except Exception:  # noqa: BLE001 —— 日历异常不阻断
            continue
        try:
            # theme_names 口径与事件抽取一致（app.state.theme_catalog）
            svc = getattr(app.state, "theme_catalog", None)
            if svc is None:
                continue
            names = [t.name for t in svc.get_catalog(limit=1000)]
            result = await asyncio.to_thread(judge_pending_batch, theme_names=names)
            if not result.get("skipped"):
                log.info("llm_aux auto batch: candidates=%s hit=%s written=%s",
                         result.get("candidates"), result.get("hit_events"),
                         result.get("directions_written"))
            elif "攒批下限" not in str(result.get("reason", "")):
                log.info("llm_aux auto batch skipped: %s", result.get("reason"))
        except Exception:  # noqa: BLE001 —— 常驻 loop 永不自灭
            log.exception("llm_aux loop iteration failed")
    log.info("llm_aux loop stopped")
