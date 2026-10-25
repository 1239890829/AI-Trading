"""Read existing plans and explicitly bound selection messages for one review day."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json

from sqlalchemy import select

from app.core.bjtime import BJ_TZ, beijing_now
from app.models.alert import AlertEvent
from app.models.notification_outbox import NotificationOutbox
from app.review.provenance import date_key, finite, source_time


def _object(raw) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _references(raw) -> list[dict]:
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def collect_outlook(day: date) -> dict:
    from app.picks.morning_brief import load_brief

    base = {"state": "missing", "trade_date": day.isoformat(), "source": "picks.morning_brief",
            "generated_at": None, "directions": [], "missing": [], "reason": "没有该日已保存方向计划，未重新生成"}
    try:
        brief = load_brief(day.strftime("%Y%m%d"))
        if not isinstance(brief, dict):
            return base
        clock = source_time(brief.get("generated_at"))
        cutoff = min(datetime.combine(day + timedelta(days=1), time(), tzinfo=BJ_TZ), beijing_now())
        if date_key(brief.get("brief_date")) != day.isoformat() or clock is None or clock >= cutoff:
            return {**base, "state": "mismatch", "reason": "原方向计划日期或生成时点不可核，未当作当日已知计划"}
        fields = ("direction", "logic", "basis", "entry_mode", "entry_basis", "trigger_conditions",
                  "falsify_conditions", "review", "event_refs")
        review_clock = source_time(_object(brief.get("review")).get("reviewed_at"))
        review_bound = review_clock is not None and clock <= review_clock < cutoff
        directions = []
        for direction in brief.get("directions", []):
            if not isinstance(direction, dict):
                continue
            item = {k: direction.get(k) for k in fields}
            item["review"] = item["review"] if review_bound and isinstance(item["review"], dict) else None
            item["review_state"] = "saved" if item["review"] is not None else "unknown"
            item["reviewed_at"] = review_clock.isoformat() if item["review"] is not None else None
            directions.append(item)
        return {**base, "state": "saved", "generated_at": clock.isoformat(), "directions": directions,
                "missing": [str(v) for v in brief.get("missing", [])],
                "reason": "原方向计划及已保存事后观察；不是新增预测、实际买入或策略收益"}
    except Exception as exc:
        return {**base, "state": "error", "reason": f"原方向计划读取失败：{str(exc)[:160]}"}


def collect_selection_journey(session_factory, day: date) -> dict:
    """Never attach same-symbol orders or later-day messages by inference."""
    base = {"state": "empty", "trade_date": day.isoformat(), "source": "alert_event",
            "items": [], "reason": "没有该日可核的原入选消息；不据此断言没有入选、通知或成交"}
    start = datetime.combine(day, time(), tzinfo=BJ_TZ)
    cutoff = min(start + timedelta(days=1), beijing_now())
    if cutoff <= start:
        return base
    try:
        with session_factory() as db:
            rows = list(db.scalars(select(AlertEvent).where(
                AlertEvent.triggered_at >= start.replace(tzinfo=None),
                AlertEvent.triggered_at < cutoff.replace(tzinfo=None),
            ).order_by(AlertEvent.id).limit(2001)))
            payloads = [(r, _object(r.snapshot)) for r in rows[:2000]]
            selections = [(r, p) for r, p in payloads if p.get("kind") == "selection"
                          and date_key(p.get("trade_date")) == day.isoformat()
                          and p.get("selection_source") in {"daily", "intraday"}
                          and isinstance(p.get("source_version"), str) and p["source_version"]]
            ids = [r.id for r, _p in selections[:100]]
            outboxes: dict[int, list] = {}
            if ids:
                for row in db.scalars(select(NotificationOutbox).where(
                    NotificationOutbox.event_id.in_(ids),
                    NotificationOutbox.created_at_ms < int(cutoff.timestamp() * 1000),
                ).order_by(NotificationOutbox.id)):
                    accepted = row.accepted_at_ms
                    accepted_in_window = type(accepted) is int and accepted >= row.created_at_ms and accepted < int(cutoff.timestamp() * 1000)
                    outboxes.setdefault(row.event_id, []).append({
                        "id": row.id, "channel": row.channel,
                        "state": "accepted" if accepted_in_window else "unknown_at_report_cutoff",
                        "reason": "已有截止前渠道接受回执；不证明送达" if accepted_in_window else "无可证截止前接受回执，未把当前可变状态回填历史",
                        "accepted_at": datetime.fromtimestamp(accepted / 1000, BJ_TZ).isoformat() if accepted_in_window else None,
                    })
            items = []
            for row, payload in selections[:100]:
                kind, version = payload["selection_source"], payload["source_version"]
                ref = f"{kind}:{day.isoformat()}:{row.symbol}"
                original_clock = source_time(payload.get("source_as_of"))
                if original_clock is None or original_clock.date() != day or original_clock >= cutoff:
                    continue
                candidate = []
                for event, saved in payloads:
                    execution = _object(saved.get("paper_execution"))
                    if (saved.get("kind") == "paper_status" and saved.get("origin_event_id") == row.id
                            and execution.get("selection_ref") == ref and execution.get("source_version") == version
                            and execution.get("scope") == "main"):
                        clock = source_time(saved.get("source_as_of"))
                        if clock is not None and start <= clock < cutoff:
                            candidate.append((clock, event.id, execution))
                execution = {"state": "unknown", "reason": "无该日明确绑定的模拟状态回执，未按同股猜单", "order": None}
                if candidate:
                    saved = max(candidate, key=lambda v: (v[0], v[1]))[2]
                    status = saved.get("status")
                    order_id = saved.get("order_id")
                    known_order = type(order_id) is int and order_id > 0
                    valid = status in {"rejected", "cancelled", "pending", "filled"}
                    valid = valid and (status not in {"pending", "filled"} or known_order)
                    if status == "filled":
                        price = finite(saved.get("filled_price"))
                        valid = valid and price is not None and price > 0
                    if valid:
                        execution = {"state": status,
                            "reason": str(saved.get("reason") or "原绑定模拟状态回执；不替代独立收益核验"),
                            "order": {k: saved.get(k) for k in ("order_id", "quantity", "filled_price", "fee", "status")}}
                proof = _object(payload.get("selection_evidence"))
                channels = row.delivered_channels
                try:
                    channels = json.loads(channels) if isinstance(channels, str) else channels
                except ValueError:
                    channels = []
                raw_basis = proof.get("pick_basis") or proof.get("source_basis")
                items.append({"symbol": row.symbol, "name": str(payload.get("name") or row.symbol),
                    "selection_source": kind, "selection_ref": ref, "source_version": version,
                    "source_as_of": original_clock.isoformat(), "origin_event_id": row.id,
                    "basis": raw_basis if isinstance(raw_basis, str) else None,
                    "event_refs": _references(proof.get("related_event_refs")),
                    "notification": {"in_app_recorded": isinstance(channels, list) and "in_app" in channels,
                        "outbox": outboxes.get(row.id, []), "delivery_state": "unknown", "read_state": "unknown"},
                    "execution": execution})
            partial = len(rows) > 2000 or len(selections) > 100
            return {**base, "state": "partial" if partial else "saved" if items else "empty", "items": items,
                    "reason": "原入选消息→明确关联的渠道接受/模拟状态；接受不等于送达或已读；最多100条、非全账户交易汇总"}
    except Exception as exc:
        return {**base, "state": "error", "reason": f"原关联记录读取失败：{str(exc)[:160]}"}


def collect_reader_context(session_factory, day: date) -> dict:
    return {"outlook": collect_outlook(day), "selection_journey": collect_selection_journey(session_factory, day)}
