#!/usr/bin/env python3
"""P2-3 层3：事件自标注数据集导出（FinGPT 派蒸馏输入候选）。

字段：事件属性（标题/分类/判定）+ 方向行（题材/方向/依据/命中方式）+
个股收益标签（T+1 / T+5，marketdb daily_k 对齐；题材级无个股归属→收益留空）。

口径纪律：
- 事件时间已是北京 naive（2026-09-09 修复）；marketdb date_ms 为毫秒 UTC 0 点，
  转北京日期 = date_ms/86400000 的 UTC 日期即北京交易日（0 点是同一日历日）。
- 收益 = fwd_close / base_close - 1，base 取事件日（published_at 的交易日）收盘，
  T+N 取其后第 N 个交易日收盘；缺数据（停牌/未到/退市）收益留 None 不臆造。
- 输出 data/labels/events_self_labeled_<date>.jsonl（append 按日文件名，避免覆盖）。

用途：事件→题材→方向→依据→半衰期→T+1/T+5 收益的自标注集；供日后蒸馏
语义判定小模型或做判定质量回测。零运行时依赖，只读 DB。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]  # backend/
ASHARE_DB = ROOT.parent / "data" / "ashare.db"
MARKET_DB = ROOT / "data" / "marketdb" / "market.duckdb"
OUT_DIR = ROOT.parent / "data" / "labels"

_MS_PER_DAY = 86_400_000


def _biz_day(ms: int) -> str:
    """date_ms（UTC 0 点）→ 北京交易日 YYYY-MM-DD（0 点同日历日，无时区偏移）。"""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _load_events(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        """
        SELECT id, fingerprint, title, url, source, source_tier,
               published_at, fact_kind, certainty, category, half_life_hours,
               source_symbol, status, created_at, summary
        FROM event_card
        ORDER BY id
        """
    ).fetchall()
    cols = ["id", "fingerprint", "title", "url", "source", "source_tier",
            "published_at", "fact_kind", "certainty", "category", "half_life_hours",
            "source_symbol", "status", "created_at", "summary"]
    dirs_map: dict[int, list[dict]] = {}
    for row in con.execute(
        "SELECT event_id, target_type, target, direction, strength, chain, basis, matched_by "
        "FROM event_direction ORDER BY event_id, id"
    ).fetchall():
        dirs_map.setdefault(row[0], []).append({
            "target_type": row[1], "target": row[2], "direction": row[3],
            "strength": row[4], "chain": row[5], "basis": row[6], "matched_by": row[7],
        })
    out = []
    for r in rows:
        e = dict(zip(cols, r))
        e["directions"] = dirs_map.get(e["id"], [])
        out.append(e)
    return out


def _load_close_map(mcon) -> dict[str, dict[str, float]]:
    """{thscode: {交易日: 收盘价}}——仅取所需区间数据全量进内存（~1500 股×250 日小）。"""
    m = {}
    for code, ms, close in mcon.execute(
        "SELECT thscode, date_ms, close_price FROM daily_k WHERE date_ms >= ?",
        [1_473_004_800_000],  # 2016-09 起即可，实际事件都是近期
    ).fetchall():
        m.setdefault(code, {})[_biz_day(ms)] = float(close)
    return m


def _next_days(closes: dict[str, float], day: str, n: int) -> list[str]:
    """day 之后的第 1..n 个交易日（有收盘价的交易日）。"""
    days = sorted(closes)
    out = []
    for d in days:
        if d > day:
            out.append(d)
            if len(out) >= n:
                break
    return out


def _sym_to_code(sym: str) -> str:
    """600105 → 600105.SH / 000523 → 000523.SZ（marketdb thscode 格式）。"""
    if sym.startswith(("6", "9")):
        return f"{sym}.SH"
    return f"{sym}.SZ"


def main() -> None:
    con = sqlite3.connect(ASHARE_DB)
    events = _load_events(con)
    con.close()

    mcon = duckdb.connect(str(MARKET_DB), read_only=True)
    closes_map = _load_close_map(mcon)
    mcon.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    out_path = OUT_DIR / f"events_self_labeled_{today}.jsonl"

    written = 0
    with_ret = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for e in events:
            pub = (e["published_at"] or "")[:10]
            if not pub:
                continue
            import sys

            sys.path.insert(0, str(ROOT))
            from app.events.extract import judge_state  # noqa: PLC0415

            # SQLite 取出 published_at 是 "YYYY-MM-DD HH:MM:SS" 字符串 → datetime naive（北京）
            pub_dt = None
            if e["published_at"]:
                pub_dt = datetime.strptime(str(e["published_at"])[:19], "%Y-%m-%d %H:%M:%S")
            js = judge_state(pub_dt, e["directions"],
                             half_life_hours=e["half_life_hours"])

            rec = {
                "event_id": e["id"],
                "published_at": e["published_at"],
                "title": e["title"],
                "source": e["source"],
                "source_tier": e["source_tier"],
                "category": e["category"],
                "certainty": e["certainty"],
                "fact_kind": e["fact_kind"],
                "judge": {"status": js["status"], "reason": js["reason"]},
                "directions": e["directions"],
                "source_symbol": e["source_symbol"],
            }
            # 收益标签：个股级（source_symbol）才对齐；题材级留空待题材成分映射
            code = _sym_to_code(e["source_symbol"]) if e["source_symbol"] else None
            closes = closes_map.get(code or "", {})
            if code and closes and pub in closes:
                base = closes[pub]
                fwd = _next_days(closes, pub, 5)
                r1 = r5 = None
                if len(fwd) >= 1 and closes[fwd[0]]:
                    r1 = round(closes[fwd[0]] / base - 1, 6)
                if len(fwd) >= 5 and closes[fwd[4]]:
                    r5 = round(closes[fwd[4]] / base - 1, 6)
                rec["ret_t1"] = r1
                rec["ret_t5"] = r5
                if r1 is not None or r5 is not None:
                    with_ret += 1
            else:
                rec["ret_t1"] = rec["ret_t5"] = None
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    print(f"导出 {written} 条 → {out_path}")
    print(f"带收益标签（个股级+marketdb 有数据）: {with_ret}")


if __name__ == "__main__":
    main()
