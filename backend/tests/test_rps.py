"""RPS 相对强度服务测试：分位正确性、复权修正、窗口不足、降级路径。

数据底座是本地 DuckDB marketdb（不入 git、CI 上不存在），所以本测试
自建小库 fixture（tmp_path），不依赖真实回补数据——CI 环境下同样全绿。
"""
import sys

import duckdb

sys.path.insert(0, ".")

from app.picks.rps import RpsService, _pct_rank_dedup, _trade_date_ms
from scripts.sync_marketdb import rebuild_adj  # noqa: E402

_MS_DAY = 86_400_000
_T0 = _trade_date_ms("20260101")  # 上海零点，与仓内口径一致


def _mk_adj_db(tmp_path, series: dict[str, list[float]], t0: int = _T0):
    """造 daily_k_adj 小库：{thscode: [close_adj 序列]}，日毫秒逐日递增。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    for code, closes in series.items():
        con.executemany(
            "INSERT INTO daily_k_adj VALUES (?, ?, ?)",
            [(code, t0 + i * _MS_DAY, c) for i, c in enumerate(closes)],
        )
    con.close()
    return db


# ---------------------------------------------------------------- 纯函数


def test_pct_rank_dedup_basic_and_ties():
    assert _pct_rank_dedup([]) == []
    assert _pct_rank_dedup([3.0]) == [50]  # 单值中性
    # 三值升序 → 0 / 50 / 100
    assert _pct_rank_dedup([-0.1, 0.0, 0.2]) == [0, 50, 100]
    # 并列组取组内最小百分位（保守口径）
    assert _pct_rank_dedup([0.1, 0.1, 0.5]) == [0, 0, 100]


# ---------------------------------------------------------------- 分位正确性


def test_rps_ranks_cross_section(tmp_path):
    """A 每日 +1%、B 横盘、C 每日 -1% → RPS50 = 100 / 50 / 0。"""
    db = _mk_adj_db(tmp_path, {
        "600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)],
        "000002.SZ": [10.0] * 130,
        "300003.SZ": [round(10 * 0.99 ** i, 6) for i in range(130)],
    })
    svc = RpsService(db_path=db)
    snap = svc.snapshot()
    assert snap["600001"]["rps50"] == 100
    assert snap["000002"]["rps50"] == 50
    assert snap["300003"]["rps50"] == 0
    # 130 根样本，rps120 同序
    assert snap["600001"]["rps120"] == 100
    assert snap["300003"]["rps120"] == 0


def test_rps_window_insufficient_symbol_excluded(tmp_path):
    """40 根样本：LAG(50) 锚点不存在 → 该股两个窗口都不覆盖。"""
    db = _mk_adj_db(tmp_path, {
        "600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)],
        "688999.SH": [round(20 * 1.05 ** i, 6) for i in range(40)],  # 次新，涨更猛
    })
    snap = RpsService(db_path=db).snapshot()
    assert "688999" not in snap  # 窗口不足 → 无分位（不臆造）
    # 剩余截面只剩一只 → 无分位意义，中性 50
    assert snap["600001"]["rps50"] == 50


def test_rps_historical_snapshot_date_bound(tmp_path):
    """trade_date 上限：截到窗口内 → 正常截面；截到窗口外 → 空覆盖。"""
    db = _mk_adj_db(tmp_path, {
        "600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)],
        "000002.SZ": [10.0] * 130,
    })
    cut = _trade_date_ms("20260302")  # 第 61 天
    assert cut > _T0
    # 截断到第 61 日：rps50 仍可用；两票截面里 +1% 的 100、横盘的 0（相对最弱）
    snap = RpsService(db_path=db).snapshot("20260302")
    assert snap["600001"]["rps50"] == 100 and snap["000002"]["rps50"] == 0
    # 截到第 11 日：rps50/rps120 全部窗口不足 → 无覆盖
    assert RpsService(db_path=db).snapshot("20260111") == {}


# ---------------------------------------------------------------- 复权修正


def _mk_raw_db_with_event(tmp_path, code: str, prices: list[float],
                          ex_index: int, **fields):
    """daily_k（未复权）+ 单个复权事件 → rebuild_adj → 返回 (db, close_adj 序列)。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE daily_k (
        thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, high_price DOUBLE,
        low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE adjust_factor (
        thscode VARCHAR, ex_date_ms BIGINT, dividend_per_share DOUBLE,
        per_share_bonus DOUBLE, allotment_ratio DOUBLE, allotment_price DOUBLE)""")
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [(code, _T0 + i * _MS_DAY, p, p, p, p) for i, p in enumerate(prices)],
    )
    con.execute(
        "INSERT INTO adjust_factor VALUES (?, ?, ?, ?, ?, ?)",
        (code, _T0 + ex_index * _MS_DAY,
         fields.get("dividend_per_share", 0), fields.get("per_share_bonus", 0),
         fields.get("allotment_ratio", 0), fields.get("allotment_price", 0)),
    )
    rebuild_adj(con)
    rows = con.execute(
        "SELECT date_ms, close_adj FROM daily_k_adj ORDER BY date_ms"
    ).fetchall()
    con.close()
    return db, [r[1] for r in rows]


def test_rebuild_adj_bonus_issue_no_gap(tmp_path):
    """10送10：除权日价格 20→10 表观腰斩，close_adj 序列应无跳变（涨幅=0）。"""
    prices = [20.0] * 30 + [10.0] * 30
    db, adj = _mk_raw_db_with_event(tmp_path, "600005.SH", prices, ex_index=30,
                                    per_share_bonus=1.0)
    assert len(adj) == 60
    # 前 30 根 ×0.5（20→10），后 30 根无未来事件 ×1.0（10）→ 全程 10
    for v in adj:
        assert abs(v - 10.0) < 1e-9


def test_rebuild_adj_dividend_no_gap(tmp_path):
    """每股分红 1 元：除权日 20→19，close_adj 全程 19。"""
    prices = [20.0] * 30 + [19.0] * 30
    db, adj = _mk_raw_db_with_event(tmp_path, "600006.SH", prices, ex_index=30,
                                    dividend_per_share=1.0)
    for v in adj:
        assert abs(v - 19.0) < 1e-9


def test_rebuild_adj_no_event_symbol_passthrough(tmp_path):
    """无事件股票：close_adj ≡ close。"""
    db, adj = _mk_raw_db_with_event(tmp_path, "600007.SH", [5.0, 6.0, 7.0], ex_index=2)
    # 事件在 index=2 但事件表仅这一个事件、且属于该股票——事件 ex_date=第3天，
    # 前两根被乘上 ratio((7-0)/7=1.0，无分红送转) → 不变
    assert [round(v, 9) for v in adj] == [5.0, 6.0, 7.0]


def test_rps_through_adjustment(tmp_path):
    """端到端：除权股在 close_adj 口径下不被错杀（横盘除权股 RPS=50 而非 0）。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE daily_k (
        thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, high_price DOUBLE,
        low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE adjust_factor (
        thscode VARCHAR, ex_date_ms BIGINT, dividend_per_share DOUBLE,
        per_share_bonus DOUBLE, allotment_ratio DOUBLE, allotment_price DOUBLE)""")
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    # 除权股：60 根横盘 20，第 61 根起 10送10 → 10，继续横盘 70 根
    prices = [20.0] * 60 + [10.0] * 70
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [("600010.SH", _T0 + i * _MS_DAY, p, p, p, p) for i, p in enumerate(prices)],
    )
    con.execute("INSERT INTO adjust_factor VALUES ('600010.SH', ?, 0, 1.0, 0, 0)",
                [_T0 + 60 * _MS_DAY])
    # 对照组：真跌股每日 -0.5%，130 根
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [("600011.SH", _T0 + i * _MS_DAY, round(10 * 0.995 ** i, 6),
          round(10 * 0.995 ** i, 6), round(10 * 0.995 ** i, 6),
          round(10 * 0.995 ** i, 6)) for i in range(130)],
    )
    rebuild_adj(con)
    con.close()
    snap = RpsService(db_path=db).snapshot()
    # 两票截面：除权股复权后真实涨幅 0（若不复权会是 -50% 被错杀成最弱档），
    # 真跌股 -0.5%/日 → 除权股排 100 / 真跌股排 0
    assert snap["600010"]["rps50"] == 100
    assert snap["600011"]["rps50"] == 0


# ---------------------------------------------------------------- 降级与缓存


def test_rps_missing_db_degrades_to_empty(tmp_path):
    svc = RpsService(db_path=tmp_path / "nope.duckdb")
    assert svc.available() is False
    assert svc.snapshot() == {}
    assert svc.get("600519") is None


def test_rps_snapshot_cached(tmp_path):
    db = _mk_adj_db(tmp_path, {"600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)]})
    svc = RpsService(db_path=db)
    a = svc.snapshot()
    b = svc.snapshot()
    assert a == b
    assert len(svc._cache) == 1  # 同 key 走缓存，不重复回源
