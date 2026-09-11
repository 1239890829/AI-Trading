"""P1-33 实证核验：大宗商品价格 → 板块「超额」传导链是否成立。

⚠️ 本脚本 **import `app.market.commodity_chain` 的 `SPECS` / `change_of`**，
因此「核验得到的数字」与「线上实际跑的规则」不可能漂移（[[KB-ENG-44]]）。

要回答的问题（KB-DEC-018：**不得采信直觉链**，「油价↑→石化涨」是外推假说）：
  ① 商品日变化与行业**超额收益**（相对上证）是否有稳定的同期/前瞻关系？
  ② 按商品自身死区分组（涨破 / 跌破）后，行业超额是否有可辨识的差？
  ③ 这个差在分年度上是否方向一致（廉价但有效的稳健性检验）？
  ④ 直接调线上 `evaluate()` 走一遍，看规则今天给出什么。

口径（三处易错点，均已收口）：
  · **超额** = 行业收益 − 上证收益（同日），不用行业绝对涨跌 —— 否则测的是 beta 不是传导；
  · **前瞻** h 表示「商品 T 日变化 → 行业 T+h 日超额」，即用 `ex.shift(-h)`；
    盘前简报可用的口径正是 **h=1**（昨日商品收盘 → 今日板块）；
  · **死区**取各商品自身日变化绝对值中位数（实测），不统一百分比。

用法：cd backend && .venv/bin/python scripts/verify_commodity_chain.py
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

from app.market import commodity_chain as cc

HORIZONS = (0, 1, 2, 3)
# 长滞后（累积超额口径）：传导若走「商品价 → 业绩 → 板块」路径，应在周级才显形
LONG_HORIZONS = (5, 10, 20)
# 同一行业的**替代候选**（研究用，不进生产）：用于验证「SPECS 里挑的那条是不是最强」
EXTRA: tuple[tuple[str, str, str, str], ...] = (
    ("AL0", "沪铝", "801050", "有色金属"),
    ("ZN0", "沪锌", "801050", "有色金属"),
    ("AU0", "沪金", "801050", "有色金属"),
    ("AG0", "沪银", "801050", "有色金属"),
    ("NI0", "沪镍", "801050", "有色金属"),
    ("I0", "铁矿石", "801040", "钢铁"),
    ("M0", "豆粕", "801010", "农林牧渔"),
    ("C0", "玉米", "801010", "农林牧渔"),
    ("SR0", "白糖", "801120", "食品饮料"),
    ("SA0", "纯碱", "801710", "建筑材料"),
    ("TA0", "PTA", "801960", "石油石化"),
    ("PP0", "聚丙烯", "801960", "石油石化"),
)


# ------------------------------------------------------------------ 取数

def commodity(code: str) -> pd.Series:
    df = ak.futures_zh_daily_sina(symbol=code)
    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").values,
                  index=pd.to_datetime(df["date"]))
    return s.dropna().sort_index()


def sw_index(code: str) -> pd.Series:
    df = ak.index_hist_sw(symbol=code, period="day")
    s = pd.Series(pd.to_numeric(df["收盘"], errors="coerce").values,
                  index=pd.to_datetime(df["日期"]))
    return s.dropna().sort_index()


def sh_index() -> pd.Series:
    df = ak.stock_zh_index_daily(symbol="sh000001")
    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").values,
                  index=pd.to_datetime(df["date"]))
    return s.dropna().sort_index()


# ------------------------------------------------------------------ 统计

def _pct(s: pd.Series) -> pd.Series:
    return (s / s.shift(1) - 1.0) * 100.0


def welch_t(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    denom = math.sqrt(va / len(a) + vb / len(b))
    if denom == 0:
        return float("nan")
    return (a.mean() - b.mean()) / denom


def build_frame(com: pd.Series, sw: pd.Series, mkt: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({
        "r_c": _pct(com),
        "r_s": _pct(sw),
        "r_m": _pct(mkt),
    }).dropna()
    df["ex"] = df["r_s"] - df["r_m"]
    return df


def _cum_excess(sw: pd.Series, mkt: pd.Series, h: int) -> pd.Series:
    """T → T+h 的**累积超额**（%）：行业区间收益 − 上证区间收益。

    与单日口径互补：单日 h 只能看「隔几天那一天的差」，累积口径看
    「如果 T 日按商品信号建仓、持有 h 天」的总差——中长期传导必须用这个测。
    """
    sw_cum = (sw.shift(-h) / sw - 1.0) * 100.0
    mkt_cum = (mkt.shift(-h) / mkt - 1.0) * 100.0
    return sw_cum - mkt_cum


def analyse(df: pd.DataFrame, spec_dead_zone: float) -> dict:
    """单条链的全部统计量（同一套口径，供生产 `basis` 直接引用）。

    ⚠️ **先 shift 再过滤**，绝不可 `df.loc[up.index, "ex"].shift(-1)`——
    那是「下一个**涨日**的超额」而非「次日超额」，会把 up-day 聚簇效应算成传导效应，
    实测能把 t 从 ~0 虚增到 11~18（本轮踩过，见 KB-ENG-44 附注）。
    """
    out: dict = {"n": int(len(df)), "dead_zone": spec_dead_zone}
    for h in HORIZONS:
        fwd = df["ex"].shift(-h)
        sub = pd.DataFrame({"r_c": df["r_c"], "ex": fwd}).dropna()
        out[f"corr{h}"] = round(float(sub["r_c"].corr(sub["ex"])), 4) if len(sub) > 30 else None
        out[f"n{h}"] = int(len(sub))

    # 分组：以商品自身死区切「涨破 / 死区内 / 跌破」；**先 shift 再过滤**
    up_mask = df["r_c"] > spec_dead_zone
    dn_mask = df["r_c"] < -spec_dead_zone
    out["n_up"], out["n_dn"] = int(up_mask.sum()), int(dn_mask.sum())
    out["n_flat"] = int(len(df) - out["n_up"] - out["n_dn"])

    # 各滞后的分组差（看清传导是同期还是滞后）
    for h in HORIZONS:
        fwd = df["ex"].shift(-h)
        e_up = fwd[up_mask].dropna()
        e_dn = fwd[dn_mask].dropna()
        out[f"gap{h}"] = round(float(e_up.mean() - e_dn.mean()), 4) if len(e_up) and len(e_dn) else None
        out[f"t{h}"] = round(welch_t(e_up, e_dn), 3)

    # 主报告口径 = h=1（盘前简报可用：昨日商品收盘 → 今日板块）
    fwd1 = df["ex"].shift(-1)
    e_up, e_dn = fwd1[up_mask].dropna(), fwd1[dn_mask].dropna()
    out.update(
        up_mean=round(float(e_up.mean()), 4) if len(e_up) else None,
        dn_mean=round(float(e_dn.mean()), 4) if len(e_dn) else None,
        diff=out["gap1"],
        t=out["t1"],
        win_up=round(float((e_up > 0).mean()), 4) if len(e_up) else None,
        win_dn=round(float((e_dn > 0).mean()), 4) if len(e_dn) else None,
    )

    # 分年度方向一致性（h=1）
    yrs, hits = [], 0
    d1 = pd.DataFrame({"r_c": df["r_c"], "fwd": df["ex"].shift(-1)}).dropna()
    for y, g in d1.groupby(d1.index.year):
        gu = g[g["r_c"] > spec_dead_zone]["fwd"]
        gd = g[g["r_c"] < -spec_dead_zone]["fwd"]
        if len(gu) < 10 or len(gd) < 10:
            continue
        yrs.append(y)
        if (gu.mean() - gd.mean()) * (out["diff"] or 0) > 0:
            hits += 1
    out["years"], out["year_hits"] = yrs, hits
    out["year_ratio"] = round(hits / len(yrs), 3) if yrs else None
    return out


def analyse_long(com: pd.Series, sw: pd.Series, mkt: pd.Series, dead_zone: float) -> dict:
    """长滞后（累积超额）检验：回答「传导是不是几周后才显形」。"""
    r_c = _pct(com)
    out: dict = {}
    for h in LONG_HORIZONS:
        cum = _cum_excess(sw, mkt, h)
        df = pd.DataFrame({"r_c": r_c, "cum": cum}).dropna()
        out[f"corr{h}"] = round(float(df["r_c"].corr(df["cum"])), 4) if len(df) > 30 else None
        up = df[df["r_c"] > dead_zone]["cum"]
        dn = df[df["r_c"] < -dead_zone]["cum"]
        out[f"gap{h}"] = round(float(up.mean() - dn.mean()), 4) if len(up) and len(dn) else None
        out[f"t{h}"] = round(welch_t(up, dn), 3)
        out[f"n{h}"] = int(len(df))
    return out


# ------------------------------------------------------------------ 主流程

def main() -> None:  # noqa: C901  —— 一次性核验脚本，长但线性
    print("=" * 118)
    print("P1-33 大宗商品 → 板块超额传导链 实证核验")
    print("口径：ex = 行业收益 − 上证收益；corrH = corr(商品T日变化, 行业T+H日超额)；盘前可用口径 = corr1")
    print("=" * 118)

    print("\n[取数] 上证指数 + 申万行业指数 ...")
    mkt = sh_index()
    print(f"  上证 sh000001：{len(mkt)} 行，{mkt.index[0].date()} ~ {mkt.index[-1].date()}")

    sw_cache: dict[str, pd.Series] = {}

    def _sw(code: str) -> pd.Series:
        if code not in sw_cache:
            sw_cache[code] = sw_index(code)
        return sw_cache[code]

    # ---- ① 生产候选（SPECS）逐条 ----
    print("\n" + "=" * 118)
    print("① 生产候选链（commodity_chain.SPECS）逐条检验")
    print("=" * 118)
    print(f"{'候选':<16}{'行业':<10}{'n':>6}{'corr0':>8}{'corr1':>8}{'corr2':>8}"
          f"{'涨n':>6}{'跌n':>6}{'gap0':>8}{'t0':>7}{'gap1':>8}{'t1':>7}{'胜涨':>7}{'胜跌':>7}{'分年':>8}")

    detail: dict[str, dict] = {}
    for spec in cc.SPECS:
        try:
            com = commodity(spec.code)
            sw = _sw(spec.sw_index)
        except Exception as exc:  # noqa: BLE001
            print(f"{spec.label:<16}{spec.sw_name:<10}  取数失败：{type(exc).__name__} {exc}")
            continue
        df = build_frame(com, sw, mkt)
        st = analyse(df, spec.dead_zone)
        detail[spec.code] = st
        # [自检] 行：分组计数 + |Δ| 中位（抓「全判同一档」这类静默失效，KB-ENG-44）
        med = round(float(df["r_c"].abs().median()), 4)
        print(f"{spec.label + spec.code:<16}{spec.sw_name:<10}{st['n']:>6}"
              f"{_f(st['corr0']):>8}{_f(st['corr1']):>8}{_f(st['corr2']):>8}"
              f"{st['n_up']:>6}{st['n_dn']:>6}{_f(st['gap0']):>8}{_f(st['t0']):>7}"
              f"{_f(st['gap1']):>8}{_f(st['t1']):>7}{_f(st['win_up']):>7}{_f(st['win_dn']):>7}"
              f"{(str(st['year_hits']) + '/' + str(len(st['years']))):>8}")
        print(f"{'   [自检]':<16}|Δ|中位={med}(死区 {spec.dead_zone}) 升{st['n_up']} 平{st['n_flat']} 降{st['n_dn']}"
              f"  corr1_n={st['n1']}  gap2={st['gap2']} t2={st['t2']} gap3={st['gap3']} t3={st['t3']}")
        # 长滞后（累积超额）：检验「传导是不是几周后才显形」
        lg = analyse_long(com, sw, mkt, spec.dead_zone)
        print(f"{'   [长滞后]':<16}" + "  ".join(
            f"h={h}: n={lg[f'n{h}']} corr={_f(lg[f'corr{h}'])} gap={_f(lg[f'gap{h}'])} t={_f(lg[f't{h}'])}"
            for h in LONG_HORIZONS))
        detail[spec.code]["long"] = lg

    # ---- ② 同行业替代候选（挑最强的那条）----
    print("\n" + "=" * 118)
    print("② 同行业替代候选（研究用，不进生产）：验证 SPECS 选的是不是最强")
    print("=" * 118)
    print(f"{'候选':<16}{'行业':<10}{'n':>6}{'corr1':>8}{'涨n':>6}{'跌n':>6}{'差':>8}{'t':>7}{'分年':>8}")
    for code, label, swc, swn in EXTRA:
        try:
            com = commodity(code)
            sw = _sw(swc)
        except Exception as exc:  # noqa: BLE001
            print(f"{label:<16}{swn:<10}  取数失败：{type(exc).__name__}")
            continue
        df = build_frame(com, sw, mkt)
        dz = round(float(df["r_c"].abs().median()), 4)
        st = analyse(df, dz)
        print(f"{label + code:<16}{swn:<10}{st['n']:>6}{_f(st['corr1']):>8}"
              f"{st['n_up']:>6}{st['n_dn']:>6}{_f(st['diff']):>8}{_f(st['t']):>7}"
              f"{(str(st['year_hits']) + '/' + str(len(st['years']))):>8}")

    # ---- ③ 线上规则实测（真实数据走一遍 evaluate）----
    print("\n" + "=" * 118)
    print("③ 线上规则实测：真实取数 → commodity_chain.evaluate()")
    print("=" * 118)
    import asyncio

    from app.services.akshare_ext import get_akshare_ext

    async def _live():
        ext = get_akshare_ext()
        pairs = await asyncio.gather(*[
            ext.commodity_daily(s.code) for s in cc.SPECS
        ], return_exceptions=True)
        series = {}
        for s, v in zip(cc.SPECS, pairs):
            series[s.code] = [] if isinstance(v, Exception) else v
        return cc.evaluate(series)

    res = asyncio.run(_live())
    print(f"  stance={res['stance']}  score={res['score']} / {res['score_range']}"
          f"  有效权重={res['active_weight']}")
    print(f"  未判定理由：{res['unjudged_reason']}")
    print(f"  时点结构：{res['timing_note']}")
    print(f"\n  {'商品':<12}{'行业':<10}{'最新日':<12}{'变化':>9}{'死区':>8}{'档位':<10}{'中期线索':>8}")
    for r in res["rows"]:
        print(f"  {r['label']:<12}{r.get('sw_name',''):<10}{str(r.get('date','—')):<12}"
              f"{_f(r.get('change')):>9}{_f(r.get('dead_zone')):>8}{str(r.get('zone','')):<10}"
              f"{('是' if r.get('mid_signal') else '—'):>8}"
              + (f"   ← {r['skip_reason']}" if r.get("skip_reason") else ""))
    if res["mid_signals"]:
        print("\n  [中期线索]（唯一通过实证的形态）")
        for s in res["mid_signals"]:
            print(f"    {s['label']}→{s['sw_name']}：本次 {s['change']:+.2f}%（{s['zone']}），"
                  f"历史中期超额 {s['edge_pct']:+.3f}pp（t={s['edge_t']}，n={s['sample_n']}）")
    else:
        print("\n  [中期线索] 无（今日无异动，或异动链未通过实证）")

    print("\n[结语] 三层检验已完成，结论已回填 SPECS 的 basis：")
    print("       · 隔夜口径（h=1）实测不成立 ⇒ weight 一律 0.0，**不输出隔夜方向**")
    print("       · 同期（h=0）关系极强 ⇒ 定性为「同日共振」，商品无领先性")
    print("       · 中期（h=5~20）仅螺纹钢→钢铁通过 ⇒ 作中期观察线索保留")


def _f(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 100 else f"{v:.2f}"
    return str(v)


if __name__ == "__main__":
    main()
