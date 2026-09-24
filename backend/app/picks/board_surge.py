"""板块异动检测器（自主发现）+ 归因（消息面/龙头结构）+ 板块强度序列落库。

背景（2026-09-13 用户拍板第一期）：
- IntradayWatcher 只跟踪盘前简报登记过的方向——盘前没人登记、盘中自己冒出来的
  题材第一拍就不在跟踪集里（2026-09-11 MLCC/PCB 实测：08:45 村田停产消息催化，
  09:37 系统快讯已捕获，但 watcher 全程无感知，14:07 二次发动 rel 达 +3.19pp）。
  本模块补「自主发现」一路，与 watcher 状态机**并行不混算**（口径各自标注）。
- 数据底座 = snapshot_service 全市场快照（60s/拍，新浪 ~5560 只）× 官方题材成分
  （theme_member 表）。每拍对每个题材聚合：成分中位涨幅、相对全市场超额（rel）、
  涨超 3%/5% 家数占比、成交额合计（当日累计口径）。
- 落库（E1）：每拍 append ``data/theme_momentum/<date>.jsonl``——归因复盘直接查表。
- 归因两路（第一期）：消息面 = 事件方向行匹配（T-6h 窗口、direction≠0、名称互含）；
  龙头结构 = 涨停池 first_seal_time 时序。板块资金分时落库与辨识度画像属第二期。

⚠️ **触发阈值为初始参数、未经实证**（本仓纪律：新信号先积累样本，参照 P1-2
「占比口径只 dry-run 计量」先例）——告警文案自带声明；样本积累后按
``strategy_verify`` 同款纪律回测校准（第三期），校准前参数改动需留痕。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.bjtime import beijing_now, beijing_now_naive
from app.core.config import settings
from app.core.db import get_session_factory
from app.market import trade_calendar as tc
from app.models.alert import AlertRule
from app.models.event import EventCard, EventDirection
from app.models.theme_catalog import Theme, ThemeMember
from app.notifiers import get_notifier_registry
from app.picks import distinctiveness

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 初始参数（未经实证）

MIN_MEMBERS = 8          # 成分少于该数的题材不判（小题材噪音大）
REL_TRIGGER = 1.2        # 相对全市场超额（百分点）：成分中位 − 全市场中位 ≥ 1.2pp
GE3_RATIO = 0.15         # 成分中涨超 3% 的占比 ≥ 15%
AMOUNT_GROWTH = 1.3      # 题材成交额合计 / 当日开盘基准 ≥ 1.3（放量）
REARM_REL_DELTA = 0.5    # 再警条件：rel 较上次提醒新高 ≥ 0.5pp
REARM_MIN_MINUTES = 30   # 再警最小间隔（分钟）
MAX_ALERTS_PER_THEME = 2 # 每题材每日提醒上限
WARMUP_MINUTES = 5       # 09:30 开盘后前 N 分钟只记基准不判（竞价噪音）
COOL_TAIL_MINUTES = 5    # 14:55 后不再触发（尾盘偷袭告警价值低，落库照常）

# ---------------------------------------------------------------- 数据目录（口径隔离）

#: 与 board_flow 的 data/boardflow/（东财口径）**刻意分目录**——本序列是
#: 「官方成分 × 快照等权自算」口径，混放会让两套口径难分彼此。
#: ⚠️ **绝对锚定，不用裸相对 `Path("data/theme_momentum")`**（2026-09-14，KB 读写分叉同族）：
#: 相对路径按**进程 CWD** 解析；标准启动（CWD=`backend/`）下两者落点相同，
#: 但只要有一个以**仓库根**为 CWD 的进程触碰同一目录（脚本 / pytest / 容器 /
#: systemd 的 WorkingDirectory），就会读出另一份"看起来存在但内容是旧的"目录——
#: 本仓已登记的教训：**运行时文件「陈旧」比「缺失」更危险**。
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "theme_momentum"


def momentum_dir() -> Path:
    return DATA_DIR


# ---------------------------------------------------------------- 题材倒排索引（每日一建）


def build_theme_index(session_factory=None) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """symbol → [(theme_code, theme_name)] 与 code → name。

    只取官方成分（theme_member 全表）；主题材名从 theme 表补齐。
    同步 DB 查询，调用方在 to_thread 里跑（见 `_snapshot_map` 使用处）。
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        names = {code: name for code, name in db.execute(select(Theme.code, Theme.name)).all()}
        index: dict[str, list[tuple[str, str]]] = {}
        for code, symbol in db.execute(select(ThemeMember.theme_code, ThemeMember.symbol)).all():
            index.setdefault(symbol, []).append((code, names.get(code, code)))
    return index, names


