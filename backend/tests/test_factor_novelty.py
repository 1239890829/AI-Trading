"""结构新颖性筛查测试（合成小仓，验证判据、三态与**接线**，不依赖真实 marketdb）。

构造要点（`RSH-003` 切片）：
- 40 只股票 × 70 交易日，20 只上行 alpha、20 只下行 ⇒ 截面有持续排序结构；
- 三个**探针**各有明确职责：
  · `dup_mom20` = 2·mom20 + 5（仿射）⇒ 必须判 `duplicate`（**机制自证①**）；
  · `neg_mom20` = −1·mom20 ⇒ 秩相关 −1 但**头部零重合**（判据不冗余的证明）；
  · `rand_x` = 只与代码有关的伪随机常数 ⇒ 必须判 `distinct`（**判别力对照**，防"一律判重"）；
- 短回看窗（5 交易日）⇒ 必须 `insufficient_sample`，**即便 |秩相关| = 1**（样本不足不得定论）。

⚠️ 本文件**不改** `FACTORS`、不写库：筛查是只读的证据层。
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from app.factors.evaluate import HORIZONS_EXEC, IC_CORR_DEDUP, _base_cte
from app.factors.library import FACTOR_BY_NAME, FACTORS, FactorDef
from app.factors.novelty import (
    DUP_RANK_CORR,
    MIN_DAYS_FOR_VERDICT,
    _cutoff_ms,
    _detail_sql,
    _mean,
    _median,
    _verdict,
    screen_candidates,
)

DAYS = 70
N_UP = 20
N_DOWN = 20


def _days(n: int) -> list[int]:
    out: list[int] = []
    d = datetime(2024, 1, 2, tzinfo=timezone.utc)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(int(d.timestamp() * 1000))
        d += timedelta(days=1)
    return out


@pytest.fixture()
def db(tmp_path):
    """合成市场库（**连接**版）：判据类用例直接在连接上跑 SQL。"""
    path = tmp_path / "m.duckdb"
    _write_market_db(path)
    con = duckdb.connect(str(path))
    yield con
    con.close()


@pytest.fixture()
def db_file(tmp_path):
    """合成市场库（**路径**版）：给需要**自行建连**的用例（CLI 守卫）。

    ⚠️ 不能复用 `db` 夹具：DuckDB 同进程内不允许对已以读写打开的库再以**只读**打开
    （实测 `ConnectionException: Can't open a connection to same database file with a
    different configuration than existing connections`）⇒ 必须"只写不持有连接"。
    """
    path = tmp_path / "m.duckdb"
    _write_market_db(path)
    return path


def _write_market_db(path: Path) -> None:
    """写合成市场库：上行/下行两组各 20 只，70 个交易日，含 volume/turnover 供量类因子。

    **写完即关连接**（理由见 `db_file`）——本函数只负责造数据，不对外暴露连接。
    """
    days = _days(DAYS)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
            " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT,"
            " turnover DOUBLE)"
        )
        con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
        rows_k, rows_adj = [], []
        for si in range(N_UP + N_DOWN):
            code = f"90.XXSHE{600000 + si}"
            alpha = 0.002 if si < N_UP else -0.002
            #: ⚠️ **每只股票一条独立噪声流**（种子按序号偏移）。
            #:  2026-09-16 实测教训：共用一条流时，噪声在同一日对**全截面同值** ⇒ 截面排序
            #:  几乎只由 alpha（涨/跌两组）决定 ⇒ `mom5/mom10/mom20/mom60` 与任何动量候选的
            #:  秩相关**全为 1**，「最近邻是谁」退化成并列里的任意一项，
            #:  「能否认出被仿射的那一个」这条判据就失去检验力（实测：最近邻被判成 `mom5`）。
            #:  噪声幅度取 0.02（10 倍于漂移）⇒ 各窗口的截面排序真正互不相同。
            rnd = ((si + 1) * 0.137) % 1.0
            price = 10.0
            for ts in days:
                rnd = (rnd * 9301 + 49297) % 233280 / 233280  # 简易 LCG（可复现）
                price = round(price * (1 + alpha + (rnd - 0.5) * 0.02), 4)
                o = round(price * (1 + (rnd - 0.5) * 0.002), 4)
                hi = round(max(o, price) * 1.01, 4)
                lo = round(min(o, price) * 0.99, 4)
                vol = 1_000_000 + int(rnd * 500_000)
                rows_k.append((code, ts, o, hi, lo, price, vol, vol * price))
                rows_adj.append((code, ts, price))
        con.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_k)
        con.executemany("INSERT INTO daily_k_adj VALUES (?, ?, ?)", rows_adj)
    finally:
        con.close()



#: 仿射重复：2·mom20 + 5（单调增 ⇒ 截面排序与 mom20 **完全相同**）。
DUP_MOM20 = FactorDef("dup_mom20", "probe", 21,
                      "2.0 * (close_adj / c20 - 1) + 5.0", "仿射探针")
#: 反序：−1·mom20。秩相关 −1（仍属"排序完全同源"）但**头部零重合**。
NEG_MOM20 = FactorDef("neg_mom20", "probe", 21,
                      "-1.0 * (close_adj / c20 - 1)", "反序探针")
#: 判别力对照：只与代码有关、与价格/量无关的伪随机常数（时间内恒定）。
RAND_X = FactorDef("rand_x", "probe", 21,
                   "(hash(thscode) % 1000) / 1000.0", "独立探针")
#: 「样本不足的更像邻居」探针：**只在最后 2 个截面日有值**的仿射重复。
#: 它与候选在那 2 天上 |ρ| = 1（**比任何长样本邻居都更像**），但有效截面日只有 2
#: ⇒ 旧口径（取 `pairs[0]`）会让这 2 日噪声把候选判成 `insufficient_sample`。
THIN_DUP = FactorDef(
    "thin_dup", "probe", 21,
    f"CASE WHEN cnt >= {DAYS - 1} THEN 2.0 * (close_adj / c20 - 1) + 5.0 END",
    "仅在最后 2 个截面日有值的仿射探针",
)


def _screen(con, *cands, **kw):
    return screen_candidates(con, list(cands), **kw)


def _by_name(report, name):
    return next(c for c in report["candidates"] if c["name"] == name)


# ---------------------------------------------------------------- 纯函数：判档
def test_verdict_insufficient_sample_beats_duplicate():
    """样本不足**优先于**结论：即便 |秩相关| = 1 也不得判 duplicate（不得定论）。"""
    assert _verdict(1.0, 1.0, MIN_DAYS_FOR_VERDICT - 1) == "insufficient_sample"
    assert _verdict(1.0, 1.0, MIN_DAYS_FOR_VERDICT) == "duplicate"


def test_verdict_duplicate_is_math_not_threshold_guess():
    assert _verdict(DUP_RANK_CORR, None, 100) == "duplicate"
    assert _verdict(DUP_RANK_CORR - 0.01, None, 100) == "redundant_hint"


def test_verdict_redundant_hint_reuses_existing_dedup_constant():
    """提示线**沿用** `IC_CORR_DEDUP`（不新造阈值）；头部重合单独也能触发提示。"""
    assert _verdict(IC_CORR_DEDUP, None, 100) == "redundant_hint"
    assert _verdict(None, IC_CORR_DEDUP, 100) == "redundant_hint"
    assert _verdict(IC_CORR_DEDUP - 0.01, IC_CORR_DEDUP - 0.01, 100) == "distinct"


def test_mean_and_median_skip_missing():
    assert _mean([1.0, None, 3.0]) == pytest.approx(2.0)
    assert _mean([None, float("nan")]) is None
    assert _median([3.0, None, 1.0, 2.0]) == pytest.approx(2.0)
    assert _median([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5)
    assert _median([]) is None


# ---------------------------------------------------------------- 入参守卫
def test_rejects_candidate_already_in_pool(db):
    """候选与既有池重名 ⇒ 必须拒绝（否则是自我比较、会凭空得到 ρ=1 的「冗余」）。"""
    with pytest.raises(ValueError, match="已在既有池内"):
        _screen(db, FACTOR_BY_NAME["mom20"])


def test_rejects_duplicate_candidate_names(db):
    with pytest.raises(ValueError, match="候选名重复"):
        _screen(db, DUP_MOM20, DUP_MOM20)


def test_rejects_invalid_horizon(db):
    with pytest.raises(ValueError, match="horizon"):
        _screen(db, DUP_MOM20, horizon=7)


def test_cutoff_ms_rejects_empty_db(tmp_path):
    con = duckdb.connect(str(tmp_path / "empty.duckdb"))
    try:
        con.execute(
            "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
            " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT,"
            " turnover DOUBLE)"
        )
        with pytest.raises(ValueError, match="daily_k 为空"):
            _cutoff_ms(con, 10)
    finally:
        con.close()


# ---------------------------------------------------------------- 接线守卫
def test_detail_sql_honors_horizon():
    """`horizon` 必须真的进 SQL（否则「按 10 日评估」实际跑的是 5 日，静默错口径）。"""
    sql = _detail_sql(DUP_MOM20, FACTOR_BY_NAME["mom20"], 0, 10)
    assert "f10 / NULLIF(f1, 0) - 1 AS fwd" in sql
    assert "f5 / NULLIF(f1, 0) - 1 AS fwd" not in sql
    assert "HORIZONS" not in sql  # 不得把常量名当 SQL 写进去


def test_panel_is_not_silently_empty(db):
    """接线守卫（防假绿）：真跑 SQL 后配对、有效日、口径字段必须都到位。

    若面板因 SQL 写错而恒为空，所有候选都会落进 `insufficient_sample`
    —— 那与「样本真的不足」**同形**，是一条会静默通过的假绿路径。
    """
    report = _screen(db, DUP_MOM20)
    cand = report["candidates"][0]
    assert cand["pairs"], "配对为空 ⇒ 面板 SQL 没有产出任何有效秩相关"
    assert cand["n_days"] >= MIN_DAYS_FOR_VERDICT
    assert cand["nearest"] is not None
    meta = report["meta"]
    assert meta["n_incumbents"] == len(FACTORS)
    assert meta["horizon"] in HORIZONS_EXEC
    assert meta["duplicate_rank_corr"] == DUP_RANK_CORR
    assert meta["redundant_hint_rank_corr"] == IC_CORR_DEDUP
    assert meta["min_days_for_verdict"] == MIN_DAYS_FOR_VERDICT
    # 长窗口因子（mom120 需 121 根，合成仓只有 70 日）无从比较 ⇒ 必须在 `uncompared` 里
    # **显式列出**，而不是静默消失（否则读者会以为「全部既有因子都比过了」）。
    assert "mom120" in cand["uncompared"]
    assert "mom20" not in cand["uncompared"]
    assert cand["n_days"] == next(p["n_days"] for p in cand["pairs"] if p["incumbent"] == "mom20")


# ---------------------------------------------------------------- 端到端：判据行为
def test_affine_duplicate_is_caught(db):
    """机制自证①：仿射（换皮）因子必须被判 `duplicate`，且最近邻就是被仿射的那个。"""
    report = _screen(db, DUP_MOM20)
    cand = _by_name(report, "dup_mom20")
    assert cand["verdict"] == "duplicate", cand
    assert cand["nearest"]["incumbent"] == "mom20"
    assert abs(cand["nearest"]["rank_corr_mean"]) >= DUP_RANK_CORR
    assert cand["topk_overlap_mean"] == pytest.approx(1.0, abs=1e-9)


def test_inverted_probe_exposes_that_two_criteria_differ(db):
    """反序因子：秩相关 −1（⇒ duplicate）但**头部零重合**。

    这条用例钉死「三个判据不是重复劳动」——若只留秩相关，就无法区分
    「同向同源」与「反向同源」；而实盘只吃头部，二者含义截然不同。
    """
    report = _screen(db, NEG_MOM20)
    cand = _by_name(report, "neg_mom20")
    assert cand["nearest"]["incumbent"] == "mom20"
    assert cand["nearest"]["rank_corr_mean"] == pytest.approx(-1.0, abs=1e-9)
    assert cand["topk_overlap_mean"] == pytest.approx(0.0, abs=1e-9)


def test_independent_probe_is_not_rubber_stamped(db):
    """判别力对照：与价格/量无关的探针不得被判重——判据必须能说「不」。"""
    report = _screen(db, RAND_X)
    cand = _by_name(report, "rand_x")
    assert cand["verdict"] == "distinct", cand
    assert abs(cand["nearest"]["rank_corr_mean"]) < IC_CORR_DEDUP
    assert cand["topk_overlap_mean"] is not None
    assert cand["topk_overlap_mean"] < IC_CORR_DEDUP


def test_short_lookback_yields_insufficient_sample(db):
    """短回看窗 ⇒ `insufficient_sample`（**不判档**，而非「看起来不相关所以 distinct」）。"""
    report = _screen(db, DUP_MOM20, lookback_days=5)
    cand = _by_name(report, "dup_mom20")
    assert cand["n_days"] < MIN_DAYS_FOR_VERDICT
    assert cand["verdict"] == "insufficient_sample"


def test_thin_pair_cannot_become_the_nearest_neighbor(db):
    """样本不足的配对**不得**当最近邻、也不得决定档位（2026-09-16 实测缺陷形态，勿回退）。

    形态：候选与 `thin_dup` 在 2 个有效日上 |ρ| = 1（**比长样本邻居更像**），与 `mom5`
    有 49 个有效日但 |ρ| 明显更低。旧口径按 `|ρ|` 取 `pairs[0]` ⇒ 把 **2 日噪声当结论**
    ⇒ 候选被判 `insufficient_sample`，即**「样本足够」被「样本不足的邻居」挡掉**，
    档位与事实相反（且症状与「样本真的不足」同形，肉眼看不出来）。
    """
    report = screen_candidates(
        db, [DUP_MOM20], incumbents=[FACTOR_BY_NAME["mom5"], THIN_DUP]
    )
    cand = _by_name(report, "dup_mom20")
    # 前置条件（防"用例自己失去意义"）：该配对确实更相似，且确实样本不足
    raw = cand["pairs"][0]
    assert raw["incumbent"] == "thin_dup", raw
    assert raw["n_days"] < MIN_DAYS_FOR_VERDICT, raw
    # 核心断言放在前置条件之后、最靠前的位置：一旦回退成 `pairs[0]`，**第一条报错就直指缺陷**
    # （若把 |ρ| 比较放前面，报错会停在「0.9999 > 0.9999」这种读不出病因的地方）。
    assert cand["nearest"]["incumbent"] == "mom5", (
        f"最近邻被样本不足的配对劫持（nearest={cand['nearest']}）；"
        f"被排除者 = {[(p['incumbent'], p['n_days']) for p in cand['thin_pairs']]}"
    )
    assert abs(raw["rank_corr_mean"]) > abs(cand["nearest"]["rank_corr_mean"]), cand
    # 旧口径的结论（把这 2 日当真）——写出来是为了让"回退成 pairs[0]"必然断红
    assert _verdict(abs(raw["rank_corr_mean"]), None, raw["n_days"]) == "insufficient_sample"
    # 新口径：最近邻只在达标配对里取 ⇒ 用 mom5 的 49 日定档，且被排除者显式留痕
    assert cand["nearest"]["incumbent"] == "mom5", cand["nearest"]
    assert cand["n_days"] >= MIN_DAYS_FOR_VERDICT
    assert cand["verdict"] != "insufficient_sample"
    assert [p["incumbent"] for p in cand["thin_pairs"]] == ["thin_dup"], cand["thin_pairs"]


def test_conditional_ic_is_reported_but_never_gates(db):
    """条件 IC 只作证据：分组齐备、数值在 [-1,1]，且**结构上无法**参与判档。

    最后一条用签名钉死（`_verdict` 只接受 秩相关 / 头部重合 / 样本日 三个入参）
    —— 条件 IC 若要影响档位，必须先进签名，改动会被这条用例拦住。
    """
    report = _screen(db, DUP_MOM20, NEG_MOM20)
    for cand in report["candidates"]:
        assert len(cand["ic_conditional"]) == 3
        assert [g["group"] for g in cand["ic_conditional"]] == ["g1", "g2", "g3"]
        for grp in cand["ic_conditional"]:
            if grp["ic"] is not None:
                assert -1.0 <= grp["ic"] <= 1.0
        assert cand["ic_unconditional"] is not None
        assert cand["verdict"] in ("duplicate", "redundant_hint", "distinct", "insufficient_sample")
    params = list(inspect.signature(_verdict).parameters)
    assert params == ["best_abs_corr", "best_overlap", "n_days"], (
        "判档入参变了：条件 IC 若被加进来，就等于用未拍板的阈值自动淘汰候选（越权）"
    )


# ---------------------------------------------------------------- 结构与口径守卫
def test_base_cte_identity_is_shared_with_evaluate():
    """口径同源（结构钉）：本模块用的必须是 `evaluate._base_cte` **同一个对象**。"""
    from app.factors import evaluate, novelty

    assert novelty._base_cte is evaluate._base_cte
    assert _base_cte(FACTOR_BY_NAME["mom5"]).count("\nlvl4 AS (") == 1


# ---------------------------------------------------------------- 接线守卫（CLI）
def test_cli_reads_sys_argv(monkeypatch, capsys, tmp_path, db_file):
    """接线守卫：**真实命令行路径**下，参数必须真的到达被测对象（[[KB-ENG-108]]）。

    ⚠️ **为什么必须 `monkeypatch.setattr(sys, "argv", ...)`，不能写 `main(["--db", ...])`**：
    缺陷形态是 `parse_args(argv or [])`。**显式传列表照样正常工作**，只有真实命令行路径
    （`argv is None`）才塌缩成 `parse_args([])` ⇒ 用 `main([...])` 写的用例对该缺陷
    **天然失明**——这正是它当初漏过本文件其余用例的原因：`--db` / `--json` / `--lookback`
    被同时丢弃，表象却是"跑得慢"，于是被误诊成性能问题（[[KB-ENG-108]]）。

    三重判别（任一失败即说明参数没到）：
    1. `--db` 指向合成库、同时把模块默认 `DB` 指到**不存在**的路径
      ⇒ 参数若被丢弃会**立刻**返回 1，不会退化成"去跑真实大库 5 分钟"（故注入自证也是秒级）；
    2. `names` 只给 `willr20` ⇒ 报告中只该有 `willr20`（参数丢失会展开成 4 个候选）；
    3. `--json` 落盘 + `--lookback 40` / `--horizon 3` 进入 `meta`（数字类参数同样要证）。
    """
    import json
    import sys

    from scripts import factor_novelty as cli

    monkeypatch.setattr(cli, "DB", tmp_path / "unused-default.duckdb")
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "factor_novelty.py", "--db", str(db_file), "willr20",
        "--lookback", "40", "--horizon", "3", "--json", str(out),
    ])

    assert cli.main() == 0, capsys.readouterr().err
    assert out.exists(), "`--json` 未落盘：参数没有到达被测对象"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [c["name"] for c in report["candidates"]] == ["willr20"], report["candidates"]
    assert report["meta"]["lookback_days"] == 40, report["meta"]
    assert report["meta"]["horizon"] == 3, report["meta"]

