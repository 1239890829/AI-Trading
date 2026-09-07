"""因子库评估引擎测试（合成小仓，验证口径与三态，不依赖真实 marketdb）。

构造要点：
- 15 只「弱」股日收益 -0.2%+noise、15 只「强」股 +0.2%+noise → 动量因子 IC 应显著为正；
- 5 只次新股仅 50 日历史 → 长窗口因子（mom120/vola20 等）必须 NULL（三态，覆盖率 < 1）；
- 1 只全一字板股（high=low）→ 全样本被「买不进」剔除 → FAIL（样本不足）；
- 末尾交易日无 T+h 前瞻 → 自动排除（防泄露的结构性验证）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from app.factors.evaluate import (
    DATA_QUALITY_NOTES,
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
    assert q["q_ann"]["5"] > q["q_ann"]["1"]


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