class ThemeIndexCache:
    """题材倒排索引的当日缓存（跨日失效；sync_members 后由调用方 invalidate）。"""

    def __init__(self) -> None:
        self._date: str = ""
        self._index: dict[str, list[tuple[str, str]]] = {}
        self._names: dict[str, str] = {}

    def get(self, session_factory=None) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
        today = beijing_now().date().isoformat()
        if self._date != today or not self._index:
            self._index, self._names = build_theme_index(session_factory)
            self._date = today
        return self._index, self._names

    def invalidate(self) -> None:
        self._date = ""


# ---------------------------------------------------------------- 聚合（纯函数，可测）


def compute_theme_momentum(
    snapshot_rows: list[dict],
    theme_index: dict[str, list[tuple[str, str]]],
) -> tuple[dict[str, dict], float]:
    """一拍聚合：题材 → {med, rel, ge3, ge5, n, amt}；返回 (逐题材, 全市场中位)。

    change_pct 缺失/非数的成员按缺失剔除（三态纪律：缺 ≠ 0）；题材内有效成员
    < MIN_MEMBERS 的不产出（避免小题材噪音）。amt = 成分 amount 合计（当日累计
    口径，环比增量由调用方对基准求比）。
    """
    cps: list[float] = []
    by_symbol: dict[str, tuple[float, float]] = {}
    for r in snapshot_rows or []:
        cp = r.get("change_pct")
        amt = r.get("amount")
        sym = r.get("symbol")
        if not sym:
            continue
        cp_f = float(cp) if isinstance(cp, (int, float)) else None
        amt_f = float(amt) if isinstance(amt, (int, float)) and amt >= 0 else 0.0
        if cp_f is not None:
            cps.append(cp_f)
        by_symbol[sym] = (cp_f if cp_f is not None else float("nan"), amt_f)
    if not cps:
        return {}, float("nan")
    market_med = _median(cps)

    acc: dict[str, dict] = {}
    for sym, themes in theme_index.items():
        cp_amt = by_symbol.get(sym)
        if cp_amt is None:
            continue
        cp, amt = cp_amt
        for code, _name in themes:
            st = acc.setdefault(code, {"cps": [], "amt": 0.0})
            if cp == cp:  # NaN 守卫
                st["cps"].append(cp)
            st["amt"] += amt

    out: dict[str, dict] = {}
    for code, st in acc.items():
        n = len(st["cps"])
        if n < MIN_MEMBERS:
            continue
        med = _median(st["cps"])
        out[code] = {
            "med": round(med, 3),
            "rel": round(med - market_med, 3),
            "ge3": round(sum(1 for c in st["cps"] if c >= 3.0) / n, 4),
            "ge5": round(sum(1 for c in st["cps"] if c >= 5.0) / n, 4),
            "n": n,
            "amt": round(st["amt"], 2),
        }
    return out, round(market_med, 3)


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return float("nan")
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


# ---------------------------------------------------------------- 检测器（状态 + 触发）


