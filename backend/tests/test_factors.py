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
