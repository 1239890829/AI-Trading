"""气象 / 气候一阶数据源（ENSO）→ 板块前瞻线索（retro §6.2 P1-32）。

## 为什么需要它

系统此前对「厄尔尼诺」的处理是**新闻关键词驱动**（`events/chains.py` 的
`_EL_NINO_KEYS`）：只有当快讯标题里出现「厄尔尼诺/拉尼娜/极端天气」时，才产
传导链方向行。这决定了它**只能「见报后跟」，做不到「提前」**——而 ENSO 是全球
最早被官方量化发布的宏观气候信号之一（NOAA 按月滚动发布，比 A 股题材反应早）。

本模块把触发条件从「文本命中」升级为「**一阶指数越线**」：
读 ONI（Oceanic Niño Index）原始序列 → 按 NOAA 官方口径判定状态 → 给出
「当前处于什么气候相位、已持续多久、强度如何」，再由调用方接到既有传导链。

## 数据源（唯一）

- NOAA CPC ONI：``https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt``
- 纯文本，四列 ``SEAS YR TOTAL ANOM``，**一行 = 一个重叠三月季**
  （DJF/JFM/…/NDJ 循环），1950 年至今，实测可达（2026-09-11）。

⚠️ 口径要点（改前必读）：

1. **季 ≠ 月**。NOAA 的「季」是三字母滑动窗口（DJF = 12月+1月+2月），
   一年 12 行。本模块用**窗口中月**做锚定（DJF 锚 1 月，JFM 锚 2 月，…），
   并要求**该季末月已结束**才纳入（`_season_end() < asof`）——否则会把根本
   还没发布的值当成已知（前视）。
2. **官方定义**：ONI ≥ +0.5 连续 **5** 个重叠季 = 厄尔尼诺；≤ −0.5 连续 5 季
   = 拉尼娜。未满 5 季但已越线 = **预警态**（本模块单列，前瞻价值最大）。
3. **ONI 天然滞后**。它是三月滑动平均、且在季末后才发布 ⇒ **不构成领先指标**。
   本模块的价值是「比新闻早一步拿到**确定性**」，不是「预测商品/股价」。
   表述必须写成状态 + 依据 + 失效条件（红线 3）。

## 三态纪律

- `state="neutral"` 是**真信息**（确未越线），与 `state=None`（数据不足/取数失败）严格区分；
- `alert` 单列：`"el_nino"` / `"la_nina"` / `None`，表示「已越线但未满 5 季」；
- 未判定时给 `unjudged_reason`，**绝不臆造 `neutral`**。

## 🔴 传导链实证结论（2026-09-11，改前必读）

`scripts/verify_climate_chain.py` 对 `chains.py` 那张人工表做了月频检验
（2000-01 ~ 2026-09，**import 本模块的 `parse_oni`/`classify`/`season_end`**，
保证核验相位 = 线上相位）。**结论是：那张表在数据上得不到支持。**

| 行业（对应题材） | h=1 厄尔尼诺月均 vs 中性月均 | t | 分年度同向 |
|---|---|---|---|
| 农林牧渔（农业种植） | +0.492% vs +0.227% | 0.29 | 3/8 |
| **基础化工（磷化工/化肥）** | **+0.170% vs +0.305%** | **−0.20** | **2/8** |
| 公用事业（绿色电力/智能电网） | +0.959% vs −0.125% | 1.69 | 5/8 |
| 电力设备 | +0.316% vs +0.281% | 0.03 | 1/5 |
| 食品饮料 | +1.563% vs +0.329% | 1.77 | 6/8 |

**三条判读**（见 [[KB-DEC-018]] / KB-DEC-019 反固化条款）：

1. **没有任何一条通过**（t≥2 且分年度过半）。样本确实小（44 个厄尔尼诺月，
   1950 至今约十余次事件），所以准确表述是「**未获支持**」而非「已证伪」；
2. **但有一处方向性反证**：基础化工（磷化工/化肥所属申万一级）的厄尔尼诺月
   超额**低于**中性月，与表里「弹性最强」的排序相反，且分年度仅 2/8 同向。
   ⇒ **「弹性排序」连方向都没站住**，绝不可当结论引用；
3. 公用事业/食品饮料 t≈1.7、分年度 5/8 与 6/8，属**弱提示**；月频、重叠样本
   相关性高，实际有效样本远小于 44 ⇒ 仍不足以当规则。

⇒ 本模块的**价值只在「气候相位」本身**（观测量，比新闻早一步拿到确定性）；
候选链作为**可解释性材料**保留，但每处输出都必须携带 `empirical_verdict`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.core.ttl_cache import TTLCache

log = logging.getLogger(__name__)

#: 实证判读（由 `scripts/verify_climate_chain.py` 产出后回填，随输出下发给消费方）
EMPIRICAL_VERDICT = (
    "**人工传导链未获数据支持**（月频检验，2000-01~2026-09，h=1 月初相位→当月超额）："
    "农林牧渔 t=0.29（分年 3/8）、基础化工 t=**−0.20**（分年 **2/8**，且方向与表相反）、"
    "公用事业 t=1.69（5/8）、电力设备 t=0.03（1/5）、食品饮料 t=1.77（6/8）——"
    "**无一通过 t≥2 且分年度过半**。厄尔尼诺事件样本本就稀少（44 个月），"
    "准确表述是「未获支持」而非「已证伪」。⇒ 候选题材只作线索，不得外推。"
)

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

#: 重叠三月季的循环顺序（NOAA 固定）。索引 +1 = 窗口中月。
SEASONS: tuple[str, ...] = (
    "DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
    "JJA", "JAS", "ASO", "SON", "OND", "NDJ",
)

#: 官方判定阈值（NOAA CPC）
ONI_THRESHOLD = 0.5
#: 官方「确立」所需连续越线季数
PERSIST_SEASONS = 5

#: 强度分档（按当前连续段的峰值 |ANOM|，NOAA 口径）
STRENGTH_BANDS: tuple[tuple[float, str], ...] = (
    (2.0, "very_strong"),
    (1.5, "strong"),
    (1.0, "moderate"),
    (0.5, "weak"),
)
STRENGTH_LABEL = {
    "weak": "弱", "moderate": "中等", "strong": "强", "very_strong": "超强",
}

#: 允许的最大滞后（天）。**取 120 而不是 60**——正常出版节奏下，季末到发布要
#: 1~2 周，而「上一季刚结束、这一季还没发布」的窗口本身就有近一个月，再加季末
#: 距今最多 31 天 ⇒ **正常滞后可达 ~75 天**。60 天会把每个月的月初误判成「源陈旧」
#: （实测：`MJJ 2026` 季末 06-30，09-03 看就是 65 天 → 假告警）。120 天仍能在一个
#: 月度更新周期内抓到真正的停更。
MAX_STALE_DAYS = 120

#: 读缓存 TTL（秒）：NOAA 月更，1 小时足够且不影响新鲜度
_CACHE_TTL = 3600.0
_CACHE = TTLCache("climate-oni", ttl=_CACHE_TTL, maxsize=4)

CHAIN_CAVEAT = (
    "「厄尔尼诺→磷化工/化肥/农业种植/电力/电网」为**人工映射的候选假设**；"
    "本模块已用月频数据检验，**未获支持**（见 `EMPIRICAL_VERDICT`）——"
    "本模块只负责给出气候相位，**不据此做跨题材外推**。"
)
TIMING_NOTE = (
    "时点结构：ONI 是**三月滑动平均且季末后发布**，天然滞后 ⇒ **不构成领先指标**；"
    "其价值在于「比新闻关键词早一步拿到确定性」，而非预测价格。"
)
DISCLAIMER = "以上为气候相位与候选传导链陈述，不构成买卖建议。"


@dataclass(frozen=True)
class OniPoint:
    """一个重叠三月季的 ONI 值。

    `anchor_month` = 窗口中月（DJF→1 … NDJ→12）；`end_month` = 窗口末月。
    """

    season: str
    year: int
    total: float
    anom: float

    @property
    def anchor_month(self) -> int:
        return SEASONS.index(self.season) + 1

    @property
    def end_month(self) -> int:
        m = self.anchor_month + 1
        return m - 12 if m > 12 else m


def season_end(p: OniPoint) -> date:
    """该季**末月**的最后一天。前视守卫用它：只有「已走完」的季才算已知。

    ⚠️ **末月跨年**：NDJ（11/12/次年1）的末月锚在次年——`NDJ 1997` 的季末是
    **1998-01-31**，不是 1997-01-31。漏掉这个 +1 会让整段历史季末提前近一年，
    把「数据滞后」误判到几乎每一个月（实测：1997-12 的 asof 被判 `stale 304 天`
    → `state=None`，厄尔尼诺月样本归零）。判定条件用 `end_month < anchor_month`
    而不是写死 `"NDJ"`，日后再增季节代号也不会漏。
    """
    from calendar import monthrange

    y, m = p.year, p.end_month
    if p.end_month < p.anchor_month:
        y += 1
    return date(y, m, monthrange(y, m)[1])


def parse_oni(text: str) -> list[OniPoint]:
    """NOAA ONI 纯文本 → OniPoint 列表（按时间升序）。

    丢弃不可解析行（表头、空行、脏行）——**不伪造补点**。
    未知季节代号一律跳过（宁可少一个点，不可把值挂到错误的月份上）。
    """
    out: list[OniPoint] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        seas, yr, _total, anom = parts
        seas = seas.strip().upper()
        if seas not in SEASONS:
            continue
        try:
            y = int(yr)
            a = float(anom)
            # TOTAL 也必须进 try：它在文件里同样是文本，脏值会让**整份解析**
            # 抛 ValueError（不是跳过一行）——实测踩过，属"一行脏数据炸掉全表"。
            t = float(_total)
        except (TypeError, ValueError):
            continue
        if not (1 <= y <= 3000):
            continue
        out.append(OniPoint(season=seas, year=y, total=t, anom=a))
    out.sort(key=lambda p: (p.year, p.anchor_month))
    return out


def _strength(peak_abs: float) -> str | None:
    for hi, name in STRENGTH_BANDS:
        if peak_abs >= hi:
            return name
    return None


def _run(points: list[OniPoint]) -> tuple[str, int, float]:
    """从**最新一季往回**数同向连续越线段。

    返回 `(方向, 连续季数, 段内峰值 |ANOM|)`；未越线返回 `("", 0, 0.0)`。
    注：以 ±ONI_THRESHOLD 为界，`|anom| == 0.5` 记入（官方口径是 ≥）。
    """
    if not points:
        return "", 0, 0.0
    last = points[-1].anom
    if last >= ONI_THRESHOLD:
        sign = 1
    elif last <= -ONI_THRESHOLD:
        sign = -1
    else:
        return "", 0, 0.0
    n = 0
    peak = 0.0
    for p in reversed(points):
        if (p.anom >= ONI_THRESHOLD) if sign > 0 else (p.anom <= -ONI_THRESHOLD):
            n += 1
            peak = max(peak, abs(p.anom))
        else:
            break
    return ("el_nino" if sign > 0 else "la_nina"), n, peak


def classify(points: list[OniPoint], *, asof=None) -> dict:
    """ONI 序列 → 气候相位判定（纯函数，零 IO）。

    只纳入 `season_end(p) < asof` 的季（前视守卫）。返回：

    - `state`：`"el_nino"` / `"la_nina"` / `"neutral"` / `None`（未判定）
    - `alert`：已越线但未满 5 季时的 `"el_nino"` / `"la_nina"`，否则 `None`
    - `strength`：当前连续段的强度档（越线才有）
    - `consecutive` / `peak_abs`：连续季数 / 段内峰值
    - `latest`：最新一季的 `{season, year, anom, end_date}`
    - `unjudged_reason`：`state is None` 时具名原因
    """
    if asof is None:
        asof = date.today()
    elif not isinstance(asof, date):
        asof = _parse_date(str(asof)) or date.today()

    usable = [p for p in points if season_end(p) < asof]
    base = {
        "as_of": asof.isoformat(),
        "threshold": ONI_THRESHOLD,
        "persist_seasons": PERSIST_SEASONS,
        "timing_note": TIMING_NOTE,
        "chain_caveat": CHAIN_CAVEAT,
        "empirical_verdict": EMPIRICAL_VERDICT,
        "epistemic_note": (
            "ONI 为 NOAA CPC 官方指数（重叠三月季海温距平），state 是**观测量**，"
            "不是对行情的预测。"
        ),
        "disclaimer": DISCLAIMER,
    }

    if not usable:
        return {
            **base,
            "state": None,
            "alert": None,
            "strength": None,
            "consecutive": 0,
            "peak_abs": None,
            "latest": None,
            "series": [],
            "unjudged_reason": "ONI 序列为空或全部季末未到（数据不足）",
            "candidate_links": [],
        }

    direction, run_len, peak = _run(usable)
    established = run_len >= PERSIST_SEASONS
    state = direction if (direction and established) else "neutral"
    alert = direction if (direction and not established) else None

    last = usable[-1]
    stale_days = (asof - season_end(last)).days
    if stale_days > MAX_STALE_DAYS:
        return {
            **base,
            "state": None,
            "alert": None,
            "strength": None,
            "consecutive": run_len,
            "peak_abs": round(peak, 3) if peak else None,
            "latest": _latest(last),
            "series": _tail(usable),
            "unjudged_reason": f"ONI 数据滞后（最新季末距今 {stale_days} 天 > {MAX_STALE_DAYS}）",
            "candidate_links": [],
        }

    return {
        **base,
        "state": state,
        "alert": alert,
        "strength": _strength(peak) if direction else None,
        "consecutive": run_len,
        "peak_abs": round(peak, 3) if peak else None,
        "latest": _latest(last),
        "series": _tail(usable),
        "unjudged_reason": None,
        "candidate_links": candidate_links(state, alert),
    }


def _latest(p: OniPoint) -> dict:
    return {
        "season": p.season,
        "year": p.year,
        "anom": round(p.anom, 3),
        "end_date": season_end(p).isoformat(),
    }


def _tail(points: list[OniPoint], n: int = 6) -> list[dict]:
    return [
        {"season": p.season, "year": p.year, "anom": round(p.anom, 3)}
        for p in points[-n:]
    ]


def candidate_links(state: str | None, alert: str | None) -> list[dict]:
    """气候相位 → 候选题材料材行（**复用 chains 的唯一真相源**）。

    只在**厄尔尼诺**侧给候选行（`el_nino_rows()` 的内容即该侧假设）。
    拉尼娜**不给候选行**——「拉尼娜 = 厄尔尼诺取反」正是 KB-DEC-018 明令禁止的
    直觉链（两者对国内主产区的影响路径不同，不可直接反号）。
    """
    if state != "el_nino" and alert != "el_nino":
        return []
    from app.events.chains import el_nino_rows

    return el_nino_rows()


def _parse_date(s: str) -> date | None:
    try:
        y, m, d = s[:10].split("-")
        return date(int(y), int(m), int(d))
    except Exception:  # noqa: BLE001  脏日期一律当缺失
        return None


# ------------------------------------------------------------------ 取数


async def fetch_oni_text() -> str:
    """拉 NOI 原始文本（带单飞缓存）。

    `trust_env=False`：禁系统代理——本机代理会吃掉外呼请求（MEMORY 已记两次）。
    """
    async def _fetch() -> str:
        import httpx

        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as cli:
            resp = await cli.get(ONI_URL)
            resp.raise_for_status()
            return resp.text

    _hit, text = await _CACHE.get_or_set("oni", _fetch)
    return text or ""


async def collect(asof=None) -> dict | None:
    """取数 + classify。取数失败返回 `None`（整块不渲染，不谎称「中性」）。"""
    try:
        text = await fetch_oni_text()
    except Exception as exc:  # noqa: BLE001  源不可得 → 如实降级
        log.warning("climate: fetch ONI failed: %s", exc)
        return None
    pts = parse_oni(text)
    if not pts:
        log.warning("climate: ONI text parsed to zero points")
        return None
    return classify(pts, asof=asof)
