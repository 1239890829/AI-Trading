"""事件→板块传导链知识表（hotspot-pipeline-design §4.1 传导层，2026-09-07）。

与 extract.py 的 ENTITY_ALIASES（1 词→1 题材）互补：别名表管「直接点名」，
本表管「一级事件→多板块多级传导」。全部人工维护、basis 必带，direction 不猜
——产业链/海外实体类事件本体即利好源头给 +1；宏观类方向引用回测结论
（docs/nfp-ashare-validation.md），证据不足时 strength=1（弱）或 0（待判）。

三类链路：
1. 产业链多级：厄尔尼诺 → 种植/磷化工/化肥/电力/电网（强度=专业弹性排序的
   工程化表达：化肥直接进利润 2 > 种植 2 > 电力/电网逻辑间接 1）
2. 海外实体：OpenAI/GPT → AI应用/AI智能体/算力（海外产品事件→A股题材映射）
3. 宏观量化：非农 → 方向由意外差决定；P0 按标题方向词二分，词表未命中显式 0

纪律：
- target 全部使用官方目录名（theme 表实测核对 2026-09-07，零臆造）；
- 每组命中一次（同组多触发词不重复产行）；跨组各自独立；
- matched_by="chain" 与 name/alias/source 区分，basis 写明链路出处；
- target_type="market" 是新增类型（A股大盘），下游全部按类型显式过滤、
  未知类型自动忽略（grep 核实 routes/picks/brief 消费方），不影响存量行为。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_NFP_BASIS = "宏观传导链 chains.nfp（docs/nfp-ashare-validation.md 10年116期回测，t 不显著，仅提示不作规则）"


def _theme_row(target: str, direction: int, strength: int, chain: str, basis: str) -> dict:
    return {
        "target_type": "theme",
        "target": target,
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"传导链表 chains（人工维护）：{basis}",
        "matched_by": "chain",
    }


def _market_row(direction: int, strength: int, chain: str, basis: str) -> dict:
    return {
        "target_type": "market",
        "target": "A股大盘",
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"传导链表 chains（人工维护）：{basis}",
        "matched_by": "chain",
    }


# ---- 1. 产业链多级：厄尔尼诺/拉尼娜/极端天气（hotspot §3.2 实测传导链）----
_EL_NINO_ROWS = [
    _theme_row("磷化工", 1, 2, "厄尔尼诺→主产区减产→施肥需求+磷肥涨价直接进利润→弹性最强", "厄尔尼诺农业链（弹性排序：化肥>种植>电力）"),
    _theme_row("化肥", 1, 2, "厄尔尼诺→农产品减产涨价→钾肥等化肥涨价直接进利润", "厄尔尼诺农业链"),
    _theme_row("农业种植", 1, 2, "厄尔尼诺→主产区天气异常→农产品减产涨价→种植弹性", "厄尔尼诺农业链（国内主粮自给、进口依赖大的是大豆/糖）"),
    _theme_row("绿色电力", 1, 1, "厄尔尼诺→极端天气→用电负荷+水电来水波动→电力（逻辑最间接，靠极端天气兑现）", "厄尔尼诺电力链"),
    _theme_row("智能电网", 1, 1, "厄尔尼诺→负荷波动放大→电网投资/特高压景气", "厄尔尼诺电网链"),
]
_EL_NINO_KEYS = ("厄尔尼诺", "拉尼娜", "极端天气", "干旱", "寒潮", "超强台风")

# ---- 2. 海外实体：OpenAI/GPT → A股 AI 链（hotspot §3.4 实测传导链）----
_GPT_ROWS = [
    _theme_row("AI应用", 1, 2, "海外大模型迭代→应用落地加速（办公/金融/内容）", "海外AI产品链"),
    _theme_row("AI智能体", 1, 2, "GPT 类 Agent「从问答到直接做事」→智能体应用加速", "海外AI产品链"),
    _theme_row("东数西算(算力)", 1, 1, "大模型训练/推理→算力需求底层受益", "海外AI产品链"),
]
_GPT_KEYS = ("OpenAI", "GPT", "ChatGPT", "Sora")

# ---- 3. 宏观：非农（方向动态判定）+ 加息/降息（常识弱方向）----
_NFP_STRONG = re.compile(r"超预期|强于预期|高于预期|意外强劲|大幅好于")
_NFP_WEAK = re.compile(r"不及预期|低于预期|弱于预期|意外疲软|大幅下滑")

_NFP_OBSERVE_ROWS = [
    _theme_row("东数西算(算力)", 0, 1, "隔夜美股科技定价→A股算力链开盘情绪观察（历史：非农日振幅+24~33%）", "非农情绪链（方向取决于意外差，未知则 0）"),
    _theme_row("苹果概念", 0, 1, "隔夜美股科技定价→A股果链开盘情绪观察", "非农情绪链（方向取决于意外差，未知则 0）"),
]


def _nfp_rows(title: str) -> list[dict]:
    """非农方向：强意外→A股偏空+高波；弱意外→偏强；词表未命中显式 0（不猜）。"""
    if _NFP_STRONG.search(title):
        direction = -1
        chain = "劳动市场意外强→宽松预期收缩→次日A股偏弱+波动放大（深A日内-0.28%；振幅+24~33%，均不显著，仅提示）"
    elif _NFP_WEAK.search(title):
        direction = 1
        chain = "劳动市场意外弱→宽松预期回升→次日A股偏强（深A +0.63%，n=18，t=1.78 不显著，仅提示）"
    else:
        direction = 0
        chain = "方向取决于意外差（标题未给意外方向），P0 不猜——待实际数据回填"
    return [_market_row(direction, 1, chain, _NFP_BASIS)] + [dict(r) for r in _NFP_OBSERVE_ROWS]


def _rate_rows(title: str) -> list[dict]:
    """加息/降息：常识弱方向（strength=1，实际取决于当时主导矛盾）；议息未决议显式 0。"""
    basis = "宏观常识链 chains.rate（方向随主导矛盾切换，仅提示）"
    if "降息" in title or "降准" in title:
        return [_market_row(1, 1, "流动性宽松→无风险利率下行→风险偏好回升→A股偏多", basis)]
    if "加息" in title:
        return [_market_row(-1, 1, "流动性收紧→美元/美债利率上行→A股承压", basis)]
    return [_market_row(0, 1, "议息决议未出/未含方向词，方向不定——显式待判", basis)]


_RATE_KEYS = ("加息", "降息", "降准", "议息", "FOMC", "美联储决议")


def match_chains(title: str) -> list[dict]:
    """标题 → 传导链方向行（纯函数，与 extract_directions 行同构）。

    每组命中一次；组内触发词取第一个命中（不重复产行）；跨组独立累加。
    宏观两组（非农/利率）行内 direction 依赖标题动态判定。
    """
    out: list[dict] = []
    if any(k in title for k in _EL_NINO_KEYS):
        hit = next(k for k in _EL_NINO_KEYS if k in title)
        out += [{**r, "basis": f"{r['basis']}；触发词「{hit}」"} for r in _EL_NINO_ROWS]
    if any(k in title for k in _GPT_KEYS):
        hit = next(k for k in _GPT_KEYS if k in title)
        out += [{**r, "basis": f"{r['basis']}；触发词「{hit}」"} for r in _GPT_ROWS]
    if "非农" in title:
        out += _nfp_rows(title)
    if any(k in title for k in _RATE_KEYS):
        out += _rate_rows(title)
    return out


# ---------------------------------------------------------------- 宏观日历（G6，零外呼先验规则）

def _nth_weekday(year: int, month: int, weekday: int, nth: int = 1) -> date:
    """某月第 nth 个周 X（weekday: 0=周一…4=周五）。"""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (nth - 1))


def _us_dst(d: date) -> bool:
    """美国夏令时（3月第2个周日 ~ 11月第1个周日）粗判定——只用于非农公布时刻文案。"""
    dst_start = _nth_weekday(d.year, 3, 6, 2)  # 周日=6
    dst_end = _nth_weekday(d.year, 11, 6, 1)
    return dst_start <= d < dst_end


def macro_calendar_note(today: date) -> str | None:
    """非农日历提醒（docs/nfp-ashare-validation.md 决议「日历提醒类标注 ✓ 可做」）。

    先验规则：非农 = 每月第一个周五（北京时间夏令时 20:30 / 冬令时 21:30 公布）。
    返回简报提示文案；与规则无关的日子返回 None（显式缺失，不凑话）。
    特殊情形（节假日顺延公布）判不出 → None，属设计内缺失。
    """
    nfp_day = _nth_weekday(today.year, today.month, 4, 1)
    if today == nfp_day:
        t = "20:30" if _us_dst(today) else "21:30"
        return (
            f"宏观日历：今晚 {t} 美国{today.month}月非农公布。历史回测（10年116期）："
            "意外强→次日A股偏弱+波动放大（深A日内-0.28%/振幅+24~33%，t 不显著，提示级）；"
            "方向由意外差决定，公布前不给确定性结论。"
        )
    if nfp_day < today <= nfp_day + timedelta(days=3):
        # 周五公布 → +3 = 下周一（首个 A 股定价日）；周六/周日简报不生成，窗口覆盖防顺延
        return (
            "宏观日历：非农已公布，最近的一个 A 股交易日为其定价日，波动或放大"
            "（历史回测：非农次日振幅 +24~33%，提示级）。复盘 A 股实际反应与意外差方向是否一致。"
        )
    return None
