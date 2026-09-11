"""RPS 相对强度服务测试：分位正确性、复权修正、窗口不足、降级路径。

数据底座是本地 DuckDB marketdb（不入 git、CI 上不存在），所以本测试
自建小库 fixture（tmp_path），不依赖真实回补数据——CI 环境下同样全绿。

**夹具锚点纪律（2026-09-11 P0-7）**：合成库必须锚到**最近的交易日**，
否则会被 `marketdb_freshness` 陈旧闸门判为陈旧而恒降级 → 分位断言全部落空。
需要"历史截面"语义的用例显式传 `t0=`（如边界截断用例）。
"""
import sys
from datetime import date, timedelta

import duckdb

sys.path.insert(0, ".")

from app.market import trade_calendar as tc
from app.picks.rps import RpsService, _pct_rank_dedup, _trade_date_ms
from scripts.sync_marketdb import rebuild_adj  # noqa: E402

_MS_DAY = 86_400_000
_T0 = _trade_date_ms("20260101")  # 上海零点，与仓内口径一致（历史夹具用）


def _recent_t0(n_days: int) -> int:
    """锚到最近交易日往前 n_days-1 个自然日 → 序列末日≈最新交易日（过闸门）。"""
    days = tc._load_persisted() or []
    anchor = days[-1] if days else date.today()
    return _trade_date_ms((anchor - timedelta(days=n_days - 1)).strftime("%Y%m%d"))


def _mk_adj_db(tmp_path, series: dict[str, list[float]], t0: int | None = None):
    """造 daily_k_adj 小库：{thscode: [close_adj 序列]}，日毫秒逐日递增。

    t0=None → 锚到最近交易日（新鲜，过闸门）；显式 t0 → 历史/陈旧场景。
    """
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    for code, closes in series.items():
        base = _recent_t0(len(closes)) if t0 is None else t0
        con.executemany(
            "INSERT INTO daily_k_adj VALUES (?, ?, ?)",
            [(code, base + i * _MS_DAY, c) for i, c in enumerate(closes)],
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
    """trade_date 上限：截到窗口内 → 正常截面；截到窗口外 → 空覆盖。

    显式历史锚点（t0=_T0）：新鲜度判定以**请求日**为基准 ⇒ 库内更新的数据不算陈旧，
    回测/复盘要的就是那一刻的截面（不会因为"今天的库"而拒绝历史查询）。
    """
    db = _mk_adj_db(tmp_path, {
        "600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)],
        "000002.SZ": [10.0] * 130,
    }, t0=_T0)
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
    base_t0 = _recent_t0(130)  # 锚到最近交易日，否则陈旧闸门会先降级
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
        [("600010.SH", base_t0 + i * _MS_DAY, p, p, p, p) for i, p in enumerate(prices)],
    )
    con.execute("INSERT INTO adjust_factor VALUES ('600010.SH', ?, 0, 1.0, 0, 0)",
                [base_t0 + 60 * _MS_DAY])
    # 对照组：真跌股每日 -0.5%，130 根
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [("600011.SH", base_t0 + i * _MS_DAY, round(10 * 0.995 ** i, 6),
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
    fr = svc.freshness()
    assert fr["available"] is False and "marketdb 不存在" in fr["reason"]


def test_rps_stale_db_degrades_to_empty(tmp_path, monkeypatch):
    """回归（P0-7，2026-09-10 实测）：**仓存在但停更 6 个交易日**必须降级。

    真实事故：marketdb 停在 2026-09-03 且同步调度器从未启动，而 rps 只判
    「仓在不在」→ 照旧用 09-03 的横截面算分位，tech_score 的 rps 维当今日数据
    静默使用。此用例锁住"陈旧 ≠ 可用"：分位算得出来也必须拒绝。
    """
    db = _mk_adj_db(tmp_path, {
        "600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)],
        "000002.SZ": [10.0] * 130,
    }, t0=_T0)  # 锚在 2026-01-01 → 相对今天滞后数十个交易日
    svc = RpsService(db_path=db)
    fr = svc.freshness()
    assert fr["available"] is True and fr["stale"] is True
    assert fr["lag"] > 3 and "数据陈旧" in fr["reason"]
    assert svc.snapshot() == {}                      # 降级为空（不是给旧分位）
    assert svc.get("600001") is None
    # 降级要留痕（一次性 warning，含可执行处置）
    assert svc._warned is True

    # 反证：把阈值放到滞后之上 → 同一库立即可用，证明"降级是因为陈阈而非别的原因"
    lenient = RpsService(db_path=db, max_stale_days=10_000)
    assert lenient.snapshot()  # 非空


def test_rps_stale_warning_logged(tmp_path, caplog):
    """降级 warning 必须带"滞后 N 个交易日"与处置脚本名（可运维）。"""
    import logging

    db = _mk_adj_db(tmp_path, {"600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)]},
                    t0=_T0)
    svc = RpsService(db_path=db)
    with caplog.at_level(logging.WARNING, logger="app.picks.rps"):
        assert svc.snapshot() == {}
    assert "数据陈旧" in caplog.text and "sync_marketdb.py" in caplog.text


def test_rps_snapshot_cached(tmp_path):
    db = _mk_adj_db(tmp_path, {"600001.SH": [round(10 * 1.01 ** i, 6) for i in range(130)]})
    svc = RpsService(db_path=db)
    a = svc.snapshot()
    b = svc.snapshot()
    assert a == b
    assert len(svc._cache) == 1  # 同 key 走缓存，不重复回源
