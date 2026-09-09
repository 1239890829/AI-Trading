"""临板雷达：涨停前识别、提醒与准入（2026-09-09 用户指令，KB-DEC-011）。

**政策**：只有涨停前已提醒过的股票才准入盘中跟踪；封板后才发现的一律不入册。
旧门槛「boards≥1」= 制度上等涨停，是滞后根源之一——已从机会候选准入中移除。

临板区（按板性缩放，封板前保留可操作跑道）：

| 板性 | 涨停幅 | 临板下沿 | 封板判定 |
|---|---|---|---|
| 主板 60/00 | 10% | 6.5% | pct ≥ 9.7 |
| 创业/科创 30/68 | 20% | 13.0% | pct ≥ 19.7 |
| ST（名称含 ST） | 5% | 3.2% | pct ≥ 4.7 |
| 北交所 8/4 开头 | 30% | 19.5% | pct ≥ 29.7 |

雷达活跃窗口：**09:20–11:30 / 13:00–15:00**（含集合竞价尾段 09:20-09:25——
竞价指示价临板即可提前预警，抢在 09:25 封板价确定之前）。每 6s 扫一遍内存
全市场快照；新进入临板区的股票：

1. `record_sighting(layer="pre_limit", gate=...)` —— 先登记（封板前一刻的入场价）
2. `dispatch_alert(kind="pre_limit")` —— 规则直发通知中心，**不经 LLM 判读（零延迟）**

当日幂等：以台账首见唯一为准（重启安全——去重源是台账而非内存）。
新鲜度守卫：快照 `last_success` 超 120s（非交易时段/数据停更）雷达静默，防陈旧数据误报。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

#: 快照新鲜度上限（秒）——超过视为陈旧（非交易时段/停更），雷达静默
SNAPSHOT_FRESH_SECONDS = 120
#: 雷达扫描间隔（秒）
SWEEP_INTERVAL = 6.0
#: 非活跃窗口的休眠间隔（秒）
IDLE_INTERVAL = 30.0

# 交易时段（含集合竞价尾段）："HH:MM" 区间
_ACTIVE_WINDOWS = (("09:20", "11:30"), ("13:00", "15:00"))


def board_limit_pct(symbol: str, name: str = "") -> float:
    """按代码段/名称推断涨停幅度（%）。

    ST（名称含 ST）5% > 北交所（8/4 开头）30% > 创业/科创（30/68）20% > 主板 10%。
    """
    if "ST" in (name or "").upper():
        return 5.0
    sym = (symbol or "").strip()
    if sym.startswith(("8", "4", "9")):  # 北交所/老三板
        return 30.0
    if sym.startswith(("30", "68")):
        return 20.0
    return 10.0


def seal_threshold(limit_pct: float) -> float:
    """封板判定线：pct ≥ 此值视为已封板（留 0.3pct 容差吸收 9.96%~10.09% 的取整差异）。"""
    return limit_pct - 0.3


def pre_limit_floor(limit_pct: float) -> float:
    """临板区下沿：涨停幅 × 0.65——保留 ≥35% 涨停幅的可操作跑道。"""
    return round(limit_pct * 0.65, 1)


def is_sealed(pct: float, limit_pct: float) -> bool:
    return pct >= seal_threshold(limit_pct)


def in_pre_limit_zone(pct: float, limit_pct: float) -> bool:
    return pre_limit_floor(limit_pct) <= pct < seal_threshold(limit_pct)


def radar_active_now(now: datetime | None = None) -> bool:
    """雷达活跃窗口：交易日同时段的 09:20–11:30 / 13:00–15:00。"""
    now = now or beijing_now()
    if now.weekday() >= 5:  # 周末（节假日由快照新鲜度守卫兜底）
        return False
    hhmm = now.strftime("%H:%M")
    return any(lo <= hhmm < hi for lo, hi in _ACTIVE_WINDOWS)


def select_candidates(
    snapshot_rows: list[dict], registered_symbols: set[str]
) -> list[dict]:
    """纯函数：从全市场快照选出「涨停前临板」候选（供雷达提醒+入册）。

    规则（KB-DEC-011）：未封板 + 进入临板区 + 当日未登记。
    已封板 = 涨停后才发现 = 一律不入册；炸板后重回临板区的票视为新的临板信号
    （此前从未在涨停前提醒过 → 允许预警，回封尝试可操作）。
    """
    out: list[dict] = []
    for row in snapshot_rows or []:
        symbol = str(row.get("symbol") or "")
        pct = row.get("change_pct")
        if not symbol or pct is None or symbol in registered_symbols:
            continue
        name = str(row.get("name") or "")
        limit = board_limit_pct(symbol, name)
        if is_sealed(float(pct), limit):
            continue  # 已封板——涨停后才发现，一律不入册（用户指令）
        if not in_pre_limit_zone(float(pct), limit):
            continue
        out.append(
            {
                "symbol": symbol,
                "name": name,
                "pct": float(pct),
                "limit_pct": limit,
                "runway_pct": round(seal_threshold(limit) - float(pct), 2),
                "price": row.get("price"),
                "turnover_rate": row.get("turnover_rate"),
            }
        )
    out.sort(key=lambda c: c["runway_pct"])  # 距封板最近的优先
    return out


async def pre_limit_sweep(app) -> int:
    """单轮扫描：选候选 → 先登记（台账）→ 再提醒（通知中心）。返回新入册数。"""
    state = app.state if hasattr(app, "state") else app
    svc = getattr(state, "snapshot_service", None)
    rows = getattr(svc, "snapshot", None) or []

    # 新鲜度守卫：非交易时段/停更时快照 last_success 停止前进——静默防误报
    last = getattr(svc, "last_success", None)
    if last is None or (datetime.now(timezone.utc) - last).total_seconds() > SNAPSHOT_FRESH_SECONDS:
        return 0

    from app.picks.watch_ledger import get_day, record_sighting

    tdate = beijing_now().date().isoformat()
    tstamp = beijing_now().strftime("%H:%M:%S")
    registered = {r.get("symbol") for r in get_day(tdate)}
    candidates = select_candidates(rows, registered)
    if not candidates:
        return 0

    from app.picks.watcher import dispatch_alert

    n_new = 0
    for c in candidates:
        # 1) 先登记（首见唯一；登记成功才提醒——保证「入册的票必然已触发临板预警」）
        row = record_sighting(
            trade_date=tdate,
            symbol=c["symbol"],
            name=c["name"],
            layer="pre_limit",
            source_theme="",
            reason={
                "gate": "pre_limit",
                "pct": c["pct"],
                "runway_pct": c["runway_pct"],
                "limit_pct": c["limit_pct"],
                "turnover_rate": c.get("turnover_rate"),
                "note": "涨停前预警（临板雷达，KB-DEC-011）",
            },
            is_leader=False,
            boards=0,
            entry_price=c.get("price"),
            entry_time=tstamp,
        )
        if row is None:
            continue  # 并发下已被登记
        n_new += 1
        # 2) 提醒：规则直发（append_alert → AlertEvent → 通知中心），不经 LLM 判读
        alert = {
            "kind": "pre_limit",
            "symbol": c["symbol"],
            "name": c["name"],
            "direction": "临板预警",
            "text": (
                f"临板 {c['pct']:.1f}%（距封板 {c['runway_pct']:.1f}pct，{c['limit_pct']:.0f}cm）"
                f"换手 {c.get('turnover_rate') or '--'}%——涨停前预警，现价 {c.get('price')}"
            ),
            "meta": {"trigger_value": c.get("price")},
        }
        try:
            await dispatch_alert(app, alert)
        except Exception:  # noqa: BLE001  提醒失败不影响登记（登记是政策关键位）
            log.exception("pre-limit alert dispatch failed: %s", c["symbol"])
    if n_new:
        log.info("[临板雷达] %d 只新入册（涨停前预警）：%s", n_new,
                 ",".join(c["symbol"] for c in candidates[:n_new]))
    return n_new


async def pre_limit_loop(app, stop: asyncio.Event) -> None:
    """常驻循环：交易时段 6s 一轮，其余 30s。停机事件受控于 lifespan。"""
    log.info("pre-limit radar loop started (KB-DEC-011)")
    while True:
        try:
            if stop.is_set():
                log.info("pre-limit radar loop stop requested")
                return
            if radar_active_now():
                await pre_limit_sweep(app)
                await asyncio.sleep(SWEEP_INTERVAL)
            else:
                await asyncio.sleep(IDLE_INTERVAL)
        except asyncio.CancelledError:
            log.info("pre-limit radar loop cancelled")
            return
        except Exception:  # noqa: BLE001  单轮失败不终止雷达
            log.exception("pre-limit radar sweep failed")
            await asyncio.sleep(IDLE_INTERVAL)
