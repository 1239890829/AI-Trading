"""历史龙头档案（猎场升级批次 C 前半，2026-09-09 用户需求 2）。

「资金记忆与龙头识别」：近 N 日涨停池按官方归因标签聚合，产出每题材的
**最高连板股档案**（龙头/妖股：symbol/name/max_boards/最近涨停日/区间），
供启动初期判断「资金最可能首选的标的」——历史上该题材的高辨识度股。

数据源：provider.get_limit_up_pool(date) 逐日回拉（ths/东财均支持历史日期），
失败日跳过（不臆造）；聚合结果缓存 data/leader_archive.json（TTL 1 天，
盘后刷新一次足够——历史档案盘中不变）。

口径纪律：连板数取区间内该股最高 consecutive_boards；「妖股」= max_boards ≥ 5；
档案只陈述历史事实，不预测（预测是议程/用户的事）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "data" / "leader_archive.json"
ARCHIVE_TTL_HOURS = 20
DEFAULT_DAYS = 30


async def build_archive(provider, days: int = DEFAULT_DAYS) -> dict:
    """回拉近 N 日涨停池 → 按题材标签聚合龙头档案（async：provider 是异步源）。"""
    today = beijing_now().date()
    # 按股聚合：symbol → {name, max_boards, last_date, tags, sum_pct}
    by_symbol: dict[str, dict] = {}
    n_days_ok = 0
    for offset in range(days):
        d = today - timedelta(days=offset)
        try:
            pool = await provider.get_limit_up_pool(d)
        except Exception:  # noqa: BLE001  单日失败跳过（节假日/源抖动）
            pool = None
        if not pool:
            continue
        n_days_ok += 1
        for rec in pool:
            if not rec.symbol:
                continue
            ent = by_symbol.setdefault(rec.symbol, {
                "name": rec.name or "", "max_boards": 0, "last_date": "",
                "tags": set(), "hit_days": 0,
            })
            boards = rec.consecutive_boards or 1
            ent["max_boards"] = max(ent["max_boards"], boards)
            ent["last_date"] = max(ent["last_date"], d.isoformat())
            ent["hit_days"] += 1
            if rec.reason:
                for tag in (rec.reason or "").split("+"):
                    tag = tag.strip()
                    if 1 < len(tag) <= 12:
                        ent["tags"].add(tag)

    # 按题材标签聚合：tag → 龙头列表（max_boards 降序 top 3）
    archive: dict[str, list[dict]] = {}
    for sym, ent in by_symbol.items():
        for tag in ent["tags"]:
            archive.setdefault(tag, []).append({
                "symbol": sym, "name": ent["name"],
                "max_boards": ent["max_boards"], "last_date": ent["last_date"],
                "hit_days": ent["hit_days"],
                "is_demon": ent["max_boards"] >= 5,
            })
    for tag in archive:
        archive[tag].sort(key=lambda x: (-x["max_boards"], -x["hit_days"]))
        archive[tag] = archive[tag][:3]

    return {
        "built_at": datetime.utcnow().isoformat(timespec="seconds"),
        "days_requested": days, "days_with_pool": n_days_ok,
        "symbols_seen": len(by_symbol),
        "themes": archive,
    }


async def get_archive(provider, days: int = DEFAULT_DAYS, force: bool = False) -> dict:
    """读缓存档案（TTL 20h）；过期/强制时重建。"""
    if not force and ARCHIVE_PATH.exists():
        try:
            raw = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
            built = datetime.fromisoformat(raw["built_at"])
            if datetime.utcnow() - built < timedelta(hours=ARCHIVE_TTL_HOURS):
                return raw
        except Exception:  # noqa: BLE001  缓存损坏 → 重建
            log.warning("leader archive cache corrupted, rebuilding")
    archive = await build_archive(provider, days)
    try:
        ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE_PATH.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("leader archive save failed")
    return archive


async def leaders_for_theme(provider, theme: str, days: int = DEFAULT_DAYS) -> list[dict]:
    """指定题材的历史龙头（含子串匹配：「代糖」命中「代糖概念/糖业」）。"""
    archive = await get_archive(provider, days)
    out: list[dict] = []
    for tag, leaders in (archive.get("themes") or {}).items():
        if theme in tag or tag in theme:
            out.extend(leaders)
    out.sort(key=lambda x: (-x["max_boards"], -x["hit_days"]))
    # 同股跨标签去重
    seen: set[str] = set()
    unique = []
    for l in out:
        if l["symbol"] in seen:
            continue
        seen.add(l["symbol"])
        unique.append(l)
    return unique[:5]