class BoardSurgeDetector:
    """当日检测状态：基准、逐题材滚动序列、提醒去重。跨日由 ``rollover`` 重置。"""

    def __init__(self) -> None:
        self.date: str = ""
        self.beats = 0
        self.theme_names: dict[str, str] = {}
        self.baseline: dict[str, float] = {}       # theme_code → 当日首个有效拍的 amt
        self.flow_baseline: dict[str, float] = {}  # theme_code → 首个有效拍的成员净额合计（亿）
        self.flow_last: dict[str, dict] = {}       # 最新一拍活跃题材资金面
        self.last_alert: dict[str, tuple[str, float]] = {}  # code → (HH:MM, 当时 rel)
        self.alert_count: dict[str, int] = {}
        self.last_moments: dict[str, dict] = {}
        self.last_market_med: float = float("nan")
        self.history: dict[str, deque] = {}        # code → deque[(HH:MM, rel)]（≤400 拍）
        self.triggered: list[dict] = []            # 当日已提醒事件（API 出口）

    def rollover_if_needed(self, today: str) -> bool:
        if self.date == today:
            return False
        names = self.theme_names  # 运行时配置（题材名映射）不随日重置
        self.__init__()
        self.date = today
        self.theme_names = names
        return True

    # -- 判定（纯逻辑，测试直接构造调用） ------------------------------------

    def evaluate(
        self,
        now_minutes: int,
        moments: dict[str, dict],
        market_med: float,
    ) -> list[dict]:
        """一拍判定：先记序列，再判触发。返回告警 dict 列表（交 dispatch_alert）。"""
        today = beijing_now().date().isoformat()
        self.rollover_if_needed(today)
        self.beats += 1
        self.last_moments = moments
        self.last_market_med = market_med
        hhmm = f"{now_minutes // 60:02d}:{now_minutes % 60:02d}"
        for code, m in moments.items():
            self.history.setdefault(code, deque(maxlen=400)).append((hhmm, m.get("rel")))
            base = self.baseline.get(code)
            if base is None and m.get("amt", 0) > 0:
                self.baseline[code] = m["amt"]

        # 时段门：开盘前 WARMUP 只记基准；尾盘 COOL_TAIL 后只落库不触发
        if now_minutes < 9 * 60 + 30 + WARMUP_MINUTES or now_minutes > 14 * 60 + 60 - COOL_TAIL_MINUTES:
            return []

        alerts: list[dict] = []
        for code, m in moments.items():
            if m.get("n", 0) < MIN_MEMBERS:
                continue
            base = self.baseline.get(code) or 0.0
            amt_ratio = (m["amt"] / base) if base > 0 else None
            rel = m.get("rel")
            ge3 = m.get("ge3")
            if rel is None or ge3 is None:
                continue  # 三态：缺数据不判，也不证伪
            fired = (
                rel >= REL_TRIGGER
                and ge3 >= GE3_RATIO
                and (amt_ratio is None or amt_ratio >= AMOUNT_GROWTH)
            )
            if not fired or not self._rearm_ok(code, now_minutes, rel):
                continue
            self.alert_count[code] = self.alert_count.get(code, 0) + 1
            self.last_alert[code] = (hhmm, rel)
            alerts.append({
                "key": f"board_surge:{code}:{today}",
                "kind": "board_surge",
                "direction": self._theme_display(code),
                "text": self._compose_text(code, m, market_med, amt_ratio, hhmm),
                "meta": {"trigger_value": rel, "threshold": REL_TRIGGER},
            })
        self.triggered.extend(alerts)
        return alerts

    def _rearm_ok(self, code: str, now_minutes: int, rel: float) -> bool:
        if self.alert_count.get(code, 0) >= MAX_ALERTS_PER_THEME:
            return False
        last = self.last_alert.get(code)
        if last is None:
            return True
        last_hhmm, last_rel = last
        last_min = int(last_hhmm[:2]) * 60 + int(last_hhmm[3:])
        return now_minutes - last_min >= REARM_MIN_MINUTES and rel >= last_rel + REARM_REL_DELTA

    # -- 展示与文本 ----------------------------------------------------------

    def _theme_display(self, code: str) -> str:
        return (getattr(self, "theme_names", {}) or {}).get(code, code)

    def _compose_text(
        self, code: str, m: dict, market_med: float, amt_ratio: float | None, hhmm: str
    ) -> str:
        """告警正文（多行；首行进日志）。归因段由调用方以 ``attach_attribution`` 追加。"""
        name = self._theme_display(code)
        head = f"【板块异动】{name} {hhmm}"
        lines = [
            head,
            f"相对全市场 {m['rel']:+.1f}pp（成分中位 {m['med']:+.1f}% vs 全市场 {market_med:+.1f}%），"
            f"≥3% 家数占比 {m['ge3'] * 100:.0f}%（{m['n']} 只成分）",
        ]
        if amt_ratio is not None:
            lines.append(f"成交额较当日基准 ×{amt_ratio:.2f}")
        lines.append("⚠️ 触发阈值为初始参数（未经实证）；本条为盘面观察提示，非买卖建议")
        return "\n".join(lines)

    def attach_attribution(self, alert: dict, attribution: dict) -> None:
        """把归因两路结果追加进告警 text（保持 basis：每条带来源与时间）。"""
        extra: list[str] = []
        news = attribution.get("news") or []
        if news:
            n0 = news[0]
            extra.append(f"可能诱因（消息面）：{n0['time']} 「{n0['title']}」（{n0['source']}）")
            if len(news) > 1:
                extra.append(f"相关消息：{news[1]['time']} 「{news[1]['title']}」")
        seals = attribution.get("seals") or []
        if seals:
            extra.append("封板时序：" + " → ".join(seals))
        if not extra:
            extra.append("归因：时间窗内无匹配事件方向行、无封板成员")
        alert["text"] = (alert.get("text") or "") + "\n" + "\n".join(extra)


