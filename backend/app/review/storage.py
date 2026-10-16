"""复盘报告的持久化、检索与历史对比（需求 6）。

存储分两处，各有职责：
- **SQLite**：结构化列用于检索与统计（"某方法论版本产生了多少 P0"、
  "某维度的历史采纳率"）。改进项单独建表，因为它会被确认/应用/回退。
- **JSON 落盘**（`data/review/reports/YYYYMMDD/<sha256>.json`）：不可变版本，
  用于历史 diff 与重放。SQLite payload 与 report_path 决定当前版本。

同一交易日重复生成时更新数据库当前指针，旧文件保留。旧 YYYYMMDD.json
只属历史格式；目录扫描、孤立文件和文件 mtime 均不能判断当前版本。
文件先以临时文件原子发布，再提交 DB；提交失败不损坏旧版本。
维护可显式调用 repair_report_projection 从已提交 payload 重建文件。

覆盖的两个坑（2026-09-01 实测，都已修）：
- 清旧行必须按 `trade_date` 过滤，不能按 `review_id`：review_id 每次生成都带新的
  HHMMSS 后缀，用新 id 过滤永远匹配不到旧行 → 孤儿行只增不减
  （7 条真实改进项累积成 113 行，采纳率分母虚高约 16 倍）。
- 覆盖不能把人的处置结论一起抹掉：已确认/已驳回的改进项要按指纹继承状态，
  否则每天重跑一次，PDCA 的 C 和 A 就被重写一遍，等于没有闭环。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from app.core.db import utcnow
from app.review.models import (
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)
from app.review.schemas import ReviewReport
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = REPO_ROOT / "data" / "review" / "reports"


def _write_report_version(trade_date: str, payload: str) -> Path:
    """Immutable, atomically published file; the DB pointer determines currentness."""
    if not re.fullmatch(r"\d{8}", trade_date):
        raise ValueError("invalid report trade_date")
    body = payload.encode("utf-8")
    directory = REPORT_DIR / trade_date
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(body).hexdigest()}.json"
    if path.exists() and path.read_bytes() == body:
        return path
    fd, temporary = tempfile.mkstemp(prefix="report-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def repair_report_projection(session_factory, trade_date: str) -> Path | None:
    """Explicit maintenance recovery from committed DB payload; never on page reads."""
    with session_factory() as db:
        row = db.execute(select(ReviewReportRow).where(
            ReviewReportRow.trade_date == trade_date)).scalar_one_or_none()
        if row is None:
            return None
        payload = row.payload
        path = _write_report_version(trade_date, payload)
        changed = db.execute(update(ReviewReportRow).where(
            ReviewReportRow.id == row.id, ReviewReportRow.payload == payload,
        ).values(report_path=str(path)))
        if changed.rowcount != 1:
            db.rollback()
            raise RuntimeError("report changed during projection recovery")
        db.commit()
        return path


def _fingerprint(item: object) -> tuple[str, str]:
    """改进项指纹：重跑时靠它认出"同一条改进项"，从而继承人的处置结论。

    只用 `category + title`——`target` / `proposed_change` 会随数据缺失集合微调
    （如"补齐阻断级数据缺失：breadth, sentiment"在 breadth 修好后变成只有 sentiment），
    拿它们做键会导致明明是同一条却认不出来、状态被打回 pending。
    代价：标题变了就认不出（会有 warning 提示），这是可接受的宽松——
    认不出最多重置一次状态，认错了才会把处置挂到不相干的改进项上。
    """
    return (getattr(item, "category", ""), getattr(item, "title", ""))


def _snapshot_disposition(db, trade_date: str) -> dict[tuple[str, str], dict]:
    """重跑前快照该交易日**已处置**的改进项（pending 无需继承）。

    不做这一步，重新生成当日复盘会把人确认/驳回过的结论全部打回 pending。
    """
    rows = db.execute(
        select(ReviewActionItemRow).where(ReviewActionItemRow.trade_date == trade_date)
    ).scalars().all()
    return {
        (r.category, r.title): {
            "row_id": r.id,
            "review_id": r.review_id,
            "status": r.status,
            "resolution_note": r.resolution_note,
            "implementation_evidence": r.implementation_evidence,
            "resolved_at": r.resolved_at,
        }
        for r in rows
        if r.status != "pending"
    }


def save_report(session_factory, report: ReviewReport) -> ReviewReport:
    """落库 + 落盘。同一交易日覆盖（但继承已处置的改进项状态）。"""
    if not report.review_id:
        report.review_id = f"RV-{report.trade_date}-{beijing_now().strftime('%H%M%S')}-{uuid4().hex[:6]}"

    db = session_factory()
    try:
        existing = db.execute(
            select(ReviewReportRow).where(ReviewReportRow.trade_date == report.trade_date)
        ).scalars().first()

        carried = _snapshot_disposition(db, report.trade_date)
        if existing:
            # Preserve disposition evidence even when a regenerated report omits an item.
            previous = json.loads(existing.payload)
            previous["_action_dispositions"] = [
                {"category": key[0], "title": key[1], **value}
                for key, value in carried.items()
            ]
            _write_report_version(report.trade_date, json.dumps(
                previous, ensure_ascii=False, default=str, sort_keys=True))
        for item in report.action_items:
            old = carried.get(_fingerprint(item))
            # Model-generated text cannot create an implementation receipt, even
            # in the immutable file or raw payload consumed by internal tools.
            item.status = old["status"] if old else "pending"
            item.resolution_note = old["resolution_note"] if old else ""
            item.implementation_evidence = json.loads(old["implementation_evidence"] or "{}") if old else {}
            item.resolved_at = old["resolved_at"].isoformat() if old and old["resolved_at"] else None
            item.disposition_basis = "management_record_not_release_verification"
        payload = json.dumps(report.model_dump(), ensure_ascii=False, default=str)
        # A file/DB failure cannot overwrite a committed version. Unreferenced versions
        # after a failed commit are recoverable artifacts, never current reports.
        path = _write_report_version(report.trade_date, payload)
        if existing:
            existing.review_id = report.review_id
            existing.methodology_version = report.methodology_version
            existing.model_requested = report.model.requested
            existing.model_actual = report.model.actual
            existing.model_degraded = 1 if report.model.degraded else 0
            existing.model_cost = report.model.cost
            existing.summary = report.summary
            existing.gap_count = len(report.data.all_gaps)
            existing.action_item_count = len(report.action_items)
            existing.payload = payload
            existing.report_path = str(path)
            existing.generated_at = utcnow()
            row = existing
        else:
            row = ReviewReportRow(
                review_id=report.review_id,
                trade_date=report.trade_date,
                methodology_version=report.methodology_version,
                model_requested=report.model.requested,
                model_actual=report.model.actual,
                model_degraded=1 if report.model.degraded else 0,
                model_cost=report.model.cost,
                summary=report.summary,
                gap_count=len(report.data.all_gaps),
                action_item_count=len(report.action_items),
                payload=payload,
                report_path=str(path),
            )
            db.add(row)

        # 清旧行 + 重新插入。两处都不能按 review_id 过滤：
        # review_id 每次生成都带新的 HHMMSS 后缀，用新 id 匹配旧行永远匹配不到
        # → 孤儿行只增不减（实测 7 条真实改进项累积成 113 行，采纳率分母虚高 16 倍）。
        # 删除前先快照已处置的改进项，插入时按指纹继承，避免把人的处置结论打回 pending。
        db.query(ReviewActionItemRow).filter(
            ReviewActionItemRow.trade_date == report.trade_date
        ).delete()
        db.query(ReviewMetaInsightRow).filter(
            ReviewMetaInsightRow.trade_date == report.trade_date
        ).delete()

        for it in report.action_items:
            old = carried.pop(_fingerprint(it), None)
            db.add(ReviewActionItemRow(
                review_id=report.review_id, trade_date=report.trade_date,
                title=it.title, category=it.category, priority=it.priority,
                expected_impact=it.expected_impact, evidence=it.evidence,
                target=it.target, proposed_change=it.proposed_change,
                status=old["status"] if old else "pending",
                resolution_note=old["resolution_note"] if old else "",
                implementation_evidence=old["implementation_evidence"] if old else "{}",
                resolved_at=old["resolved_at"] if old else None,
            ))
        # 还有剩的说明上次的改进项这次没再产出：处置结论随之失效，记一条提示
        for fp in carried:
            log.info("改进项本次未再产出，历史处置已保留于旧版本：%s / %s", fp[0], fp[1][:40])
        for mi in report.meta_insights:
            db.add(ReviewMetaInsightRow(
                review_id=report.review_id, trade_date=report.trade_date,
                methodology_version=report.methodology_version, dimension=mi.dimension,
                observation=mi.observation, effectiveness=mi.effectiveness,
                evidence=mi.evidence, suggestion=mi.suggestion,
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 情绪周期序列钩子（retro #17）：报告里的情绪判定落 sentiment_history（幂等，已存在跳过）。
    # 放在报告提交之后、独立 session——情绪落库失败不影响复盘报告本身。
    try:
        from app.market.sentiment_history import extract_from_review_payload, upsert_if_absent

        entry = extract_from_review_payload(report.model_dump())
        if entry and upsert_if_absent(session_factory, entry):
            log.info("sentiment history added: %s (%s)", entry["trade_date"], entry["phase"])
    except Exception:
        log.warning("sentiment history hook failed (报告已落库，不影响复盘)", exc_info=True)

    log.info("review saved: %s (%s)", report.review_id, path)
    return report


def report_exists(session_factory, trade_date: str) -> bool:
    """该交易日是否已有复盘报告。

    调度器靠它做"今天跑过了吗"的判定——这个状态必须**持久化**：
    用内存变量记录的话，每次进程重启都会重跑当日复盘（当前时间已过触发点时
    条件天然满足），而重跑又会生成新的 review_id，是孤儿改进项的主要来源。
    """
    db = session_factory()
    try:
        return (
            db.execute(
                select(ReviewReportRow.id).where(ReviewReportRow.trade_date == trade_date)
            ).first()
            is not None
        )
    finally:
        db.close()


def _sync_action_items_from_db(db, report: ReviewReport) -> None:
    """把改进项的**数据库主键**与**可变处置状态**覆盖到 payload 快照上。

    两件事都必须做，缺一个改进项闭环就是断的：

    1. **回填主键**：`save_report` 落库时没有持久化 `ActionItem.id`
       （payload JSON 里的 `AI-xxxxxxxx` 只是报告内的临时编号），而改进项要能被
       确认/应用/回退，就必须有可唯一定位到行的键——`review_action_items.id`
       是唯一选择，同时也是前端 PATCH 的寻址键。
    2. **同步处置状态**：`get_report` 读的是 `ReviewReportRow.payload`，
       那是**生成时的快照**。改进项被 PATCH 处置后只有表行变了，payload 仍停在
       pending ——若不同步，界面上就表现为「点了确认，回读还是待处置」
       （2026-09-01 实测：PATCH 返回 confirmed 200，回读报告详情仍是 pending）。
       **处置状态的权威来源只能是表行，不是 payload。**

    对齐依据：`save_report` 按 `report.action_items` 的顺序逐条插入，因此表内
    按 id 升序的行序列与 payload 列表**严格同序**，可按索引对齐。
    长度不一致说明报告被外部改写过 → 此时整批跳过，
    宁可让这些条目不可操作，也绝不错配到别的改进项上。

    处置说明与时刻同样从表行投影，便于读者追踪实施/发布/复测凭据。
    这些是管理记录，不冒充代码已发布或效果已验证。
    """
    rows = db.execute(
        select(ReviewActionItemRow)
        .where(ReviewActionItemRow.review_id == report.review_id)
        .order_by(ReviewActionItemRow.id)
    ).scalars().all()
    if (len(rows) != len(report.action_items)
            or any(_fingerprint(row) != _fingerprint(item) for row, item in zip(rows, report.action_items))):
        log.warning(
            "action item count mismatch for %s: db=%d payload=%d，跳过状态同步",
            report.review_id, len(rows), len(report.action_items),
        )
        for item in report.action_items:
            item.id = ""
            item.status = "pending"
            item.resolution_note = ""
            item.implementation_evidence = {}
            item.resolved_at = None
            item.disposition_basis = "disposition_unavailable"
        return
    for row, item in zip(rows, report.action_items):
        item.id = str(row.id)
        item.status = row.status
        item.resolution_note = row.resolution_note
        item.implementation_evidence = json.loads(row.implementation_evidence or "{}")
        item.resolved_at = row.resolved_at.isoformat() if row.resolved_at else None
        item.disposition_basis = "management_record_not_release_verification"


def get_report(session_factory, trade_date: str) -> ReviewReport | None:
    db = session_factory()
    try:
        row = db.execute(
            select(ReviewReportRow).where(ReviewReportRow.trade_date == trade_date)
        ).scalars().first()
        if not row:
            return None
        report = ReviewReport.model_validate_json(row.payload)
        _sync_action_items_from_db(db, report)
        return report
    finally:
        db.close()


# ---------------------------------------------------------------- 改进项状态变更


#: 允许的处置状态。`pending` 保留在内，用于"撤销确认"。
ALLOWED_STATUSES = ("pending", "recorded", "confirmed", "applied", "rejected", "reverted")

#: 需要填写处置说明的状态——没有理由的驳回/回退是无法归因的，
#: 后期统计"哪类改进项总被驳回"时全靠这段文字。
NOTE_REQUIRED = ("applied", "rejected", "reverted")


class ActionItemStaleError(Exception):
    """PATCH 的寻址指纹与表行现状不符——id 已随报告重新生成而漂移。

    `review_action_items.id` 是 SQLite rowid 别名且未加 AUTOINCREMENT，
    `save_report` 重跑时删除重建会把 id 复用甚至跨交易日串号（2026-09-01
    实测 111→72）。仅凭 id 寻址，用户拿陈旧页面点处置会把结论挂到
    不相干的改进项上——那是**静默错配**，不报错、无法察觉。
    守卫让陈旧 id 显式失败（路由映射为 409），刷新页面拿到新 id 再操作。
    """


def update_action_item_status(
    session_factory,
    item_id: str,
    status: str,
    note: str = "",
    *,
    expect_trade_date: str,
    expect_category: str,
    expect_title: str,
    only_statuses: tuple[str, ...] | None = None,
    implementation_evidence: dict[str, str] | None = None,
) -> dict:
    """变更单条改进项的处置状态。

    这是 PDCA 闭环的落点：改进项被确认/应用/回退后，
    `evaluate_historical_effectiveness` 算出的采纳率才有意义
    （2026-09-01 核查：107 条全部 pending、采纳率 0%，根因就是缺这个入口）。

    :param item_id: `review_action_items.id`（由 `get_report` 回填到 payload）
    :param status: 见 ALLOWED_STATUSES
    :param note: 处置说明；rejected/reverted 必填
    :param expect_trade_date: 守卫三元组之一——调用方**看到的**改进项的交易日
    :param expect_category: 守卫三元组之二——调用方看到的类别
    :param expect_title: 守卫三元组之三——调用方看到的标题
    :return: 更新后的行摘要
    :raises ValueError: item_id 非整数 / 状态非法 / 必填说明缺失
    :raises LookupError: 找不到对应改进项
    :raises ActionItemStaleError: 三元组与表行不符（id 已漂移，必须刷新重取）
    """
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"status 非法：{status!r}，允许值 {ALLOWED_STATUSES}")
    if status in NOTE_REQUIRED and not note.strip():
        raise ValueError(f"status={status} 必须填写 note（没有理由的处置无法归因）")
    evidence = implementation_evidence or {}
    if not isinstance(evidence, dict):
        raise ValueError("实施凭据必须是结构化对象")
    allowed_evidence = {"implementation_ref", "release_version", "verification_ref"}
    if set(evidence) - allowed_evidence or any(not isinstance(v, str) or len(v) > 2000 for v in evidence.values()):
        raise ValueError("实施凭据字段或长度无效")
    evidence = {k: v.strip() for k, v in evidence.items()}
    if status == "applied" and any(not evidence.get(k) for k in allowed_evidence):
        raise ValueError("登记实施需要 implementation_ref、release_version 和 verification_ref；缺少发布或复测时请保持 confirmed")
    encoded_evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    try:
        pk = int(item_id)
    except (TypeError, ValueError):
        raise ValueError(f"item_id 需为整数主键，收到 {item_id!r}") from None

    db = session_factory()
    try:
        row = db.execute(
            select(ReviewActionItemRow).where(ReviewActionItemRow.id == pk)
        ).scalars().first()
        if row is None:
            raise LookupError(f"改进项 {pk} 不存在")

        # id 漂移守卫：id 对、内容不对 = 这个 id 已经不是用户看到的那条了。
        # 只校验 id 会把处置写到重跑后恰好复用该 id 的别的改进项上。
        fp = (row.trade_date, row.category, row.title)
        expected = (expect_trade_date, expect_category, expect_title)
        if fp != expected:
            raise ActionItemStaleError(
                f"改进项 {pk} 与请求指纹不符（报告重新生成后 id 会漂移）："
                f"请求指 {expected}，表行现为 {fp}。请刷新页面后重试"
            )

        if only_statuses is not None and row.status not in only_statuses:
            raise ActionItemStaleError("处置已变化，不覆盖人工结论")
        if only_statuses is not None and row.status == status and row.resolution_note == note.strip():
            return {"id": row.id, "review_id": row.review_id, "trade_date": row.trade_date,
                    "title": row.title, "status": row.status, "resolution_note": row.resolution_note,
                    "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None}
        before = {"status": row.status, "note": row.resolution_note,
                  "implementation_evidence": json.loads(row.implementation_evidence or "{}"),
                  "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None}
        previous_status, previous_note = row.status, row.resolution_note
        previous_evidence = row.implementation_evidence
        result = db.execute(update(ReviewActionItemRow).where(
            ReviewActionItemRow.id == pk, ReviewActionItemRow.status == previous_status,
            ReviewActionItemRow.resolution_note == previous_note,
            ReviewActionItemRow.implementation_evidence == previous_evidence,
            ReviewActionItemRow.trade_date == expect_trade_date,
            ReviewActionItemRow.category == expect_category, ReviewActionItemRow.title == expect_title,
        ).values(status=status, resolution_note=note.strip(),
                 implementation_evidence=encoded_evidence,
                 resolved_at=None if status in ("pending", "recorded") else utcnow()))
        if result.rowcount != 1:
            raise ActionItemStaleError("处置并发变化，请刷新后重试")
        db.refresh(row)
        from app.models.agent import AgentAudit
        db.add(AgentAudit(actor="user" if only_statuses is None else "scheduler",
                          action="review.disposition", target=f"review:{row.trade_date}:{pk}",
                          before=json.dumps(before, ensure_ascii=False),
                          after=json.dumps({"status": status, "note": note.strip(),
                                            "implementation_evidence": evidence,
                                            "trade_date": row.trade_date, "category": row.category,
                                            "title": row.title}, ensure_ascii=False)))
        db.commit()
        return {
            "id": row.id,
            "review_id": row.review_id,
            "trade_date": row.trade_date,
            "title": row.title,
            "status": row.status,
            "resolution_note": row.resolution_note,
            "implementation_evidence": json.loads(row.implementation_evidence or "{}"),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_reports(session_factory, limit: int = 30) -> list[dict]:
    db = session_factory()
    try:
        rows = db.execute(
            select(ReviewReportRow).order_by(ReviewReportRow.trade_date.desc()).limit(limit)
        ).scalars().all()
        return [{
            "review_id": r.review_id, "trade_date": r.trade_date,
            "methodology_version": r.methodology_version,
            "model_actual": r.model_actual, "model_degraded": bool(r.model_degraded),
            "summary": r.summary, "gap_count": r.gap_count,
            "action_item_count": r.action_item_count,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        } for r in rows]
    finally:
        db.close()


def compare_reports(a: ReviewReport, b: ReviewReport) -> dict:
    """两日报告对比：看变化而不是看绝对值。"""
    ga = {(g.field, g.source) for g in a.data.all_gaps}
    gb = {(g.field, g.source) for g in b.data.all_gaps}

    def _phase(r: ReviewReport) -> str | None:
        return (r.data.market.sentiment or {}).get("phase")

    return {
        "from": a.trade_date,
        "to": b.trade_date,
        "methodology_changed": a.methodology_version != b.methodology_version,
        "gaps_fixed": sorted(f"{f}" for f, s in ga - gb),
        "gaps_new": sorted(f"{f}" for f, s in gb - ga),
        "gaps_persistent": sorted(f"{f}" for f, s in ga & gb),
        "sentiment": {"from": _phase(a), "to": _phase(b)},
        "action_items": {
            "from": len(a.action_items), "to": len(b.action_items),
            "resolved": [i.title for i in a.action_items if i.status != "pending"],
        },
    }
