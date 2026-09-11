"""量能语义层（P1-35）：把散落各处、口径与**方向都不一致**的「缩量/放量」收口为统一六态。

## 为什么需要它（2026-09-10 逐处 grep 实测）

量能判定此前散在 **7 处**，不仅阈值不同，**量纲与方向也不同**：

| 位置 | 度量 | 缩量判据 | 方向 |
|---|---|---|---|
| `market/tech_score.py` | 量比（当日量/前 5 日均量） | < 0.6 | 大 = 放量 |
| `picks/engine.py::score_capital` | 量比 | < 0.5 扣分 | 大 = 放量 |
| `picks/intraday_rules.py` | 量比 | < 0.8（缩量回踩） | 大 = 放量 |
| `services/dragon_service.py` | **换手率 %** | < 3 死亡换手 | 大 = 爆量 |
| `picks/lurk_pool.py` | **换手率 %** | < 2 长期缩量 | 大 = 放量 |
| `factors/library.py` | **vma20 = 20 日均量/今日量** | **> 1 才是缩量** | **大 = 缩量（倒数！）** |
| `picks/chip_signal.py` | 形态命名 | 放量滞涨 / 缩量回踩主峰 | — |

⇒ 同一份行情，`vma20 = 1.5` 与 `量比 = 1.5` **语义完全相反**；「缩量」一词在不同处
指不同的量、不同的阈值。这是典型的「跨口径不可混用」（KB-ENG-39 同源纪律）。

## 收口口径（做什么 / 不做什么）

**做**：统一**词汇表** + 统一**方向**（本模块 `ratio` 一律 = 当期量 / 基准量，**> 1 即放量**）
+ **量价配合**语义（量的大小本身不构成判断，必须与价格方向合看）+ 三态（判不出给 `unknown`）
+ 三层解读文案（同一状态在 个股/板块/大盘 的解读不同——这是「三层共用」的实质）。

**不做**：**不统一阈值**。各处场景不同（个股择时 / 换手健康度 / 因子截面），阈值差异是
**合理的**，强行拉平才是错误。故阈值以 `BANDS` **显式登记**（谁在用 + 依据），
接入是可选、渐进的；**本模块不反向修改任何既有打分**（零回归）。

## 六态

`ratio` 分区 × `change_pct` 方向：

- `spike` —— 爆量分歧：量比极端，方向意义被稀释（**涨跌都是分歧**）
- `surge_up` / `surge_down` —— 放量上攻 / 放量下杀
- `shrink_up` / `shrink_down` —— 缩量上行 / 缩量回调
- `normal` —— 量能平稳（中间区）
- `unknown` —— 判不出（三态纪律：缺数据 ≠ 正常）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VolumeState = Literal[
    "spike", "surge_up", "surge_down", "shrink_up", "shrink_down", "normal", "unknown",
]

Context = Literal["stock", "board", "index"]


@dataclass(frozen=True)
class VolumeBands:
    """一组阈值分档（**不改既有判定**，仅供新接入方选用与登记）。

    shrink: 低于此 ratio 视为缩量；spike: 高于此 ratio 视为爆量（分歧）。
    """

    key: str
    shrink: float
    spike: float
    usage: str


#: 显式登记：每档标注**谁在用 / 依据**。新增消费方请在此登记，不要就地写魔法数。
BANDS: dict[str, VolumeBands] = {
    "tech": VolumeBands(
        "tech", 0.6, 4.0,
        "tech_score 量价维（当日量/前5日均量）；<0.6 显著缩量 / >4 爆量",
    ),
    "capital": VolumeBands(
        "capital", 0.5, 4.0,
        "engine.score_capital 量比打分档（≥1.5 +12 / 0.8-1.5 +5 / <0.5 -8）",
    ),
    "intraday": VolumeBands(
        "intraday", 0.8, 4.0,
        "intraday_rules 缩量回踩线 PULLBACK_VOLUME_RATIO=0.8",
    ),
}

#: 放量/缩量的分界（统一方向：ratio > 1 即放量）。中间区 [shrink, 1.0] 归 normal。
EXPAND_LINE = 1.0

#: 价格方向判为「平」的死区（%）：小于此幅度不计方向，避免 ±0.01% 被读成涨跌。
FLAT_EPS_PCT = 0.05


def _resolve_bands(bands: str | VolumeBands) -> VolumeBands:
    if isinstance(bands, VolumeBands):
        return bands
    try:
        return BANDS[bands]
    except KeyError as exc:  # fail-fast：不静默回退到默认档（会掩盖调用方拼错）
        raise ValueError(f"unknown volume bands: {bands!r}; expected one of {sorted(BANDS)}") from exc


def volume_state(
    ratio: float | None,
    change_pct: float | None,
    *,
    bands: str | VolumeBands = "tech",
) -> VolumeState:
    """量价配合 → 六态。**任何输入判不出即 unknown**（不硬贴标签）。

    ratio：当期量 / 基准量（**> 1 = 放量**）。change_pct：同期涨跌幅（%）。
    两者任一为 None、或 ratio 非正 → unknown（三态：缺数据 ≠ 正常）。
    """
    if ratio is None or change_pct is None:
        return "unknown"
    if not isinstance(ratio, (int, float)) or not isinstance(change_pct, (int, float)):
        return "unknown"
    if ratio <= 0:
        return "unknown"

    b = _resolve_bands(bands)
    if ratio >= b.spike:
        return "spike"
    flat = abs(change_pct) < FLAT_EPS_PCT
    if ratio > EXPAND_LINE:
        if flat:
            return "normal"
        return "surge_up" if change_pct > 0 else "surge_down"
    if ratio < b.shrink:
        if flat:
            return "normal"
        return "shrink_up" if change_pct > 0 else "shrink_down"
    return "normal"


def ratio_from_inverse(value: float | None, *, eps: float = 1e-9) -> float | None:
    """把「基准量/当期量」型指标（如 qlib `vma20`）转成本模块统一方向（当期量/基准量）。

    ⚠️ 这是**防方向搞反**的关键工具：`vma20 = 20 日均量 / 今日量`，其 >1 表示**缩量**，
    与「量比 >1 表示放量」正好相反。直接把它塞进 `volume_state` 会把缩量读成放量。
    value <= 0 / None → None（三态）。
    """
    if value is None or not isinstance(value, (int, float)) or value <= eps:
        return None
    return 1.0 / value


def volume_state_from_inverse(
    inverse_ratio: float | None,
    change_pct: float | None,
    *,
    bands: str | VolumeBands = "tech",
) -> VolumeState:
    """`ratio_from_inverse` + `volume_state` 的组合入口（供 vma20 家族直接使用）。"""
    return volume_state(ratio_from_inverse(inverse_ratio), change_pct, bands=bands)


#: 状态 → 个股层短标签（统一词汇；新接入方应引用此处而非自造同义词）。
_LABEL: dict[VolumeState, str] = {
    "spike": "爆量分歧",
    "surge_up": "放量上攻",
    "surge_down": "放量下杀",
    "shrink_up": "缩量上行",
    "shrink_down": "缩量回调",
    "normal": "量能平稳",
    "unknown": "量能未判定",
}

#: 状态 × 三层语境解读。同一状态在个股/板块/大盘的含义**不同**——这是「三层共用」的实质。
_CONTEXT_NOTE: dict[VolumeState, dict[Context, str]] = {
    "spike": {
        "stock": "量能极端放大，多空分歧剧烈，方向意义被稀释",
        "board": "板块放天量，分歧加剧，注意炸板与退潮",
        "index": "全市场放天量，分歧剧烈，谨防高位换手",
    },
    "surge_up": {
        "stock": "量价齐升，资金主动进攻",
        "board": "板块放量上攻，资金合力进场",
        "index": "指数放量上行，量能确认",
    },
    "surge_down": {
        "stock": "放量下跌，抛压真实（非缩量阴跌）",
        "board": "板块放量下杀，资金撤离",
        "index": "指数放量下跌，恐慌抛压",
    },
    "shrink_up": {
        "stock": "缩量上行：惜售或承接不足，须结合位置判（高位=动能衰减）",
        "board": "板块缩量上涨：分歧，接力意愿弱",
        "index": "指数缩量新高：量能未跟上，警惕假突破",
    },
    "shrink_down": {
        "stock": "缩量回调：抛压不重（良性回踩）或无人接（阴跌），看是否破位",
        "board": "板块缩量回落：观望，非恐慌",
        "index": "指数缩量回调：承接尚可，非恐慌出逃",
    },
    "normal": {
        "stock": "量能平稳，无异常信号",
        "board": "板块量能平稳",
        "index": "指数量能平稳",
    },
    "unknown": {
        "stock": "量价数据不足，未判定",
        "board": "板块量价数据不足，未判定",
        "index": "指数量价数据不足，未判定",
    },
}


def describe(state: VolumeState, *, context: Context = "stock") -> str:
    """状态 → 中文解读（**文案单点收口**）。context 取 stock / board / index。"""
    note = _CONTEXT_NOTE.get(state, _CONTEXT_NOTE["unknown"])
    return note.get(context) or note["stock"]


def label(state: VolumeState) -> str:
    """状态 → 短标签（统一词汇，供表格/徽标）。"""
    return _LABEL.get(state, _LABEL["unknown"])


def is_actionable(state: VolumeState) -> bool:
    """该状态是否构成可读信号（unknown 不算——避免把"没数据"当"没问题"）。"""
    return state != "unknown"
