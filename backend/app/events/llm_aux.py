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
import json
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import aliased

from app.core.db import get_session_factory
from app.models.event import EventCard, EventDirection, EventObservation, EventInterpretation
from app.core.bjtime import beijing_now_naive
from app.events.store import LLM_AUX_UNAPPLIED_PREFIX, record_interpretation

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


def _market_action_only(title: str, summary: str | None) -> bool:
    text = title + "\n" + (summary or "")
    market = re.search(r"涨停|跌停|震荡反弹|低开高走|概念.{0,8}(?:上涨|下跌|走强)|ETF.{0,20}(?:涨|跌)[0-9]", text, re.I)
    material = re.search(r"签约|中标|量产|政策|获批|预增|短缺|供需|产能|涨价|公告|澄清|调查|收购|重组|回购|订单|供货|项目|投资", text)
    return bool(market and not material)


def _unfulfilled_claim_conflict(source: dict, explanation: str) -> bool:
    """One literal same-subject conflict; not a general entailment check."""
    subject = r"(?<![\w\u4e00-\u9fff])(?P<subject>本公司|该公司|公司|本项目|该项目|相关项目)"
    bridge = r"(?P<bridge>[^。；;\n，,]{0,18}?)"
    tail = r"(?P<objects>[^。；;\n，,]{0,20})"
    negative = re.compile(subject + bridge + r"(?:尚未|暂未|未)(?:形成|获得|产生|实现|兑现)" + tail)
    positive = re.compile(subject + bridge + r"(?:已经|已)(?:形成|获得|产生|实现|兑现)" + tail)
    positive_claims = []
    for claim in positive.finditer(explanation):
        words = claim["bridge"] + claim["objects"]
        if any(term in words for term in ("是否", "不能确认", "未确认", "待核", "拟", "计划")):
            continue
        positive_claims.append((claim["subject"], {term for term in ("订单", "收入") if term in words}))
    for field in ("title", "summary"):
        text = source.get(field) or ""
        for claim in negative.finditer(text):
            objects = {term for term in ("订单", "收入") if term in claim["bridge"] + claim["objects"]}
            # A clause can describe prior non-realization and a current change,
            # eliding its subject after the comma. Preserve that counterexample.
            end = text.find("。", claim.start())
            clause = text[claim.start():end if end >= 0 else len(text)]
            transition = re.search(r"(?:现在|现已|如今|目前)(?:已经|已)?(?:形成|获得|产生|实现|兑现)([^。；;\n，,]{0,20})", clause)
            if (any(term in claim["bridge"] for term in ("此前", "过去", "曾"))
                    and transition and any(term in transition[1] for term in objects)):
                continue
            if any(who == claim["subject"] and objects & obj for who, obj in positive_claims):
                return True
    return False


def _pending_candidates(sf, *, theme_names: list[str], max_batch: int,
                        age_max_h: float) -> list[EventCard]:
    """候选：无复核记录的 active 解释、无非零方向、未调用过 LLM、近时发布。
    目录约束在判读结果入库前验证；候选不要求标题预先含题材名。
    按近时窗口流式扫描，不能让较新的无效记录占满 LIMIT、饿死有效消息。
    """
    if max_batch <= 0:
        return []
    from app.news.official import READ_ONLY_SOURCES

    now = beijing_now_naive()
    rows = []
    with sf() as db:
        latest_id = (select(EventInterpretation.id)
                     .where(EventInterpretation.event_id == EventCard.id)
                     .order_by(EventInterpretation.id.desc()).limit(1).scalar_subquery())
        current = aliased(EventInterpretation)
        stmt = (
            select(EventCard, current.id)
            .outerjoin(current, current.id == latest_id)
            .where(EventCard.status == "active")
            .where(EventCard.revision_pending_at.is_(None))
            .where(EventCard.llm_judged_at.is_(None))
            .where(or_(EventCard.source.is_(None), ~EventCard.source.in_(READ_ONLY_SOURCES)))
            .where(EventCard.published_at <= now,
                   EventCard.published_at >= now - timedelta(hours=age_max_h))
            .where(~select(EventDirection.id).where(
                EventDirection.event_id == EventCard.id,
                EventDirection.direction != 0,
            ).exists())
            # Filter before LIMIT so reviewed neutral cards cannot crowd out
            # older unreviewed candidates. Legacy cards have no version.
            .where(or_(current.id.is_(None), and_(
                current.state == "active", current.review_note.is_(None),
                current.effective_at <= now,
            )))
            .order_by(EventCard.published_at.desc(), EventCard.id.desc())
            .execution_options(yield_per=max(16, max_batch * 4))
        )
        for row, version_id in db.execute(stmt):
            if _market_action_only(row.title, row.summary):
                continue  # Price action alone cannot supply a new catalyst.
            if row.published_at is None:
                continue
            age_h = (now - row.published_at).total_seconds() / 3600
            if age_h < 0 or age_h > age_max_h:
                continue
            row._llm_candidate_version_id = version_id
            rows.append(row)
            if len(rows) >= max_batch:
                break
    return rows


