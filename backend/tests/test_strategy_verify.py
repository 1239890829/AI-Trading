"""策略核验器（app/research/strategy_verify.py）单测。

用**合成数据**（5 只票 × 40 天，涨幅线性可控）验证统计口径正确性 —— 不依赖真实 marketdb，
所以能在 CI 跑。真实库上的结论由 scripts/verify_*.py 产出。

合成数据的解析解（用于断言）：
    A +1%/日 → fwd5 = +5.1010%     B 0%/日 → 0        C −1%/日 → −4.9010%
    D +2%/日 → +10.4081%           E +10%/日 → +61.0510%
    同日等权市场均值 fwd5 = 14.3318%   ⇒  市场中性超额 = 个股 fwd5 − 14.3318
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from app.research import strategy_verify as sv

SPECS = {
    "A.SH": (10.0, 1.01),
    "B.SH": (20.0, 1.00),
    "C.SH": (30.0, 0.99),
    "D.SH": (40.0, 1.02),
    "E.SH": (50.0, 1.10),   # 每日 +10% → 疑似涨停
}
START = datetime(2025, 12, 1)
DAYS = 40
KEPT = DAYS - 20             # 预热 20 根 → 每只 20 行
MKT_FWD5 = 14.331818064      # 同日等权市场均值（手算解析值）


def make_con(db_path: Path, **build_kwargs) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(str(db_path))
    c.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)"
    )
    rows = []
    for code, (p0, ratio) in SPECS.items():
        for i in range(DAYS):
            ms = int((START + timedelta(days=i)).timestamp() * 1000)
            px = p0 * (ratio ** i)
            rows.append((code, ms, px, px * 1.01, px * 0.99, px, 1000.0, px * 1000))
    c.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?)", rows)
    sv.build(c, sv.BuildConfig(**build_kwargs))
    return c


@pytest.fixture()
def con(tmp_path):
    c = make_con(tmp_path / "m.duckdb")
    yield c
    c.close()


# 第二组合成数据：**市场因子逐日大幅波动**（+4% / −3% 交替）+ 个股 alpha。
# 用途：只有市场均值逐日不同，"市场中性必须按日对齐"才可测 ——
# 用恒定比例那组数据，错位 join 也测不出来（每天均值几乎相同）。恒等式：mean(alpha)=0。
V_ALPHA = {"A.SH": 0.01, "B.SH": 0.00, "C.SH": -0.01}
V_DAYS = 30


def _market_factor(i: int) -> float:
    return 0.04 if i % 2 == 0 else -0.03


@pytest.fixture()
def con_var(tmp_path):
    c = duckdb.connect(str(tmp_path / "v.duckdb"))
    c.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)"
    )
    rows = []
    for code, alpha in V_ALPHA.items():
        px = 100.0
        for i in range(V_DAYS):
            ms = int((START + timedelta(days=i)).timestamp() * 1000)
            if i > 0:
                px *= 1 + _market_factor(i) + alpha
            rows.append((code, ms, px, px, px, px, 1000.0, px * 1000))
    c.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?)", rows)
    sv.build(c, sv.BuildConfig())
    yield c
    c.close()


# ---------------------------------------------------------------- build

def test_build_row_count_and_columns(con):
    """每只票去掉前 20 根预热 K 线 → 5 × 20 = 100 行；关键列齐全。"""
    cols = {d[0] for d in con.execute("DESCRIBE sig").fetchall()}
    assert {"chg", "vr", "turn", "vol_step_up", "ma_short", "dev_short", "at_limit",
            "fwd5", "rn"} <= cols
    assert con.execute("SELECT count(*) FROM sig").fetchone()[0] == 5 * KEPT


def test_market_mean_is_exposed_on_the_view(con):
    """同日市场均值在 `sigv` 上（`sig` 只有个股特征）—— 两表职责不混。"""
    assert {"mfwd1", "mfwd5", "mfwd10"} <= {d[0] for d in con.execute("DESCRIBE sigv").fetchall()}
    vals = [r[0] for r in con.execute("SELECT DISTINCT mfwd5 FROM sigv").fetchall()]
    assert all(abs(v - MKT_FWD5) < 1e-6 for v in vals if v is not None)
    assert None in vals  # 尾部 5 天无 fwd5 → 市场均值同为 NULL（缺就是缺，不填 0）


def test_vol_ratio_uses_prior_window_excluding_today(con):
    """量比分母 = 前 5 日均量（不含当日）；合成数据量恒定 → 恒等于 1。"""
    mn, mx = con.execute("SELECT min(vr), max(vr) FROM sig").fetchone()
    assert abs(mn - 1.0) < 1e-9 and abs(mx - 1.0) < 1e-9


def test_turn_is_null_without_float_shares(con):
    """未提供流通股本时 turn 为 NULL（而不是 0）——缺数据不能伪装成有数据。"""
    assert con.execute("SELECT count(*) FROM sig WHERE turn IS NOT NULL").fetchone()[0] == 0


def test_at_limit_flags_limit_up_day(con):
    """at_limit 只标记涨幅 ≥ 阈值的信号日（E.SH 每日 +10%）。"""
    n = con.execute("SELECT count(*) FROM sig WHERE at_limit = 1").fetchone()[0]
    assert n == KEPT
    assert con.execute("SELECT count(DISTINCT thscode) FROM sig WHERE at_limit = 1").fetchone()[0] == 1




def test_current_snapshot_float_shares_never_filters_historical_universe(tmp_path):
    fs_sql = (
        "(SELECT * FROM (VALUES ('A.SH', 'ST today', 1000000.0)) "
        "AS t(thscode, name, float_shares))"
    )
    c = make_con(tmp_path / "fs.duckdb", float_shares_sql=fs_sql)
    try:
        assert c.execute("SELECT count(*) FROM sig").fetchone()[0] == 5 * KEPT
        assert c.execute("SELECT count(*) FROM sig WHERE thscode='A.SH' AND turn IS NOT NULL").fetchone()[0] == KEPT
        assert c.execute("SELECT count(*) FROM sig WHERE thscode='B.SH' AND turn IS NULL").fetchone()[0] == KEPT
    finally:
        c.close()


def test_snapshot_float_shares_tie_break_is_deterministic(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    c = duckdb.connect()
    try:
        out = snap / "a.parquet"
        c.execute(f"""
            COPY (
              SELECT * FROM (VALUES
                ('000001','SZ','older',10.0,100.0,1000.0,TIMESTAMP '2026-09-10 14:00:00'),
                ('000001','SZ','newer',20.0,200.0,1000.0,TIMESTAMP '2026-09-10 14:30:00')
              ) AS t(symbol,market,name,price,nmc,amount,received_at)
            ) TO '{out}' (FORMAT PARQUET)
        """)
        sql = sv.snapshot_float_shares_sql(snap)
        row = c.execute(f"SELECT name, float_shares FROM {sql}").fetchone()
        assert row == ("newer", 100000.0)
    finally:
        c.close()


# ---------------------------------------------------------------- 市场中性（核心口径）

def test_market_neutral_excess_sums_to_zero_over_all_rows(con):
    """市场中性口径的自证：不过滤时，全样本的市场中性超额必须为 0。

    这是"同日全市场均值"定义的直接推论；一旦不为 0，说明 mfwd 与 fwd 没对齐日期
    （例如 join 用错键）——那所有 alpha 结论都是假的。
    """
    base = sv.baseline(con)
    assert base["n"] == 5 * KEPT
    assert abs(base["x5"]) < 1e-9
    assert abs(base["x1"]) < 1e-9


def test_market_neutral_separates_timing_from_selection(con):
    """市场中性把「绝对收益」与「相对市场的超额」分开 —— 这是防"顺大盘被读成 alpha"的关键。"""
    rows = {r["grp"]: r for r in sv.single(con, {
        "A": "thscode = 'A.SH'", "D": "thscode = 'D.SH'", "E": "thscode = 'E.SH'"})}
    # E：原始 +61.05%，减去同日市场均值 14.33% → 中性 +46.72%
    assert abs(rows["E 单独"]["m5"] - 61.051) < 1e-6
    assert abs(rows["E 单独"]["x5"] - (61.051 - MKT_FWD5)) < 1e-6
    # A：原始收益为正（+5.10%），但跑输同日市场均值 → 中性为负（"涨了但没跑赢"）
    assert rows["A 单独"]["m5"] > 0 and rows["A 单独"]["x5"] < 0
    # D：原始 +10.41% 看着不错，但同日等权均值被 E 拉到 14.33% → 中性同样为负
    assert rows["D 单独"]["m5"] > 0 and rows["D 单独"]["x5"] < 0


def test_market_mean_is_same_day_cross_section(con_var):
    """市场均值必须=**同一交易日**的截面均值。这是唯一能抓住"join 用错日期键"的断言
    —— 用逐日大幅波动的市场因子构造，错一天就会露馅。"""
    sql = """
        SELECT s.date_ms, s.mfwd5, x.expect
        FROM (SELECT DISTINCT date_ms, mfwd5 FROM sigv) s
        JOIN (SELECT date_ms, avg(fwd5) AS expect FROM sig GROUP BY date_ms) x USING (date_ms)
        WHERE s.mfwd5 IS NOT NULL
    """
    rows = con_var.execute(sql).fetchall()
    assert rows and all(abs(got - expect) < 1e-9 for _, got, expect in rows)


def test_market_change_column_is_same_day_median(con_var):
    """环境分层（④）用的 `mchg` = 当日全市场涨幅**中位数**，且必须与个股行按日对齐。"""
    sql = """
        SELECT s.date_ms, s.mchg, x.expect
        FROM (SELECT DISTINCT date_ms, mchg FROM sigv) s
        JOIN (SELECT date_ms, quantile_cont(chg, 0.5) AS expect FROM sig GROUP BY date_ms) x
          USING (date_ms)
        WHERE s.mchg IS NOT NULL
    """
    rows = con_var.execute(sql).fetchall()
    assert rows and all(abs(got - expect) < 1e-9 for _, got, expect in rows)


def test_market_neutral_zero_sum_with_volatile_market(con_var):
    """市场因子逐日 ±4%/−3% 时，全样本市场中性超额仍恒为 0（口径自证）。"""
    base = sv.baseline(con_var)
    assert base["n"] == 3 * (V_DAYS - 20)
    assert abs(base["x5"]) < 1e-9
    # 个股 alpha 全为正/全为负时符号必须正确（alpha 均值 = 0）
    rows = {r["grp"]: r for r in sv.single(con_var, {"A": "thscode = 'A.SH'", "C": "thscode = 'C.SH'"})}
    assert rows["A 单独"]["x5"] > 0 > rows["C 单独"]["x5"]


# ---------------------------------------------------------------- 五道检验

def test_funnel_counts_are_cumulative(con):
    """S1 只留下 A（20 行）；S2 谁都不过 → 卡在 S2 的 20 行，其余 80 行卡在 S1。"""
    rows = {r["grp"]: r["n"] for r in sv.funnel(con, {"S1": "chg BETWEEN 0.5 AND 1.5", "S2": "vr >= 2"})}
    assert rows["1_ 卡在 S1"] == 4 * KEPT
    assert rows["2_ 卡在 S2"] == KEPT
    assert rows["3_ 全部通过"] == 0  # 0 条也必须显式出现，不能从结果里消失


def test_single_conditions_are_independent_not_mutually_exclusive(con):
    """R15（2026-09-14 修）：单条件必须**各自独立**统计，重叠样本可同时进入多组。

    旧实现是 `CASE WHEN (S1) ... WHEN (S2) ...`，**首命中分配** ⇒ 各条件互斥：
    S1 命中的行不会再进 S2，「独立贡献」被记成「增量贡献」，且**调换书写顺序
    结论就变**。本用例同时钉住两条性质：
      ① n 之和不等于总样本量（重叠被重复计数 = 独立性的证据）；
      ② **顺序置换后逐组数值完全相同**（结论可复现）。
    仅断言 ① 不够：一个"两边都少算"的实现也能凑出不等号。
    """
    a = {r["grp"]: r for r in sv.single(con, {"S1": "chg BETWEEN 0.5 AND 1.5", "S2": "vr >= 1"})}
    b = {r["grp"]: r for r in sv.single(con, {"S2": "vr >= 1", "S1": "chg BETWEEN 0.5 AND 1.5"})}
    # 合成数据里 chg 恒定（A +1%/日、其余 0%/日）⇒ S1 命中 A 的全部 20 行，
    # S2（vr 恒 1）命中全部 100 行 ⇒ 重叠 20 行必须**同时**出现在两组里。
    assert a["S1 单独"]["n"] == KEPT
    assert a["S2 单独"]["n"] == 5 * KEPT          # 旧实现这里是 4*KEPT（被 S1 吃掉 20 行）
    assert a["S1 单独"]["n"] + a["S2 单独"]["n"] > 5 * KEPT   # 重叠被重复计数
    assert a["zz 都不满足"]["n"] == 0
    # 顺序置换不改变任何一组的数值（逐字段比对，不只看 n）
    assert {k: {kk: vv for kk, vv in v.items()} for k, v in a.items()} == \
           {k: {kk: vv for kk, vv in v.items()} for k, v in b.items()}


# ---------------------------------------------------------------- R15 成熟度口径

def test_win_rate_denominator_is_mature_samples_only(con):
    """R15 主判据：胜率分母 = **成熟样本数**（`count(fwd_h)`），不是全部样本。

    旧实现 `avg(CASE WHEN net > 0 THEN 1 ELSE 0 END)` 有两处偏差叠加：
    ① `NULL > 0` → NULL → 落 ELSE 0 ⇒ 未成熟样本被**当成亏损**计入分子；
    ② 分母是 `count(*)` ⇒ 长窗口（pending 更多）的胜率被**方向固定地**压低。
    合成数据解析解：每只票 20 行、末尾 5 行 fwd5 未到期 ⇒ 成熟 75 / 未成熟 25；
    成熟样本里 A/D/E 盈（3×15=45）、B 恰好 0（不算盈）、C 亏 ⇒ 45/75 = **0.6**。
    旧口径会报 45/100 = 0.45。
    """
    base = sv.baseline(con, horizons=[5])
    assert base["n5"] == 75 and base["p5"] == 25
    assert base["n5"] + base["p5"] == base["n"] == 5 * KEPT   # 分子分母都看得见
    assert abs(base["w5"] - 0.6) < 1e-9
    # 明确排除旧口径（45/100），否则本断言对"分子漏算"这类退化也成立
    assert abs(base["w5"] - 0.45) > 0.1


def test_pending_count_tracks_horizon_length(con):
    """未成熟计数必须随窗口长度单调增加（fwd1 尾部 1 行 / fwd10 尾部 10 行）。"""
    base = sv.baseline(con, horizons=[1, 5, 10])
    assert (base["n1"], base["p1"]) == (95, 5)
    assert (base["n10"], base["p10"]) == (50, 50)
    assert base["n1"] + base["p1"] == base["n10"] + base["p10"] == 5 * KEPT


def test_all_pending_group_reports_unknown_not_zero(con):
    """全未成熟分组的胜率必须是 **NULL（unknown）**，不是 0 —— 三态纪律。

    尾部 5 行（rn 36..40）的 fwd5 全为 NULL。旧实现会给出 `w5 = 0.0`
    （"零胜率"），那是把一个**未到期的窗口**读成了**确定的失败**。
    """
    rows = sv.stats(con, group="CASE WHEN rn > 35 THEN 'pad' ELSE 'mat' END", horizons=[5])
    by = {r["grp"]: r for r in rows}
    assert by["pad"]["n5"] == 0 and by["pad"]["p5"] == 25
    assert by["pad"]["w5"] is None and by["pad"]["m5"] is None
    assert by["mat"]["n5"] == 75 and by["mat"]["w5"] is not None


def test_summarize_row_exposes_pending(con):
    """核验登记必须同时给出 `n` 与 `pending`——只给 n 会掩盖"分母被谁稀释"。"""
    out = sv.summarize_row(sv.baseline(con, horizons=[5]), horizon=5)
    assert out["n"] == 75 and out["pending"] == 25
    assert abs(out["win_rate"] - 0.6) < 1e-4


def test_sensitivity_keeps_other_conditions_fixed(con):
    """敏感性分析只放开被测参数：样本量应等于"其余条件"的样本量。"""
    band = "CASE WHEN chg < 0 THEN 'a 跌' WHEN chg < 9 THEN 'b 温和' ELSE 'c 涨停附近' END"
    rows = sv.sensitivity(con, band, "vr >= 1")
    assert sum(r["n"] for r in rows) == 5 * KEPT
    by = {r["grp"]: r["n"] for r in rows}
    assert by["a 跌"] == KEPT and by["c 涨停附近"] == KEPT


def test_yearly_groups_by_calendar_year(con):
    """40 天跨 2025/2026 两个自然年 → 两行，且能数出"年度均值为正的年数"。"""
    rows = sv.yearly(con, "thscode = 'D.SH'")
    assert len(rows) == 2
    assert sv.year_counts(rows, horizon=5) == (2, 2)


def test_split_sample_partitions_by_time_with_visible_purge_and_embargo(con):
    mid = int((START + timedelta(days=30)).timestamp() * 1000)
    out = sv.split_sample(con, "chg >= 0", mid, horizons=[5])
    total = con.execute("SELECT count(*) FROM sig WHERE chg >= 0").fetchone()[0]
    parts = [out[k]["n"] for k in ("train", "purged", "embargoed", "test")]
    assert sum(parts) == total
    assert all(n > 0 for n in parts)
    split = out["split"]
    assert split["purge_sessions"] == split["embargo_sessions"] == 5
    assert split["purged_days"] == split["embargo_days"] == 5
    assert split["train_last_ms"] < split["split_date_ms"] < split["test_first_ms"]


def test_split_windows_fail_closed_when_holdout_is_outside_or_consumed(con):
    before = int((START + timedelta(days=1)).timestamp() * 1000)
    after = int((START + timedelta(days=100)).timestamp() * 1000)
    mid = int((START + timedelta(days=30)).timestamp() * 1000)
    with pytest.raises(ValueError, match="样本内部"):
        sv.split_windows(con, before, horizons=[5])
    with pytest.raises(ValueError, match="样本内部"):
        sv.split_windows(con, after, horizons=[5])
    with pytest.raises(ValueError, match="train 或 test 为空"):
        sv.split_windows(con, mid, horizons=[5], purge_sessions=100)


@pytest.mark.parametrize("horizons", [[0], [-1], [True]])
def test_split_windows_rejects_invalid_horizons(con, horizons):
    mid = int((START + timedelta(days=30)).timestamp() * 1000)
    with pytest.raises(ValueError, match="horizons"):
        sv.split_windows(con, mid, horizons=horizons)


def test_limit_up_share_reports_executability(con):
    """可成交性：E 档 100% 落在疑似涨停 → 纸面收益再高也不可执行。"""
    out = sv.limit_up_share(con, "chg >= 5")
    assert out["n"] == KEPT and out["limit_up_share"] == 1.0


# ---------------------------------------------------------------- 边界与口径开关

def test_baseline_returns_zero_row_for_empty_slice(con):
    """空切片是合法输入：返回 n=0，而不是抛 IndexError（曾经的实现如此）。"""
    r = sv.baseline(con, where="FALSE")
    assert r["n"] == 0 and r["m5"] is None and r["x5"] is None


def test_cost_is_deducted_from_every_horizon(con):
    """成本按往返基点扣（100bps = 1 个百分点），市场中性列同样扣
    —— 否则"扣成本后仍有 alpha"就是假的。"""
    raw = sv.baseline(con)
    net = sv.baseline(con, cfg=sv.VerifyConfig(cost_bps=100))
    assert abs((raw["m5"] - net["m5"]) - 1.0) < 1e-9
    assert abs((raw["m1"] - net["m1"]) - 1.0) < 1e-9
    assert abs((raw["x5"] - net["x5"]) - 1.0) < 1e-9


def test_horizons_are_detected_from_built_table(tmp_path):
    """前瞻窗口是**建表期**属性：查询期一律探测，不引用不存在的列。"""
    c = make_con(tmp_path / "h.duckdb", horizons=(2,))
    try:
        assert sv.horizons_of(c) == (2,)
        r = sv.baseline(c)
        assert "m2" in r and "m5" not in r
    finally:
        c.close()


def test_render_includes_header_and_rows(con):
    out = sv.render(sv.funnel(con, {"S1": "chg >= 0"}), sv.horizons_of(con), base=sv.baseline(con))
    assert "中性超额" in out and "基准" in out and "全部通过" in out


def test_date_lo_returns_window_start(con):
    assert sv.date_lo(con, 10_000) < sv.date_lo(con, 5)


def test_welch_t_sign(con):
    a = sv.baseline(con, where="thscode = 'E.SH'")
    b = sv.baseline(con, where="thscode = 'B.SH'")
    assert sv.welch_t(a["m5"], a["s5"], a["n5"], b["m5"], b["s5"], b["n5"]) > 0



def test_bug027_actual_sql_all_pending_summary_stays_zero(con):
    rows = sv.stats(con, group="CASE WHEN rn > 35 THEN 'pad' ELSE 'mat' END", horizons=[5])
    pending = next(row for row in rows if row["grp"] == "pad")
    summary = sv.summarize_row(pending)
    assert pending["n"] == 25 and summary["n"] == 0 and summary["pending"] == 25
    assert summary["sample_basis"] == "mature" and summary["validation_errors"] == []
    result = sv.gate_verdict(summary, yearly_pos=9, yearly_tot=10,
                            limit_up_share=0.05, excess_median=0.2, excess_win_rate=0.57)
    assert result["verdict"] == sv.VERDICT_OBSERVE
    assert any("样本不足" in reason for reason in result["failed"])
