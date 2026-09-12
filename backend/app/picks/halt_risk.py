"""停牌核查 / 异常波动风险评估（docs/summary/stock-strategy.md 第一批规则）。

为什么需要这一维：停牌核查 = 资金锁死 1~5 个交易日，**期间不可申报、不可撤单**，
所有止损纪律在停牌期完全失效。向上最多一个涨停（+10%），向下是连续跌停叠加
无法止损——收益结构严重不对称，期望值为负。而在这套规则落地前，系统对
"异动 / 停牌 / 立案"三个维度**零覆盖**：退潮期门控照样输出清单且不带任何风险标注。

规则基线（2026 年现行，上交所交易规则 6.10 / 6.11；深交所同源）：
- 普通异常波动（只发公告，**不停牌**）：3 日累计偏离值 主板 ±20% / ST ±20% /
  创业板科创板 ±30% / 北交所 ±40%
- 严重异常波动（**强制停牌核查**）：10 日累计偏离值 ≥+100%（或 ≤−50%）、
  30 日 ≥+200%（或 ≤−70%）、10 日内同向异动次数 主板 4 次 / 双创 3 次

📌 2026-07-06 并轨（沪深交易所 2026-04-24 修订《交易规则》，7/6 生效）：
主板风险警示股（ST/*ST）**涨跌幅 5%→10%**，**异动披露阈值 ±12%→±20%**，
与主板普通股完全拉齐；创业板/科创板 ST 维持 20%、北交所维持 30%（不降档）。
本模块此前按旧规则写死 ST=5%/12%，已于 2026-09-11 更正。

口径选择（重要，2026-09-02 诊断结论）：
**连板高度在这里按「收盘涨停连续天数」算，不按涨停池 lbc。** 两者不是一回事：
- 收盘涨停连续天数 = 监管口径。交易所算偏离值只看**收盘价的涨跌幅**，不看盘中
  炸没炸板。用于停牌风险必须是这个口径。
- 涨停池 lbc = 封板质量口径。东财把盘中反复炸板的票剔出涨停池（如 09-02 新赛股份
  炸板 6 次、收盘价确实等于涨停价，但 lbc 不计、被归入炸板池）。用于梯队/情绪
  判断它是合理的，用于停牌风险则会漏判——**正是它让系统把 5 连板的新赛判成
  「非涨停」**。
两个口径服务的目的不同，不需要统一，但必须显式区分、各归各用。

红线 3：本模块只做风险识别与标注，不构成买卖建议。
"""

from __future__ import annotations

import math
import re
from typing import Any

#: 各板块的基准指数（偏离值 = 个股区间涨跌幅 − 基准指数区间涨跌幅）。
#: 代码为腾讯格式（沪市需 sh 前缀，深市无前缀默认深市），已实测可取。
BENCHMARK_INDEX: dict[str, str] = {
    "sh_main": "sh000001",  # 沪市主板 → 上证指数
    "sz_main": "399107",    # 深市主板 → 深证A指（官方口径）
    "gem": "399102",        # 创业板 → 创业板综指（官方口径）
    "star": "sh000688",     # 科创板 → 科创50（官方口径）
    "bse": "899050",        # 北交所 → 北证50
}

#: 普通异常波动阈值（3 日累计偏离值，绝对值）。
#: "st" 仅指**沪深主板 ST**——双创/北交所的 ST 股票归各自板块口径
#: （代码前缀优先，见 board_of）。
#: ⚠️ 2026-07-06 起主板 ST 异动披露阈值已由 ±12% 调整为 **±20%**（与涨跌幅
#: 5%→10% 同步并轨），故 "st" 与 sh_main/sz_main 取值一致。保留独立键是为了
#: 让「ST 与主板普通股并轨」这件事在代码里显式可见，而非静默合并掉。
ABNORMAL_3D_THRESHOLD: dict[str, float] = {
    "sh_main": 20.0,
    "sz_main": 20.0,
    "gem": 30.0,
    "star": 30.0,
    "bse": 40.0,
    "st": 20.0,
}

#: 严重异常波动阈值（10 日 / 30 日累计偏离值）
SEVERE_10D = 100.0
SEVERE_30D = 200.0

#: 能算出 3 日偏离值的最少日 K 根数（起点收盘 + 3 个交易日）
MIN_BARS_FOR_DEV = 4

