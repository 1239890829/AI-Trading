"""盘中方向跟踪（选股 2.0 §5–6，docs/stock-picking-system-2026-09-02.md，批次 B）。

结构（红线级，与 intraday_rules 同一纪律）：
- `DirectionTracker`：单方向**纯状态机**，零 IO。输入一拍数据（板块涨幅/
  涨停家数/最高板/龙头涨幅 + 环境分位与相位），输出状态变更与待发提醒。
  规则判定全部委托 intraday_rules（confirm_signal/falsify_signal）——
  "回测通过的规则"和"线上跑的规则"是同一份代码。
- `IntradayWatcher`：持有一组 tracker；`step(beat)` 推进全部方向并汇合提醒。
- `collect_beat_inputs` / `watcher_loop`：取数与调度在服务层（本模块 IO 侧）。

状态机语义：
- 峰值/转负计数只在有数据的拍更新；**整拍缺数据的方向跳过**（缺 ≠ 证伪，
  三态纪律的盘中形态）。
- 证伪一次性：任一触发器命中后当天不再确认、不再提醒（triggers 留档可解释）。
- 确认提醒按 (方向, 个股) 当日去重，且每方向上限 MAX_ALERTS_PER_DIRECTION——
  防止龙头轮动把用户手机刷爆。

一拍数据（beat）形状：
    {"now_minutes": int, "trading": bool,
     "themes": {题材: {"pct": float|None, "limit_up": int|None, "max_boards": int|None,
                        "leader_symbol": str|None, "leader_name": str|None,
                        "leader_pct": float|None, "limit_down": int|None,
                        "volume_ratio": float|None, "members": [str, ...]}},
     "env": {"phase": str|None, "promo_percentile": float|None}}

数据源（缺什么在 beat 里显式 unknown，绝不冒充）：
- 题材归因：ths 涨停池 parse_theme_tags → 家数/最高板/龙头（连板最高成员）
- 板块涨幅：东财 get_board_metrics 概念+行业（provider 内已按 total 翻页）；
  题材名 → 板块名精确匹配，失败取"包含关系且板块名最短"，仍无 = unknown
- volume_ratio：近似量比（§5.1#4 降级口径）= 快照当日累计量 / 昨日全天量
  / 已开市占比，取题材内最强成员（max）；昨日量按日缓存，失败 = unknown。
  精确基线（TDX 同期累计量）待批次 D 落库后切换
- limit_down：数据源暂缺 → None，falsify 的 leader_break 触发器不激活
- 环境：compute_market_sentiment 每 env_refresh_seconds 刷新缓存；
  刷新失败沿用上次值（不猜新值）——盘中60s/拍全量重算情绪太重且浪费配额
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.db import get_session_factory
from app.market import trade_calendar as tc
from app.market.trading_status import beijing_now
from app.models.alert import AlertRule
from app.notifiers import get_notifier_registry
from app.picks import intraday_rules as rules
from app.picks.intraday_rules import (
    build_alert,
    confirm_signal,
    falsify_signal,
    position_size,
)
from app.repositories.alert_repo import AlertRepository
from app.services.theme_service import normalize_theme, parse_theme_tags

log = logging.getLogger(__name__)

#: watcher 专用系统规则名（record_trigger 的外键要求 rule 存在；get-or-create）
WATCHER_RULE_NAME = "__picks_watcher__"
MAX_ALERTS_PER_DIRECTION = 3   # 每方向每日确认提醒上限（防龙头轮动刷屏）
VR_MEMBERS_CAP = 5             # 量比计算的题材成员上限（按板数取最强 5 只）


# ---------------------------------------------------------------- 纯函数：板块匹配


def match_board_pct(tag: str, board_pct: dict[str, float]) -> float | None:
    """题材标签 → 东财板块涨幅。精确命中优先；否则取"包含关系且板块名最短"
    （最短 = 语义最贴近，如「粮食」→「粮食概念」而非「粮食安全概念」）；
    仍无 → None（unknown）。绝不拿不相干的板块冒充。
    """
    if tag in board_pct:
        return board_pct[tag]
    candidates = [
        (name, pct)
        for name, pct in board_pct.items()
        if name and (tag in name or name in tag)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: len(x[0]))
    return candidates[0][1]


# ---------------------------------------------------------------- 纯状态机


@dataclass
class DirectionTracker:
    """单方向盘中状态机（纯逻辑零 IO，可被批次 D 回测框架直接回放）。"""

    direction: str
    logic: str = ""
    pool: list[dict] = field(default_factory=list)
    # —— 盘中推进状态 ——
    peak_pct: float | None = None
    below_zero_beats: int = 0
    beats: int = 0
    missing_beats: int = 0
    confirmed: bool = False
    falsified: bool = False
    falsify_triggers: list[dict] = field(default_factory=list)
    alerted: set = field(default_factory=set)   # 当日已提醒个股（去重）
    last_confirm: dict | None = None

    def step(self, theme: dict | None, env: dict | None, now_minutes: int | None) -> list[dict]:
        """推进一拍。返回待发提醒（调用方负责分发与落库；本方法零 IO）。"""
        self.beats += 1
        if theme is None:
            # 该方向本拍无数据：unknown，不推进峰值也不证伪（缺 ≠ 证伪）
            self.missing_beats += 1
            return []
        pct = theme.get("pct")
        if pct is not None:
            self.peak_pct = pct if self.peak_pct is None else max(self.peak_pct, pct)
            self.below_zero_beats = self.below_zero_beats + 1 if pct < 0 else 0
        if self.falsified:
            return []  # 证伪一次性：当天不再确认、不再提醒

        f = falsify_signal(
            peak_pct=self.peak_pct,
            current_pct=pct,
            below_zero_beats=self.below_zero_beats,
            leader_broke_board=theme.get("leader_broke_board"),
            theme_limit_down=theme.get("limit_down"),
            promo_percentile=(env or {}).get("promo_percentile"),
            phase=(env or {}).get("phase"),
        )
        if f["falsified"]:
            self.falsified = True
            self.falsify_triggers = f["triggers"]
            return [self._falsify_alert(f["triggers"], pct)]

        c = confirm_signal(
            theme_pct=pct,
            theme_limit_up=theme.get("limit_up"),
            theme_max_boards=theme.get("max_boards"),
            leader_pct=theme.get("leader_pct"),
            volume_ratio=theme.get("volume_ratio"),
            promo_percentile=(env or {}).get("promo_percentile"),
            phase=(env or {}).get("phase"),
            now_minutes=now_minutes,
        )
        self.last_confirm = c
        self.confirmed = self.confirmed or c["confirmed"]
        if not c["confirmed"]:
            return []
        alert = self._confirm_alert(c, theme)
        return [alert] if alert else []

    # ---- 提醒构造（仍是纯函数；分发在服务层）----

    def _falsify_alert(self, triggers: list[dict], pct: float | None) -> dict:
        detail = "；".join(t["detail"] for t in triggers)
        text = "\n".join([
            f"【方向证伪】{self.direction}",
            f"触发：{detail}",
            f"当前板块涨幅：{'缺失' if pct is None else f'{pct}%'}（盘中峰值 {self.peak_pct}%）",
            "处理：该方向当日停止确认与提醒；已提示个股按各自止损纪律执行",
            "8. 状态：非投资建议，模拟跟踪",
        ])
        return {
            "key": f"{self.direction}:falsify",
            "kind": "falsify",
            "direction": self.direction,
            "symbol": "",
            "name": "",
            "text": text,
            "at": beijing_now().isoformat(),
            "meta": {
                "trigger_value": pct,
                "threshold": rules.FALSIFY_DRAWDOWN_PCT,
                "triggers": triggers,
            },
        }

    def _confirm_alert(self, confirm: dict, theme: dict) -> dict | None:
        if len(self.alerted) >= MAX_ALERTS_PER_DIRECTION:
            return None
        # 候选个股：盘中龙头（涨停池连板最高）优先；缺失时回退盘前标的池
        symbol = theme.get("leader_symbol")
        name = theme.get("leader_name") or ""
        role = "龙头" if symbol else ""
        if not symbol:
            for p in self.pool:
                if p.get("symbol") and p["symbol"] not in self.alerted:
                    symbol, name, role = p["symbol"], p.get("name") or "", p.get("role") or ""
                    break
        if not symbol or symbol in self.alerted:
            return None
        self.alerted.add(symbol)
        unknown = [c["label"] for c in confirm["checks"] if c["met"] is None]
        risks = [f"「{label}」盘中判不出来" for label in unknown]
        risks.append("第一版无分时/均线数据：买入区间与止损留空，不臆造")
        pos = position_size(confirm["strength"])
        text = build_alert(
            direction=self.direction,
            symbol=symbol,
            name=name,
            role=role or "待判",
            confirm=confirm,
            logic=self.logic or "盘前方向盘中确认走强",
            buy_range=None,
            stop=None,
            position=pos,
            risks=risks,
            plan_note="缺分时与均线，参考回调企稳/放量突破/龙头回封条件（§6.3）",
        )
        return {
            "key": f"{self.direction}:{symbol}:confirm",
            "kind": "confirm",
            "direction": self.direction,
            "symbol": symbol,
            "name": name,
            "text": text,
            "at": beijing_now().isoformat(),
            "meta": {
                "trigger_value": theme.get("pct"),
                "threshold": rules.CONFIRM_THEME_PCT_EARLY,
                "strength": confirm.get("strength"),
                "position": pos,
            },
        }


class IntradayWatcher:
    """当日盘前方向的 tracker 组。step(beat) 推进全部方向并汇合提醒。"""

    def __init__(self, directions: list[dict]):
        self.trackers = [
            DirectionTracker(
                direction=d.get("direction") or "",
                logic=d.get("logic") or "",
                pool=[p for p in (d.get("pool") or []) if p.get("symbol")],
            )
            for d in directions
            if d.get("direction")
        ]
        self.started_at = beijing_now().isoformat()
        self.beat_count = 0

    def step(self, beat: dict) -> list[dict]:
        self.beat_count += 1
        env = beat.get("env") or {}
        themes = beat.get("themes") or {}
        alerts: list[dict] = []
        for tr in self.trackers:
            alerts.extend(tr.step(themes.get(tr.direction), env, beat.get("now_minutes")))
        return alerts

    def state(self) -> dict:
        return {
            "active": True,
            "started_at": self.started_at,
            "beat_count": self.beat_count,
            "trackers": [
                {
                    "direction": t.direction,
                    "beats": t.beats,
                    "missing_beats": t.missing_beats,
                    "peak_pct": t.peak_pct,
                    "below_zero_beats": t.below_zero_beats,
                    "confirmed": t.confirmed,
                    "falsified": t.falsified,
                    "falsify_triggers": t.falsify_triggers,
                    "alerted_symbols": sorted(t.alerted),
                    "last_confirm": t.last_confirm,
                }
                for t in self.trackers
            ],
        }


# ---------------------------------------------------------------- IO：一拍取数


def _beat_themes_from_pool(pool: list) -> dict[str, dict]:
    """涨停池 → 每题材的盘中节拍（家数/最高板/龙头=连板最高成员）。

    members：按板数降序的成员代码表（cap VR_MEMBERS_CAP）——量比按
    "题材内最强成员"计算（max），龙头封板后自身量比衰减不失真。
    """
    themes: dict[str, dict] = {}
    for r in pool:
        boards = int(r.consecutive_boards or 1)
        for raw in parse_theme_tags(getattr(r, "reason", None)):
            tag = normalize_theme(raw)
            st = themes.setdefault(
                tag,
                {"limit_up": 0, "max_boards": 0, "_leader": None, "_members": []},
            )
            st["limit_up"] += 1
            st["max_boards"] = max(st["max_boards"], boards)
            st["_members"].append((boards, r.symbol))
            cur = st["_leader"]
            if cur is None or boards > cur["boards"]:
                st["_leader"] = {
                    "symbol": r.symbol,
                    "name": getattr(r, "name", None) or "",
                    "boards": boards,
                    "pct": getattr(r, "change_pct", None),
                }
    for st in themes.values():
        ld = st.pop("_leader") or {}
        members = [s for _, s in sorted(st.pop("_members"), reverse=True)]
        st["members"] = members[:VR_MEMBERS_CAP]
        st["leader_symbol"] = ld.get("symbol")
        st["leader_name"] = ld.get("name")
        st["leader_boards"] = ld.get("boards")
        st["leader_pct"] = ld.get("pct")
        st.setdefault("pct", None)       # 板块涨幅待东财匹配，先置 unknown
        st.setdefault("limit_down", None)  # 数据源暂缺 → leader_break 触发器不激活
        st.setdefault("volume_ratio", None)  # 待快照+昨日量计算，先置 unknown
    return themes


async def _board_pcts(hub) -> tuple[dict[str, float], int]:
    """东财板块涨幅（概念+行业）。失败返回空表 → 全部 pct=unknown，不臆造。"""
    composite = hub.provider if hasattr(hub.provider, "providers") else None
    target = next(
        (p for p in (composite.providers if composite else [hub.provider]) if p.name == "eastmoney"),
        None,
    )
    if target is None:
        return {}, 0
    out: dict[str, float] = {}
    for kind in ("concept", "industry"):
        try:
            rows = await target.get_board_metrics(kind)
        except Exception as exc:
            log.warning("watcher beat: board metrics %s failed: %s", kind, exc)
            continue
        for row in rows or []:
            name, pct = row.get("name"), row.get("change_pct")
            if name and pct is not None:
                out[name] = float(pct)
    return out, len(out)


async def _refresh_env(state, env_cache: dict, refresh_after: float) -> dict:
    """环境（phase + promo 分位）缓存刷新。失败沿用上次值（不猜新值）。"""
    cached = env_cache.get("env")
    if cached is not None and time.monotonic() - float(env_cache.get("at") or 0.0) < refresh_after:
        return cached
    fresh = {"phase": None, "promo_percentile": None}
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(state.hub, state.snapshot_service) or {}
        fresh = {
            "phase": sent.get("phase"),
            "promo_percentile": (
                ((sent.get("calibration") or {}).get("percentile") or {}).get("promo_1to2") or {}
            ).get("percentile"),
        }
        env_cache["env"] = fresh
        env_cache["at"] = time.monotonic()
    except Exception as exc:
        log.warning("watcher env refresh failed: %s（沿用上次值）", exc)
    return env_cache.get("env") or fresh


async def _prev_day_volume(hub, symbol: str) -> float | None:
    """昨日全天量（股）。腾讯日线 qfqday 盘中含今日未完成 bar——过滤掉
    ts 日期 ≥ 今日（北京）后取最后一根；过滤后为空 = 无昨日数据 → None。
    量纲：Kline.volume 已 ×100 成股，与快照 Quote.volume 同单位。
    失败返回 None（unknown），调用方缓存后当日不重试。
    """
    try:
        from datetime import timedelta

        from app.market.trading_status import beijing_now as _now

        klines = await hub.provider.get_kline(symbol, "1d")
        today = _now().date()
        prev = [
            k for k in (klines or [])
            if k.volume is not None and (k.ts + timedelta(hours=8)).date() < today
        ]
        return float(prev[-1].volume) if prev else None
    except Exception as exc:
        log.debug("watcher vr: prev-day volume %s failed: %s", symbol, exc)
        return None


async def _attach_volume_ratios(
    hub, snapshot_service, themes: dict[str, dict], vr_cache: dict, today_key: str,
    now_minutes: int | None,
) -> None:
    """为每题材附 volume_ratio = max(成员近似量比)（§5.1#4 降级口径接线）。

    - 当日累计量：**优先 snapshot_service 全市场快照**（约 5500 只、盘中 60s
      轮询、symbol 裸 6 位 / volume 股，与昨日量同量纲）——题材成员任意覆盖；
      快照缺失时回退 hub.get_quotes（同步内存读，仅 watchlist 成员在缓存）。
      2026-09-04 修复：原实现 `await hub.get_quotes(...)` 双重错——hub 方法
      是同步的（await list 抛 TypeError 被吞成 warning），且 hub 只装 watchlist
      股票，题材成员多数不在其中 → 量比恒 unknown。
    - 昨日全天量：按日缓存 {date, vols:{symbol: 股|None}}，None 当日不重试；
    - max 语义 = 题材内最强量能，龙头封板后自身量比衰减不失真；
    - 任一环失败 → 该题材 volume_ratio 保持 None（unknown），绝不臆造。
    """
    from app.picks.intraday_rules import compute_volume_ratio

    symbols = {s for st in themes.values() for s in (st.get("members") or [])}
    if not symbols:
        return
    if vr_cache.get("date") != today_key or "vols" not in vr_cache:
        vr_cache.clear()
        vr_cache["date"] = today_key
        vr_cache["vols"] = {}
    vols_cache: dict = vr_cache["vols"]
    for s in sorted(symbols):
        if s not in vols_cache:
            vols_cache[s] = await _prev_day_volume(hub, s)
    # 当日累计量：全市场快照（覆盖任意题材成员）→ hub 缓存回退（仅 watchlist）。
    # 两条路径都是纯内存读，无 IO，不再包 try/except 吞错。
    snap = getattr(snapshot_service, "snapshot", None) or []
    vol_today = {
        r["symbol"]: r["volume"] for r in snap if r.get("symbol") and r.get("volume")
    }
    if not vol_today:
        quotes = hub.get_quotes(sorted(symbols))
        vol_today.update({q.symbol: q.volume for q in quotes or [] if q.volume})
    for st in themes.values():
        ratios = [
            r for r in (
                compute_volume_ratio(vol_today.get(s), vols_cache.get(s), now_minutes)
                for s in (st.get("members") or [])
            )
            if r is not None
        ]
        st["volume_ratio"] = max(ratios) if ratios else None


async def collect_beat_inputs(app, env_cache: dict, *, env_refresh_seconds: float) -> dict:
    """一拍取数：交易日历 → ths 涨停池归因 → 东财板块匹配 → 环境缓存。

    app 兼容 FastAPI 实例或其 .state 对象（与 morning_brief.collect_evidence 同规则）。
    """
    state = app.state if hasattr(app, "state") else app
    hub = state.hub
    now = beijing_now()
    beat: dict = {
        "now_minutes": now.hour * 60 + now.minute,
        "trading": False,
        "themes": {},
        "env": env_cache.get("env"),
        "pool_count": 0,
        "board_count": 0,
    }
    days = None
    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        log.warning("watcher beat: calendar failed: %s", exc)
    td = tc.last_trade_date(days, asof=now.date()) if days else None
    # 盘中口径：今天是交易日且处于交易时段；盘前/盘后拍数无意义（池子是静止的）
    beat["trading"] = bool(td == now.date() and tc.in_trading_window(now))
    if not beat["trading"]:
        return beat

    try:
        pool = await hub.provider.get_limit_up_pool(td) or []
    except Exception as exc:
        log.warning("watcher beat: limit-up pool failed: %s", exc)
        pool = []
    beat["pool_count"] = len(pool)
    themes = _beat_themes_from_pool(pool)

    board_pct, board_count = await _board_pcts(hub)
    beat["board_count"] = board_count
    for tag, st in themes.items():
        st["pct"] = match_board_pct(tag, board_pct)

    # 量比接线（§5.1#4 降级口径）：快照当日量 + 昨日量按日缓存。
    # 失败路径全部落 unknown——量比判不出来时 confirm 只是缺一项，不阻断其他判定。
    vr_cache = getattr(state, "picks_vr_cache", None)
    if vr_cache is None:
        vr_cache = {"date": "", "vols": {}}
        state.picks_vr_cache = vr_cache
    await _attach_volume_ratios(
        hub, getattr(state, "snapshot_service", None),
        themes, vr_cache, td.strftime("%Y%m%d"), beat["now_minutes"],
    )

    beat["themes"] = themes
    beat["env"] = await _refresh_env(state, env_cache, env_refresh_seconds)
    return beat


# ---------------------------------------------------------------- IO：提醒分发


def _default_watcher_channels() -> str:
    """watcher 规则默认 channels（settings 逗号串 → JSON 列表字符串）。"""
    import json

    from app.core.config import settings

    return json.dumps([c.strip() for c in settings.picks_watcher_channels.split(",") if c.strip()])


def ensure_system_rule(session_factory) -> AlertRule:
    """watcher 专用系统规则（get-or-create）。detached 后只读 id/name/channels。

    升级语义：v1 硬编码默认 ``["in_app", "log"]`` 的旧规则行升级为当前配置默认
    （feishu 进列）——该值是历史默认而非用户有意定制（用户定制任何其他值不动）；
    webhook 未配置时 feishu 通道按既有语义显式跳过，不会伪装成功。
    """
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == WATCHER_RULE_NAME).one_or_none()
        if row is None:
            row = AlertRule(
                name=WATCHER_RULE_NAME,
                enabled=1,
                condition_type="picks_intraday",
                scope="all",
                threshold=0.0,
                channels=_default_watcher_channels(),
            )
            db.add(row)
        elif row.channels == '["in_app", "log"]':
            row.channels = _default_watcher_channels()
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


async def dispatch_alert(app, alert: dict) -> bool:
    """去重（append_alert）→ record_trigger → NotifierRegistry 分发。

    返回 False = 当日重复（key 已存在），调用方无须重试。app 兼容实例或 .state。
    """
    from app.picks.morning_brief import append_alert, brief_for_today

    target, _ = brief_for_today()
    if not append_alert(target, alert):
        return False
    state = app.state if hasattr(app, "state") else app
    session_factory = get_session_factory()
    rule = ensure_system_rule(session_factory)
    repo = getattr(state, "alert_repo", None) or AlertRepository(session_factory)
    meta = alert.get("meta") or {}
    event = repo.record_trigger(
        rule.id,
        alert.get("symbol") or "000000",
        float(meta.get("trigger_value") or 0.0),
        float(meta.get("threshold") or 0.0),
        snapshot={
            "kind": alert.get("kind"),
            "direction": alert.get("direction"),
            "text": alert.get("text"),
        },
    )
    channels = await get_notifier_registry().dispatch(event, rule)
    repo.update_event_channels(event.id, channels)
    first_line = (alert.get("text") or "").splitlines()[0] if alert.get("text") else alert.get("key")
    log.warning("[PICKS-WATCHER] %s", first_line)
    return True


# ---------------------------------------------------------------- IO：调度


def ensure_watcher(app) -> IntradayWatcher | None:
    """当日简报 → watcher（有实例复用，无简报返回 None）。app 兼容实例或 .state。"""
    state = app.state if hasattr(app, "state") else app
    existing = getattr(state, "picks_watcher", None)
    if existing is not None:
        return existing
    from app.picks.morning_brief import brief_for_today

    _, payload = brief_for_today()
    if not payload:
        return None
    watcher = IntradayWatcher(payload.get("directions") or [])
    state.picks_watcher = watcher
    return watcher


async def watcher_loop(app, stop: asyncio.Event) -> None:
    """盘中调度（lifespan 任务）：交易时段内每拍取数 → step → 分发。

    无简报时空转（每 10 分钟提醒一次日志，不刷屏）；单拍失败不终止循环。
    """
    interval = max(5.0, settings.picks_watcher_interval_seconds)
    env_refresh = settings.picks_watcher_env_refresh_seconds
    env_cache: dict = getattr(app.state, "picks_env_cache", None) or {"at": 0.0, "env": None}
    app.state.picks_env_cache = env_cache
    idle_warned = False
    while not stop.is_set():
        try:
            watcher = ensure_watcher(app)
            if watcher is None:
                if not idle_warned:
                    log.info("picks watcher idle: 今日无盘前简报，先 POST /api/picks/morning-brief/generate")
                    idle_warned = True
            else:
                idle_warned = False
                beat = await collect_beat_inputs(app, env_cache, env_refresh_seconds=env_refresh)
                if beat.get("trading"):
                    for a in watcher.step(beat):
                        if await dispatch_alert(app, a):
                            log.info("picks watcher alert dispatched: %s", a.get("key"))
                        else:
                            log.info("picks watcher alert deduped: %s", a.get("key"))
        except Exception:
            log.exception("picks watcher beat failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