# ---------------------------------------------------------------- 归因（第一期两路）


def match_news_events(
    theme_name: str,
    since: object,
    session_factory=None,
    limit: int = 2,
) -> list[dict]:
    """消息面归因：``since``（北京时间 naive datetime）之后、direction≠0 的题材
    方向行，target 与题材名互含即命中。

    打分 = 时间近因（越新越高）× 来源层级（source_tier 1 最好）；只取 Top ``limit``。
    纯查询函数（可测）：不触碰行情、不写库。
    """
    sf = session_factory or get_session_factory()
    name = (theme_name or "").strip()
    if not name:
        return []
    with sf() as db:
        rows = db.execute(
            select(
                EventCard.published_at, EventCard.title, EventCard.source,
                EventCard.source_tier, EventDirection.target,
            )
            .join(EventDirection, EventDirection.event_id == EventCard.id)
            .where(
                EventCard.published_at >= since,
                EventCard.status == "active",
                EventCard.revision_pending_at.is_(None),
                EventDirection.direction != 0,
                EventDirection.target_type == "theme",
            )
            .order_by(EventCard.published_at.desc())
            .limit(300)
        ).all()
    hits: list[tuple[float, dict]] = []
    for published_at, title, source, tier, target in rows:
        tgt = (target or "").strip()
        if len(tgt) < 2 or len(name) < 2:
            continue
        if tgt not in name and name not in tgt:
            continue
        recency = 1.0
        tier_f = float(tier) if isinstance(tier, (int, float)) else 3.0
        score = recency * (6.0 - min(tier_f, 5.0))
        hits.append((score, {
            "time": str(published_at)[5:16],
            "title": (title or "")[:60],
            "source": source or "",
            "tier": tier,
            "target": tgt,
        }))
    hits.sort(key=lambda x: -x[0])
    return [h for _, h in hits[:limit]]


def _pool_value(row, key: str):
    """涨停池兼容 provider 的 Pydantic 记录与历史 dict 夹具。"""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def seal_sequence(
    pool: list,
    member_symbols: set[str],
    limit: int = 3,
) -> list[str]:
    """龙头结构归因：题材内涨停成员按 first_seal_time 升序（封板时序即带动次序）。"""
    hit = [p for p in pool or [] if _pool_value(p, "symbol") in member_symbols]
    hit.sort(key=lambda p: (_pool_value(p, "first_seal_time") or "99:99"))
    out = []
    for p in hit[:limit]:
        boards = _pool_value(p, "consecutive_boards") or _pool_value(p, "lbc") or 1
        out.append(
            f"{_pool_value(p, 'name')} {_pool_value(p, 'first_seal_time') or '?'}（{boards}板）"
        )
    return out


# ---------------------------------------------------------------- 落库（E1）


