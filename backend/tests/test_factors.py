"""因子库评估引擎测试（合成小仓，验证口径与三态，不依赖真实 marketdb）。

构造要点：
- 15 只「弱」股日收益 -0.2%+noise、15 只「强」股 +0.2%+noise → 动量因子 IC 应显著为正；
- 5 只次新股仅 50 日历史 → 长窗口因子（mom120/vola20 等）必须 NULL（三态，覆盖率 < 1）；
- 1 只全一字板股（high=low）→ 全样本被「买不进」剔除 → FAIL（样本不足）；
- 末尾交易日无 T+h 前瞻 → 自动排除（防泄露的结构性验证）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from app.factors.evaluate import (
    ALGO_VERSION,
    DATA_QUALITY_NOTES,
    DAYS_PER_YEAR,
    MIN_CROSS_SECTION,
    _agg_window,
    _annotate_verdict_changes,
    _base_cte,
    _judge_quintiles,
    _mature_key,
    _pearson,
    evaluate_factor,
    run_full_eval,
)
from app.factors.library import FACTORS, FACTOR_BY_NAME

DAYS = 260
N_STRONG = 15
N_WEAK = 15
N_NEW = 5      # 次新（50 日历史）
N_LIMIT = 1    # 全一字板


def _days(n: int) -> list[int]:
    out = []
    d = datetime(2024, 1, 2, tzinfo=timezone.utc)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(int(d.timestamp() * 1000))
        d += timedelta(days=1)
    return out


def _mk_db(tmp_path):
    days = _days(DAYS)
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT, turnover DOUBLE)"
    )
    con.execute(
        "CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)"
    )
    rows_k, rows_adj = [], []
    rnd = 0.5
    for si in range(N_STRONG + N_WEAK + N_NEW + N_LIMIT):
        code = f"90.XXSHE{600000 + si}"
        hist = days if si < N_STRONG + N_WEAK else days[-50:]  # 次新只留后 50 日
        # 组内异质 alpha：强股 0.1%~0.3%、弱股 -0.3%~-0.1% 均匀排开——
        # 保证截面有持续排序差异（否则 corr 零方差 → NaN）
        if si < N_STRONG:
            alpha = 0.001 + (si / N_STRONG) * 0.002
        elif si < N_STRONG + N_WEAK:
            alpha = -0.003 + ((si - N_STRONG) / N_WEAK) * 0.002
        else:
            alpha = 0.0
        price = 10.0
        for ts in hist:
            # 一字板股原地不动；其余 恒定 alpha + 微噪
            if si >= N_STRONG + N_WEAK + N_NEW:
                ret = 0.0
            else:
                rnd = (rnd * 9301 + 49297) % 233280 / 233280  # 简易 LCG
                ret = alpha + (rnd - 0.5) * 0.001
            price = round(price * (1 + ret), 4)
            o = round(price * (1 + (rnd - 0.5) * 0.002), 4)
            if si >= N_STRONG + N_WEAK + N_NEW:
                hi = lo = price  # 一字板：high == low
            else:
                hi = round(max(o, price) * 1.012, 4)
                lo = round(min(o, price) * 0.988, 4)
            turnover = 5e7 + rnd * 1e8
            rows_k.append((code, ts, o, hi, lo, price, 1_000_000, turnover))
            rows_adj.append((code, ts, price))
    con.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_k)
    con.executemany("INSERT INTO daily_k_adj VALUES (?, ?, ?)", rows_adj)
    return con, dict(con.execute("SELECT date_ms, count(*) FROM daily_k_adj GROUP BY date_ms").fetchall())


@pytest.fixture()
def db(tmp_path):
    con, market_daily = _mk_db(tmp_path)
    yield con, market_daily
    con.close()


# ---------------------------------------------------------------- 纯函数
def test_pearson():
    assert _pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert _pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)
    assert _pearson([1, 1, 1], [1, 2, 3]) is None  # 常数序列
    assert _pearson([1], [1]) is None


# ---------------------------------------------------------------- 单因子管道
def test_momentum_ic_positive(db):
    """构造的趋势结构下，动量因子 IC 必须显著为正（口径正确性）。"""
    con, market_daily = db
    r = evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    w = r["windows"][str(r["best_horizon"])]
    assert w["ic_mean"] > 0.5, f"构造趋势下 mom20 IC 应显著为正，实测 {w['ic_mean']}"
    assert w["n_days"] > 100
    # 强弱结构 → Q5（高动量组）年化收益应显著高于 Q1
    q = r["quintile"]
    assert q["available"]
    assert q["q_avg_ann_simple_pct"]["5"] > q["q_avg_ann_simple_pct"]["1"]


def test_next_new_stocks_three_state(db):
    """次新股（50 日历史）在长窗口因子下必须缺失，不得凑数——覆盖率 < 1。"""
    con, market_daily = db
    r = evaluate_factor(con, FACTOR_BY_NAME["mom120"], market_daily)
    assert r["coverage"] is not None and r["coverage"] < 1.0
    r2 = evaluate_factor(con, FACTOR_BY_NAME["vola20"], market_daily)
    assert r2["coverage"] < 1.0


def test_limit_up_one_word_excluded(db):
    """全一字板股（high=low，买不进）→ 全样本剔除 → FAIL 样本不足。"""
    con, market_daily = db
    con2 = duckdb.connect()
    con2.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT, turnover DOUBLE)"
    )
    con2.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    days = _days(60)
    rows = [(f"90.XXSHE{i}", ts, 10.0, 10.0, 10.0, 10.0, 100, 1e8)
            for i in range(40) for ts in days]
    con2.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con2.executemany(
        "INSERT INTO daily_k_adj VALUES (?, ?, ?)",
        [(f"90.XXSHE{i}", ts, 10.0) for i in range(40) for ts in days],
    )
    md = dict(con2.execute("SELECT date_ms, count(*) FROM daily_k_adj GROUP BY date_ms").fetchall())
    r2 = evaluate_factor(con2, FACTOR_BY_NAME["mom5"], md)
    assert r2["verdict"] == "FAIL"
    assert any("样本不足" in x for x in r2["reasons"])
    con2.close()


def test_no_lookahead_tail(db):
    """末尾交易日没有 T+h 前瞻 → 该日不得计入 IC（防泄露结构验证）。"""
    con, market_daily = db
    r = evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    total_days = len(market_daily)
    assert r["windows"][str(r["best_horizon"])]["n_days"] <= total_days - 3  # 末尾 ≥1 日无 fwd3


# ---------------------------------------------------------------- 全量评估
def test_run_full_eval_end_to_end(db, tmp_path):
    con, _ = db
    con.close()  # run_full_eval 自行开连接
    db_path = None
    # 从 fixture 拿路径：fixture 基于 tmp_path/m.duckdb
    db_path = tmp_path / "m.duckdb"
    out = tmp_path / "factors" / "report.json"
    report = run_full_eval(db_path, out_path=out)
    assert len(report["factors"]) == len(FACTORS)
    assert all(r["verdict"] in ("PASS", "CONDITIONAL", "FAIL") for r in report["factors"])
    assert out.exists()
    # 构造趋势下动量族应至少一个 PASS/CONDITIONAL
    s = report["summary"]
    assert set(s["pass"]) | set(s["conditional"]), "构造趋势下应有因子通过准入"
    # 数据质量结论必须携带（幸存者偏差等）
    assert "survivorship" in report["data_quality"]
    assert DATA_QUALITY_NOTES["survivorship"].startswith("universe")


def test_registry_complete():
    """注册表完整性：11 因子、名字唯一、min_bars 单调合理。"""
    names = [f.name for f in FACTORS]
    assert len(names) == len(set(names))
    assert set(names) >= {"mom5", "mom10", "mom20", "mom60", "mom120",
                          "vola20", "amt_ratio", "liq20", "amihud20", "gap", "range20"}
    assert FACTOR_BY_NAME["mom120"].min_bars == 121
    assert FACTOR_BY_NAME["mom5"].min_bars == 6


# ---------------------------------------------------------------- 年化口径（R17）
def _qrows(period_by_q: dict[int, float], per_year: dict | None = None) -> list[dict]:
    """构造 `_judge_quintiles` 输入：`ALL` 行 + 可选分年行。"""
    rows = [{"yr": "ALL", "q": q, "avg_fwd": v} for q, v in period_by_q.items()]
    for yr, mapping in (per_year or {}).items():
        rows += [{"yr": yr, "q": q, "avg_fwd": v} for q, v in mapping.items()]
    return rows


_FLAT = {1: -0.01, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.03}


@pytest.mark.parametrize("horizon", [3, 5, 10, 20])
def test_quintile_annualization_uses_hold_days(horizon):
    """R17：年化缩放系数必须是 243/(h−1)——期间收益是「持有 h−1 个交易日」的收益。

    旧实现直接用 `×DAYS_PER_YEAR`（= 把期间收益当日收益），h=20 时高估 19 倍。
    """
    hold_days = horizon - 1
    q = _judge_quintiles(_qrows(_FLAT), horizon)

    assert q["available"]
    assert q["horizon"] == horizon
    assert q["hold_days"] == hold_days
    assert q["annualization"] == "simple_linear"
    # 一手量：期间收益原样保留，不做任何缩放
    assert q["q_avg_period"]["5"] == pytest.approx(0.03)
    assert q["q_avg_period"]["1"] == pytest.approx(-0.01)
    assert q["long_short_period"] == pytest.approx(0.04)
    # 日均近似 = 期间收益 / (h−1)（字段量化到 8 位小数 ⇒ 容差按步长取 1e-8）
    assert q["q_avg_daily_approx"]["5"] == pytest.approx(0.03 / hold_days, abs=1e-8)
    # 简单线性年化 = 期间收益 × 243/(h−1)（绝对断言；百分数字段量化到 2 位小数 ⇒ abs=0.01）
    assert q["long_short_ann_pct"] == pytest.approx(
        0.04 * DAYS_PER_YEAR / hold_days * 100, abs=0.01
    )
    # 缩放系数恒为 243/(h−1)：与误口径 ×243 之比恰等于 h−1
    # （`long_short_ann_pct` 已量化到 2 位小数 ⇒ 容差按量化步长放宽，不是口径误差）
    naive = 0.04 * DAYS_PER_YEAR * 100
    assert naive / q["long_short_ann_pct"] == pytest.approx(hold_days, rel=1e-4)


def test_quintile_annualization_matches_review_case():
    """审查报告原文用例落钉：19 日持有期、期间多空 2% ⇒ 简单年化 ≈ 25.58%，不是 486%。"""
    rows = _qrows({1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.02})
    q = _judge_quintiles(rows, 20)
    assert q["long_short_period"] == pytest.approx(0.02)
    assert q["long_short_ann_pct"] == pytest.approx(25.58, abs=0.01)
    assert abs(q["long_short_ann_pct"] - 486.0) > 100  # 误口径值必须已被排除


def test_quintile_no_implementable_annual_return():
    """验收第 4 条：重叠持有时不得把缩放值冒充可实现年化收益——三态必须显式为 None。"""
    q = _judge_quintiles(_qrows(_FLAT), 10)
    assert q["implementable_annual_return_pct"] is None
    assert "重叠" in q["note"] and "非可实现" in q["note"]


def test_quintile_yearly_ls_is_q5_minus_q1():
    """R17：年度多空必须是**同年 Q5 − Q1**；旧实现只取 Q5（含市场 beta 的绝对收益）。"""
    rows = _qrows(
        {1: -0.02, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.04},
        per_year={"2024": {1: -0.01, 5: 0.03}, "2025": {1: 0.02, 5: -0.05}},
    )
    q = _judge_quintiles(rows, 5)  # hold_days = 4
    assert q["yearly_ls_period"]["2024"] == pytest.approx(0.04)    # 0.03 − (−0.01)
    assert q["yearly_ls_period"]["2025"] == pytest.approx(-0.07)   # −0.05 − 0.02（可为负）
    # 绝对断言（非相对）：年化 = 期间 × 243/4
    assert q["yearly_ls_ann_pct"]["2024"] == pytest.approx(0.04 * 243 / 4 * 100, abs=1e-9)
    assert q["yearly_ls_ann_pct"]["2025"] == pytest.approx(-0.07 * 243 / 4 * 100, abs=1e-9)
    # 旧口径（只取 Q5）会得到 +182.25% / −303.75%：必须与之不等
    assert abs(q["yearly_ls_ann_pct"]["2024"] - 0.03 * 243 / 4 * 100) > 1e-6
    assert abs(q["yearly_ls_ann_pct"]["2025"] - (-0.05 * 243 / 4 * 100)) > 1e-6


def test_quintile_yearly_missing_quantile_is_skipped():
    """三态：某年缺 Q1 或 Q5 时该年不产出年度多空，不得凑 0。"""
    rows = _qrows(
        _FLAT,
        per_year={"2024": {1: -0.01, 5: 0.03}, "2025": {2: 0.0, 5: 0.03}},
    )
    q = _judge_quintiles(rows, 5)
    assert "2024" in q["yearly_ls_period"]
    assert "2025" not in q["yearly_ls_period"]   # 缺 Q1
    assert "2025" not in q["yearly_ls_ann_pct"]


def test_quintile_field_names_reject_daily_semantics():
    """命名守卫：`q_avg_daily` / `q_ann` 是「把期间收益当日收益」的名字，不得回潮（R17 根因）。"""
    q = _judge_quintiles(_qrows(_FLAT), 5)
    assert "q_avg_daily" not in q and "q_ann" not in q
    assert {"q_avg_period", "q_avg_daily_approx", "q_avg_ann_simple_pct"} <= set(q)


def test_quintile_horizon_one_rejected():
    """持有期 = horizon−1 必须 ≥1；h=1（T+1 进 T+1 出、零持有）应显式报错。

    旧实现没有 horizon 参数，这类非法输入会静默算出无意义年化。
    """
    with pytest.raises(ValueError):
        _judge_quintiles(_qrows(_FLAT), 1)


def test_evaluate_factor_quintile_carries_hold_days(db):
    """端到端：主窗口年化系数必须与 best_horizon 一致（逐组绝对核对）。"""
    con, market_daily = db
    r = evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    q = r["quintile"]
    h = r["best_horizon"]
    assert h in (3, 5, 10, 20), f"主窗口应取自执行窗口集，实测 {h}"
    assert q["horizon"] == h and q["hold_days"] == h - 1
    assert q["implementable_annual_return_pct"] is None
    for k, period in q["q_avg_period"].items():
        assert q["q_avg_ann_simple_pct"][k] == pytest.approx(
            period * DAYS_PER_YEAR / (h - 1) * 100, abs=0.01
        )


# ---------------------------------------------------------------- IC 成熟度口径（R16）

R16_DAYS = 60
R16_N_LONG = 34
R16_N_SHORT = 6
R16_SHORT_FULL = 55    # DB1：短历史股票 55 行（尾部比长历史少 5 行）
# DB2：把短历史股票的**未成熟行整段删掉**（只留 0..34）——用于「删掉不该影响结果的行，
# 结果必须逐位不变」的不变量测试。区间 [35,39] 上：长历史股票 20 日窗口已成熟，
# 短历史股票尚未成熟 ⇒ DB1 该日 n=40 / n20=34，DB2 该日 n=34 / n20=34。
R16_SHORT_TRUNC = 35
#: 区间 [35,39]：长历史股票（59）的 20 日窗口已成熟，短历史股票（54）尚未成熟。
#: ⚠️ 用**真实 date_ms**（不是序号）——年份序号与毫秒时间戳混用会静默比不中任何一天。
R16_PARTIAL_DATES = _days(R16_DAYS)[35:40]


def _mk_hetero_db(tmp_path, name: str, *, short_rows: int):
    """**尾部长度不一致**的小仓 —— 这是 R16 的唯一充分条件。

    尾部整齐的仓（所有股票同起同止）在任一交易日内要么全成熟、要么全不成熟，
    两版实现给出同样的结果：**测了等于没测**。只有同一天内「部分成熟」才能测出
    「未成熟样本带假名次进入 corr」与「守卫拿全截面 n 放行」。
    """
    days = _days(R16_DAYS)
    con = duckdb.connect(str(tmp_path / name))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT, turnover DOUBLE)"
    )
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    rows_k, rows_adj = [], []
    rnd = 0.37
    for si in range(R16_N_LONG + R16_N_SHORT):
        code = f"90.XXHET{700000 + si}"
        hist = days if si < R16_N_LONG else days[:short_rows]
        alpha = 0.0012 if si % 2 == 0 else -0.0012
        price = 10.0
        for ts in hist:
            rnd = (rnd * 9301 + 49297) % 233280 / 233280
            price = round(price * (1 + alpha + (rnd - 0.5) * 0.004), 4)
            o = round(price * (1 + (rnd - 0.5) * 0.002), 4)
            rows_k.append((code, ts, o, round(max(o, price) * 1.01, 4),
                           round(min(o, price) * 0.99, 4), price, 1_000_000, 5e7 + rnd * 1e8))
            rows_adj.append((code, ts, price))
    con.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_k)
    con.executemany("INSERT INTO daily_k_adj VALUES (?, ?, ?)", rows_adj)
    return con


def _ic20_rows(con, factor_name: str = "mom20") -> dict[int, dict]:
    """跑生产 `_base_sql`，返回 {date_ms: daily 行}。"""
    from app.factors.evaluate import _base_sql

    cur = con.execute(_base_sql(FACTOR_BY_NAME[factor_name]))
    cols = [d[0] for d in cur.description]
    return {r[cols.index("date_ms")]: dict(zip(cols, r)) for r in cur.fetchall()}


def test_daily_exposes_per_horizon_mature_count(db):
    """`daily` 必须输出各窗口自己的成熟计数 `n{h}`（close 口径 `nc{h}`）——守卫的输入。"""
    con, _ = db
    row = next(iter(_ic20_rows(con, "mom20").values()))
    for h in (3, 5, 10, 20):
        assert f"n{h}" in row, f"缺成熟计数列 n{h}"
        assert _mature_key(h) == f"n{h}"
    assert _mature_key(1) == "nc1"          # close 口径必须与执行窗口分开命名
    # 各窗口成熟数必须 ≤ 全截面、且随窗口变长单调不增（结构自证）
    assert row["n20"] <= row["n10"] <= row["n5"] <= row["n3"] <= row["n"]


def test_immature_rows_do_not_influence_ic(tmp_path):
    """R16 **行为级主判据**：把未成熟行整段删掉，同一交易日的 `ic20` 必须逐位不变。

    这是实现无关的断言——它不关心"怎么排名"，只要求「尚未到期的样本」对 IC **零影响」。
    区间 [35,39] 上 DB1 有 40 行（20 日窗口仅 34 只成熟），DB2 只有那 34 行；
    两边的 ic20 必须完全相等（旧实现会把 6 个未成熟样本带假名次塞进 corr，
    每个交易日的 ic20 都会偏移 ⇒ 本用例在旧实现下变红，已由注入验证确认）。
    """
    a = _mk_hetero_db(tmp_path, "a.duckdb", short_rows=R16_SHORT_FULL)
    b = _mk_hetero_db(tmp_path, "b.duckdb", short_rows=R16_SHORT_TRUNC)
    try:
        da, dbb = _ic20_rows(a), _ic20_rows(b)
        for ts in R16_PARTIAL_DATES:
            assert ts in da and ts in dbb, "区间 [35,39] 两侧都必须有该交易日"
            assert da[ts]["n"] == R16_N_LONG + R16_N_SHORT   # 全截面 40（旧守卫会放行）
            assert da[ts]["n20"] == R16_N_LONG               # 成熟仅 34
            assert dbb[ts]["n"] == dbb[ts]["n20"] == R16_N_LONG
            assert da[ts]["n20"] >= MIN_CROSS_SECTION        # 成熟数过线 ⇒ 该日确实参与聚合
            assert da[ts]["ic20"] == pytest.approx(dbb[ts]["ic20"], abs=1e-12), ts
    finally:
        a.close()
        b.close()


def test_fully_immature_date_has_null_ic(tmp_path):
    """三态：整日样本都未到期时 `ic20` 必须是 **NULL**（不是 0、也不是一个数）。

    [40,54] 区间的交易日，长历史股票的 20 日窗口也已未成熟 ⇒ 成熟截面 0。
    """
    con = _mk_hetero_db(tmp_path, "c.duckdb", short_rows=R16_SHORT_FULL)
    try:
        rows = _ic20_rows(con)
        full = [ts for ts, r in rows.items() if r["n20"] == 0]
        assert full, "构造数据必须存在整日未成熟的交易日"
        assert all(rows[ts]["ic20"] is None for ts in full)
    finally:
        con.close()


def test_agg_window_guard_uses_per_horizon_mature_count():
    """R16 **判据正确性**：注入 `n_key` 后，守卫读的是该窗口的成熟数而不是全截面 n。

    构造「全截面 32（旧判据放行）、20 日窗口仅 24（新判据必须拦下）」的 31 天：
    旧路径（不传 `n_key`）保留、新路径丢弃——两条路径都必须出现，否则测不出差异
    （KB-ENG-65：A/B 等价对照钉不住"两路共用的判据本身失效"）。
    """
    def _daily(n20: int) -> list[dict]:
        return [{"date_ms": i, "ic20": 0.1, "ic3": 0.1, "n": 32, "n20": n20, "n3": 32}
                for i in range(31)]

    assert _agg_window(_daily(24), 20, 0.0) is not None                 # 旧行为对照
    assert _agg_window(_daily(24), 20, 0.0, n_key="n20") is None        # 成熟不足 ⇒ 丢弃
    assert _agg_window(_daily(32), 20, 0.0, n_key="n20") is not None    # 成熟足够 ⇒ 保留
    # 同一份输入、不同窗口：3 日窗口成熟 32 只，不受 20 日窗口拖累
    assert _agg_window(_daily(24), 3, 0.0, n_key="n3") is not None


def test_agg_window_mature_boundary_29_30():
    """R16 验收明确要求「成熟 29/30 边界」：29 不得过线，30 恰好过线。"""
    def _daily(mature: int) -> list[dict]:
        return [{"date_ms": i, "ic20": 0.1, "n": 40, "n20": mature} for i in range(30)]

    assert MIN_CROSS_SECTION == 30
    assert _agg_window(_daily(29), 20, 0.0, n_key="n20") is None
    assert _agg_window(_daily(30), 20, 0.0, n_key="n20") is not None


def test_report_records_maturity(db):
    """成熟度必须进报告（R16）：`n` 是多少只、各窗口成熟率多少，都要能被看见。"""
    con, market_daily = db
    r = evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    m = r["maturity"]
    assert set(m) == {"1", "3", "5", "10", "20"}
    for h in ("3", "5", "10", "20"):
        assert m[h]["days"] > 0
        assert 0 < m[h]["mature_rate"] <= 1.0
        # 窗口越长、成熟率越低（尾部未到期样本更多）
    assert m["20"]["mature_rate"] < m["3"]["mature_rate"]
    assert m["3"]["days_ge_min"] <= m["3"]["days"]


def test_annotate_verdict_changes_pure():
    """R15-R17 验收：旧结论保留、翻转标待复核、未翻转不误标（纯函数，无管道依赖）。"""
    results = [
        {"name": "a", "verdict": "PASS"},        # 旧 PASS → 现 PASS：不变
        {"name": "b", "verdict": "FAIL"},        # 旧 PASS → 现 FAIL：翻转，须标 pending
        {"name": "c", "verdict": "CONDITIONAL"}, # 旧 CONDITIONAL → 现 CONDITIONAL：不变
        {"name": "d", "verdict": "PASS"},        # 旧报告里没有（新增因子）：verdict_prev=None
    ]
    recheck = _annotate_verdict_changes(results, {"a": "PASS", "b": "PASS", "c": "CONDITIONAL"})

    assert [r["verdict_prev"] for r in results] == ["PASS", "PASS", "CONDITIONAL", None]
    assert [r["verdict_changed"] for r in results] == [False, True, False, False]
    assert results[1]["recheck"] == "pending"
    assert "recheck" not in results[0] and "recheck" not in results[2]
    assert "recheck" not in results[3]        # 无旧结论 ⇒ 不判翻转（不把新增当翻转）
    assert recheck == [{"name": "b", "verdict_prev": "PASS", "verdict_now": "FAIL"}]


def test_run_full_eval_records_algo_version_and_recheck(db, tmp_path):
    """R15-R17：口径版本入报告；旧结论保留为 `verdict_prev`，翻转项标 `recheck` 待复核。

    只断言**与管道噪声无关的不变量**（`verdict_prev` 直接来自落盘旧报告）：
    合成仓在准入阈值附近存在 run-to-run 抖动（见 §六 观察项），故不钉「翻转集合恰等于某因子」。
    """
    con, _ = db
    con.close()
    db_path = tmp_path / "m.duckdb"
    out = tmp_path / "factors" / "report.json"

    rep1 = run_full_eval(db_path, out_path=out)
    assert rep1["algo_version"] == ALGO_VERSION
    assert rep1["protocol"]["algo_version"] == ALGO_VERSION
    ann = rep1["protocol"]["annualization"]
    assert ann["method"] == "simple_linear"
    assert ann["factor"] == "243/(h−1)"
    assert ann["approximation"] is True and ann["implementable"] is False
    assert rep1["recheck"] == []                              # 首次跑无旧结论可比
    assert all(r["verdict_prev"] is None for r in rep1["factors"])
    v1 = {r["name"]: r["verdict"] for r in rep1["factors"]}

    # 篡改落盘报告的某一因子结论 → 重算必须原样保留为 verdict_prev（不静默覆盖）
    old = json.loads(out.read_text(encoding="utf-8"))
    victim = old["factors"][0]
    tampered = "FAIL" if victim["verdict"] != "FAIL" else "PASS"
    victim["verdict"] = tampered
    out.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    rep2 = run_full_eval(db_path, out_path=out)
    assert {r["name"]: r["verdict_prev"] for r in rep2["factors"]}[victim["name"]] == tampered
    # 未篡改的因子，旧结论必须与 rep1 逐字一致
    for r in rep2["factors"]:
        if r["name"] != victim["name"]:
            assert r["verdict_prev"] == v1[r["name"]], r["name"]
    # 待复核清单与逐因子翻转标记双向一致
    assert [x["name"] for x in rep2["recheck"]] == [
        r["name"] for r in rep2["factors"] if r["verdict_changed"]
    ]
    for x in rep2["recheck"]:
        assert x["verdict_prev"] != x["verdict_now"]
    assert victim["name"] in [x["name"] for x in rep2["recheck"]]


def test_run_full_eval_archives_prev_report_and_lists_versioned_review(db, tmp_path):
    """GOV-001 端到端：覆盖前**归档**旧报告；口径变更时 `review_required` 覆盖**全部**旧结论。

    做法：先跑一次拿真实结论 → 把落盘报告的口径伪造成旧版本 → 再跑一次。
    第二轮必须同时满足：① 第一轮报告已归档进 `history/`；② `prev_algo_version` = 伪造值；
    ③ `algo_changed=True`；④ `review_required` = **所有有旧结论的因子**（含三态未变的），
    而 `recheck` 严格是它的子集（只管翻转）——**翻转清单 ≠ 复核清单**。
    """
    con, _ = db
    con.close()
    db_path = tmp_path / "m.duckdb"
    out = tmp_path / "factors" / "report.json"

    rep1 = run_full_eval(db_path, out_path=out)
    assert rep1["prev_algo_version"] is None     # 首次跑：无旧报告
    assert rep1["algo_changed"] is None          # 无历史 ⇒ **未判定**（不是"未变更"）
    assert rep1["review_required"] == []
    assert rep1["archived_prev_report"] is None

    # 伪造旧口径（模拟「报告由更早的口径产出」）
    old = json.loads(out.read_text(encoding="utf-8"))
    old["algo_version"] = "2020-01-01.legacy"
    out.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    rep2 = run_full_eval(db_path, out_path=out)

    hist = out.parent / "history"
    assert hist.is_dir(), "旧报告未被版本化留存（制度 §7.2 的承诺仍只写在文档里）"
    assert any("2020-01-01.legacy" in p.name for p in hist.iterdir())
    assert rep2["archived_prev_report"] and "2020-01-01.legacy" in rep2["archived_prev_report"]

    assert rep2["prev_algo_version"] == "2020-01-01.legacy"
    assert rep2["algo_changed"] is True

    review = {x["name"] for x in rep2["review_required"]}
    recheck = {x["name"] for x in rep2["recheck"]}
    assert recheck <= review, "翻转项必须包含在复核清单内"
    # 复核清单 = 全部有旧结论的因子（含未翻转），不只是一部分
    assert review == {r["name"] for r in rep2["factors"] if r["verdict_prev"] is not None}
    assert all("2020-01-01.legacy" in x["reason"] for x in rep2["review_required"])


# ---------------------------------------------------------------- RSH-001：rank20（qlib 滚动窗口百分位）
RANK20_W = 20
RANK20_DATES = 30


def _mk_rank20_db(tmp_path):
    """rank20 专用小仓（30 交易日；窗口 20 行、`min_bars` 21）——**手算可验证**的五种形态。

    - `tie`    大量并列 → 钉「平均名次」口径（该股上必然与 `<=` 口径分叉）
    - `flat`   全并列   → 并列口径的唯一判别器：平均名次 = (n+1)/2n < 1，`<=` 口径恒 = 1
    - `mono`   严格单调 → 无并列，两口径一致（证明分叉**只在**并列处）
    - `null`   前两日缺数 → 三态：窗口内含 NULL 的行必须 NULL；同时是「分母须用
                 `COUNT(col)` 而非 `len(list(col))`」的判别点（见第三条用例）
    - `sparse` 行不连续 → 窗口按**行**滑动而非按日历；朴素参考必须按该股**自身行序**取窗口

    返回 `(con, series)`；`series[code]` 为该股按日期升序的 close_adj（**已剔除被丢弃的行**），
    供朴素参考实现独立复算 —— 即「同源判据」：生产 SQL 与参考实现共用同一份输入。
    """
    dates = _days(RANK20_DATES)
    raw: dict[str, list[float | None]] = {
        "tie": [10.0, 12.0, 12.0, 11.0, 12.0, 10.0] * 5,
        "flat": [7.0] * RANK20_DATES,
        "mono": [float(20 + i) for i in range(RANK20_DATES)],
        "null": [None if i < 2 else 10.0 + float(i % 5) for i in range(RANK20_DATES)],
        "sparse": [9.0 + float(i % 4) for i in range(RANK20_DATES)],
    }
    dropped = {"sparse": {3, 4, 11, 12, 13, 22}}
    series: dict[str, list[float | None]] = {}
    rows_k, rows_adj = [], []
    for code, vals in raw.items():
        kept: list[float | None] = []
        for i, v in enumerate(vals):
            if i in dropped.get(code, ()):
                continue
            kept.append(v)
            c = v if v is not None else 8.0
            rows_k.append((code, dates[i], c * 0.99, c * 1.02, c * 0.98, c, 1_000_000, 5e7))
            rows_adj.append((code, dates[i], v))
        series[code] = kept
    con = duckdb.connect(str(tmp_path / "rank20.duckdb"))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT, turnover DOUBLE)"
    )
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    con.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?)", rows_k)
    con.executemany("INSERT INTO daily_k_adj VALUES (?,?,?)", rows_adj)
    return con, series


def _rank20_rows(con) -> dict[str, list[tuple[int, int, float | None]]]:
    """用**生产** `_base_cte` 取 `(date_ms, w20_n, rank20_w)`。

    刻意复用生产 SQL 而不是在测试里重写一份等价 SQL —— 重写会造出「两处都错得一样」
    也能全绿的假护栏（同源恒等式纪律）。`w20_n` 是窗口内**非 NULL** 样本数（分母）。
    """
    sql = _base_cte(FACTOR_BY_NAME["rank20"]) + (
        "\nSELECT thscode, date_ms, w20_n, rank20_w FROM lvl4 ORDER BY thscode, date_ms"
    )
    out: dict[str, list[tuple[int, int, float | None]]] = {}
    for code, ts, n, v in con.execute(sql).fetchall():
        out.setdefault(code, []).append((ts, n, v))
    return out


def _naive_rank20(seq: list[float | None]) -> list[float | None]:
    """朴素参考实现（独立于 SQL）：20 行滑动窗口内的**平均名次**百分位。

    逐条对齐 `evaluate.py::lvl4.rank20_w`：
    - 窗口 = 该股自身**行序**上最近 20 行（含当前行）——按行不按日历（停牌跳空自然处理）；
    - 分母 = 窗口内非 NULL 的 close_adj 个数；
    - 有效样本 < 20 或当前行 close_adj 缺失 → None（三态，不凑 0）；
    - 取值 = (n_lt + n_le + 1) / (2n)，等价 pandas `rank(pct=True, method="average")`。
    """
    out: list[float | None] = []
    for i, cur in enumerate(seq):
        win = seq[max(0, i - RANK20_W + 1): i + 1]
        valid = [v for v in win if v is not None]
        if cur is None or len(valid) < RANK20_W:
            out.append(None)
            continue
        n_lt = sum(1 for v in valid if v < cur)
        n_le = sum(1 for v in valid if v <= cur)
        out.append((n_lt + n_le + 1) / (2 * len(valid)))
    return out


def test_rank20_matches_naive_rolling_window_percentile(tmp_path):
    """RSH-001 **主判据（同源）**：生产 `_base_cte` 的 SQL 与独立朴素参考实现逐点一致。

    覆盖：并列 / 全并列 / 严格单调 / 含缺数 / 行不连续五形态，共 5 只票 × 最多 30 日。
    """
    con, series = _mk_rank20_db(tmp_path)
    try:
        got = _rank20_rows(con)
    finally:
        con.close()

    n_val = n_null = 0
    for code, seq in series.items():
        exp = _naive_rank20(seq)
        rows = got[code]
        assert len(rows) == len(seq) == len(exp), code
        for (ts, _n, sql_v), e in zip(rows, exp):
            if e is None:
                assert sql_v is None, f"{code}@{ts}: 期望 NULL，实测 {sql_v}"
                n_null += 1
            else:
                assert sql_v is not None, f"{code}@{ts}: 期望 {e}，实测 NULL"
                assert sql_v == pytest.approx(e, abs=1e-12), f"{code}@{ts}"
                n_val += 1
    # 防「全 NULL / 全跳过」式空断言：两侧都必须有足量样本
    assert n_val >= 40 and n_null >= 40, (n_val, n_null)


def test_rank20_tie_semantics_pinned_to_average_rank(tmp_path):
    """并列语义必须钉死：全并列窗口给出 (n+1)/(2n)，**不是** 1.0（`<=`／最大名次口径）。

    `flat` 股全并列且窗口恒满 20 样本 ⇒ 平均名次 = 21/40 = 0.525；`<=` 口径恒 = 1.0。
    两口径**只在并列处**分叉，故本用例是并列语义的唯一判别器（`mono` 股则两口径一致）。
    """
    con, series = _mk_rank20_db(tmp_path)
    try:
        got = _rank20_rows(con)
    finally:
        con.close()

    flat = [v for _ts, _n, v in got["flat"] if v is not None]
    assert len(flat) == RANK20_DATES - RANK20_W + 1, flat      # 恰 11 行窗口已满
    assert all(v == pytest.approx(0.525, abs=1e-12) for v in flat), flat
    assert not any(abs(v - 1.0) < 1e-12 for v in flat), "取值落到了 <= 口径上（并列取最大名次）"

    # 单调股无并列 ⇒ 两口径一致：窗口满时现价即窗口最大值 ⇒ 恒 1.0
    mono = [v for _ts, _n, v in got["mono"] if v is not None]
    assert mono and all(v == pytest.approx(1.0, abs=1e-12) for v in mono), mono


def test_rank20_window_with_missing_bars_is_null_not_zero(tmp_path):
    """三态纪律 + 分母正确性：窗口内含缺数时必须 NULL，**并证明分母不能取 `len(list(...))`**。

    `null` 股前两日缺数 ⇒ 第 20 行（1-based）的窗口**行数已达 20**，但有效样本只有 18。
    这正是判别点：若分母或用守卫误用 `len(list(close_adj) OVER r20c)`（**含 NULL**），
    该行会算出 0.0~1.0 的**假值**（把缺数当成"最弱样本"）；正确实现必须给 NULL。

    诚实边界：生产库 `close_adj` 实测**无 NULL**（10 271 084 行 / 0 例）⇒ 本路径当前是**防御性**的；
    「窗口有效样本不足」才是活路径（次新股上市后前 20 日）。两条都必须能变红，故用手工仓覆盖。
    """
    con, _series = _mk_rank20_db(tmp_path)
    try:
        got = _rank20_rows(con)
        # 机制证据：同一行的「窗口行数」与「有效样本数」在此确实分叉
        probe = con.execute(
            _base_cte(FACTOR_BY_NAME["rank20"])
            + "\nSELECT thscode, date_ms, w20_n,"
            " len(list(close_adj) OVER (PARTITION BY thscode ORDER BY date_ms"
            " ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) AS n_list"
            " FROM lvl4 WHERE thscode = 'null' ORDER BY date_ms"
        ).fetchall()
    finally:
        con.close()

    rows = got["null"]
    assert rows[0][2] is None and rows[1][2] is None, "缺数行本身必须 NULL"
    # 第 20 行：窗口 20 行已满、有效样本 18 ⇒ 必须 NULL
    assert rows[RANK20_W - 1][1] == 18, rows[RANK20_W - 1]
    assert rows[RANK20_W - 1][2] is None, "有效样本不足却给了值（分母／守卫误用 len(list)）"
    assert probe[RANK20_W - 1][3] == RANK20_W and probe[RANK20_W - 1][2] == 18, probe[RANK20_W - 1]
    # 第 21 行：窗口含 1 个缺数 ⇒ 有效样本 19 ⇒ 仍必须 NULL
    assert rows[RANK20_W][1] == 19 and rows[RANK20_W][2] is None
    # 第 22 行起窗口干净 ⇒ 必须有值（防「整列 NULL 也算通过」）
    assert rows[RANK20_W + 1][1] == RANK20_W and rows[RANK20_W + 1][2] is not None
    assert all(r[2] is not None for r in rows[RANK20_W + 1:]), "窗口已干净却仍 NULL"


def test_adding_factor_does_not_change_existing_numeric_conclusions(db, tmp_path, monkeypatch):
    """RSH-001 **定例的判据**：新增因子属**纯附加**——既有因子的数值结论必须逐字不变。

    这正是「新增因子**不** bump `ALGO_VERSION`」的依据（见 `evaluate.py` 该常量下方注释）：
    `ALGO_VERSION` 承载的是判定口径（signal/entry/exit、剔除、排名与截面守卫、阈值）；
    纯新增因子不改其中任何一项，也不改任何既有因子的输出 ⇒ 不构成口径变更。若仅因新增就
    bump，`review_required` 会把**数值并未失效**的历史结论全标为待复核（误报）。
    因子池自身的可见性由报告 `factors`/`summary` 列表承担，并记入制度 §8 版本日志。

    **与固有抖动分离**（本用例的关键设计）：`ntile(5) OVER (... ORDER BY f)` 无 tie-break，
    并行执行下并列块的切分顺序不定（账本 `BUG-002`）⇒ `quintile` 层**天然不可复现**。
    故不能简单地"排除 `quintile` 了事"，而是**先同池连跑两次建立抖动基线**，
    再拿「同池两次的差异」去解释「加因子前后的差异」——把新因子的影响从既有缺陷里摘出来。
    基线里稳定、且加因子前后不一致的字段，才是真回归。
    """
    con, _ = db
    con.close()
    db_path = tmp_path / "m.duckdb"
    import app.factors.evaluate as ev

    full = ev.run_full_eval(db_path, out_path=tmp_path / "full.json")
    again = ev.run_full_eval(db_path, out_path=tmp_path / "again.json")  # 同池第二次
    keep = tuple(f for f in FACTORS if f.name != "rank20")
    assert len(keep) == len(FACTORS) - 1
    monkeypatch.setattr(ev, "FACTORS", keep)
    base = ev.run_full_eval(db_path, out_path=tmp_path / "base.json")

    f1 = {r["name"]: r for r in full["factors"]}
    f2 = {r["name"]: r for r in again["factors"]}
    fb = {r["name"]: r for r in base["factors"]}

    # ---- ① 固有抖动基线：IC 层必须零抖动；quintile 层应当抖（否则说明 BUG-002 已修）
    assert all(f1[n]["windows"] == f2[n]["windows"] for n in f1), "同池两次的 windows 应零抖动"
    assert all(f1[n]["daily_ic"] == f2[n]["daily_ic"] for n in f1), "同池两次的 daily_ic 应零抖动"
    jitter = [n for n in f1 if "quintile" in f1[n] and f1[n]["quintile"] != f2[n]["quintile"]]
    assert jitter, (
        "同池两次的 quintile 未出现抖动 ⇒ BUG-002 似已修复："
        "请把 `quintile` 移出下方 skip 集并收紧本用例"
    )

    # ---- ② 加因子前后：基线中稳定的字段必须逐字相同
    #: 排除项及其理由：`quintile` = 上述固有抖动（BUG-002）；`verdict` 由 quintile 派生，
    #: 同样受影响；`redundant_with`/`reasons` 是「去重提示、人工取舍」而非结论，按新池重算；
    #: `recheck` 只在有历史结论时出现（两次都是首次跑）。
    skip = {"quintile", "verdict", "redundant_with", "reasons", "recheck"}
    #: 钉死「稳定集」的成员：将来新增字段必须在此显式归类，不允许悄悄溜出比对范围。
    pinned = {
        "best_horizon", "category", "coverage", "daily_ic", "maturity",
        "min_bars", "name", "note", "rolling", "verdict_changed", "verdict_prev", "windows",
    }
    assert set(f1["mom20"]) - skip == pinned, sorted(set(f1["mom20"]) - skip)

    assert set(f1) - set(fb) == {"rank20"}, "两者差异必须恰是新因子"
    for name, rb in fb.items():
        ra = f1[name]
        assert sorted(ra) == sorted(rb), (name, sorted(ra), sorted(rb))
        for k in sorted(set(ra) - skip):
            assert ra[k] == rb[k], (name, k)


def test_additivity_guard_can_actually_fail(db, monkeypatch):
    """**判据自证**：把 base 链里**共享**的 `c20` 改动一点，「逐字不变」必须变红。

    没有这条，上面那条同源比对可能只是「比较了两次同样的东西」而永远全绿
    （KB-ENG-65：守卫必须能变红；必须是改真实行为，加注释充数不算）。

    ⚠️ **注入必须"改序"，不能只"改值"**——本条曾两次注入失败而**全绿通过**，两次都不是
    "守卫太弱"，而是注入本身没动到排序：
    ① `c20 → c20 × 1.01`：`f = close/c20 − 1` 在缩放下变成 `(f−0.01)/1.01`，是 `f` 的
       **仿射单调变换**，而 IC 走 `percent_rank`、**对单调变换完全不变**（`liq20` note 同性质）；
    ② `c20 → c20 × (1 + 0.5·(rn % 2))`：本夹具的老股**同日上市、行号一致** ⇒ `rn % 2` 在任一
       交易日内**跨股票取同值**，退化成又一次全局缩放，仍是仿射变换。
    故此处按 `hash(thscode)` 缩放——**同一交易日内跨股票不同**，才能真正打乱截面排序。
    """
    con, market_daily = db
    import app.factors.evaluate as ev

    orig = ev._base_cte

    def _perturbed(factor):
        sql = orig(factor)
        patched = sql.replace(
            "LAG(close_adj, 20)  OVER w AS c20",
            "(LAG(close_adj, 20) OVER w) * (1 + 0.5 * (hash(thscode) % 2)) AS c20",
        )
        assert patched != sql, "注入未生效：base 链中 c20 的写法已变，须同步本用例"
        return patched

    clean = ev.evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    monkeypatch.setattr(ev, "_base_cte", _perturbed)
    dirty = ev.evaluate_factor(con, FACTOR_BY_NAME["mom20"], market_daily)
    assert dirty["windows"] != clean["windows"], "共享列被改动却毫无差异 ⇒ 上面那条同源比对是空断言"


# ---------------------------------------------------------------- RSH-003：MFI20 / OBV20（TA-Lib 逐个转正）
MFI_OBV_W = 20
MFI_OBV_DATES = 30


def _mk_mfi_obv_db(tmp_path):
    """MFI20 / OBV20 专用小仓（30 交易日；窗口 20 行、`min_bars` 22）。

    **夹具的关键构造**：令 `high = close×1.02`、`low = close×0.98`、`close = close`，
    则 `(H+L+C)/3 ≡ close_price` ⇒ 复权典型价 **`tp_adj ≡ close_adj`**。
    于是期望值可直接从 `close_adj` 手算，无需在测试里把生产公式再实现一遍
    ——后者会造出「两处都错得一样」也能全绿的假护栏（同源恒等式纪律）。

    六形态（每只票独立走一条路径）：
    - `up`    每日 +1% 单调上行 → OBV = **+1**；MFI = **100**（负流出为 0 的极限）
    - `dn`    对称下行           → OBV = **−1**；MFI = **0**
    - `flat`  全程持平           → OBV = **0**；MFI **必须 NULL**（无方向 ≠ 中性 50）
    - `mix`   涨跌交替           → 无解析解，交由朴素参考逐点判定
    - `null`  前两日 `close_adj` 缺失 → 早期窗口有效样本 <20 ⇒ NULL（三态）
    - `exdiv` **除权形态（本条最关键）**：第 10 日 10 送 10，原始价 20.9 → 10.45，复权价连续。
      **复权口径下方向仍单调上行 ⇒ MFI = 100**；若照抄 TA-Lib 的原价 TP，
      该日会被读成巨量流出 ⇒ MFI 明显 < 100。该形态钉死 note 声明的口径①。

    返回 `(con, series)`；`series[code] = (close_adj, volume, turnover)`，
    均为该股按日期升序的序列，供朴素参考独立复算（与生产 SQL 共用同一份输入）。
    """
    dates = _days(MFI_OBV_DATES)
    n = MFI_OBV_DATES
    vol = 1_000_000
    paths: dict[str, list[tuple[float, float | None]]] = {
        "up": [(v, v) for v in (10 * (1 + 0.01 * i) for i in range(n))],
        "dn": [(v, v) for v in (10 * (1 - 0.005 * i) for i in range(n))],
        "flat": [(10.0, 10.0)] * n,
        "mix": [(11.0 if i % 2 else 10.0, 11.0 if i % 2 else 10.0) for i in range(n)],
        "null": [(10.0 + (i % 5), None if i < 2 else 10.0 + (i % 5)) for i in range(n)],
    }
    # exdiv：除权前 20.9 基准；第 10 日起原始价减半（10 送 10）、复权价**连续**
    base = 20.0 * (1 + 0.005 * 9)
    paths["exdiv"] = [
        ((20.0 * (1 + 0.005 * i)), (20.0 * (1 + 0.005 * i))) if i < 10
        else (base / 2 * (1 + 0.005 * (i - 9)), base * (1 + 0.005 * (i - 9)))
        for i in range(n)
    ]

    rows_k, rows_adj = [], []
    series: dict[str, tuple[list, list, list]] = {}
    for code, path in paths.items():
        ca_seq, tn_seq = [], []
        for i, (cp, ca) in enumerate(path):
            rows_k.append((code, dates[i], cp * 0.99, cp * 1.02, cp * 0.98, cp, vol, cp * vol))
            rows_adj.append((code, dates[i], ca))
            ca_seq.append(ca)
            tn_seq.append(cp * vol)
        series[code] = (ca_seq, [vol] * n, tn_seq)

    con = duckdb.connect(str(tmp_path / "mfi_obv.duckdb"))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
        " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT, turnover DOUBLE)"
    )
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    con.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?)", rows_k)
    con.executemany("INSERT INTO daily_k_adj VALUES (?,?,?)", rows_adj)
    return con, series


def _mfi_obv_rows(con) -> dict[str, list[tuple[int, int, float | None, int, float | None]]]:
    """用**生产** `_base_cte` 取 `(date_ms, mfi20_n, mfi20_w, obv20_n, obv20_w)`。

    刻意经 `ev._base_cte` 间接取（而不是文件顶部 import 的 `_base_cte`）——
    这样下面那条**注入自证**用例 `monkeypatch` 才落得到实处。
    """
    import app.factors.evaluate as ev

    sql = ev._base_cte(FACTOR_BY_NAME["mfi20"]) + (
        "\nSELECT thscode, date_ms, mfi20_n, mfi20_w, obv20_n, obv20_w"
        " FROM lvl4 ORDER BY thscode, date_ms"
    )
    out: dict[str, list[tuple[int, int, float | None, int, float | None]]] = {}
    for code, ts, mn, mv, on, ov in con.execute(sql).fetchall():
        out.setdefault(code, []).append((ts, mn, mv, on, ov))
    return out


def _naive_mfi20(tp: list[float | None], turnover: list[float | None]) -> list[float | None]:
    """朴素参考（独立于 SQL）：20 行窗口内 `TP_adj` 上升/下降日的**额**之和。

    逐条对齐 `evaluate.py::lvl4.mfi20_w`：窗口按该股**自身行序**取最近 20 行（含当前行）；
    前值取 `tp[j-1]`（与 SQL `LAG` 同义）；有效样本 <20 或 `正+负 = 0` → None；
    `负 = 0 且 正 > 0` → 100.0（极限，不是缺失）。
    """
    out: list[float | None] = []
    for i in range(len(tp)):
        idx = range(max(0, i - MFI_OBV_W + 1), i + 1)
        pos = neg = 0.0
        n = 0
        for j in idx:
            a, prev, t = tp[j], (tp[j - 1] if j > 0 else None), turnover[j]
            if a is None or prev is None or t is None:
                continue
            n += 1
            if a > prev:
                pos += a * t
            elif a < prev:
                neg += a * t
        if n < MFI_OBV_W or pos + neg <= 0:
            out.append(None)
        else:
            out.append(100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / neg))
    return out


def _naive_obv20(ca: list[float | None], volume: list[int | None]) -> list[float | None]:
    """朴素参考（独立于 SQL）：20 行窗口内 `Σ sign(Δclose_adj)·volume / Σ volume`。

    分子分母**样本面一致**（都要求 `close_adj` / 前值 / `volume` 三者非 NULL）——
    与生产 SQL 同步收严过；有效样本 <20 或分母 ≤0 → None。
    """
    out: list[float | None] = []
    for i in range(len(ca)):
        idx = range(max(0, i - MFI_OBV_W + 1), i + 1)
        num = den = 0.0
        n = 0
        for j in idx:
            a, prev, v = ca[j], (ca[j - 1] if j > 0 else None), volume[j]
            if a is None or prev is None or v is None:
                continue
            n += 1
            den += v
            if a > prev:
                num += v
            elif a < prev:
                num -= v
        out.append(None if (n < MFI_OBV_W or den <= 0) else num / den)
    return out


def test_mfi_obv_match_naive_reference(tmp_path):
    """RSH-003 **主判据（同源）**：生产 `_base_cte` 与独立朴素参考逐点一致。

    有方向信息的四形态（up / dn / mix / exdiv）逐点比对并附**防空断言**；
    `flat` 形态**本来就没有方向**，其 MFI 参考侧必须**全 NULL**——单独断言，
    而不是从循环里悄悄跳过（跳过等于把「该为空」这一事实也变成无人核对的区域）。
    """
    con, series = _mk_mfi_obv_db(tmp_path)
    try:
        got = _mfi_obv_rows(con)
        for code in ("up", "dn", "mix", "exdiv"):
            ca, vol, tn = series[code]
            exp_mfi, exp_obv = _naive_mfi20(ca, tn), _naive_obv20(ca, vol)
            # 防空断言：参考实现本身必须产出足量非 NULL，否则"逐点一致"可能是两个空集
            assert sum(v is not None for v in exp_mfi) >= 5, f"{code}: MFI 参考全空，用例失效"
            assert sum(v is not None for v in exp_obv) >= 5, f"{code}: OBV 参考全空，用例失效"
            for k, (_, _, mv, _, ov) in enumerate(got[code]):
                if exp_mfi[k] is None:
                    assert mv is None, f"{code}[{k}] MFI 应 NULL，实得 {mv}"
                else:
                    assert mv is not None and abs(mv - exp_mfi[k]) < 1e-9, \
                        f"{code}[{k}] MFI {mv} != {exp_mfi[k]}"
                if exp_obv[k] is None:
                    assert ov is None, f"{code}[{k}] OBV 应 NULL，实得 {ov}"
                else:
                    assert ov is not None and abs(ov - exp_obv[k]) < 1e-12, \
                        f"{code}[{k}] OBV {ov} != {exp_obv[k]}"

        # flat：MFI 两侧都应全 NULL（无方向）；OBV 分子恒 0、分母有效 ⇒ 应为 0.0，不是 NULL
        ca, vol, tn = series["flat"]
        assert all(v is None for v in _naive_mfi20(ca, tn)), "持平形态 MFI 参考应为全 NULL"
        exp_obv = _naive_obv20(ca, vol)
        assert any(v is not None for v in exp_obv), "持平形态 OBV 参考不应全空（分母有效）"
        for k, (_, _, mv, _, ov) in enumerate(got["flat"]):
            assert mv is None, f"flat[{k}] MFI 应为 NULL，实得 {mv}"
            if exp_obv[k] is None:
                assert ov is None, f"flat[{k}] OBV 应 NULL，实得 {ov}"
            else:
                assert ov is not None and abs(ov - exp_obv[k]) < 1e-12, \
                    f"flat[{k}] OBV {ov} != {exp_obv[k]}"
    finally:
        con.close()


def test_mfi_obv_three_state_extremes_and_gaps(tmp_path):
    """三态判据（三条，**不可混为一谈**）：

    ① 单调全流入 ⇒ MFI = **100**、OBV = **+1** —— 这是**真实的极端读数**，必须保留；
       若用 `NULLIF(负流, 0)` 图省事，会把「最强」塌成「未判定」而丢掉全部极值样本。
    ② 全程持平 ⇒ MFI **NULL**（无方向信息）；此处输出 50.0 就是**凭空的凑数值**
       （三态纪律禁止「缺数当中间值」），而 OBV 是 0.0（分子恒 0、分母有效 ⇒ 真实为 0）。
    ③ 窗口含缺失 ⇒ NULL，且**缺失不传染**（`null` 形态后段恢复出值）。
    """
    con, series = _mk_mfi_obv_db(tmp_path)
    try:
        got = _mfi_obv_rows(con)
        up = got["up"]
        assert up[-1][2] == pytest.approx(100.0), f"单调上行 MFI 应为 100，实得 {up[-1][2]}"
        assert up[-1][4] == pytest.approx(1.0), f"单调上行 OBV 应为 +1，实得 {up[-1][4]}"
        dn = got["dn"]
        assert dn[-1][2] == pytest.approx(0.0), f"单调下行 MFI 应为 0，实得 {dn[-1][2]}"
        assert dn[-1][4] == pytest.approx(-1.0), f"单调下行 OBV 应为 −1，实得 {dn[-1][4]}"

        flat = got["flat"]
        assert flat[-1][2] is None, f"全程持平 MFI 必须 NULL（不是 50），实得 {flat[-1][2]}"
        assert flat[-1][4] == pytest.approx(0.0), f"全程持平 OBV 应为 0，实得 {flat[-1][4]}"

        null_rows = got["null"]
        assert null_rows[MFI_OBV_W][2] is None, "窗口落在缺失段内时必须 NULL"
        assert null_rows[MFI_OBV_W][4] is None, "窗口落在缺失段内时必须 NULL"
        tail = [r for r in null_rows if r[2] is not None]
        assert tail, "缺失段滑出窗口后必须恢复出值（缺失不传染）"
    finally:
        con.close()


def test_mfi_exdiv_uses_adjusted_typical_price(tmp_path):
    """**除权口径钉死**（library.py note 声明的口径①，本用例是其唯一判别器）。

    第 10 日 10 送 10：原始价 20.9 → 10.45，而复权价连续。若 TP 走复权，
    方向始终单调上行 ⇒ MFI = 100；若照抄 TA-Lib 的**原价** TP，该日会被读成
    巨量流出 ⇒ MFI 显著 < 100。**A 股每半年一次的分红送转季都会踩这个坑**，
    不是罕见边界。
    """
    con, series = _mk_mfi_obv_db(tmp_path)
    try:
        rows = _mfi_obv_rows(con)["exdiv"]
        assert rows[-1][2] == pytest.approx(100.0), (
            f"除权日被误读为资金流出 ⇒ TP 未复权（MFI={rows[-1][2]}）"
        )
        assert rows[-1][4] == pytest.approx(1.0), f"复权价单调上行，OBV 应为 +1（实得 {rows[-1][4]}）"
        ca, _, _ = series["exdiv"]
        assert ca[9] < ca[10], "夹具自身失效：复权价未保持连续"
    finally:
        con.close()


def test_mfi_exdiv_guard_can_actually_fail(tmp_path, monkeypatch):
    """**判据自证**：把 `tp_adj` 的复权因子去掉（退回 TA-Lib 原价口径），除权日的假流出必须显形。

    没有这条，上面那条用例可能只是「断言了一个恒真式」而永远全绿（KB-ENG-65：
    **守卫必须能变红**，且必须是改真实行为，加注释充数不算）。
    """
    import app.factors.evaluate as ev

    con, _ = _mk_mfi_obv_db(tmp_path)
    try:
        clean = _mfi_obv_rows(con)["exdiv"][-1][2]
        orig = ev._base_cte

        def _unadjusted(factor):
            sql = orig(factor)
            patched = sql.replace(
                "(high_price + low_price + close_price) / 3.0\n"
                "               * (close_adj / NULLIF(close_price, 0)) AS tp_adj",
                "(high_price + low_price + close_price) / 3.0 AS tp_adj",
            )
            assert patched != sql, "注入未生效：tp_adj 写法已变，须同步本用例"
            return patched

        monkeypatch.setattr(ev, "_base_cte", _unadjusted)
        dirty = _mfi_obv_rows(con)["exdiv"][-1][2]
        assert clean == pytest.approx(100.0)
        assert dirty is not None and dirty < 99.0, (
            f"未复权口径下除权日未产生假流出 ⇒ 上面那条口径钳制是空断言（实得 {dirty}）"
        )
    finally:
        con.close()