#: 第一批规则参数。
#: 数值仍为经验初值，但**已于 2026-09-11 用近 6 个月 + 全样本 10 年实测核验**
#: （`scripts/verify_halt_risk.py`）——核验结论与「为什么不按均值调参」见模块末尾
#: 「校准结论」段。**不要仅凭均值显著性把这些数字改小或删掉。**
RED_DEV_10D = 80.0        # R1：10 日偏离 ≥80% 硬排除（距 100% 红线约剩 2 个板）
YELLOW_DEV_10D_LO = 50.0  # Y3：10 日偏离 50%~80%
PENALTY_Y1 = 6.0          # Y1：3 日偏离已达普通异动
PENALTY_Y3 = 8.0          # Y3：10 日偏离 50%~80%
PENALTY_BOARDS: dict[int, float] = {4: 5.0, 5: 10.0}  # Y2：≥6 板统一 15 分
PENALTY_BOARDS_MAX = 15.0
POSITION_FACTOR_P1 = 0.5      # P1：连板 ≥3 → 仓位上限减半
POSITION_FACTOR_P2 = 1.0 / 3  # P2：命中 Y1/Y3 → 仓位上限 1/3


#: ST 名称判定：ST / *ST / SST / S*ST 前缀，或名称中独立成词的 ST
#: （部分数据源把标记放尾部，如「国华网安 ST」）。子串匹配会误命中
#: 无关字母组合，必须按词边界。
_ST_NAME_RE = re.compile(r"(?:^|[^A-Z])\*?ST(?:$|[^A-Z])")


def _is_st(name: str | None) -> bool:
    nm = (name or "").strip().upper()
    return nm.startswith(("ST", "*ST", "S*ST", "SST")) or bool(_ST_NAME_RE.search(nm))


def board_of(symbol: str, name: str | None = None) -> str:
    """按代码与名称判定板块（决定涨限与基准指数）。

    **代码前缀优先于 ST 名称判定**（2026-09-08 修复）：交易所现行规则下
    创业板/科创板全部股票（含 ST）涨跌幅 20%、北交所 30%，只有沪深主板
    ST 适用不同口径。旧版把 ST 检查放在最前，300688 这类双创 ST
    会被按 5% 涨限漏判连板、按 12% 阈值误判异动。
    （2026-09-11 注：主板 ST 已于 2026-07-06 并轨至 10%/20%，"st" 键现在
    仅用于选择基准指数，不再改变涨限与异动阈值的数值。）
    """
    sym = str(symbol or "")
    if sym.startswith(("300", "301")):
        return "gem"
    if sym.startswith(("688", "689")):
        return "star"
    if sym.startswith(("8", "4", "920")):
        return "bse"
    if _is_st(name):
        return "st"
    if sym.startswith("6"):
        return "sh_main"
    return "sz_main"


def limit_pct(board: str) -> float:
    """涨跌停幅度（%）。

    ⚠️ 2026-07-06 起沪深主板 ST 由 5% 放宽至 10%（与主板普通股并轨）。
    "st" 与 sh_main / sz_main 因此同为 10.0；保留独立键是让 board_of 的
    板块语义（影响基准指数选择）继续可表达。
    """
    return {
        "st": 10.0,
        "gem": 20.0,
        "star": 20.0,
        "bse": 30.0,
        "sh_main": 10.0,
        "sz_main": 10.0,
    }[board]


def benchmark_symbol(board: str, symbol: str = "") -> str:
    """该板块的基准指数代码。

    ⚠️ ST 不是独立市场：`board_of` 返回 "st" 只说明涨限与异动阈值不同，
    基准指数仍要按它所属的市场取（沪市 ST → 上证指数，深市 ST → 深证A指）。
    早期版本直接 `BENCHMARK_INDEX[board]`，ST 股会 KeyError 崩溃。
    """
    if board == "st":
        board = "sh_main" if str(symbol).startswith("6") else "sz_main"
    return BENCHMARK_INDEX[board]


# ---------- 指标计算（纯函数，可直接单测） ----------

def _pct_of(bars: list[dict[str, Any]], i: int) -> float | None:
    """第 i 根相对前一根的涨跌幅（%）。

    ⚠️ **不能只认 `change_pct` 字段**：实测腾讯源当日 bar 的 change_pct 为 None
    （`ts=2026-09-02, close=6.73, change_pct: null`），且同一接口在不同时点会
    因熔断切源而时有时无。只认该字段 → 连板数恒为 0、全市场"零风险"的假象。
    收盘价是所有源都有的，缺失时自己算。
    """
    pct = bars[i].get("change_pct")
    if pct is not None:
        return float(pct)
    if i <= 0:
        return None
    prev, cur = bars[i - 1].get("close"), bars[i].get("close")
    if not prev or not cur:
        return None
    return (float(cur) / float(prev) - 1) * 100