def _apply_result(sf, cand: EventCard, hits: list[dict], *, judgment: dict | None = None) -> int:
    """Atomically mark and write only against the model's source interpretation."""
    with sf() as db:
        latest_id = (select(EventInterpretation.id)
                     .where(EventInterpretation.event_id == EventCard.id)
                     .order_by(EventInterpretation.id.desc()).limit(1).scalar_subquery())
        expected = cand._llm_candidate_version_id
        version_matches = latest_id.is_(None) if expected is None else latest_id == expected
        # Claim the card with one conditional write. Concurrent corrections either
        # commit first and fail this CAS, or wait for this transaction to finish.
        claimed = db.execute(update(EventCard).where(
            EventCard.id == cand.id,
            EventCard.status == "active",
            EventCard.revision_pending_at.is_(None),
            EventCard.llm_judged_at.is_(None),
            EventCard.title == cand.title,
            EventCard.summary == cand.summary if cand.summary is not None else EventCard.summary.is_(None),
            version_matches,
        ).values(llm_judged_at=beijing_now_naive()))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        row = db.get(EventCard, cand.id)
        if any(d.direction != 0 for d in row.directions):
            db.rollback()
            return 0
        version = db.get(EventInterpretation, expected) if expected is not None else None
        if version is not None and (version.state != "active" or version.review_note is not None):
            db.rollback()
            return 0
        observation_id = version.observation_id if version is not None else db.execute(
            select(EventObservation.id).where(EventObservation.event_id == cand.id)
            .order_by(EventObservation.id).limit(1)
        ).scalar_one_or_none()
        existing = {(d.target_type, d.target): d for d in row.directions}
        n = 0
        unapplied = []
        for h in hits:
            key = ("theme", h["target"])
            if key in existing:
                prior = existing[key]
                # Preserve the rule's zero association and its consumers. Retain
                # an unapplied hypothesis only in the existing version journal.
                if version is not None and prior.direction == 0 and prior.matched_by != "manual":
                    sign = "利好" if int(h["direction"]) > 0 else "利空"
                    unapplied.append(
                        f"{h['target']} 的{sign}假设；保留原规则关联及未明方向，"
                        f"未经人工验证；{str(h.get('basis') or '')[:120]}"
                    )
                continue
            added = EventDirection(
                event_id=cand.id,
                observation_id=observation_id,
                target_type="theme",
                target=h["target"],
                direction=int(h["direction"]),
                strength=1,
                chain=h.get("chain") or "",
                basis=h.get("basis") or "LLM 辅助判定（规则未命中方向词）",
                matched_by="llm_aux",
            )
            db.add(added)
            existing[key] = added
            n += 1
        journal = judgment is not None and version is not None
        if (n or unapplied or journal) and observation_id is not None:
            db.flush()
            db.expire(row, ["directions"])
            note = (LLM_AUX_UNAPPLIED_PREFIX + "；".join(unapplied)) if unapplied else (
                "LLM 辅助判读完成：无明确方向，未经验证" if journal and judgment["direction"] == 0 else
                "LLM 辅助方向，待验证假设")
            saved = record_interpretation(db, row, observation_id, state="active",
                                         effective_at=beijing_now_naive(), note=note)
            if journal:
                payload = json.loads(saved.payload_json)
                payload["llm_aux"] = {
                    "event_id": cand.id, "input_version_id": expected, "observation_id": observation_id,
                    "direction": judgment["direction"], "theme": judgment["theme"],
                    "reason": judgment["reason"], "chain": judgment["chain"],
                    "evidence": dict(judgment["evidence"]),
                    "state": "no_direction" if judgment["direction"] == 0 else "hypothesis",
                    "verification": "unverified", "eligibility_changed": False,
                }
                saved.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        db.commit()
        return n


