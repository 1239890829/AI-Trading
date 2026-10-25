"""Bounded current-membership projection; never generates a selection or history."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re


def discovery_symbols(value: str) -> list[str]:
    parts = value.split(",")
    if len(parts) > 200 or any(not re.fullmatch(r"[0-9]{6}", part) for part in parts):
        raise ValueError("symbols 必须为最多 200 个逗号分隔的六位证券代码")
    return list(dict.fromkeys(parts))


def discovery_trade_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("trade_date 必须为 YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("trade_date 必须为 YYYY-MM-DD")
    return value


def membership_view(symbols: list[str], mapping: dict[str, list[dict]], *,
                    requested_trade_date: str | None, observed_at: str,
                    unavailable: bool = False) -> dict:
    """A digest identifies returned content, not a point-in-time member version."""
    unknown_reason = ("当前本地题材归属读取不可用，未补取或猜测" if unavailable else
                      "当前本地官方成分未记录该股归属，不等于没有题材")
    items = []
    for symbol in symbols:
        themes = sorted([
            {"theme_code": row["theme_code"], "theme_name": row["theme_name"],
             "source": row.get("source")}
            for row in mapping.get(symbol, [])
        ], key=lambda row: row["theme_code"])
        items.append({"symbol": symbol, "state": "matched" if themes else "unknown",
                      "themes": themes, "reason": None if themes else unknown_reason})
    matched = sum(bool(item["themes"]) for item in items)
    state = "unavailable" if unavailable else "ready" if matched == len(items) else "partial" if matched else "unknown"
    identity = {"scope": "current_not_historical", "requested_trade_date": requested_trade_date,
                "state": state, "items": items}
    version = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
    return {"data": {
        **identity, "content_version": version, "source": "local_theme_catalog",
        "source_as_of": None, "observed_at": observed_at,
        "count": len(items), "matched_count": matched, "unknown_count": len(items) - matched,
    }, "meta": {
        "basis": "当前本地官方成分及有效人工修正；非历史归属、非事件因果，不改变入选、研究或委托资格",
        "partial": matched != len(items),
    }}
