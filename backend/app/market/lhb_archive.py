"""龙虎榜当日归档（P2-37 二期 E4）。

目的：归因复盘与游资画像需要**可回查**的上榜历史——现拉端点（/api/longhu）只有
实时视图，历史查询每次重打东财 datacenter。本模块把每日 records 落一份 json
（data/lhb/<YYYYMMDD>.json），供 distinctiveness 画像（上榜次数）与复盘归因读取。

设计：
- **json 落盘不进 SQLite**——与 boardflow daily.json 同哲学：只追加、按日切、
  无关联查询需求，避免 ORM 迁移成本；
- 席位明细不归档（每票一次请求 ×63）：/api/longhu/{symbol} 按需现拉已够复盘用，
  归档聚合行即可支撑「上榜次数/净额」类画像；
- 幂等：文件已存在且非空即跳过（发布时间 ~17:00，之前拉的空结果不落盘，留待重试）；
- 失败显式 reason（三态），绝不拿空列表冒充「今日无人上榜」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

LHB_DIR = Path("data/lhb")
ARCHIVE_HOUR_MIN = 17 * 60 + 5   # 17:05 起尝试（东财收盘后 ~17:00 披露）
ARCHIVE_HOUR_MAX = 23 * 60       # 23:00 后不再尝试


def lhb_dir() -> Path:
    return LHB_DIR


def day_path(trade_date_compact: str) -> Path:
    return LHB_DIR / f"{trade_date_compact}.json"


async def archive_day(provider, trade_date: date_cls, *, force: bool = False) -> dict:
    """拉取并归档单日龙虎榜。返回 {archived, reason?, path?, count?}。"""
    key = trade_date.strftime("%Y%m%d")
    path = day_path(key)
    if path.exists() and not force:
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("records"):
                return {"archived": False, "reason": "already_archived", "path": str(path)}
        except Exception:  # noqa: BLE001  损坏文件 → 重写
            log.warning("lhb archive 损坏，重写：%s", path)
    records = await provider.get_longhu_records(trade_date)
    if not records:
        return {"archived": False, "reason": "empty_list", "date": key}
    payload = {
        "trade_date": trade_date.isoformat(),
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in records],
    }
    await asyncio.to_thread(_write_atomic, path, payload)
    log.info("lhb archived: %s（%d 条）", key, len(records))
    return {"archived": True, "path": str(path), "count": len(records)}


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_day(trade_date_compact: str) -> dict | None:
    """读单日归档；无文件/损坏 → None（调用方按缺失处理，不臆造）。"""
    path = day_path(trade_date_compact)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("lhb archive 读取失败：%s", path, exc_info=True)
        return None


def count_symbol_hits(symbols: set[str], *, lookback_days: int = 60, base_dir: Path | None = None) -> dict:
    """近 N 个**已归档日**内每只代码的上榜次数。

    返回 {hits: {symbol: n}, archived_days: 实际扫描到的文件数}——archived_days
    是消费方必须随行的口径披露（归档是前向积累，天数≠lookback_days 属正常）。
    """
    d = base_dir or LHB_DIR
    hits: dict[str, int] = {}
    files = 0
    if d.exists():
        for path in sorted(d.glob("*.json"), reverse=True)[:lookback_days]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            files += 1
            for r in payload.get("records") or []:
                sym = r.get("symbol")
                if sym in symbols:
                    hits[sym] = hits.get(sym, 0) + 1
    return {"hits": hits, "archived_days": files}


# ---------------------------------------------------------------- 调度


async def lhb_archive_loop(app, stop) -> None:
    """盘后调度（lifespan 任务）：交易日 17:05-23:00 每 15 分钟尝试归档当日榜单。"""
    import asyncio
    import contextlib

    from app.core.bjtime import beijing_now
    from app.market import trade_calendar as tc

    interval = 15 * 60
    while not stop.is_set():
        try:
            state = app.state if hasattr(app, "state") else app
            provider = getattr(getattr(state, "hub", None), "provider", None)
            now = beijing_now()
            now_minutes = now.hour * 60 + now.minute
            if provider is not None and ARCHIVE_HOUR_MIN <= now_minutes <= ARCHIVE_HOUR_MAX:
                days = await tc.trading_days(provider)
                td = tc.last_trade_date(days, asof=now.date())
                if td == now.date():
                    result = await archive_day(provider, td)
                    if result.get("archived") or result.get("reason") == "already_archived":
                        # 当日完成即休眠到次日（每 15 分钟空转检查成本低，保持简单）
                        pass
        except Exception:
            log.exception("lhb archive tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