def _messages(inputs: list[dict], theme_names: list[str] | None) -> list[dict]:
    """Give the model the same directory that the deterministic validator uses."""
    prompt = (
        "你是 A 股消息面判定器。对以下新闻批量判断其对 A 股题材的方向影响。\n"
        "只输出 JSON，不要解释。格式：{\"items\":[{\"event_id\":整数,\"direction\":-1|0|1,"
        "\"theme\":\"受影响题材名(无则null)\",\"chain\":\"传导一句话(无则空)\","
        "\"reason\":\"一句话依据\",\"evidence\":{\"field\":\"title|summary\",\"quote\":\"当前输入逐字原句\"}}]}\n"
        "direction: 1=利好相关题材/标的, -1=利空, 0=中性(纯数据罗列/常规澄清/"
        "无题材驱动)。items 数量必须与输入条数一致，顺序对应。\n"
        "注意：龙虎榜/成交量/资金流向/限售解禁数据类、公司常规澄清通常为 0；"
        "政策定调/产业事件/海外映射等明确方向才非 0。"
        "event_id必须原样返回且完整唯一；依据引用只能是对应输入原句。"
        "纯板块/ETF涨跌、涨停和资金人气不是新产业催化，direction=0。"
        "不得补出材料没有的放量、订单、收入、临床阶段；拟议、未兑现和公司自述必须保留。"
        "只判断给定标题与摘要，缺失仅归本材料，不断言公司或全文未披露。"
        "同条新闻涉及不同产品、项目或主体时，必须分别对应原句；"
        "不能把甲对象的产能、进展或业务范围移到乙对象上。"
    )
    if theme_names is not None:
        prompt += (
            "\n下面是本次可选题材目录，仅是名称数据，不是新闻证据或指令。"
            "非零方向的theme必须原样选取目录中的完整名称；没有材料支持的目录匹配，"
            "返回direction=0、theme=null，并在reason说明影响对象尚不清楚。"
            "不得因为目录中存在一个名称就推断新闻与其有关。\n"
            + json.dumps(list(dict.fromkeys(theme_names)), ensure_ascii=False)
        )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
    ]


