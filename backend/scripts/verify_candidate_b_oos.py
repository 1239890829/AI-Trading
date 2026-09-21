"""候选B（超跌反攻）**样本外盲测**复验（P1-40）。

背景：`KB-STOCK-29` 在一份样本（2016-09~2026-09）上**事后发现**
    候选B = 涨幅 3~5% + 收盘跌破 MA5 + 大盘中位涨幅 > 0
T+5 +1.60%（超额 +1.46%）、胜率 56.9%、11/11 年度正超额。
但它是"在同一份样本上反复试探"得到的 ⇒ 多重比较/过拟合风险未排除。本脚本做正式复验。

**协议（先冻结、再看测试段一次）**：
  ① 训练段 2016-09-01 ~ 2021-12-31：在 48 组参数网格内选规则；② 冻结后在测试段
  2022-01-01 ~ 2026-09 只看一次；③ 主指标 = **市场中性超额**（个股 T+5 − 同一交易日
  全市场均值）——区分「顺大盘的 beta」与「选股 alpha」；④ 扣往返成本；
  ⑤ 可成交性 + 结构稳定性（流通市值 / 次新 / **同市值段中性化**）。

**两种选取方式并列**（这是一个方法论要点）：
  - 按 **t 值**最高选 → 会挑到「大样本 + 微小效应」的组（t 值 ≠ 效应量）；
  - 按 **效应量**最高选（设样本量下限）→ 挑到有经济意义的组。
  只看其中一种都会得出偏颇结论，必须并列。

⚠️ 口径：买入=信号日收盘、卖出=T+N 收盘；不含滑点/停牌；流通股本由最新快照反推
（前视偏差）；ST 按当期名称剔除。
⚠️ **数据窥探声明**：候选B 是**用全样本（含测试段）**发现的，因此它在测试段的成绩
**不构成干净的样本外证据**；真正干净的只有「网格在训练段选、测试段验」这一条路径。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research import strategy_trials as st  # noqa: E402
from app.research import strategy_verify as sv  # noqa: E402
from app.research import verify_registry as vr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT.parent / "data" / "parquet" / "snapshots" / "20260910"
SPLIT = datetime(2022, 1, 1, tzinfo=timezone.utc)

BANDS = [
    ("涨幅0~3%", "chg BETWEEN 0 AND 3"),
    ("涨幅3~5%", "chg BETWEEN 3 AND 5"),
    ("涨幅5~7%", "chg BETWEEN 5 AND 7"),
    ("涨幅3~7%", "chg BETWEEN 3 AND 7"),
]
DEPTHS = [
    ("跌破MA5（任意）", "dev_short < 0"),
    ("跌破 0~3%", "dev_short < 0 AND dev_short >= -3"),
    ("跌破 3~8%", "dev_short < -3 AND dev_short >= -8"),
    ("跌破 >8%", "dev_short < -8"),
]
MARKETS = [
    ("大盘不限", "TRUE"),
    ("大盘涨 >0", "mchg > 0"),
    ("大盘涨 >0.5%", "mchg > 0.5"),
]

CANDIDATE_B = "(chg BETWEEN 3 AND 5) AND (dev_short < 0) AND (mchg > 0)"
FIVE_STEP = (
    "(chg BETWEEN 3 AND 5) AND (vr >= 1) AND (vol_step_up = 1) "
    "AND (ma_short > ma_mid AND ma_mid > ma_long AND close > ma_long AND dev_short <= 5)"
)
SMALL_CAP = "close * float_shares < 3e9"

H = 5
HEADER = f"{'组':<34}{'样本量':>9}{'T+5 均值':>11}{'中性超额':>11}{'胜率':>9}{'t':>8}"


def _t(r: dict) -> float:
    n, s, x = r.get(f"n{H}"), r.get(f"s{H}"), r.get(f"x{H}")
    if not n or n < 2 or s is None or x is None:
        return 0.0
    return x / (s / (n ** 0.5))


def _line(label: str, r: dict) -> str:
    if not r or not r.get(f"n{H}"):
        return f"{label:<34}{r.get('n', 0):>9,}   （无样本）"
    return (f"{label:<34}{r['n']:>9,}{r[f'm{H}']:>+10.2f}%{r[f'x{H}']:>+10.2f}%"
            f"{r[f'w{H}'] * 100:>8.1f}%{_t(r):>+8.1f}")


def _pctl(vals: list[float], p: float) -> float:
    s = sorted(vals)
    pos = (len(s) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def main() -> int:
    con = sv.connect()
    build_cfg = sv.BuildConfig(
        float_shares_sql=sv.snapshot_float_shares_sql(SNAPSHOT_DIR),
        extra_cols=", fs.float_shares AS float_shares",
    )
    sv.build(con, build_cfg)
    split_ms = int(SPLIT.timestamp() * 1000)
    admission_cfg = sv.VerifyConfig(cost_bps=sv.ADMISSION_COST_BPS)
    split = sv.split_windows(con, split_ms, horizons=[H])
    train_where, holdout_where = split["train_where"], split["test_where"]
    holdout_where_s = holdout_where.replace("date_ms", "s.date_ms")
    cost_pct = sv.ADMISSION_COST_BPS / 100.0
    print(
        f"训练段/测试段按交易日 purge+embargo（各 {H} 日）｜请求切分 {SPLIT.date()}｜"
        f"研究准入成本={sv.ADMISSION_COST_BPS:.0f}bps"
    )

    # ---------------- 网格
    grid = []
    combos = [(b, d, m) for b in BANDS for d in DEPTHS for m in MARKETS]
    for i, (b, d, m) in enumerate(combos):
        cond = f"({b[1]}) AND ({d[1]}) AND ({m[1]})"
        tr = sv.baseline(con, where=f"({cond}) AND ({train_where})", cfg=admission_cfg)
        te = sv.baseline(con, where=f"({cond}) AND ({holdout_where})", cfg=admission_cfg)
        grid.append({"label": f"{b[0]} | {d[0]} | {m[0]}", "cond": cond, "tr": tr, "te": te})
        if (i + 1) % 12 == 0:
            print(f"  …网格 {i + 1}/{len(combos)}", file=sys.stderr)

    eligible = [g for g in grid if (g["tr"].get("n5") or 0) >= 1000]
    print(f"\n可评估组（训练段 n5 ≥ 1000）：{len(eligible)}/{len(grid)}")

    print("\n" + "=" * 94)
    print("【1】训练段网格：按 t 值排名（注意 t 值 ≠ 效应量）")
    print("=" * 94)
    by_t = sorted(eligible, key=lambda g: -_t(g["tr"]))
    print(HEADER)
    print("-" * len(HEADER))
    for g in by_t[:5]:
        print(_line("  训练 " + g["label"], g["tr"]))
    print("\n  按 t 值的末 3 组（对照）：")
    for g in by_t[-3:]:
        print(_line("  训练 " + g["label"], g["tr"]))

    print("\n按 **效应量**（中性超额）排名：")
    by_x = sorted(eligible, key=lambda g: -(g["tr"].get("x5") or -9))
    print(HEADER)
    print("-" * len(HEADER))
    for g in by_x[:5]:
        print(_line("  训练 " + g["label"], g["tr"]))

    # ---------------- 三种选取 → 测试段
    print("\n" + "=" * 94)
    print("【2】三种选取方式冻结 → 测试段（干净样本外只有前两种）")
    print("=" * 94)
    picks = []
    if by_t:
        picks.append(("A·max-t（n≥1000）", by_t[0]["cond"], by_t[0]["tr"], by_t[0]["te"]))
    cand_x = [g for g in by_x if (g["tr"].get("n5") or 0) >= 3000]
    if cand_x:
        picks.append(("B·max-效应量（n≥3000）", cand_x[0]["cond"], cand_x[0]["tr"], cand_x[0]["te"]))
    picks.append(("C·原候选B（含数据窥探）", CANDIDATE_B,
                  sv.baseline(con, where=f"({CANDIDATE_B}) AND ({train_where})", cfg=admission_cfg),
                  sv.baseline(con, where=f"({CANDIDATE_B}) AND ({holdout_where})", cfg=admission_cfg)))
    print(HEADER)
    print("-" * len(HEADER))
    for name, _c, tr, te in picks:
        print(_line(f"  {name} · 训练", tr))
        print(_line(f"  {name} · **测试**", te))
    print(_line("  原版五步法 · 训练", sv.baseline(con, where=f"({FIVE_STEP}) AND ({train_where})", cfg=admission_cfg)))
    print(_line("  原版五步法 · **测试**", sv.baseline(con, where=f"({FIVE_STEP}) AND ({holdout_where})", cfg=admission_cfg)))
    print(_line("  全市场 · 训练", sv.baseline(con, where=train_where, cfg=admission_cfg)))
    print(_line("  全市场 · **测试**", sv.baseline(con, where=holdout_where, cfg=admission_cfg)))

    # ---------------- 方向一致性
    print("\n" + "=" * 94)
    print("【3】方向一致性与效应量分布（不受单点挑选噪声影响）")
    print("=" * 94)
    pos_tr = [g for g in eligible if (g["tr"].get("x5") or 0) > 0]
    keep = [g for g in pos_tr if (g["te"].get("x5") or 0) > 0]
    neg_tr = [g for g in eligible if (g["tr"].get("x5") or 0) <= 0]
    neg_keep = [g for g in neg_tr if (g["te"].get("x5") or 0) > 0]
    print(f"训练段中性超额 > 0：{len(pos_tr)}/{len(eligible)}；其中测试段仍 > 0："
          f"{len(keep)}/{len(pos_tr)}（{len(keep) / max(1, len(pos_tr)) * 100:.0f}%）")
    print(f"训练段 ≤ 0 的组在测试段为正：{len(neg_keep)}/{len(neg_tr)}"
          f"（{len(neg_keep) / max(1, len(neg_tr)) * 100:.0f}%，样本 {len(neg_tr)} 组，"
          f"对照力弱）")
    trx = [g["tr"]["x5"] for g in eligible if g["tr"].get("x5") is not None]
    tex = [g["te"]["x5"] for g in eligible if g["te"].get("x5") is not None]
    print(f"\n训练段中性超额分布：p25 {_pctl(trx, 25):+.2f}% / 中位 {_pctl(trx, 50):+.2f}%"
          f" / p75 {_pctl(trx, 75):+.2f}%  (max {max(trx):+.2f}%)")
    print(f"测试段中性超额分布：p25 {_pctl(tex, 25):+.2f}% / 中位 {_pctl(tex, 50):+.2f}%"
          f" / p75 {_pctl(tex, 75):+.2f}%  (max {max(tex):+.2f}%)")
    print("  ⇒ 中位数从训练到测试的衰减幅度，比单点成绩更能说明「有多少是真的」")

    # ---------------- 成本
    print("\n" + "=" * 94)
    print("【4】成本（往返基点）对测试段中性超额的影响")
    print("=" * 94)
    print(f"{'规则':<34}{'0bps':>12}{'25bps':>12}{'35bps':>12}")
    print("-" * 70)
    for name, c, _tr, _te in picks:
        vals = []
        for bps in (0, 25, 35):
            r = sv.baseline(con, where=f"({c}) AND ({holdout_where})",
                            cfg=sv.VerifyConfig(cost_bps=bps))
            vals.append(f"{r['x5']:+.2f}%")
        print(f"{name:<34}{vals[0]:>12}{vals[1]:>12}{vals[2]:>12}")

    # ---------------- 可成交性 + 结构
    selected_grid = cand_x[0] if cand_x else next(
        g for g in grid if g["label"] == "涨幅3~5% | 跌破MA5（任意） | 大盘涨 >0"
    )
    main_cond = selected_grid["cond"]
    main_name = selected_grid["label"]
    selected_trial_id = next(
        f"grid-{i + 1:02d}" for i, g in enumerate(grid) if g is selected_grid
    )
    print("\n" + "=" * 94)
    print(f"【5】结构稳定性（测试段）—— 主规则 = {main_name}")
    print("=" * 94)
    lu = sv.limit_up_share(con, f"({main_cond}) AND ({holdout_where})")
    print(f"信号日疑似涨停（收盘买不进）：{(lu['limit_up_share'] or 0) * 100:.1f}%"
          f"（当日平均涨幅 {lu['chg_mean']:.2f}%）")
    test_where = f"({main_cond}) AND ({holdout_where})"
    mv_band = ("CASE WHEN float_shares IS NULL THEN 'z 股本未知' "
               "WHEN " + SMALL_CAP + " THEN 'a 流通<30亿' "
               "WHEN close * float_shares < 1e10 THEN 'b 30~100亿' ELSE 'c >100亿' END")
    print("\n—— 按流通市值分档（原始 + 全市场中性）")
    print(sv.render(sv.sensitivity(con, mv_band, test_where), sv.horizons_of(con),
                    base=sv.baseline(con, where=test_where)))
    print("\n—— **同市值段中性化**：扣掉「同一交易日 + 同一市值段」的均值，检验是否只是小市值 beta")
    seg_sql = f"""
        WITH seg AS (
            SELECT date_ms, CASE WHEN float_shares IS NULL THEN 'unknown'
                                  WHEN {SMALL_CAP} THEN 'small' ELSE 'big' END AS seg,
                   avg(fwd{H}) AS sfwd
            FROM sigv WHERE fwd{H} IS NOT NULL GROUP BY 1, 2
        ), mkt AS (
            SELECT date_ms, avg(fwd{H}) AS mfwd FROM sigv WHERE fwd{H} IS NOT NULL GROUP BY 1
        )
        SELECT count(*) AS n,
            avg(s.fwd{H} - m.mfwd - {cost_pct}) AS ex_mkt,
            median(s.fwd{H} - m.mfwd - {cost_pct}) AS med_mkt,
            avg(CASE WHEN s.fwd{H} - m.mfwd - {cost_pct} > 0 THEN 1.0 ELSE 0 END) AS win_mkt,
            avg(s.fwd{H} - g.sfwd - {cost_pct}) AS ex_seg,
            median(s.fwd{H} - g.sfwd - {cost_pct}) AS med_seg,
            avg(CASE WHEN s.fwd{H} - g.sfwd - {cost_pct} > 0 THEN 1.0 ELSE 0 END) AS win_seg
        FROM sigv s
        JOIN mkt m ON m.date_ms = s.date_ms
        JOIN seg g ON g.date_ms = s.date_ms
                   AND g.seg = CASE WHEN s.float_shares IS NULL THEN 'unknown'
                                      WHEN {SMALL_CAP} THEN 'small' ELSE 'big' END
        WHERE ({main_cond}) AND ({holdout_where_s}) AND s.fwd{H} IS NOT NULL
    """
    n, ex_mkt, med_mkt, win_mkt, ex_seg, med_seg, win_seg = con.execute(seg_sql).fetchone()
    if n:
        print(f"    n={n:,}")
        print(f"    对全市场中性：均值 {ex_mkt:+.2f}%  中位 {med_mkt:+.2f}%  "
              f"跑赢比例 {win_mkt * 100:.1f}%")
        print(f"    对同市值段中性：均值 {ex_seg:+.2f}%  中位 {med_seg:+.2f}%  "
              f"跑赢比例 {win_seg * 100:.1f}%")
        print("    ⚠️ 判读要点：**中位数与「跑赢比例」**比均值更能说明是否只靠少数极端样本 ——")
        print("       均值明显为正、中位接近 0 或跑赢比例低于 50% ⇒ 收益高度右偏（赢家极少），")
        print("       这种分布对资金曲线极不友好，且对样本期极敏感。")
    print("\n—— 按上市时长（本数据内 <250 个交易日 ≈ 次新）")
    age_band = "CASE WHEN rn <= 250 THEN 'a 次新' ELSE 'b 老股' END"
    print(sv.render(sv.sensitivity(con, age_band, test_where), sv.horizons_of(con),
                    base=sv.baseline(con, where=test_where)))
    print("\n—— 测试段分年度")
    yr = sv.yearly(con, test_where, cfg=admission_cfg, horizons=[H])
    print(sv.render(yr, sv.horizons_of(con)))
    pos, tot = sv.year_counts(yr, horizon=H)
    print(f"   年度中性超额 > 0：{pos}/{tot}")

    # ---- 【6】结论登记（S2-11）------------------------------------------------
    # 同 `verify_two_thirty_five.py`：结论此前只流向 stdout。这里落的是**测试段**
    # （样本外）的口径，因为候选B 的登记状态本来就建立在样本外成绩上；
    # 「数据窥探声明」写进 sample，避免后人把这份产物误读成干净的样本外证据。
    print("\n" + "=" * 94)
    print("【6】结论登记")
    print("=" * 94)
    m = sv.summarize_row(
        sv.baseline(con, where=test_where, cfg=admission_cfg, horizons=[H]),
        horizon=H, cost_bps=sv.ADMISSION_COST_BPS,
    )
    lu_main = lu or {}
    # 最终准入的中性中位/跑赢比例只吃主规则 + purged holdout；不能复用上面的
    # 当前股本市值分层 join，否则会把结构诊断里的非 PIT 股本偷偷带回 gate。
    gate_med_mkt, gate_win_mkt = con.execute(f"""
        SELECT median(fwd{H} - mfwd{H} - {cost_pct}),
               avg(CASE WHEN fwd{H} - mfwd{H} - {cost_pct} > 0 THEN 1.0 ELSE 0.0 END)
        FROM sigv
        WHERE ({test_where}) AND fwd{H} IS NOT NULL AND mfwd{H} IS NOT NULL
    """).fetchone()
    trial_evidence = st.trial_family_evidence(
        [
            {
                "trial_id": f"grid-{i + 1:02d}", "label": g["label"], "condition": g["cond"],
                "train": {
                    "n": g["tr"].get(f"n{H}"), "excess": g["tr"].get(f"x{H}"),
                    "std": g["tr"].get(f"s{H}"),
                },
            }
            for i, g in enumerate(grid)
        ],
        selected_trial_id=selected_trial_id,
    )
    overlap_evidence = st.signal_overlap_evidence(
        con, target_label=main_name, target_cond=main_cond,
        incumbents={"two_thirty_five": FIVE_STEP}, where=holdout_where,
    )
    protocol = sv.validation_protocol(
        horizon=H, cost_bps=sv.ADMISSION_COST_BPS, split=split,
        selection_scope="test_informed_hypothesis_family",
        build_config=build_cfg, gate_features=("chg", "dev_short", "mchg"),
        trial_evidence=trial_evidence, overlap_evidence=overlap_evidence,
    )
    gate = sv.gate_verdict(
        m, yearly_pos=pos, yearly_tot=tot, limit_up_share=lu_main.get("limit_up_share"),
        excess_median=gate_med_mkt, excess_win_rate=gate_win_mkt, protocol=protocol,
    )
    headline = (
        f"purged 测试段 T+{H}（{sv.ADMISSION_COST_BPS:.0f}bps）："
        f"均值 {m['mean']:+.2f}%（中性 {m['excess']:+.2f}%）、"
        f"中性中位 {gate_med_mkt:+.2f}%、中性跑赢 {(gate_win_mkt or 0) * 100:.1f}%、"
        f"年度为正 {pos}/{tot}、疑似涨停 {(lu_main.get('limit_up_share') or 0) * 100:.1f}%"
        f" ⇒ {gate['verdict']}"
    )
    path = vr.save_record(
        "pullback_reversal",
        verdict=gate["verdict"],
        headline=headline,
        # 中性中位/胜率一并入库：它们才是判据依据，落到产物里才能回查
        metrics={**m, "excess_median": gate_med_mkt, "excess_win_rate": gate_win_mkt},
        sample={
            "n_signals": m["n"],
            "split": "2022-01-01",
            "segment": "purged/embargoed holdout",
            "split_evidence": split,
            "yearly_pos": pos,
            "yearly_tot": tot,
            "limit_up_share": lu_main.get("limit_up_share"),
            "caveat": "候选B 假设族曾使用全样本（含测试段）发现；且未完成既有信号重叠检查，故协议门保持 observe",
        },
        source="scripts/verify_candidate_b_oos.py",
        extra={"gate_failed": gate["failed"], "gate_unchecked": gate["unchecked"],
               "gate": gate, "rule": main_name, "condition": main_cond},
    )
    print(f"    {headline}")
    print(f"    判据命中：{gate['note']}")
    print(f"    已登记 → {path}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
