"""P1-32 实证核验：ENSO（ONI）气候相位 → A 股行业超额，是否真的存在关系。

⚠️ 本脚本 **import 生产模块** `app.market.climate`（`parse_oni` / `classify` /
`season_end`），因此「核验时的相位判定」与「线上跑的相位判定」不可能漂移
（[[KB-ENG-44]]）。

要回答的问题（KB-DEC-018：**不得采信直觉链**；KB-DEC-019 反固化条款）：
  ① `chains.py` 里「厄尔尼诺 → 磷化工/化肥/农业种植/电力/电网」这张人工表，
     在真实数据上到底有没有可辨识的关系？
  ② 若有，是**同期**（同一月份共同反映）还是**前瞻**（先知道气候相位、再看下月表现）？
     只有前瞻口径才配得上「提前」二字——这正是 P1-32 立项的理由。
  ③ 分年度是否方向一致（廉价但有效的稳健性检验）。

口径（三处易错点）：
  · **只测超额** = 行业月收益 − 上证月收益。用绝对涨跌测的是 beta，不是传导；
  · **月频**。ONI 是重叠三月季、月更，日频粒度没有意义（会伪造成千上万"独立"样本）；
  · **两套时点**（与 `verify_overnight_bias.py` / `verify_commodity_chain.py` 同族）：

    | 口径 | asof 取 | 含义 |
    |---|---|---|
    | **同期 h=0** | 当月**末** | 该月的气候相位（含当月季值）；**含前视**，仅作参照 |
    | **前瞻 h=1** | 当月**初** | 月初**已发布**的相位 → 当月行业超额；**这才是可用口径** |

  · 另测 h=2/h=3（月初相位 → 后第 2/3 个月超额），看是否有更长的滞后。

用法：cd backend && .venv/bin/python scripts/verify_climate_chain.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math

import akshare as ak
import pandas as pd

from app.core.bjtime import beijing_today
from app.market import climate as cl

#: 申万一级行业：覆盖 chains.py 人工表点名的题材所属的上游行业
#: （磷化工/化肥 → 基础化工；农业种植 → 农林牧渔；绿色电力/智能电网 → 公用事业）
SW_SECTORS: tuple[tuple[str, str], ...] = (
    ("801010", "农林牧渔"),
    ("801030", "基础化工"),
    ("801160", "公用事业"),
    ("801730", "电力设备"),
    ("801120", "食品饮料"),
)

ONI_URL = cl.ONI_URL

_HDR = "=" * 78


def _p(msg: str = "") -> None:
    print(msg)


def fetch_oni() -> list[cl.OniPoint]:
    import httpx

    with httpx.Client(trust_env=False, timeout=20.0) as c:
        r = c.get(ONI_URL)
        r.raise_for_status()
        text = r.text
    pts = cl.parse_oni(text)
    _p(f"ONI 原始行解析：{len(pts)} 季（{pts[0].year}{pts[0].season} ~ {pts[-1].year}{pts[-1].season}）")
    return pts


def monthly_series(points: list[cl.OniPoint], asof_mode: str) -> pd.Series:
    """逐月相位序列（索引 = 月初 Timestamp）。

    `asof_mode="h0"` → asof 取**当月末**（含当月季值，参照口径）；
    `asof_mode="lead"` → asof 取**月初**（只含月初已发布的季，可用口径）。
    """
    if not points:
        return pd.Series(dtype=object)
    lo = pd.Timestamp(year=points[0].year, month=1, day=1)
    hi = pd.Timestamp(year=points[-1].year, month=12, day=1)
    idx = pd.date_range(lo, hi, freq="MS")
    out: dict[pd.Timestamp, str | None] = {}
    for ts in idx:
        if asof_mode == "h0":
            asof = (ts + pd.offsets.MonthEnd(0)).date()
        else:
            asof = ts.date()
        res = cl.classify(points, asof=asof)
        out[ts] = res["state"]
    return pd.Series(out, dtype=object)


def sw_monthly(code: str) -> pd.Series:
    df = ak.index_hist_sw(symbol=code, period="day")
    s = pd.Series(
        pd.to_numeric(df["收盘"], errors="coerce").values,
        index=pd.to_datetime(df["日期"]),
    ).dropna().sort_index()
    m = s.resample("ME").last()
    return m.pct_change() * 100.0


def sh_monthly() -> pd.Series:
    df = ak.stock_zh_index_daily(symbol="sh000001")
    s = pd.Series(
        pd.to_numeric(df["close"], errors="coerce").values,
        index=pd.to_datetime(df["date"]),
    ).dropna().sort_index()
    m = s.resample("ME").last()
    return m.pct_change() * 100.0


def welch_t(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    denom = math.sqrt(va / len(a) + vb / len(b))
    if not denom or math.isnan(denom):
        return float("nan")
    return (a.mean() - b.mean()) / denom


def _align(state: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """把「月初索引」的相位序列对齐到「月末索引」的收益序列。

    ⚠️ 这是本脚本第一个版本静默失效的地方：`state.reindex(ex.index)` 因两边
    索引一个在月初（MS）、一个在月末（ME）而**全变 NaN** → 所有月份都记「未判定」，
    检验全部作废却不报错。凡跨频率对齐，一律显式换算到同一锚点（此处统一到月初），
    并用 `[自检]` 的「未判定月数」把这类失效暴露出来。
    """
    key = target_index.to_period("M").to_timestamp()
    return pd.Series(state.reindex(key).values, index=target_index)


def analyse(ex: pd.Series, state: pd.Series, *, label: str, h: int) -> dict:
    """`state`（月初或月末相位）→ 前 h 个月的行业超额。

    **`ex.shift(-h)` 再按 state 切片**（不是 `ex[state==…].shift(-h)`）——
    后者取的是「下一个**满足条件的**月份」，会把相位聚集效应算成传导效应
    （`verify_commodity_chain.py` 踩过：t 从 ~2 虚增到 11~18）。
    """
    fwd = ex.shift(-h)
    st = _align(state, ex.index)
    g_up = fwd[(st == "el_nino") & fwd.notna()]
    g_dn = fwd[(st == "la_nina") & fwd.notna()]
    g_ne = fwd[(st == "neutral") & fwd.notna()]
    na = fwd[st.isna()].notna().sum()

    t = welch_t(g_up, g_dn) if len(g_up) >= 3 and len(g_dn) >= 3 else float("nan")
    t_vs_ne = welch_t(g_up, g_ne) if len(g_up) >= 3 and len(g_ne) >= 3 else float("nan")
    # 自检行：分组计数 + 中位数（专抓「全判同一档」这类静默失效）
    _p(
        f"  [自检] {label} h={h}: n(厄尔尼诺)={len(g_up)} 中位"
        f"={g_up.median():+.3f} | n(拉尼娜)={len(g_dn)} 中位={g_dn.median():+.3f} | "
        f"n(中性)={len(g_ne)} 中位={g_ne.median():+.3f}"
        + (f" | 未判定月={na}" if na else "")
    )
    return {
        "label": label,
        "h": h,
        "n_el": len(g_up),
        "mean_el": g_up.mean() if len(g_up) else float("nan"),
        "med_el": g_up.median() if len(g_up) else float("nan"),
        "n_la": len(g_dn),
        "mean_la": g_dn.mean() if len(g_dn) else float("nan"),
        "med_la": g_dn.median() if len(g_dn) else float("nan"),
        "n_ne": len(g_ne),
        "mean_ne": g_ne.mean() if len(g_ne) else float("nan"),
        "t_el_la": t,
        "t_el_ne": t_vs_ne,
    }


def yearly_direction(ex: pd.Series, state: pd.Series, *, h: int) -> tuple[int, int]:
    """分年度一致性：逐年比较「厄尔尼诺月均值 − 中性月均值」的符号。"""
    fwd = ex.shift(-h)
    st = _align(state, ex.index)
    same = total = 0
    for _yr, grp in fwd.groupby(fwd.index.year):
        s = st.reindex(grp.index)
        a = grp[(s == "el_nino") & grp.notna()]
        b = grp[(s == "neutral") & grp.notna()]
        if len(a) < 2 or len(b) < 2:
            continue
        total += 1
        if (a.mean() - b.mean()) > 0:
            same += 1
    return same, total


def main() -> None:
    pts = fetch_oni()
    _p(f"ONI 最新一季：{pts[-1].year} {pts[-1].season} ANOM={pts[-1].anom:+.2f}"
       f"（季末 {cl.season_end(pts[-1]).isoformat()}）")

    # asof 走权威时钟：`pd.Timestamp.today()` 按进程时区取日，非 CST 机器差一天
    today = beijing_today()
    prod = cl.classify(pts, asof=today)
    _p(
        f"线上 classify(asof={today}) → state={prod['state']} alert={prod['alert']} "
        f"strength={prod['strength']} consecutive={prod['consecutive']} "
        f"peak={prod['peak_abs']}"
    )
    if prod["unjudged_reason"]:
        _p(f"  unjudged_reason={prod['unjudged_reason']}")
    _p(f"  候选题材 {len(prod['candidate_links'])} 行："
       + "、".join(r["target"] for r in prod["candidate_links"]))

    mkt = sh_monthly()
    state_h0 = monthly_series(pts, "h0")
    state_lead = monthly_series(pts, "lead")
    dist = state_lead.value_counts(dropna=False).to_dict()
    _p(f"逐月相位分布（月初口径，全历史）：{dist}")
    if not dist.get("el_nino"):
        _p("⚠️ 月初口径下厄尔尼诺样本为 0 —— 后续检验无意义，终止")
        return

    rows: list[dict] = []
    for code, name in SW_SECTORS:
        try:
            sw = sw_monthly(code)
        except Exception as exc:  # noqa: BLE001  单行业失败不影响其余
            _p(f"⚠️ {name}({code}) 取数失败：{exc}")
            continue
        ex = (sw - mkt).dropna()
        _p(f"\n{_HDR}\n{name}（申万 {code}）  月超额样本 {len(ex)} 个月"
           f"  {ex.index[0].date()} ~ {ex.index[-1].date()}")
        # 参照口径（含前视）
        _p(" — 同期（月末相位，含前视，仅参照）")
        analyse(ex, state_h0, label=name, h=0)
        _p(" — 前瞻（月初相位 → 当月，**可用口径**）")
        rows.append(analyse(ex, state_lead, label=name, h=1))
        for h in (2, 3):
            rows.append(analyse(ex, state_lead, label=name, h=h))

    _p(f"\n{_HDR}\n汇总：月初相位 → 行业月超额（可用口径）")
    _p(f"{'行业':<8}{'h':>3}{'n厄':>6}{'厄均':>9}{'n拉':>6}{'拉均':>9}"
       f"{'n中':>6}{'中均':>9}{'t(厄-拉)':>10}{'t(厄-中)':>10}")
    for r in rows:
        _p(
            f"{r['label']:<8}{r['h']:>3}{r['n_el']:>6}{r['mean_el']:>9.3f}"
            f"{r['n_la']:>6}{r['mean_la']:>9.3f}{r['n_ne']:>6}{r['mean_ne']:>9.3f}"
            f"{r['t_el_la']:>10.2f}{r['t_el_ne']:>10.2f}"
        )

    _p(f"\n{_HDR}\n分年度一致性（厄尔尼诺月均 − 中性月均 > 0 的年数；h=1）")
    for code, name in SW_SECTORS:
        try:
            sw = sw_monthly(code)
        except Exception:  # noqa: BLE001
            continue
        ex = (sw - mkt).dropna()
        same, total = yearly_direction(ex, state_lead, h=1)
        _p(f"  {name:<8} {same}/{total}" + ("  （样本年不足）" if total < 5 else ""))

    _p(
        "\n判读提示：月频、厄尔尼诺事件本就稀少（1950 至今约十余次），"
        "**样本量天然不足以支撑强结论**；本脚本的输出用于回答"
        "「这张人工表配不配叫已验证」，而不是给出一条可交易的择时规则。"
    )


if __name__ == "__main__":
    main()