def consecutive_limit_up_days(bars: list[dict[str, Any]], limit: float, tol: float = 0.6) -> int:
    """收盘涨停连续天数（**监管口径**：只看收盘涨跌幅，不看盘中炸板）。

    :param tol: 容差（百分点）。涨停价是四舍五入到分的，实际涨幅常见
        9.97%~10.08%（如 5.05→5.56 = +10.099%），不设容差会漏判。
    """
    n = 0
    for i in range(len(bars or []) - 1, -1, -1):
        pct = _pct_of(bars, i)
        if pct is None or pct < limit - tol:
            break
        n += 1
    return n


def interval_pct(bars: list[dict[str, Any]], days: int) -> float | None:
    """区间涨跌幅（%，复利）。bars 按时间升序，末尾为最新。

    需要 days+1 根（起点是 days 个交易日前的收盘价）。数据不足返回 None——
    **不臆造、不用更短的区间冒充**（短区间偏离值必然偏小，会系统性漏判）。
    """
    if not bars or len(bars) < days + 1:
        return None
    start = bars[-days - 1].get("close")
    end = bars[-1].get("close")
    if not start or not end:
        return None
    return (float(end) / float(start) - 1) * 100


def deviation_pct(
    stock_bars: list[dict[str, Any]],
    index_bars: list[dict[str, Any]],
    days: int,
) -> float | None:
    """N 日累计偏离值（百分点）= 个股区间涨跌幅 − 基准指数区间涨跌幅。

    这是监管口径的核心：**必须减指数**。指数大涨时个股涨 30% 也可能不触发异动；
    反之指数大跌时个股小涨也可能越线。两侧任一侧数据不足 → 返回 None。
    """
    s = interval_pct(stock_bars, days)
    if s is None:
        return None
    i = interval_pct(index_bars, days)
    if i is None:
        return None
    return s - i


# ---------- 规则评估 ----------

def assess(
    *,
    symbol: str,
    name: str | None = None,
    bars: list[dict[str, Any]] | None = None,
    index_bars: list[dict[str, Any]] | None = None,
    suspended: bool = False,
) -> dict[str, Any]:
    """评估单只标的的停牌核查 / 异动风险。

    :param bars: 个股日 K（升序，末尾最新），至少 31 根才能算全 30 日偏离
    :param index_bars: 对应板块基准指数日 K。缺失时偏离值相关规则全部降级为
        「不可评」并显式标注——**不拿个股涨幅冒充偏离值**（那会系统性高估风险）
    :param suspended: 是否停牌中（系统已有 trading_status）

    :return: 见下方字段说明；`available=False` 表示数据不足，规则未生效。
    """
    board = board_of(symbol, name)
    bars = list(bars or [])
    idx = list(index_bars or [])

    lim = limit_pct(board)
    boards = consecutive_limit_up_days(bars, lim)
    dev_3 = deviation_pct(bars, idx, 3)
    dev_10 = deviation_pct(bars, idx, 10)
    dev_30 = deviation_pct(bars, idx, 30)

    red: list[str] = []
    yellow: list[dict[str, Any]] = []
    notes: list[str] = []
    penalty = 0.0
    position_factor = 1.0

    #: 能算出 3 日偏离值的最少数据（起点收盘 + 3 个交易日）
    missing: list[str] = []
    if len(bars) < MIN_BARS_FOR_DEV:
        missing.append("个股日K不足")
    if len(idx) < MIN_BARS_FOR_DEV:
        missing.append("基准指数日K不足")

    # 🔴 红线：硬排除
    # 注：监管阈值在下跌方向同样存在（10 日偏离 ≤−50%、30 日 ≤−70% 也会触发
    # 严重异常波动），但**本模块只判上涨侧**。选股场景的标的是待买入的强势股，
    # 下跌侧停牌核查不发生在这个池子里；若将来用于持仓监控，必须补上这两个下界。
    if suspended:
        red.append("R3 当前停牌")
    if dev_10 is not None and dev_10 >= RED_DEV_10D:
        # R1 与 R2 的 10 日档是包含关系：≥100% 措辞升级为「已触发」
        if dev_10 >= SEVERE_10D:
            red.append(f"R2 10 日偏离 {dev_10:.1f}% ≥100%（已触发严重异常波动）")
        else:
            # 剩余空间按该股涨限折算成"还差几个板"——写死"约 2 个板"会在
            # 已贴线（如偏离 99.9%）时严重误导。
            gap = SEVERE_10D - dev_10
            tail = (
                f"（已贴 100% 红线，仅剩 {gap:.1f}pct）"
                if gap <= 5
                else f"（距 100% 红线约 {max(1, math.ceil(gap / lim))} 个板）"
            )
            red.append(f"R1 10 日偏离 {dev_10:.1f}% ≥80%{tail}")
    elif dev_30 is not None and dev_30 >= SEVERE_30D:
        red.append(f"R2 30 日偏离 {dev_30:.1f}% ≥200%（已触发严重异常波动）")

    # 🟡 黄线：降权（红线已排除的不再重复扣分，避免分数被重复惩罚）
    if not red:
        threshold_3d = ABNORMAL_3D_THRESHOLD[board]
        if dev_3 is not None and dev_3 >= threshold_3d:
            yellow.append({"code": "Y1", "label": f"3 日偏离 {dev_3:.1f}% ≥{threshold_3d:.0f}%（已触发普通异动公告）", "penalty": PENALTY_Y1})
            penalty += PENALTY_Y1
        if boards >= 4:
            p = PENALTY_BOARDS.get(boards, PENALTY_BOARDS_MAX)
            yellow.append({"code": "Y2", "label": f"{boards} 连板（高度风险）", "penalty": p})
            penalty += p
        if dev_10 is not None and YELLOW_DEV_10D_LO <= dev_10 < RED_DEV_10D:
            yellow.append({"code": "Y3", "label": f"10 日偏离 {dev_10:.1f}%（50%~80% 区间）", "penalty": PENALTY_Y3})
            penalty += PENALTY_Y3

    # 💰 仓位约束（与扣分独立，红线票已被排除故不计算）
    if not red:
        if boards >= 3:
            position_factor = min(position_factor, POSITION_FACTOR_P1)
            notes.append("P1 连板 ≥3 板 → 单票仓位上限减半")
        if any(y["code"] in ("Y1", "Y3") for y in yellow):
            position_factor = min(position_factor, POSITION_FACTOR_P2)
            notes.append("P2 命中异动类黄线 → 单票仓位上限 1/3")

    if missing:
        notes.append("数据不足（" + "、".join(missing) + "）→ 偏离值类规则未生效，不臆造")

    available = len(bars) >= MIN_BARS_FOR_DEV and len(idx) >= MIN_BARS_FOR_DEV
    return {
        "symbol": symbol,
        "board": board,
        "benchmark": benchmark_symbol(board, symbol),
        "limit_pct": lim,
        "boards": boards,
        "dev_3d": None if dev_3 is None else round(dev_3, 2),
        "dev_10d": None if dev_10 is None else round(dev_10, 2),
        "dev_30d": None if dev_30 is None else round(dev_30, 2),
        "red_lines": red,
        "yellow_lines": yellow,
        "penalty": round(penalty, 1),
        "position_factor": round(position_factor, 3),
        "notes": notes,
        "available": available,
    }


