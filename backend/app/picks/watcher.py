"""盘中方向跟踪（选股 2.0 §5–6，docs/summary/stock-strategy.md，批次 B）。

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
- 板块涨幅：board_flow.get_board_list 概念+行业（板块数据唯一入口，自带
  盘中 30s 缓存）；题材名 → 板块名精确匹配，失败取"包含关系且板块名最短"，
  仍无 = unknown
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
from bisect import bisect_right
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.db import get_session_factory
from app.market import board_flow
from app.market import trade_calendar as tc
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
from app.services.theme_service import (
    match_board_name_shortest,
    normalize_theme,
    parse_theme_tags,
)
from app.core.bjtime import beijing_now, to_beijing  # S2-8 时区收敛

log = logging.getLogger(__name__)

#: watcher 专用系统规则名（record_trigger 的外键要求 rule 存在；get-or-create）
WATCHER_RULE_NAME = "__picks_watcher__"
MAX_ALERTS_PER_DIRECTION = 3   # 每方向每日确认提醒上限（防龙头轮动刷屏）
VR_MEMBERS_CAP = 5             # 量比计算的题材成员上限（按板数取最强 5 只）
#: 昨日量并发取数上限。**对齐 `api/routes/market.py` 与 relay-rank 已验证的 8**
#: （那两处注释同写「N 大时可能触发 WAF」）——不是随手取的数，是项目实测安全值。
VR_FETCH_CONCURRENCY = 8


# ---------------------------------------------------------------- 纯函数：板块匹配


def match_board_pct(tag: str, board_pct: dict[str, float]) -> float | None:
    """题材标签 → 东财板块涨幅。

    匹配口径（精确优先 → 双向包含取**板块名最短**）的**唯一实现**在
    `theme_service.match_board_name_shortest`——本函数只负责把命中的板块名换成涨幅
    （R-1，2026-09-12 评审批次 1：此前 `watcher` 与 `backtest` 各写一份同口径实现）。
    匹配不到 → None（unknown）。绝不拿不相干的板块冒充。
    """
    name = match_board_name_shortest(tag, board_pct.keys())
    return board_pct[name] if name is not None else None


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


#: 大单异动阈值回退值：题材成员当日主力净流入首破 1.0 亿提醒。
#: **2026-09-10 源头收紧（P1-16）：0.3 → 1.0 亿**。依据（实测 09-08~09-10 库内
#: 151 条 flow_surge）：①触发值中位数仅 0.92 亿，0.3 亿档贡献了 51.7% 的事件；
#: ②判读层 105 条判读中 102 条 ignore（97.1%）；③噪音挤占判读预算，实测 75 条
#: 事件**从未被判读**（含 falsify 18 / high_board_break 3）。1.0 亿保留 48% 事件量、
#: 砍掉一半低幅事件。仍为经验初值，实参走 settings.picks_flow_surge_yi，回测校准后定稿。
FLOW_SURGE_YI = 1.0
#: 每拍最多产出条数（按净额取 Top，防单拍批量刷屏——与板块规则 BOARD_ALERT_PER_BEAT 同纪律）。
FLOW_ALERT_PER_BEAT = 5

# ---- 板块级资金规则（P1-1，2026-09-10）----
#: ①单拍净额增量突增：板块当日累计主力净额（东财 f62）相对上一拍的增量阈值。
#: watcher 一拍 ≈ 60s，故该阈值即"每分钟净流入"。首拍无基线 → 跳过（不臆造）。
BOARD_FLOW_SURGE_YI = 0.5
#: ②低吸异动：当日累计净流入下限 + 涨幅上限（资金在进、价格没动）。
BOARD_LOW_ABSORB_YI = 2.0
BOARD_LOW_ABSORB_PCT = 2.0
#: 每拍每规则最多产出的提醒条数（按幅度取 Top，防板块轮动刷屏）。
BOARD_ALERT_PER_BEAT = 3

#: ③**占比口径 —— 现阶段只做 dry-run 计量，不参与触发**（P1-2 残余，2026-09-12 起）。
#: **为什么需要这个口径**（实测 2026-09-12，全量 1000 个板块）：绝对额阈值对大板块偏松、
#: 对小板块偏紧——净额同样落在 [0.4, 0.6] 亿的板块里，占比跨度
#: **0.61%（核污染防治）→ 5.09%（卫浴制品）**，相差 **8.3 倍**；净额榜首「通信设备」
#: 47.41 亿只占 2.67%，而净额更小的「被动元件」29.09 亿却占 8.74%
#: ⇒ 一刀切的绝对额会把**占比更猛**的板块排在后面。
#:
#: **为什么不直接上线、先计量**（2026-09-12 裁定）：分位口径"取当日横截面前 5%"
#: 在数学上**必然**产出 ≈ 0.05×N 个候选；全量 N≈1000 ⇒ **≈50 个/拍**，
#: 而每拍上限只有 `BOARD_ALERT_PER_BEAT=3` ⇒ **几乎每拍满额**，
#: 按 3/拍 × 约 240 拍 ≈ 上限 **720/天**，对比当日实测 **156/天**
#: （`data/picks/briefs/20260911.json`）是 **4.6 倍**，会直接冲掉 P1-16 修好的
#: 「噪音挤占判读预算」。而"这条线该落在哪"取决于每拍 `delta_ratio` 的真实分布，
#: **该分布没有任何历史序列**：
#: · `daykline.json`：bar = `[date, main_net_yi, change_pct]`，**无成交额**
#:   ⇒ ratio 反推不出（仅 Top20/kind）；
#: · `daily.json`：**确有**日频 ratio（`[code, name, net, ratio, pct]`，Top50/kind），
#:   但 2026-09-12 实测只有 **5 个交易日**（09-07~09-11），且日频 ≠ 拍频
#:   （时间尺度差三个数量级，拿它定"每分钟"的线属量纲错配）。
#: 故先按拍累计直方图（`_probe_board_ratio`），跑满一个交易日再据实定线。
BOARD_RATIO_PCTL = 95.0
#: 横截面「够宽」的判定阈值（带回 ratio 的板块数）。低于此数的拍标为**窄拍**
#: （正常全量约 1000 个板块，窄拍多为上游取数降级），其样本**照常保留**但另计
#: `samples_narrow` 供读的人排除——不丢弃有效观测，也不假装它没问题。
BOARD_RATIO_MIN_SAMPLES = 50
#: 计量直方图分箱上界（百分点/拍）。落箱规则 = `bisect_right(BINS, v)`，即
#: 箱 i 表示 `BINS[i-1] <= v < BINS[i]`（箱 0 为 `v < BINS[0]`，末箱为 `v >= BINS[-1]`）。
#: 因此事后可直接对尾部求和，反推「任取一条线 t 会放出多少候选」。
BOARD_RATIO_BINS = (0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)


def flow_surge_yi() -> float:
    """大单异动阈值（亿）。配置非正/非法 → 回退 `FLOW_SURGE_YI` 并记日志（不静默改口径）。"""
    from app.core.config import settings

    raw = getattr(settings, "picks_flow_surge_yi", FLOW_SURGE_YI)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        log.warning("picks_flow_surge_yi 非法（%r），回退默认 %.2f 亿", raw, FLOW_SURGE_YI)
        return FLOW_SURGE_YI
    if val <= 0:
        log.warning("picks_flow_surge_yi 非正（%r），回退默认 %.2f 亿", raw, FLOW_SURGE_YI)
        return FLOW_SURGE_YI
    return val


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
        # 题材成员主力净额跟踪（P1 方向2）：symbol -> {"peak": 累计净额峰值, "alerted": bool}
        self.flow_state: dict[str, dict] = {}
        # 板块级资金跟踪（P1-1）：board 名 -> {"last": 上一拍累计净额, "last_ratio":
        # 上一拍累计占比, "surge"/"absorb": bool}
        # `last_ratio` 仅供占比口径的 dry-run 计量用（`_probe_board_ratio`），不参与触发。
        self.board_flow_state: dict[str, dict] = {}
        # 占比口径 dry-run 计量（2026-09-12，见 BOARD_RATIO_* 注释）。**不产任何告警**，
        # 只累计每拍 delta_ratio 的分布；跑满一个交易日后再据实决定阈值线。
        # ⚠️ **内存态、当日有效、重启即清零**：要拿满一天的分布，当日盘中**不得重启 8000**
        # （与"交易日 12:00 前禁重启"的既有约束同向）。读数出口 =
        # `GET /api/picks/watcher/state` → `board_ratio_probe`（复用既有端点，未新增）。
        self.board_ratio_probe: dict = {
            "beats": 0,               # 计入的**宽**拍数（样本 ≥ BOARD_RATIO_MIN_SAMPLES）
            "beats_insufficient": 0,  # 样本不足被标为窄拍的拍数
            "samples": 0,             # 板块·拍 样本总数（含窄拍）
            "samples_narrow": 0,      # 其中来自窄拍的样本数（供对账：samples = wide + narrow）
            # **两份直方图**（2026-09-12 评审 F-8 修复）。旧实现只有一份 `hist`
            # （含全部样本）+ 一个 `samples_narrow` 计数：「要排除窄拍」只用**一个
            # 总数**表达、其分布没被保留 ⇒ 事后**无法**从 `hist` 里扣掉窄拍那部分，
            # 「用干净样本定线」在数据上根本做不到，只能重跑一天。
            # 现在：`hist_all` 供审计（含窄拍），`hist_wide` 是定线的**唯一依据**。
            "hist_all": [0] * (len(BOARD_RATIO_BINS) + 1),
            "hist_wide": [0] * (len(BOARD_RATIO_BINS) + 1),
            "total": 0.0,             # 全部样本 delta_ratio 累计和
            "total_wide": 0.0,        # 宽拍样本 delta_ratio 累计和
        }

    def step(self, beat: dict) -> list[dict]:
        self.beat_count += 1
        env = beat.get("env") or {}
        themes = beat.get("themes") or {}
        alerts: list[dict] = []
        for tr in self.trackers:
            alerts.extend(tr.step(themes.get(tr.direction), env, beat.get("now_minutes")))
        alerts.extend(self._step_flows(beat.get("flows") or {}))
        alerts.extend(self._step_board_flows(beat.get("board_flows") or {}))
        return alerts

    def _step_flows(self, flows: dict) -> list[dict]:
        """题材成员主力净额跟踪（P1 方向2）：当日累计首破阈值 → 大单异动提醒。

        f62 是**当日累计**口径，无需差分；「首破」语义让持续流入只报第一拍，
        同票每日至多一次（append_alert 按 key 去重兜底）。只报流入侧——选股
        场景的标的在涨停池里，流出异动由炸板池/跌停池覆盖，不重复建设。

        **源头收紧（P1-16，2026-09-10）**：①阈值由 settings `picks_flow_surge_yi`
        给（默认 1.0 亿，原 0.3 亿）；②每拍按净额取 Top `FLOW_ALERT_PER_BEAT`，
        未入选的**不置 alerted**，下一拍仍有候选机会（不是丢弃，只是排队）。
        阈值为经验初值（未经回测校准，同 halt_risk 模式，校准前仅作提示）。
        """
        threshold = flow_surge_yi()
        cands: list[tuple[float, str, dict]] = []
        for sym, it in flows.items():
            if not isinstance(it, dict) or not it.get("available"):
                continue
            main = it.get("main")
            if main is None:
                continue
            st = self.flow_state.setdefault(sym, {"peak": None, "alerted": False})
            st["peak"] = main if st["peak"] is None else max(st["peak"], main)
            if st["alerted"] or main < threshold:
                continue
            cands.append((main, sym, it))

        out: list[dict] = []
        for main, sym, it in sorted(cands, key=lambda x: x[0], reverse=True)[:FLOW_ALERT_PER_BEAT]:
            self.flow_state.setdefault(sym, {})["alerted"] = True
            name = it.get("name") or ""
            out.append({
                "key": f"flow-surge-{sym}",
                "kind": "flow_surge",
                "direction": "",
                "symbol": sym,
                "name": name,
                "text": (
                    f"💰 大单异动 {name}({sym})：主力净流入 {main:.2f} 亿"
                    f"（当日累计首破 {threshold} 亿，题材成员跟踪）"
                ),
                "meta": {"trigger_value": round(main, 3), "threshold": threshold},
            })
        return out

    def _step_board_flows(self, boards: dict) -> list[dict]:
        """板块级资金规则（P1-1）：①单拍净额增量突增 ②低吸异动（资金进、价没动）。

        与个股 `_step_flows` 的关键差别：f62 是**当日累计**口径——个股用「首破阈值」
        规避差分，而板块要的是"正在加速"，**必须与上一拍做差**。watcher 一拍 ≈60s，
        故增量即"每分钟净流入"。首拍无基线 → 只记基线不判定（不臆造）。

        **触发逻辑与绝对额口径保持原样、逐字未动**——占比口径当前**只计量、不触发**
        （2026-09-12 裁定，理由见 `BOARD_RATIO_*` 常量注释）：分位口径取前 5% 会必然
        产出 ≈50 候选/拍而撞满 3/拍上限，告警量约 4.6 倍，但"线该画在哪"缺少拍频分布
        依据 ⇒ 先由 `_probe_board_ratio` 累计真实分布，跑满一个交易日再据实定线，
        避免拿未标定的口径直接上线（同 P1-16「噪音挤占判读预算」的教训）。

        两类各当日一次（key 含规则名+板块名，`append_alert` 兜底）；每拍按幅度取
        Top `BOARD_ALERT_PER_BEAT`，防板块轮动刷屏。阈值为经验初值、未回测校准。
        """
        out: list[dict] = []
        surge: list[tuple[float, str, dict]] = []
        absorb: list[tuple[float, str, dict]] = []
        carry = 0   # 本拍带回可用 ratio 的板块数（横截面宽度，用于判定该拍是否计入）
        for name, it in boards.items():
            if not name or not isinstance(it, dict):
                continue
            net = it.get("net")
            if net is None:
                continue
            st = self.board_flow_state.setdefault(
                name, {"last": None, "last_ratio": None, "surge": False, "absorb": False}
            )
            prev, st["last"] = st["last"], net
            pct = it.get("pct")
            # 占比仅供 dry-run 计量：`last_ratio` 的读写不参与任何触发判定
            prev_ratio, st["last_ratio"] = st["last_ratio"], it.get("ratio")
            if st["last_ratio"] is not None:
                carry += 1                            # 本拍带回可用 ratio（横截面宽度）
            if prev_ratio is not None and st["last_ratio"] is not None:
                self._probe_board_ratio(st["last_ratio"] - prev_ratio)
            if prev is not None:
                delta = net - prev
                if not st["surge"] and delta >= BOARD_FLOW_SURGE_YI:
                    surge.append((delta, name, {"net": net, "delta": delta}))
            if (not st["absorb"] and net >= BOARD_LOW_ABSORB_YI
                    and pct is not None and pct < BOARD_LOW_ABSORB_PCT):
                absorb.append((net, name, {"net": net, "pct": pct}))

        self._probe_close_beat(carry)

        for delta, name, d in sorted(surge, key=lambda x: x[0], reverse=True)[:BOARD_ALERT_PER_BEAT]:
            self.board_flow_state.setdefault(name, {})["surge"] = True
            out.append({
                "key": f"board-flow-surge-{name}",
                "kind": "board_flow_surge",
                "direction": name,
                "text": (
                    f"🌊 板块资金突增 {name}：本拍主力净流入 +{d['delta']:.2f} 亿"
                    f"（当日累计 {d['net']:.2f} 亿；阈值 {BOARD_FLOW_SURGE_YI} 亿/拍）"
                ),
                "meta": {"trigger_value": round(d["delta"], 3),
                         "threshold": BOARD_FLOW_SURGE_YI,
                         "cum_net_yi": round(d["net"], 3)},
            })
        for net, name, d in sorted(absorb, key=lambda x: x[0], reverse=True)[:BOARD_ALERT_PER_BEAT]:
            self.board_flow_state.setdefault(name, {})["absorb"] = True
            out.append({
                "key": f"board-low-absorb-{name}",
                "kind": "board_low_absorb",
                "direction": name,
                "text": (
                    f"🧲 低吸异动 {name}：主力净流入 {d['net']:.2f} 亿但仅涨 {d['pct']:.2f}%"
                    f"（流入 ≥{BOARD_LOW_ABSORB_YI} 亿 且 涨幅 <{BOARD_LOW_ABSORB_PCT}%）"
                ),
                "meta": {"trigger_value": round(d["net"], 3),
                         "threshold": BOARD_LOW_ABSORB_YI, "change_pct": d["pct"]},
            })
        return out

    def _probe_board_ratio(self, delta_ratio: float) -> None:
        """占比口径 dry-run 计量：把本拍 delta_ratio 记进**本拍缓冲**（不产告警、不影响触发）。

        **不按横截面宽度过滤样本**：单个板块的 `delta_ratio` 是独立于"本拍共有几个板块"
        的有效观测，丢弃它属于无谓的信息损失。宽度只影响**怎么解读**，故由
        `beats` / `beats_insufficient` / `samples_narrow` 另行标注，让读的人自行取舍。

        ⚠️ 这里**只缓冲、不落箱**：本拍是宽拍还是窄拍，要等整拍扫完才知道（横截面宽度
        `carry` 由 `_step_board_flows` 的循环累加），而本函数正是在循环内被调用。
        落箱动作因此移到 `_probe_close_beat` —— 这是 F-8 的修法：旧实现在循环内直接写
        唯一那份 `hist`，等扫完发现是窄拍时样本**已经混进去了**，只能补一个计数、
        再也拆不开。
        """
        self.board_ratio_probe.setdefault("_beat_deltas", []).append(delta_ratio)

    def _probe_close_beat(self, carry: int) -> None:
        """收一拍：按**横截面宽度**落箱，并标注该拍是宽拍还是窄拍。

        `carry < BOARD_RATIO_MIN_SAMPLES` 的"窄拍"多为上游取数降级（正常全量约 1000 个
        板块），其样本**不丢弃**但只进 `hist_all`，**不进 `hist_wide`**——定线只看后者。
        首拍（全员无基线、delta 数为 0）只要横截面够宽就仍算有效拍——
        "还没有上一拍可比"与"板块数太少"成因不同，不可混记。
        """
        p = self.board_ratio_probe
        deltas = p.pop("_beat_deltas", [])
        for v in deltas:
            p["samples"] += 1
            p["total"] += v
            p["hist_all"][bisect_right(BOARD_RATIO_BINS, v)] += 1
        if carry < BOARD_RATIO_MIN_SAMPLES:
            p["beats_insufficient"] += 1
            p["samples_narrow"] += len(deltas)
            return
        p["beats"] += 1
        # `beat_deltas` 保留为「宽拍样本数」的独立计数（与 `samples_narrow` 不同源），
        # 便于事后对账 `samples == beat_deltas + samples_narrow`。
        p["beat_deltas"] = p.get("beat_deltas", 0) + len(deltas)
        for v in deltas:
            p["total_wide"] += v
            p["hist_wide"][bisect_right(BOARD_RATIO_BINS, v)] += 1

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
            "flow_tracked": len(self.flow_state),
            "board_flow_tracked": len(self.board_flow_state),
            "flow_alerted": sorted(s for s, v in self.flow_state.items() if v.get("alerted")),
            # 占比口径 dry-run 计量（P1-2 残余）：**不产告警**，只为"线该画在哪"取证。
            # 落在这里是因为 GET /api/picks/intraday/watcher/state 早已暴露 state()，
            # 复用既有出口、不新增端点（红线 6：默认复用而非新建）。
            "board_ratio_probe": self._probe_snapshot(),
        }

    def _probe_snapshot(self) -> dict:
        """计量快照：两份直方图 + 可对账的计数。空计量如实报 `enabled=False`（三态）。

        **定线只看 `hist_wide`**（宽拍 = 横截面够宽、上游未降级）；`hist_all` 供审计，
        两者之差即窄拍样本（= `samples_narrow`）。分箱 = `bisect_right(BOARD_RATIO_BINS, v)`；
        负值（占比下降）照记——定线时既要看尾部多厚，也要看分布是否对称
        （「占比突增」本就该只在右尾）。
        """
        p = self.board_ratio_probe
        n = p["samples"]
        wide = p.get("beat_deltas", 0)
        return {
            "enabled": p["beats"] > 0,
            "bins": list(BOARD_RATIO_BINS),
            "hist_all": list(p["hist_all"]),
            "hist_wide": list(p["hist_wide"]),
            "samples": n,
            "samples_wide": wide,
            "samples_narrow": p["samples_narrow"],
            "beats": p["beats"],
            "beats_insufficient": p["beats_insufficient"],
            "min_samples": BOARD_RATIO_MIN_SAMPLES,
            "pctl": BOARD_RATIO_PCTL,
            "mean": round(p["total"] / n, 4) if n else None,
            "mean_wide": round(p["total_wide"] / wide, 4) if wide else None,
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
    """板块涨幅（概念+行业）。走 board_flow 唯一入口（板块数据治理规则：
    任何模块不得自行请求东财板块接口）。hub 参数保留以兼容调用方与测试
    monkeypatch 签名。失败返回空表 → 全部 pct=unknown，不臆造。"""
    out: dict[str, float] = {}
    for kind in ("concept", "industry"):
        rows, errs = await board_flow.get_board_list(kind)
        if rows is None:
            log.warning("watcher beat: board list %s unavailable: %s", kind, "; ".join(errs))
            continue
        for row in rows:
            name, pct = row.get("name"), row.get("change_pct")
            if name and pct is not None:
                out[name] = float(pct)
    return out, len(out)


async def _board_flows(hub) -> dict[str, dict]:
    """板块主力净额（概念+行业）：name -> {net, ratio, pct, code}（净额单位亿，东财 f62 口径）。

    与 `_board_pcts` 同源、同走 board_flow 唯一入口（30s TTL 缓存 → 实际零额外上游
    调用）。**拆成独立函数是为了不动 `_board_pcts` 的签名**——测试按旧签名
    monkeypatch 它。net 缺失（None）如实保留，调用方按 unknown 处理，
    绝不拿 0 冒充"无流入"（三态纪律）。

    `ratio` = `main_net_ratio`（主力净额占板块成交额比，%，东财 f184；缺 f184 时
    `board_flow` 已按 f62/f6 同式补算）——**此前被丢弃、2026-09-12 接入**，供
    surge 的占比口径使用。同样可缺失（None），调用方按 unknown 跳过。
    """
    out: dict[str, dict] = {}
    for kind in ("concept", "industry"):
        rows, errs = await board_flow.get_board_list(kind)
        if rows is None:
            log.warning("watcher beat: board flow %s unavailable: %s", kind, "; ".join(errs))
            continue
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            out[name] = {
                "net": row.get("main_net_yi"),
                "ratio": row.get("main_net_ratio"),
                "pct": row.get("change_pct"),
                "code": row.get("board_code"),
            }
    return out


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

        from app.core.bjtime import beijing_now as _now

        klines = await hub.provider.get_kline(symbol, "1d")
        today = _now().date()
        prev = [
            k for k in (klines or [])
            if k.volume is not None and to_beijing(k.ts).date() < today
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
    # 昨日量：只补**缓存未命中**的项（`vr_cache` 按日持久 ⇒ 除当日首拍外几乎零成本）。
    # 取数**并发化**（2026-09-12 评审批次 3 / P-1）：旧写法是
    # `for s in sorted(symbols): vols_cache[s] = await _prev_day_volume(hub, s)`
    # —— 串行 await，每只一次**真实 HTTP**（腾讯日线），题材成员并集几十只时
    # 就是几十个 RTT 逐个相加，盘前首拍被拖长。与 relay-rank（P0-3）同型：
    # 纯 IO 并发、结果集合与失败语义都不变。
    #
    # 异常兜底**不需要**再包一层：`_prev_day_volume` 的 try 覆盖其整个函数体
    # （连 `hub.provider` 缺失的 AttributeError 也吞掉）并返回 None，故
    # `asyncio.gather` 默认语义下不会有异常逃逸、单只失败不会连坐其它只。
    # 该契约由 `test_volume_ratio_prev_day_volume_failure_isolated` 钉住 ——
    # 若有人把 `_prev_day_volume` 改成向外抛，那条测试会先红。
    missing = [s for s in sorted(symbols) if s not in vols_cache]
    if missing:
        sem = asyncio.Semaphore(VR_FETCH_CONCURRENCY)

        async def _load_one(sym: str) -> tuple[str, float | None]:
            async with sem:
                return sym, await _prev_day_volume(hub, sym)

        for sym, vol in await asyncio.gather(*(_load_one(s) for s in missing)):
            vols_cache[sym] = vol
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
    # 板块级资金（P1-1）：与涨幅同源（board_flow 唯一入口，命中 30s TTL 缓存）。
    # 失败 → 键缺席，_step_board_flows 无输入（不臆造、不阻断其他判定）。
    try:
        beat["board_flows"] = await _board_flows(hub)
    except Exception as exc:  # noqa: BLE001
        log.warning("watcher beat: board flow failed: %s", exc)
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

    # 个股资金流接线（P1 方向2）：题材成员当日主力净额，一次 ulist 覆盖全部成员。
    # 失败/空 → flows 键缺席，_step_flows 按 unknown 处理（不臆造、不阻断其他判定）。
    try:
        from app.market import stock_flow

        flow_syms = sorted({s for st in themes.values() for s in (st.get("members") or [])})
        if flow_syms:
            flow_payload = await stock_flow.get_stock_flow(flow_syms[: stock_flow.MAX_SYMBOLS])
            beat["flows"] = flow_payload.get("items") or {}
    except Exception as exc:  # noqa: BLE001  资金流是增强信号，失败不影响方向跟踪
        log.warning("watcher beat: stock flow failed: %s", exc)

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
    """watcher 专用系统规则（get-or-create + channels 跟随配置默认）。detached 后只读 id/name/channels。

    系统规则由系统管理（非用户定制对象）：channels 每次与 settings 默认同步——
    2026-09-08 用户指令「中间态不再推飞书」落地后，配置默认去 feishu，DB 里
    历史固化的 feishu 行在下次 ensure 时自动收敛，无需手工迁移。
    """
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == WATCHER_RULE_NAME).one_or_none()
        default_channels = _default_watcher_channels()
        if row is None:
            row = AlertRule(
                name=WATCHER_RULE_NAME,
                enabled=1,
                condition_type="picks_intraday",
                scope="all",
                threshold=0.0,
                channels=default_channels,
            )
            db.add(row)
        elif row.channels != default_channels:
            row.channels = default_channels
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _snapshot_price(app, symbol) -> float | None:
    """从快照取现价（只在调用方没有价格语义字段时作回退）。取不到 → None。"""
    if not symbol:
        return None
    try:
        svc = getattr(app.state if hasattr(app, "state") else app, "snapshot_service", None)
        for sr in getattr(svc, "snapshot", None) or []:
            if sr.get("symbol") == symbol:
                p = sr.get("price")
                return float(p) if isinstance(p, (int, float)) and p > 0 else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _alert_price(alert: dict, snap_price: float | None = None) -> float | None:
    """取告警的**价格**——只认价格语义字段，**绝不用 `trigger_value`**。

    🔴 历史缺陷（2026-09-10 发现并修复，污染数据与影响见 `retro-and-gaps.md`）：
    `trigger_value` 在本模块有四种语义且**没有一种表示价格**——
    falsify/confirm = 涨跌幅、flow_surge/board_flow = 净额（亿元）；
    `buy_point` 的价格放在 `meta["price"]`（不是 trigger_value）。
    此前 `entry_price` 与 `maybe_open(price=...)` 两处都直接取 `trigger_value`，后果：
      · `watch_ledger` 把「主力净流入 0.32 亿」写成股价 0.32 → pnl 算出 **22150%**、
        verdict 误判 success（09-09 有 71 行）；
      · `paper_order` 以「题材涨跌幅 1.82」当价格开模拟仓 → **模拟成本价错误**
        （实测单号 1/2：603330 以 1.82 元挂单）。
    取数优先级：`meta["price"]`（价格语义字段）→ 快照现价 → **None**。
    ⚠️ 返回 None 时调用方必须接受「判不出」：`record_sighting` 会让该行在清算时
    跳过（不产生 verdict），`maybe_open` 会拒绝开仓——**宁可不判，不可判错**。
    """
    meta = alert.get("meta") or {}
    p = meta.get("price")
    if isinstance(p, (int, float)) and p > 0:
        return float(p)
    if isinstance(snap_price, (int, float)) and snap_price > 0:
        return float(snap_price)
    return None


async def dispatch_alert(app, alert: dict, *, rule_provider=None) -> bool:
    """去重（append_alert）→ record_trigger → NotifierRegistry 分发。

    rule_provider：返回 AlertRule 的零参函数（如买点事件用 __picks_buy_point__
    独立规则与通道）；默认 watcher 系统规则。alert["card"]（dict，可选）会进
    snapshot，FeishuNotifier 见 card 即发 interactive 卡片形态。
    返回 False = 当日重复（key 已存在），调用方无须重试。app 兼容实例或 .state。
    """
    from app.picks.morning_brief import append_alert, brief_for_today

    target, _ = brief_for_today()
    if not append_alert(target, alert):
        return False
    # 猎场批次 A（需求 7/8）：盘中确认个股**首见即入台账**（持久化保留，当日唯一）；
    # 入选依据 = kind/text/direction（入选时刻的证据快照）。失败只记日志不阻断分发。
    if alert.get("symbol"):
        with contextlib.suppress(Exception):
            from app.picks.pre_limit_radar import board_limit_pct, is_sealed
            from app.picks.watch_ledger import record_sighting

            # 2026-09-09 用户指令：缺名称=无效提醒——name 空时从快照补，仍空则不登记
            snap_pct: float | None = None
            snap_price: float | None = None
            if not alert.get("name"):
                try:
                    snap_rows = getattr(app.state if hasattr(app, "state") else app, "snapshot_service", None)
                    for sr in getattr(snap_rows, "snapshot", None) or []:
                        if sr.get("symbol") == alert["symbol"]:
                            alert["name"] = sr.get("name") or ""
                            snap_pct = sr.get("change_pct")
                            snap_price = sr.get("price")
                            break
                except Exception:  # noqa: BLE001
                    pass
            if not alert.get("name"):
                log.warning("watcher alert %s 无名称且快照缺失——不入台账", alert["symbol"])
                return False
            # KB-DEC-011（2026-09-09 用户指令）：只有涨停前提醒过的才入台账——
            # 提醒时刻已封板（或无行情佐证可证明未封板）→ 只提醒不入册
            if snap_pct is None:
                try:
                    snap_rows = getattr(app.state if hasattr(app, "state") else app, "snapshot_service", None)
                    for sr in getattr(snap_rows, "snapshot", None) or []:
                        if sr.get("symbol") == alert["symbol"]:
                            snap_pct = sr.get("change_pct")
                            snap_price = sr.get("price")
                            break
                except Exception:  # noqa: BLE001
                    pass
            if snap_pct is None or is_sealed(float(snap_pct), board_limit_pct(str(alert["symbol"]), str(alert["name"]))):
                log.warning(
                    "watcher alert %s 提醒时已封板/无行情佐证（pct=%s）——KB-DEC-011 不入台账（提醒照发）",
                    alert["symbol"], snap_pct,
                )
            else:
                record_sighting(
                    trade_date=beijing_now().date().isoformat(),
                    symbol=str(alert["symbol"]),
                    name=str(alert.get("name") or ""),
                    layer="pre_limit" if alert.get("kind") == "pre_limit"
                    else ("today_strongest" if (rule_provider is not None) else "quiet_starting"),
                    source_theme=str(alert.get("direction") or ""),
                    reason={"kind": alert.get("kind"), "text": (alert.get("text") or "")[:300],
                            "direction": alert.get("direction") or ""},
                    entry_price=_alert_price(alert, snap_price),
                    entry_time=beijing_now().strftime("%H:%M:%S"),
                )
    # 仓位引擎（2026-09-09 闭环「持仓」段）：买点/确认触发 → 是否自动开模拟仓由
    # 引擎按当日盘面裁定（阶段仓位上限/闸门/角色权重/确定性门槛）——嗅到≠买入，
    # pre_limit 预警不在开仓白名单。
    if alert.get("kind") in ("buy_point", "confirm"):
        with contextlib.suppress(Exception):
            from app.picks.position_engine import maybe_open

            await maybe_open(
                app,
                symbol=str(alert["symbol"]), name=str(alert.get("name") or ""),
                trigger=str(alert["kind"]),
                price=_alert_price(alert, _snapshot_price(app, alert.get("symbol"))),
            )

    state = app.state if hasattr(app, "state") else app
    session_factory = get_session_factory()
    rule = rule_provider(session_factory) if rule_provider is not None else ensure_system_rule(session_factory)
    repo = getattr(state, "alert_repo", None) or AlertRepository(session_factory)
    meta = alert.get("meta") or {}
    snapshot: dict = {
        "kind": alert.get("kind"),
        "direction": alert.get("direction"),
        "text": alert.get("text"),
        # 名称必须随快照落库：悬浮球（alert_triage.pending_bubbles）要求
        # symbol+name 齐备，缺 name 整条过滤（2026-09-09 用户指令）。
        # 此前只落 kind/direction/text → 实测 14 条 notify 全部 name=None，
        # 悬浮球一条 watcher 个股提醒都收不到（2026-09-10 修复）。
        "name": alert.get("name") or None,
    }
    if isinstance(alert.get("card"), dict):
        snapshot["card"] = alert["card"]
    event = repo.record_trigger(
        rule.id,
        alert.get("symbol") or "000000",
        float(meta.get("trigger_value") or 0.0),
        float(meta.get("threshold") or 0.0),
        snapshot=snapshot,
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
