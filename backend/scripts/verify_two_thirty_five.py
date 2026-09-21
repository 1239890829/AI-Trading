"""「两点半五步法」实证核验（KB-STOCK-29 的可重跑版）。

**本脚本是 `app/research/strategy_verify.py` 的第一个复用者** —— 原来这里有一份
手写的窗口函数 SQL + 统计渲染（与 `verify_triple_volume*.py` 大量重复），现已全部
收敛到核验器，脚本只负责"声明规则"。

规则（用户 2026-09-10 提供，逐条转成可计算谓词）：
  S1 涨幅 3~5%｜S2 量比 ≥1（原文理想带 1.5~3）｜S3 换手率 5~15%
  S4 成交量阶梯式放大（当日 > 前1日 > 前2日）｜S5 均线多头排列 + 不偏离 MA5 + 站上 MA20
  大盘环境前提：大盘健康（全市场中位涨幅 > 0）
⚠️ 口径：买入=信号日收盘；不含费用滑点；换手率由最新快照反推股本（估算值）。
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
H = 5

S1 = "chg BETWEEN 3 AND 5"
S2 = "vr >= 1"
S3 = "turn BETWEEN 5 AND 15"
S4 = "vol_step_up = 1"
S5 = "ma_short > ma_mid AND ma_mid > ma_long AND close > ma_long AND dev_short <= 5"
CONDS = {"S1 涨幅3~5%": S1, "S2 量比≥1": S2, "S3 换手5~15%": S3,
         "S4 阶梯放大": S4, "S5 均线多头": S5}
ALL = " AND ".join(f"({c})" for c in CONDS.values())


def _labeled(con, label: str, cond: str) -> dict:
    """取一组统计并**打上可读标签**。

    注意：`sv.baseline()` 的行标签恒为 `__base__`，直接把它塞进 `render()` 会得到
    一列全叫「基准」的行（本脚本初版就踩了这个坑）——分组标签由调用方负责。
    """
    r = sv.baseline(con, where=cond)
    r["grp"] = label
    return r


def main() -> int:
    con = sv.connect()
    build_cfg = sv.BuildConfig(
        float_shares_sql=sv.snapshot_float_shares_sql(SNAPSHOT_DIR),
        extra_cols=", fs.float_shares AS float_shares")
    n = sv.build(con, build_cfg)  # 审计 C5：市值分层所需（20260910 快照，前视近似）
    hz = sv.horizons_of(con)
    dates = [r[0] for r in con.execute("SELECT DISTINCT date_ms FROM sig ORDER BY date_ms").fetchall()]
    print(f"特征样本 {n:,} 行 | {len(dates)} 个交易日")
    print("口径：买入=信号日收盘；换手率为 2026 当前股本近似（非 PIT）；全样本区仅作诊断")
    base_all = sv.baseline(con)
    split_ms = int(SPLIT.timestamp() * 1000)
    split = sv.split_windows(con, split_ms, horizons=[H])
    holdout_where = split["test_where"]
    admission_cfg = sv.VerifyConfig(cost_bps=sv.ADMISSION_COST_BPS)
    cost_pct = sv.ADMISSION_COST_BPS / 100.0

    print("\n" + "=" * 118)
    print("① 累计漏斗（全样本）——「越筛越差」还是「越筛越好」")
    print("=" * 118)
    print(sv.render(sv.funnel(con, CONDS), hz, base=base_all))
    print("\n—— 近 250 交易日")
    d250 = sv.date_lo(con, 250)
    print(sv.render(sv.funnel(con, CONDS, where=f"date_ms >= {d250}"), hz,
                    base=sv.baseline(con, where=f"date_ms >= {d250}")))

    print("\n" + "=" * 118)
    print("② 单条件独立（哪一步真携带信息）")
    print("=" * 118)
    print(sv.render(sv.single(con, {k: v for k, v in CONDS.items()}), hz, base=base_all))

    print("\n" + "=" * 118)
    print("③ 参数敏感性（逐个参数分层，其余四步按原文默认值固定）")
    print("=" * 118)
    sens = [
        ("S1 涨幅带", S1, " AND ".join(v for k, v in CONDS.items() if k.startswith("S1")),
         "CASE WHEN chg<0 THEN 'a 下跌' WHEN chg<2 THEN 'b 0~2%' WHEN chg<3 THEN 'c 2~3%' "
         "WHEN chg<5 THEN 'd 3~5%（原文）' WHEN chg<7 THEN 'e 5~7%' WHEN chg<9.5 THEN 'f 7~9.5%' "
         "ELSE 'g 涨停附近' END"),
        ("S2 量比带", S2, " AND ".join(v for k, v in CONDS.items() if k.startswith("S2")),
         "CASE WHEN vr<1 THEN 'a <1（原文剔除）' WHEN vr<1.5 THEN 'b 1~1.5' "
         "WHEN vr<=3 THEN 'c 1.5~3（原文理想）' WHEN vr<=5 THEN 'd 3~5' WHEN vr<=10 THEN 'e 5~10' "
         "ELSE 'f >10' END"),
        ("S3 换手率带（估算）", S3, " AND ".join(v for k, v in CONDS.items() if k.startswith("S3")),
         "CASE WHEN turn<3 THEN 'a <3%' WHEN turn<5 THEN 'b 3~5%' WHEN turn<=15 THEN 'c 5~15%（原文）' "
         "WHEN turn<=25 THEN 'd 15~25%' ELSE 'e >25%' END"),
        ("S5 MA5 偏离度", S5, " AND ".join(v for k, v in CONDS.items() if k.startswith("S5")),
         "CASE WHEN dev_short<0 THEN 'a 低于MA5' WHEN dev_short<3 THEN 'b 0~3%' "
         "WHEN dev_short<5 THEN 'c 3~5%（原文阈值）' WHEN dev_short<8 THEN 'd 5~8%' "
         "WHEN dev_short<12 THEN 'e 8~12%' ELSE 'f >12%' END"),
    ]
    for name, _own, others, expr in sens:
        print(f"\n—— {name}")
        print(sv.render(sv.sensitivity(con, expr, others), hz,
                        base=sv.baseline(con, where=f"({others})")))

    print("\n" + "=" * 118)
    print("④ 大盘环境分层（原文核心前提；大盘 = 全市场个股涨幅中位数）")
    print("=" * 118)
    regime = ("CASE WHEN mchg<-1 THEN 'a 大盘大跌(<-1%)' WHEN mchg<-0.2 THEN 'b 大盘小跌' "
              "WHEN mchg<=0.2 THEN 'c 大盘持平' WHEN mchg<=1 THEN 'd 大盘上涨' "
              "ELSE 'e 大盘大涨(>1%)' END")
    print("—— 全市场（不加条件）")
    print(sv.render(sv.sensitivity(con, regime, "TRUE"), hz, base=base_all))
    print("\n—— 五步全通过样本内")
    print(sv.render(sv.sensitivity(con, regime, ALL), hz, base=sv.baseline(con, where=f"({ALL})")))

    print("\n" + "=" * 118)
    print("④b 个股属性分层（审计 C5，§6.25）：价格档 × 流通市值档——负超额是否均匀")
    print("=" * 118)
    # 市值 = close × float_shares（float_shares 来自 20260910 快照反推：前视近似，仅作分层归属）
    cap_expr = ("CASE WHEN close * float_shares < 3e9 THEN 'a 小盘<30亿' "
                "WHEN close * float_shares < 1e10 THEN 'b 中盘30-100亿' "
                "WHEN close * float_shares < 3e10 THEN 'c 大盘100-300亿' "
                "ELSE 'd 超大盘>300亿' END")
    price_expr = ("CASE WHEN close < 5 THEN 'a 低价<5' WHEN close <= 20 THEN 'b 中价5-20' "
                  "ELSE 'c 高价>20' END")
    print("—— 原版五步全通过 × 个股属性")
    print(sv.render(sv.sensitivity(con, price_expr, ALL), hz,
                    base=sv.baseline(con, where=f"({ALL})")))
    print(sv.render(sv.sensitivity(con, cap_expr, ALL), hz,
                    base=sv.baseline(con, where=f"({ALL})")))
    print("—— 候选B（S1+跌破MA5+大盘涨）× 个股属性（KB-STOCK-30 右偏 tail 的归属检验）")
    cand = f"({S1}) AND dev_short < 0 AND mchg > 0"
    print(sv.render(sv.sensitivity(con, price_expr, cand), hz,
                    base=sv.baseline(con, where=f"({cand})")))
    print(sv.render(sv.sensitivity(con, cap_expr, cand), hz,
                    base=sv.baseline(con, where=f"({cand})")))

    print("\n" + "=" * 118)
    print("⑤ 候选子规则独立验证（每条单独与全市场对照；探索性，多重比较下必有偶然显著）")
    print("=" * 118)
    variants = [
        ("S1 涨幅3~5%（原文步①）", f"({S1})"),
        ("S1 + 大盘涨", f"({S1}) AND mchg > 0"),
        ("S1 + 量比<1（原文剔除的）", f"({S1}) AND vr < 1"),
        ("S1 + 换手<5%（原文下限之下）", f"({S1}) AND turn < 5"),
        ("S1 + 跌破 MA5", f"({S1}) AND dev_short < 0"),
        ("涨幅0~3% + 跌破 MA5", "chg BETWEEN 0 AND 3 AND dev_short < 0"),
        ("S1 + 跌破 MA5 + 大盘涨", f"({S1}) AND dev_short < 0 AND mchg > 0"),
        ("五步全通过（对照）", ALL),
    ]
    print(sv.render([_labeled(con, l, c) for l, c in variants], hz, base=base_all))
    print("  变体顺序：" + " ／ ".join(f"{i + 1}.{l}" for i, (l, _c) in enumerate(variants)))

    print("\n" + "=" * 118)
    print("⑥ 分年度稳定性（对照：全市场）")
    print("=" * 118)
    cand = f"({S1}) AND dev_short < 0 AND mchg > 0"
    for label, cond in (("候选B（S1+跌破MA5+大盘涨）", cand), ("原版五步全通过", ALL)):
        rows = sv.yearly(con, cond)
        pos, tot = sv.year_counts(rows, horizon=5)
        print(f"\n—— {label}：年度中性超额 > 0 的有 {pos}/{tot} 年")
        print(sv.render(rows, hz))
    rows = sv.yearly(con, "TRUE")
    pos, tot = sv.year_counts(rows, horizon=5)
    print(f"\n—— 全市场基准：年度均值 > 0 的有 {pos}/{tot} 年")

    print("\n" + "=" * 118)
    print("⑦ 候选B 参数刻画（规则定清楚：哪一档涨幅 / 跌破多深）")
    print("=" * 118)
    sub = "dev_short < 0 AND mchg > 0"
    band = ("CASE WHEN chg<0 THEN 'a 下跌' WHEN chg<3 THEN 'b 0~3%' WHEN chg<5 THEN 'c 3~5%' "
            "WHEN chg<7 THEN 'd 5~7%' WHEN chg<9.5 THEN 'e 7~9.5%' ELSE 'f 涨停附近' END")
    depth = ("CASE WHEN dev_short>=-1 THEN 'a 刚破 MA5' WHEN dev_short>=-3 THEN 'b 破 -1~-3%' "
             "WHEN dev_short>=-8 THEN 'c 破 -3~-8%' WHEN dev_short>=-15 THEN 'd 破 -8~-15%' "
             "ELSE 'e 破 <-15%（深跌）' END")
    print("—— 按当日涨幅分档")
    print(sv.render(sv.sensitivity(con, band, sub), hz, base=sv.baseline(con, where=f"({sub})")))
    print("\n—— 按跌破 MA5 深度分档")
    print(sv.render(sv.sensitivity(con, depth, sub), hz, base=sv.baseline(con, where=f"({sub})")))

    # ---- ⑧ 结论登记（S2-11）--------------------------------------------------
    # 此前本脚本的结论只流向 stdout，登记册里那条 ⛔ 靠人工誊写、无时间戳、不可回查。
    # 落盘后每条状态背后都有一份可复核的证据；**verdict 由 `gate_verdict` 依
    # KB-DEC-019 判据算出，不在此写死**——重跑后数据变了，结论自己会变。
    print("\n" + "=" * 118)
    print("⑧ 结论登记")
    print("=" * 118)
    gate_where = f"({ALL}) AND ({holdout_where})"
    all_row = sv.baseline(con, where=gate_where, cfg=admission_cfg, horizons=[H])
    m5 = sv.summarize_row(all_row, horizon=H, cost_bps=sv.ADMISSION_COST_BPS)
    yrows = sv.yearly(con, gate_where, cfg=admission_cfg, horizons=[H])
    ypos, ytot = sv.year_counts(yrows, horizon=H)
    lu = sv.limit_up_share(con, gate_where)
    med_mkt, win_mkt = con.execute(f"""
        SELECT median(fwd{H} - mfwd{H} - {cost_pct}),
               avg(CASE WHEN fwd{H} - mfwd{H} - {cost_pct} > 0 THEN 1.0 ELSE 0.0 END)
        FROM sigv
        WHERE ({gate_where}) AND fwd{H} IS NOT NULL AND mfwd{H} IS NOT NULL
    """).fetchone()
    train_row = sv.baseline(
        con, where=f"({ALL}) AND ({split['train_where']})", cfg=admission_cfg, horizons=[H]
    )
    trial_evidence = st.trial_family_evidence(
        [{
            "trial_id": "two-thirty-five-v1", "label": "two_thirty_five", "condition": ALL,
            "train": {"n": train_row.get(f"n{H}"), "excess": train_row.get(f"x{H}"),
                      "std": train_row.get(f"s{H}")},
        }],
        selected_trial_id="two-thirty-five-v1",
    )
    overlap_evidence = st.signal_overlap_evidence(
        con, target_label="two_thirty_five", target_cond=ALL,
        incumbents={"pullback_reversal": cand}, where=holdout_where,
    )
    protocol = sv.validation_protocol(
        horizon=H, cost_bps=sv.ADMISSION_COST_BPS, split=split,
        selection_scope="external_preregistered", build_config=build_cfg,
        # S3/turn 仍依赖 2026-09-10 当前流通股本，build evidence 会机械标成 non-PIT。
        gate_features=("chg", "vr", "turn", "vol_step_up", "ma_short", "ma_mid",
                       "ma_long", "close", "dev_short"),
        trial_evidence=trial_evidence, overlap_evidence=overlap_evidence,
    )
    gate = sv.gate_verdict(
        m5, yearly_pos=ypos, yearly_tot=ytot, limit_up_share=lu.get("limit_up_share"),
        excess_median=med_mkt, excess_win_rate=win_mkt, protocol=protocol,
    )
    headline = (
        f"五步法 purged 测试段 T+{H}（{sv.ADMISSION_COST_BPS:.0f}bps）："
        f"均值 {m5['mean']:+.2f}%（中性 {m5['excess']:+.2f}%）、"
        f"中性中位 {(med_mkt or 0):+.2f}%、中性跑赢 {(win_mkt or 0) * 100:.1f}%、"
        f"年度为正 {ypos}/{ytot}、疑似涨停 {(lu.get('limit_up_share') or 0) * 100:.1f}%"
        f" ⇒ {gate['verdict']}"
    )
    path = vr.save_record(
        "two_thirty_five",
        verdict=gate["verdict"],
        headline=headline,
        metrics={**m5, "excess_median": med_mkt, "excess_win_rate": win_mkt},
        sample={
            "n_signals": m5["n"],
            "trade_days": len(dates),
            "split": "2022-01-01",
            "segment": "purged/embargoed holdout",
            "split_evidence": split,
            "yearly_pos": ypos,
            "yearly_tot": ytot,
            "limit_up_share": lu.get("limit_up_share"),
        },
        source="scripts/verify_two_thirty_five.py",
        extra={"gate_failed": gate["failed"], "gate_unchecked": gate["unchecked"],
               "gate": gate, "conditions": CONDS},
    )
    print(f"    {headline}")
    print(f"    判据命中：{gate['note']}")
    print(f"    已登记 → {path}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
