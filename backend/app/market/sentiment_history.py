"""情绪周期序列（retro #17）：每日情绪判定落库 → 近 N 日曲线 + 周期起点定位。

数据来源两路（同构，均来自 compute_market_sentiment 的输出）：
- review：复盘 Agent 收盘报告里的 `data.market.sentiment`（save_report 钩子提取）；
- live：交易日 15:00 后查询历史序列时惰性现算补录。

回填纪律：已存在的日期**不覆盖**——历史是对那一天的判断，留最新一份即可，
重复回填不允许悄悄改写（与 review_reports 同日覆盖语义不同：这里序列是
"当时判了什么"的记录，覆盖会破坏与复盘报告的可对照性）。

周期定位（locate_cycle）：情绪相位按 强（回暖/升温/高潮）/ 中（分歧）/
弱（退潮/冰点）三段分组，周期起点 = 序列中最近一次分段切换的日期。
纯函数，无 IO。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base

log = logging.getLogger(__name__)

# 相位 → 三段分组
PHASE_GROUPS: dict[str, str] = {
    "回暖": "strong",
    "升温": "strong",
    "高潮": "strong",
    "分歧": "neutral",
    "退潮": "weak",
    "冰点": "weak",
}


class SentimentHistoryRow(Base):
    __tablename__ = "sentiment_history"

    trade_date: Mapped[str] = mapped_column(String(8), primary_key=True)  # YYYYMMDD
    phase: Mapped[str] = mapped_column(String(16))
    temperature: Mapped[float | None] = mapped_column(default=None)
    confidence: Mapped[str | None] = mapped_column(String(8), default=None)
    phase_unreliable: Mapped[int] = mapped_column(default=0)
    source: Mapped[str] = mapped_column(String(16))  # review / live
    detail: Mapped[str] = mapped_column(Text, default="")  # 完整情绪 JSON
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# SQLAlchemy 需要的类型导入（放在类定义之后会提升可读性，但必须在使用前）

def upsert_if_absent(session_factory, entry: dict) -> bool:
    """写入当日情绪（已存在则跳过）。entry: {trade_date(YYYYMMDD), phase, temperature,
    confidence, phase_unreliable, source, detail(dict)} → 是否新增。"""
    db = session_factory()
    try:
        exists = db.execute(
            select(SentimentHistoryRow).where(
                SentimentHistoryRow.trade_date == entry["trade_date"]
            )
        ).scalars().first()
        if exists:
            return False
        db.add(SentimentHistoryRow(
            trade_date=entry["trade_date"],
            phase=entry["phase"],
            temperature=entry.get("temperature"),
            confidence=entry.get("confidence"),
            phase_unreliable=1 if entry.get("phase_unreliable") else 0,
            source=entry.get("source", "live"),
            detail=json.dumps(entry.get("detail") or {}, ensure_ascii=False, default=str),
        ))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def extract_from_review_payload(payload: dict) -> dict | None:
    """复盘报告 payload → 情绪 entry；无情绪块返回 None。"""
    try:
        s = payload["data"]["market"]["sentiment"]
    except (KeyError, TypeError):
        return None
    trade_date = s.get("trade_date") or payload.get("trade_date")
    phase = s.get("phase")
    if not trade_date or not phase:
        return None
    return {
        "trade_date": str(trade_date).replace("-", ""),
        "phase": str(phase),
        "temperature": s.get("temperature"),
        "confidence": s.get("confidence"),
        "phase_unreliable": bool(s.get("phase_unreliable")),
        "source": "review",
        "detail": s,
    }


def backfill_from_reports(session_factory) -> int:
    """扫描 review_reports.payload 里已有的情绪判定回填序列（幂等）→ 新增条数。"""
    from app.review.models import ReviewReportRow

    db = session_factory()
    added = 0
    try:
        rows = db.execute(select(ReviewReportRow)).scalars().all()
        db.close()
        for row in rows:
            try:
                payload = json.loads(row.payload)
            except (TypeError, ValueError):
                continue
            entry = extract_from_review_payload(payload)
            if entry and upsert_if_absent(session_factory, entry):
                added += 1
        return added
    except Exception:
        db.rollback()
        raise
    finally:
        if db.in_transaction():
            db.close()


def get_history(session_factory, days: int = 10) -> list[dict]:
    """最近 N 个交易日的序列（升序）。"""
    db = session_factory()
    try:
        rows = db.execute(
            select(SentimentHistoryRow)
            .order_by(SentimentHistoryRow.trade_date.desc())
            .limit(days)
        ).scalars().all()
        return [
            {
                "trade_date": r.trade_date,
                "phase": r.phase,
                "temperature": r.temperature,
                "confidence": r.confidence,
                "phase_unreliable": bool(r.phase_unreliable),
                "source": r.source,
            }
            for r in reversed(rows)
        ]
    finally:
        db.close()


def locate_cycle(history: list[dict]) -> dict:
    """周期起点定位（纯函数）。

    规则：相位按 强/中/弱 三段分组；从最新往回找最近一次分段切换——
    周期起点 = 切换点的日期（即当前分段自那天起持续至今）。
    序列空返回空周期；单条记录周期即其自身。
    """
    if not history:
        return {"start_date": None, "days": 0, "current_group": None, "segments": []}

    def group_of(phase: str) -> str:
        return PHASE_GROUPS.get(str(phase), "neutral")

    segments: list[dict] = []  # [{group, start_idx}]
    for i, h in enumerate(history):
        g = group_of(h["phase"])
        if not segments or segments[-1]["group"] != g:
            segments.append({"group": g, "start_idx": i})
    current = segments[-1]
    start = history[current["start_idx"]]
    days = len(history) - current["start_idx"]
    return {
        "start_date": start["trade_date"],
        "start_phase": start["phase"],
        "days": days,
        "current_group": current["group"],
        "segments": [
            {
                "group": seg["group"],
                "from": history[seg["start_idx"]]["trade_date"],
                "phases": [
                    h["phase"] for h in history[seg["start_idx"]:
                    (segments[j + 1]["start_idx"] if j + 1 < len(segments) else len(history))]
                ],
            }
            for j, seg in enumerate(segments)
        ],
    }
