"""题材热度时序前向落库（选股 2.0 批次 D）。

每天收盘后（15:35 复盘调度内）把当日涨停池题材热度快照写一份 JSONL：
``data/picks/heat/YYYYMMDD.jsonl``，每行一个题材。**前向积累**——不回补
历史：涨停池虽可回溯，但"当日收盘口径"的板块涨幅与情绪环境只有当天能
如实取到，回补会混入口径。量级 ~30 行/天，纯文件、零 SQL（与简报同哲学）。

字段（每行）：
- date：交易日（涨停池归属日，非墙钟日期——周末调度不会把数据记到周末）
- tag / limit_up / max_boards / leader_symbol / leader_pct：题材节拍
  （与盘后复盘同一份数据路径 ``watcher._beat_themes_from_pool``）
- pct：匹配到的东财板块涨幅（match_board_pct 同口径；匹配不到 = null 如实缺）
- phase / promo_percentile：当日情绪环境（全文件行共享）
- pool_count：当日涨停总数（题材密度的分母参照）

幂等：磁盘哨兵——目标文件已存在且非空即跳过（与复盘 already_reviewed
同哲学：重启不丢去重状态，长驻调度每分钟 tick 也不会重复拉收盘事实）。
题材为空（涨停池拉取失败/真零涨停日）不落盘，下一个 tick 重试——绝不拿
空文件冒充当日记录。

边界（诚实降级）：本文件是**收盘时点快照**；分钟级同时段基线（精确量比、
盘中峰值等）待分钟数据落库后扩展——量比字段暂缺，不做臆造。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.picks.morning_brief import REPO_ROOT
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

HEAT_DIR = REPO_ROOT / "data" / "picks" / "heat"

# 飙升榜收盘快照（B1）：单文件 JSONL append，每行一个 {date, rank, symbol, ...}
SKYROCKET_PATH = HEAT_DIR / "skyrocket.jsonl"


def _heat_path(trade_date_compact: str) -> Path:
    """文件名用紧凑 %Y%m%d（与简报文件同约定）；行内 date 字段保持 isoformat
    （与涨停池键 / metric_history 可直接 join）。"""
    return HEAT_DIR / f"{trade_date_compact}.jsonl"


def heat_rows_from_facts(facts: dict) -> list[dict]:
    """收盘事实（collect_closing_facts 产物）→ JSONL 行（纯函数，按 tag 稳定排序）。"""
    env = facts.get("env") or {}
    rows: list[dict] = []
    for tag in sorted(facts.get("themes") or {}):
        st = facts["themes"][tag]
        rows.append({
            "date": facts.get("pool_date"),
            "tag": tag,
            "limit_up": st.get("limit_up"),
            "max_boards": st.get("max_boards"),
            "leader_symbol": st.get("leader_symbol"),
            "leader_pct": st.get("leader_pct"),
            "pct": st.get("pct"),
            "phase": env.get("phase"),
            "promo_percentile": env.get("promo_percentile"),
            "pool_count": facts.get("pool_count"),
        })
    return rows


async def record_daily_heat(state) -> dict:
    """收盘后落当日热度快照（幂等，磁盘哨兵前置——先查文件再拉事实）。

    Returns:
        执行摘要；跳过时 recorded=0 且带 reason（already_recorded /
        calendar_unavailable / date_mismatch / empty_themes / no_pool_date）。
    """
    from app.market import trade_calendar as tc
    from app.picks.review_intraday import collect_closing_facts

    state = state.state if hasattr(state, "state") else state

    # 磁盘哨兵前置：交易日从本地日历取（便宜），文件已存在就不再碰行情配额
    try:
        days = await tc.trading_days(state.hub.provider)
        td = tc.last_trade_date(days, asof=beijing_now().date())
    except Exception as exc:
        return {"recorded": 0, "reason": "calendar_unavailable", "detail": str(exc)}
    if td is None:
        return {"recorded": 0, "reason": "calendar_unavailable"}
    path = _heat_path(td.strftime("%Y%m%d"))
    if path.exists() and path.stat().st_size > 0:
        return {"recorded": 0, "reason": "already_recorded", "date": td.isoformat()}

    facts = await collect_closing_facts(state)
    if (facts.get("pool_date") or "") != td.isoformat():
        # 数据归属日与日历不一致：极小概率的跨午夜竞态——跳过重试，不落错名文件
        return {
            "recorded": 0, "reason": "date_mismatch",
            "calendar": td.isoformat(), "facts": facts.get("pool_date"),
        }
    rows = heat_rows_from_facts(facts)
    if not rows:
        return {"recorded": 0, "reason": "empty_themes", "missing": facts.get("missing")}

    env = facts.get("env") or {}
    if env.get("phase") is None and env.get("promo_percentile") is None:
        # 环境缺失照常落库（题材事实比环境更难重取），但必须留痕——
        # 首跑实测（2026-09-02）：重启补落时快照未预热，env 静默变 null，
        # 题材生命周期统计将整列缺维度，只能靠这行日志发现。
        log.warning(
            "heat history env missing for %s（phase/promo_percentile 均为 null）；"
            "facts missing: %s", td.isoformat(), facts.get("missing"),
        )

    HEAT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    log.info("heat history recorded: %s（%d 个题材）", td.isoformat(), len(rows))
    return {"recorded": len(rows), "date": td.isoformat(), "path": str(path)}


async def record_daily_skyrocket(state) -> dict:
    """收盘后把飙升榜 Top20 追加进 skyrocket.jsonl（B1 前向积累，与 heat 同哲学）。

    - 前向积累不回补：榜单只有当日口径，历史靠逐日积累；
    - 去重：文件里已有当日 date 即跳过（重启/多 tick 安全）；
    - 口径：ths period=day（24 小时榜），行内 date 为交易日归属；
    - 失败显式返回 reason，绝不静默。
    """
    from app.market import trade_calendar as tc

    state = state.state if hasattr(state, "state") else state
    try:
        days = await tc.trading_days(state.hub.provider)
        td = tc.last_trade_date(days, asof=beijing_now().date())
    except Exception as exc:
        return {"recorded": 0, "reason": "calendar_unavailable", "detail": str(exc)}
    if td is None:
        return {"recorded": 0, "reason": "calendar_unavailable"}

    HEAT_DIR.mkdir(parents=True, exist_ok=True)
    if SKYROCKET_PATH.exists():
        try:
            if f'"date": "{td.isoformat()}"' in SKYROCKET_PATH.read_text(encoding="utf-8"):
                return {"recorded": 0, "reason": "already_recorded", "date": td.isoformat()}
        except Exception:
            log.warning("skyrocket history dedupe check failed", exc_info=True)

    try:
        rows = (await state.hub.provider.get_skyrocket_list("day"))[:20]
    except Exception as exc:
        return {"recorded": 0, "reason": "source_failed", "detail": str(exc)}
    if not rows:
        return {"recorded": 0, "reason": "empty_list", "date": td.isoformat()}

    lines = [
        json.dumps({
            "date": td.isoformat(),
            "rank": r.get("rank"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "heat": r.get("heat"),
            "rank_change": r.get("rank_change"),
        }, ensure_ascii=False)
        for r in rows
    ]
    tmp = SKYROCKET_PATH.with_suffix(".jsonl.tmp")
    if SKYROCKET_PATH.exists():
        prev = SKYROCKET_PATH.read_text(encoding="utf-8")
    else:
        prev = ""
    tmp.write_text(prev + "\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, SKYROCKET_PATH)
    log.info("skyrocket history recorded: %s（%d 行）", td.isoformat(), len(lines))
    return {"recorded": len(lines), "date": td.isoformat()}


def load_heat_rows(*, limit_days: int | None = None) -> list[dict]:
    """读热度时序（按文件名日期升序拼接）。损坏文件跳过不中断。

    批次 D 之后的调参复验、题材生命周期统计都从这读——回测用的
    metric_history 是全市场指标，这里是题材粒度的对照组。
    """
    if not HEAT_DIR.exists():
        return []
    paths = sorted(HEAT_DIR.glob("*.jsonl"))
    if limit_days:
        paths = paths[-limit_days:]
    out: list[dict] = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception as exc:
                log.warning("heat %s corrupted line skipped: %s", p.name, exc)
    return out