def _append_jsonl(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")


async def persist_beat(date_str: str, hhmm: str, market_med: float, moments: dict[str, dict],
                       flows: dict[str, dict] | None = None) -> None:
    """每拍落一行（全题材 + 活跃题材资金面）；文件按日切。IO 丢线程池（KB-ENG-67 同批先例）。"""
    line = {
        "ts": hhmm,
        "market_med": market_med,
        "themes": {c: {k: v for k, v in m.items()} for c, m in moments.items()},
    }
    if flows:
        line["flows"] = flows
    path = DATA_DIR / f"{date_str}.jsonl"
    await asyncio.to_thread(_append_jsonl, path, line)


# ---------------------------------------------------------------- 调度


def _state(app):
    return app.state if hasattr(app, "state") else app


def get_detector(app) -> BoardSurgeDetector:
    state = _state(app)
    det = getattr(state, "board_surge_detector", None)
    if det is None:
        det = BoardSurgeDetector()
        state.board_surge_detector = det
    return det


def get_index_cache(app) -> ThemeIndexCache:
    state = _state(app)
    cache = getattr(state, "board_surge_index_cache", None)
    if cache is None:
        cache = ThemeIndexCache()
        state.board_surge_index_cache = cache
    return cache


async def _beat(app, detector: BoardSurgeDetector, index_cache: ThemeIndexCache) -> list[dict]:
    """一拍：快照 → 聚合 → 判定 → 资金面 → 落库（含 flows） → 归因 → 告警 dict。

    资金面（二期 B 路）：活跃题材（当日已提醒 ∪ rel Top5）的成员主力净额合计
    （东财 f62 当日累计口径，Top60 成分封顶）。随 E1 行一并落库，供盘后回放。
    """
    state = _state(app)
    svc = getattr(state, "snapshot_service", None)
    rows = getattr(svc, "snapshot", None) or []
    if not rows:
        return []
    now = beijing_now()
    now_minutes = now.hour * 60 + now.minute
    hhmm = f"{now_minutes // 60:02d}:{now_minutes % 60:02d}"
    index, _names = await asyncio.to_thread(index_cache.get)
    moments, market_med = await asyncio.to_thread(compute_theme_momentum, rows, index)
    if not moments:
        return []
    detector.theme_names = _names
    alerts = detector.evaluate(now_minutes, moments, market_med)

    # 活跃题材集合：当日已提醒 ∪ 最新一拍 rel Top5
    member_sets = _theme_member_sets(index)
    top_rel = sorted(
        (c for c, m in moments.items() if m.get("n", 0) >= MIN_MEMBERS),
        key=lambda c: -(moments[c].get("rel") or -99),
    )[:5]
    active = sorted({a["key"].split(":")[1] for a in alerts} | set(top_rel))[:8]
    flows = await _collect_flows(state, detector, member_sets, active)

    # 落库（E1 行 + 二期 flows 字段）：盘后回放的资金面时间线
    await persist_beat(now.date().isoformat(), hhmm, market_med, moments, flows)

    if not alerts:
        return []

    # 交易日与涨停池（归因用；失败 → 归因缺失段如实标注，不阻断告警）
    td = None
    hub = getattr(state, "hub", None)
    provider = getattr(hub, "provider", None)
    try:
        days = await tc.trading_days(provider) if provider else None
        td = tc.last_trade_date(days, asof=now.date()) if days else None
    except Exception:  # noqa: BLE001
        td = None
    pool: list[dict] = []
    if td == now.date() and provider is not None:
        try:
            pool = await provider.get_limit_up_pool(td) or []
        except Exception:  # noqa: BLE001
            pool = []
    since = beijing_now_naive() - timedelta(hours=6)
    sf = get_session_factory()
    for a in alerts:
        code = a["key"].split(":")[1]
        try:
            news = await asyncio.to_thread(
                match_news_events, a.get("direction") or "", since, sf
            )
        except Exception:  # noqa: BLE001
            news = []
        seals = seal_sequence(pool, member_sets.get(code, set())) if pool else []
        detector.attach_attribution(a, {"news": news, "seals": seals})
        # 资金面行（B 路）：净额合计口径显式标注；取不到 → 不输出该行（三态）
        fl = flows.get(code)
        if fl and fl.get("net_sum") is not None:
            a["text"] += (
                f"\n资金面：成员主力净额合计 {fl['net_sum']:+.2f} 亿"
                f"（Top{fl['n']} 成分，东财 f62 当日累计，未覆盖 {fl['skipped']} 只）"
            )
            a["meta"]["flow_net_sum"] = fl["net_sum"]
        # 辨识度候选（C 路）：历史画像 Top5（分数随行暴露，初始权重未经实证）
        try:
            cand = await asyncio.to_thread(
                distinctiveness.score_candidates, sorted(member_sets.get(code, set()))
            )
            line = distinctiveness.format_candidates(cand, _names)
            if line:
                a["text"] += f"\n{line}"
                a["meta"]["candidates"] = [
                    {"symbol": i["symbol"], "score": i["score"],
                     "name": _names.get(i["symbol"], i["symbol"])}
                    for i in cand.get("items", [])[:5] if i.get("score") is not None
                ]
        except Exception:  # noqa: BLE001
            log.warning("board surge 候选画像失败（不阻断告警）：%s", code, exc_info=True)
    return alerts


async def _collect_flows(
    state, detector: BoardSurgeDetector, member_sets: dict[str, set[str]], active: list[str]
) -> dict[str, dict]:
    """活跃题材成员净额聚合（东财 f62 当日累计；基准 = 当日首个有效拍）。

    失败/空 → 该题材 flows 缺席（三态：不臆造 0）；北交所成员显式计入 skipped。
    """
    from app.market import stock_flow

    out: dict[str, dict] = {}
    for code in active:
        members = sorted(member_sets.get(code, set()))
        capable = [s for s in members if stock_flow.stock_secid(s)]
        if not capable:
            continue
        try:
            payload = await stock_flow.get_stock_flow(capable[: stock_flow.MAX_SYMBOLS])
        except Exception:  # noqa: BLE001
            continue
        items = payload.get("items") or {}
        nets = [v.get("main") for v in items.values() if isinstance(v, dict)]
        nets = [x for x in nets if isinstance(x, (int, float))]
        if not nets:
            continue
        net_sum = round(sum(nets), 2)
        base = detector.flow_baseline.get(code)
        if base is None and net_sum != 0.0:
            detector.flow_baseline[code] = net_sum
        out[code] = {
            "net_sum": net_sum,
            "n": len(nets),
            "skipped": len(members) - len(capable),
            "baseline": detector.flow_baseline.get(code),
        }
    detector.flow_last = out
    return out


def _theme_member_sets(index: dict[str, list[tuple[str, str]]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for sym, themes in index.items():
        for code, _n in themes:
            out.setdefault(code, set()).add(sym)
    return out


def ensure_board_surge_rule(session_factory=None) -> AlertRule:
    """板块异动专用系统规则（get-or-create；channels 跟随 settings 默认）。"""
    import json as _json

    sf = session_factory or get_session_factory()
    default_channels = _json.dumps(
        [c.strip() for c in settings.board_surge_channels.split(",") if c.strip()]
    )
    with sf() as db:
        row = db.query(AlertRule).filter(AlertRule.name == "__board_surge__").one_or_none()
        if row is None:
            row = AlertRule(
                name="__board_surge__",
                enabled=1,
                condition_type="board_surge",
                scope="all",
                threshold=REL_TRIGGER,
                channels=default_channels,
            )
            db.add(row)
        elif row.channels != default_channels:
            row.channels = default_channels
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


async def board_surge_loop(app, stop: asyncio.Event) -> None:
    """盘中调度（lifespan 任务）：与 watcher 同节奏（60s），独立数据口径。"""
    interval = max(15.0, settings.board_surge_interval_seconds)
    from app.repositories.alert_repo import AlertRepository

    while not stop.is_set():
        try:
            now = beijing_now()
            days = None
            state = _state(app)
            hub = getattr(state, "hub", None)
            provider = getattr(hub, "provider", None)
            if provider is not None:
                try:
                    days = await tc.trading_days(provider)
                except Exception:  # noqa: BLE001
                    days = None
            td = tc.last_trade_date(days, asof=now.date()) if days else None
            if td == now.date() and tc.in_trading_window(now):
                detector = get_detector(app)
                alerts = await _beat(app, detector, get_index_cache(app))
                if alerts:
                    repo = AlertRepository(get_session_factory())
                    rule = ensure_board_surge_rule()
                    for a in alerts:
                        meta = a.get("meta") or {}
                        event = repo.record_trigger(
                            rule.id, "000000", float(meta.get("trigger_value") or 0.0),
                            float(meta.get("threshold") or 0.0),
                            snapshot={
                                "kind": a.get("kind"), "direction": a.get("direction"),
                                "text": a.get("text"), "name": a.get("direction") or None,
                            },
                        )
                        channels = await get_notifier_registry().dispatch(event, rule)
                        repo.update_event_channels(event.id, channels)
                        log.warning("[BOARD-SURGE] %s", (a.get("text") or "").splitlines()[0])
        except Exception:
            log.exception("board surge beat failed")
        import contextlib

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


# ---------------------------------------------------------------- API 出口


def todays_state(app) -> dict:
    """GET /api/picks/board-surge：当日序列概览 + 最新一拍 Top 板块 + 已提醒事件。"""
    detector = get_detector(app)
    now = beijing_now()
    top = sorted(
        ((c, m) for c, m in detector.last_moments.items() if m.get("n", 0) >= MIN_MEMBERS),
        key=lambda x: -(x[1].get("rel") or -99),
    )[:10]
    return {
        "date": detector.date or now.date().isoformat(),
        "beats": detector.beats,
        "market_med": detector.last_market_med,
        "params": {
            "rel_trigger": REL_TRIGGER, "ge3_ratio": GE3_RATIO,
            "amount_growth": AMOUNT_GROWTH, "min_members": MIN_MEMBERS,
            "calibrated": False,
        },
        "flows": {
            c: {"net_sum": f.get("net_sum"), "n": f.get("n"),
                "baseline": f.get("baseline")}
            for c, f in detector.flow_last.items()
        },
        "top_themes": [
            {
                "code": c,
                "name": detector._theme_display(c),
                **{k: m.get(k) for k in ("med", "rel", "ge3", "ge5", "n")},
            }
            for c, m in top
        ],
        "alerts": list(reversed(detector.triggered[-20:])),
    }
