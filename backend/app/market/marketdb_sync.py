"""marketdb 盘后增量同步调度器（调研采纳第 4 批的运维收尾）。

背景：marketdb（DuckDB 日K 仓，scripts/sync_marketdb.py 建仓）是 RPS 横截面
与 tech_score v3 第 8 维的数据地基。此前日常增量依赖人工跑脚本——忘跑则
RPS 分位基于陈旧截面，且无从察觉。本模块把它挂进 lifespan：每日盘后自动
增量同步（近 10 交易日 dump + 复权重建，量级分钟级）。

设计要点（每条都有出处）：
- **子进程隔离**：同步是同步阻塞 IO（httpx 下载全市场 dump + duckdb 写入），
  用 asyncio.create_subprocess_exec 跑 scripts/sync_marketdb.py——崩溃/卡死
  不伤主进程；cwd 传 backend/（脚本要求：Settings 的 env_file 是相对路径）；
  venv 用 sys.executable（与后端同一环境，依赖齐）。
- **磁盘幂等**：成功一次写 data/marketdb/sync_state.json（last_sync_date），
  重启后当日不重跑。P1-D-2 的教训（premarket_scheduler 幂等改磁盘持久化）：
  进程内 state 重启后失忆 → 重复下载。手动跑过 CLI 的当日调度器会再跑一次
  增量——按 (thscode, date_ms) 去重，重复跑无害，不为此引入跨进程锁。
- **失败重试**：失败不写 state，下个 check 间隔自然重试（增量幂等，重复跑
  无害；dump 是预签名 URL 直下）。stderr 尾部留进日志。
- **非交易日照跑**：不做交易日判定——增量按日期去重，非交易日空同步无害；
  交易日历判定的节假日陷阱（trade_calendar 被测试覆盖残片的前科）不值得为
  省一次空下载去冒。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from app.core.bjtime import beijing_now


log = logging.getLogger(__name__)

#: app/market/ → backend/
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "sync_marketdb.py"
STATE_PATH = BACKEND_ROOT / "data" / "marketdb" / "sync_state.json"


def _load_state(path: Path | None = None) -> dict:
    """读幂等 state；文件缺失/损坏一律当 {}（同步是幂等的，宁可多跑一次）。"""
    p = path or STATE_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        log.warning("marketdb sync state unreadable, treating as empty: %s", p)
        return {}


def should_run_sync(
    state: dict,
    now: datetime,
    *,
    run_hour: int,
    run_minute: int,
) -> bool:
    """当日过点后且今日尚未成功同步 → True（纯函数，测试锚定）。"""
    if now.hour < run_hour or (now.hour == run_hour and now.minute < run_minute):
        return False
    return state.get("last_sync_date") != now.strftime("%Y%m%d")


def _mark_synced(now: datetime, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"last_sync_date": now.strftime("%Y%m%d"), "at": now.isoformat()},
                   ensure_ascii=False),
        encoding="utf-8",
    )


async def _run_subprocess() -> tuple[int, str]:
    """跑 scripts/sync_marketdb.py（日常增量，无 --full/--factors）。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPT_PATH),
        cwd=str(BACKEND_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    err_tail = (err or b"")[-500:].decode("utf-8", "replace").strip()
    return proc.returncode or 0, err_tail


async def run_sync_once() -> bool:
    """单次同步：成功写 state 返回 True；失败留痕返回 False（不抛）。"""
    code, err_tail = await _run_subprocess()
    if code == 0:
        _mark_synced(beijing_now())
        return True
    log.warning("marketdb sync failed: exit=%s stderr=%s", code, err_tail)
    return False


async def marketdb_sync_scheduler(
    *,
    stop: asyncio.Event,
    run_hour: int = 16,
    run_minute: int = 30,
    check_interval_seconds: float = 300.0,
) -> None:
    """盘后增量同步调度（lifespan 任务）：每日过点后当日一次，磁盘幂等。"""
    while not stop.is_set():
        try:
            now = beijing_now()
            if should_run_sync(
                _load_state(), now, run_hour=run_hour, run_minute=run_minute
            ):
                t0 = time.monotonic()
                if await run_sync_once():
                    log.info("marketdb sync done in %.1fs", time.monotonic() - t0)
        except Exception:
            log.exception("marketdb sync scheduler failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