def veto_reasons(result: dict[str, Any]) -> list[str]:
    """红线 → 选股 veto 文案（进 `synthesize` 的 vetoes，综合分 ×0.4 并显式记录）。

    只回核心结论——具体数值在 `red_lines` 里，veto 文案会被拼成
    「一票否决：xxx（综合分 ×0.4）」，再塞数字读起来像验证码。
    """
    out = []
    for r in result.get("red_lines") or []:
        code = r.split(" ", 1)[0]
        out.append(
            {
                "R1": "逼近严重异常波动，停牌核查风险高",
                "R2": "已触发严重异常波动，面临强制停牌核查",
                "R3": "当前停牌，停牌期不可申报不可撤单",
            }.get(code, "停牌核查风险")
        )
    return out


def risk_labels(result: dict[str, Any]) -> list[str]:
    """供卡片展示的风险标注（含黄线）。"""
    labels = [f"🔴 {r}" for r in result.get("red_lines") or []]
    labels += [f"🟡 {y['label']}" for y in result.get("yellow_lines") or []]
    return labels


# =====================================================================
# 校准结论（P1-22，2026-09-11）
# =====================================================================
# 脚本：`scripts/verify_halt_risk.py`（**import 本模块的常量与 `assess`**，故核验
# 口径 = 线上口径；抽样 200 行用生产 `assess()` 复算，0 不一致）。
# 样本：主窗口 2026-03-13 ~ 2026-09-03（120 交易日，池内 49,426 行）；
#       全样本 2016-12-05 ~ 2026-09-03（535,880 行）。
#
# 【为什么不能按「均值显著性」调参】——这是本项最重要的结论。
# 表面上看，红线组（dev10 ≥ 80）的前瞻超额均值 ≈ 0，甚至不显著：
#   · 收盘口径 h=1：红线 −0.080%（t=−0.20，不显著）vs 对照 +0.164%
#   · 全池等权组合剔除红线：仅 +0.0016pp/日（t=+0.475，可忽略）
#   · 最强 5 只代理（近似 picks 容量）剔除红线后：累计 +165.38% → +184.08%，
#     但**最大回撤由 −21.59% 恶化到 −28.59%**
# 若只看均值，结论会是「剔除红线没用、甚至更差」⇒ 应当删掉这条规则。
# **但均值不是这里的目标函数。** 红线的正当性来自**收益结构的安全不对称性**：
# 红线票已处 80%+ 偏离、距 100% 强制停牌红线仅剩 1~2 个板；一旦停牌，
# 资金锁死 1~5 个交易日、**期间不可申报不可撤单**（所有止损纪律失效），
# 上行最多 1 个涨停（+10%）、下行是连续跌停叠加无法止损 ⇒ 期望结构不对称为负。
# 这类「尾部风险 / 不可交易性」在均值与 t 值上**看不见**，必须按制度约束排除。
# 故 RED_DEV_10D 采用「硬排除」而非「扣分」——扣分意味着仍可能被选中。
#
# 【分项校准结论】
# 1. Y3（dev10 ∈ [50, 80)）——**全表最稳健的信号，建议保留甚至加强**
#    分年度稳定性（全样本，命中组 − 对照组的超额差，负 = 命中组更差）：
#      ex5 口径（h=5）：命中组更差 **10/11 年**（仅 2016 +0.52 例外）
#      ex1 口径（h=1）：5/11 年
#    ⇒ 这是唯一在长样本上方向高度一致的分组，且「差」的方向与扣分一致。
# 2. Y2（连板扣分 PENALTY_BOARDS）——**方向部分成立，但依据不是"连板必跌"**
#    · 收盘口径（T 日收盘买入，**对涨停组系统性高估**，因为当日封板买不到）：
#      boards 1→≥6 的超额**单调递增**（+1.32%/+2.20%/+3.03%/+3.17%/+3.86%/+4.36%，
#      t 达 +19.9~+3.6）⇒ **表面上推翻「连板该扣分」**。
#    · **可成交口径（T+1 开盘买入）结论翻转**：boards 1~5 全部转负
#      （−0.151%/−0.312%/−0.539%/−0.560%/−0.334%，t 为负），仅 boards≥6 微正 +0.057%。
#      ⇒ **连板溢价是"收盘价幻觉"，在可成交价格上并不存在。**
#    · 分年度：boards≥4 在 ex5 口径仅 2/10 年、ex1 口径 0/10 年命中组更差
#      ⇒ 连板扣分**方向在长样本上站不住**，保留它是出于「连板 = 已累积涨幅」的
#      风险对称性考量（与红线同源），**不是**因为回测显示连板股会跌。
#    ⚠️ 因此 PENALTY_BOARDS 的数值（5/10/15）**属经验刻度**，只保证「单调递增、
#    有上限、不越权到硬排除」，不代表已找到最优值。若要改，请针对**可成交口径**
#    重跑本脚本，不要采信收盘口径。
# 3. Y1（3 日偏离达普通异动，PENALTY_Y1）——方向与 Y3 同源但更弱
#    收盘口径 h=1 命中组 +0.974%（t=+7.36，正）；可成交口径转 +0.359%（t=+3.18）。
#    ⇒ 与连板同理：正向溢价来自不可成交的收盘价。保留扣分，理由同 Y2。
# 4. 触发频率（主窗口池内）——**没有一条规则稀有到"调不调无所谓"**：
#    红线 0.77%（日均 3.2 只，111/120 日有命中）、Y3 3.65%、Y1 5.76%、
#    Y2 boards≥4 0.41%（101/120 日）。⇒ 参数确实有实际作用面，值得定稿。
# 5. 已知边界（勿超出结论范围引用）：
#    · marketdb **无名称历史** ⇒ ST 无法历史还原（2026-07-06 并轨前主板 ST
#      涨限 5%，此前连板数被**低估**）；
#    · 用 rn ≥ 31（上市满 31 交易日）规避新股无涨跌幅限制期；
#    · 停牌跨期偏离值失真——与生产 `assess()` **同源同限**，非脚本额外缺陷；
#    · 池定义刻意用 POOL_DEV10=15 远低于待标定阈值 50，避免「池怎么定义」
#      把「阈值该定哪」的答案预设掉。
#
# 复查方式：cd backend && .venv/bin/python scripts/verify_halt_risk.py
# （全量约 10 分钟；结果应在 ⑤ 段显示「抽样 200 行，不一致 0 行」才算口径可信）
# =====================================================================