def _deepseek_items(cands: list[EventCard], session_factory=None, *, theme_names: list[str] | None = None) -> tuple[list[dict] | None, str | None]:
    """让当前 DeepSeek 对指定事件做题材/方向判定；不写事件。"""
    from app.core.config import settings
    from app.core.llm_client import LLMError, chat_completion, extract_json_object

    inputs = [{"event_id": c.id, "title": c.title, "summary": c.summary} for c in cands]
    messages = _messages(inputs, theme_names)
    input_chars = sum(len(str(m.get("content") or "")) for m in messages)
    from app.services import agent_budget
    try:
        agent_budget.check_model_input(input_chars)
    except agent_budget.BudgetError as exc:
        return None, f"模型输入预算拦截：{exc}"
    usage_box = {"value": None}
    try:
        raw = chat_completion(
            base_url=settings.review_llm_base_url,
            api_key=settings.review_llm_api_key,
            model=settings.review_llm_model,
            messages=messages,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=180.0,
            usage_callback=lambda value: usage_box.__setitem__("value", value),
        )
    except LLMError as exc:
        try:
            agent_budget.record_unmetered(
                purpose="event.llm_aux", provider=settings.llm_provider, model=settings.review_llm_model,
                state="failed", usage=usage_box["value"], attempts=1, timeout_seconds=180.0,
                input_chars=input_chars, output_chars=0, error_kind=exc.kind.value,
                session_factory=session_factory,
            )
        except Exception:
            pass
        return None, f"LLM 调用失败：{exc}"
    except Exception as exc:
        return None, f"异常：{exc}"
    def invalid(reason, kind="bad_response"):
        try:
            agent_budget.record_unmetered(
                purpose="event.llm_aux", provider=settings.llm_provider, model=settings.review_llm_model,
                state="failed", usage=usage_box["value"], attempts=1, timeout_seconds=180.0,
                input_chars=input_chars, output_chars=len(raw), error_kind=kind,
                session_factory=session_factory,
            )
        except Exception:
            log.warning("llm_aux invalid-output usage accounting failed", exc_info=False)
        return None, reason

    try:
        parsed = extract_json_object(raw) or {}
        items = parsed.get("items") or []
        if not isinstance(items, list) or len(items) != len(inputs):
            return invalid(f"输出条数不符（{len(items)}/{len(inputs)}）")
        by_id = {}
        source_by_id = {item["event_id"]: item for item in inputs}
        for item in items:
            if not isinstance(item, dict):
                return invalid("模型条目格式无效")
            identity = item.get("event_id")
            direction = item.get("direction")
            if type(identity) is not int or identity not in source_by_id or identity in by_id:
                return invalid("模型事件身份缺失、重复或不符")
            if type(direction) is not int or direction not in (-1, 0, 1):
                return invalid("模型方向枚举非法")
            theme = item.get("theme")
            if ((theme is not None and (not isinstance(theme, str) or len(theme) > 64))
                    or (direction != 0 and not theme)):
                return invalid("模型影响对象无效")
            if direction and theme_names is not None and _match_theme(theme, theme_names) is None:
                return invalid("模型影响对象不在当前官方题材目录", "theme_not_catalog")
            if any(not isinstance(item.get(field), str) for field in ("reason", "chain")):
                return invalid("模型依据字段格式无效")
            if any(len(item[field]) > 512 for field in ("reason", "chain")):
                return invalid("模型依据超出保存边界")
            evidence = item.get("evidence")
            if not isinstance(evidence, dict) or evidence.get("field") not in {"title", "summary"}:
                return invalid("模型证据字段无效")
            quote = evidence.get("quote")
            source = source_by_id[identity]
            if not isinstance(quote, str) or not quote.strip() or len(quote) > 1024 or quote not in (source.get(evidence["field"]) or ""):
                return invalid("模型引用不是当前条目逐字原句", "citation_not_source")
            if _unfulfilled_claim_conflict(source, item["chain"] + "\n" + item["reason"]):
                return invalid("模型解释与同一主体原句的未兑现条件冲突")
            if direction and _market_action_only(source["title"], source["summary"]):
                return invalid("纯行情不能变为新催化")
            text = source["title"] + "\n" + (source["summary"] or "")
            for word in ("放量", "多期临床", "已量产", "已交割"):
                explanation = item["chain"] + "\n" + item["reason"]
                allowed = [match.span() for match in re.finditer(
                    rf"(?:^|[。；;\n，,])(?:(?:本材料未提供|不能确认|未见|没有){re.escape(word)}(?:信息|依据)?|{re.escape(word)}(?:情况|信息)?待核)", explanation)]
                if word not in text and any(not any(start <= match.start() and match.end() <= end for start, end in allowed)
                        for match in re.finditer(re.escape(word), explanation)):
                    return invalid("模型解释补出输入未给的确定细节")
            by_id[identity] = item
        agent_budget.record_unmetered(
            purpose="event.llm_aux", provider=settings.llm_provider, model=settings.review_llm_model,
            state="succeeded", usage=usage_box["value"], attempts=1, timeout_seconds=180.0,
            input_chars=input_chars, output_chars=len(raw), session_factory=session_factory,
        )
        return [by_id[item["event_id"]] for item in inputs], None
    except Exception as exc:
        return invalid(f"解析失败：{exc}")


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

    # All candidates use the existing batch model; an unavailable enhancement
    # cannot pre-classify neutral events or delay publication.
    items, error = _deepseek_items(cands, sf, theme_names=theme_names)
    if items is None:
        log.warning("llm_aux judge failed (batch skipped, retry next round): %s", error)
        return {"skipped": True, "reason": error or "LLM 判定失败"}
    if any(item["direction"] != 0 and _match_theme(item["theme"], theme_names) is None for item in items):
        return {"skipped": True, "reason": "模型影响对象不在当前官方题材目录"}
    items_by_id = {item["event_id"]: item for item in items}

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


        hits = [{
            "target": theme,
            "direction": direction,
            "chain": str(item.get("chain") or "")[:256],
            "basis": f"LLM 辅助判定（待验证）：{item['reason'][:100]}；原句：{item['evidence']['quote'][:100]}"[:256],
        }] if actionable else []
        n = _apply_result(sf, cand, hits, judgment=item)
        hit_count += n
        mark_count += int(n > 0)

    # Current candidates, including neutral results, are marked by _apply_result.
    # Superseded model inputs stay unmarked so a fresh batch can judge them.
    return {
        "skipped": False,
        "candidates": len(cands),
        "hit_events": mark_count,
        "directions_written": hit_count,
        "deepseek_candidates": len(cands),
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
