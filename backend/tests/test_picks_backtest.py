"""选股 2.0 批次 D：确认规则回测框架（app/picks/backtest.py）回归测试。

锁五类行为：
1. 题材聚合口径：top_tags 家数降序/高度次序；tag_stats 龙头=连板最高（并列取涨幅）。
2. 题材→板块匹配与盘中 watcher.match_board_pct 同语义：精确 → 双向包含取最短，
   匹配不到不冒充（宁可 unmatched 如实计数）。
3. 收益口径：fwd = T+1 开盘 → T+3 收盘；窗口越界/输入缺失如实给 None。
4. 网格协议：每组阈值必须带全样本基线（触发 + 未触发 = 全体）；
   建议需触发样本 ≥MIN_TRIGGERED_FOR_ADVICE（防过拟合，宁缺毋滥）。
5. collect 的 identical-pool 回退哨兵：连续两天涨停池集合相同 → 该日样本全跳
   （与 metric_history 同款，防数据源静默回退到最近交易日的事故）。

注意：backend/tests/test_backtest.py 是日线回测引擎（app/market/backtest.py）
的防泄露测试——本文件只测选股回测，两者不可混。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from app.picks import backtest as bt


# ---------------------------------------------------------------- 桩与工具


@dataclass
class Rec:
    """涨停池记录桩（只带回测用到的字段）。"""

    symbol: str
    reason: str | None = None
    consecutive_boards: int | None = None
    change_pct: float | None = None


def _bars(*specs: tuple) -> list[dict]:
    """板块日 K 桩：specs = (date, open, close, turnover)。"""
    return [
        {"date": d, "open": o, "close": c, "turnover": t, "volume": None}
        for d, o, c, t in specs
    ]


def _sample(fwd, theme_pct, vr=2.0, **over) -> dict:
    """样本桩：除 theme_pct/vr 外其余确认项全满足，confirm 结果由网格阈值决定。"""
    inputs = dict(
        theme_pct=theme_pct, theme_limit_up=3, theme_max_boards=4,
        leader_pct=6.0, volume_ratio=vr, promo_percentile=50.0,
        phase=None, now_minutes=14 * 60,
    )
    inputs.update(over)
    return {"date": "d1", "tag": "AI应用", "board": "AI应用", "fwd": fwd, "inputs": inputs}


# ---------------------------------------------------------------- 题材聚合


def test_top_tags_orders_by_count_then_boards():
    pool = [
        Rec("1", reason="AI应用+算力", consecutive_boards=2),
        Rec("2", reason="AI应用", consecutive_boards=1),
        Rec("3", reason="算力", consecutive_boards=1),
        Rec("4", reason="低空经济", consecutive_boards=5),
        Rec("5", reason=None),  # 无归因题材 → 进不了任何题材
    ]
    tags = bt.top_tags(pool, top_n=3)
    assert [t["tag"] for t in tags] == ["AI应用", "算力", "低空经济"]
    assert tags[0]["count"] == 2 and tags[0]["max_boards"] == 2
    assert tags[2]["max_boards"] == 5  # 家数并列时高度参与次序
    assert bt.top_tags(pool, top_n=2)[0]["tag"] == "AI应用"


def test_tag_stats_leader_is_highest_board_then_pct():
    pool = [
        Rec("1", reason="AI应用", consecutive_boards=2, change_pct=10.0),
        Rec("2", reason="AI应用", consecutive_boards=3, change_pct=9.9),
        Rec("3", reason="AI应用", consecutive_boards=3, change_pct=10.05),
        Rec("4", reason="低空经济", consecutive_boards=1, change_pct=10.0),
    ]
    s = bt.tag_stats(pool, "AI应用")
    assert s == {"limit_up": 3, "max_boards": 3, "leader_pct": 10.05}
    # 无成员 → None（题材当日冷却，是事实不是 unknown）
    assert bt.tag_stats(pool, "不存在的题材") is None


# ---------------------------------------------------------------- 题材→板块匹配


def test_match_board_name_same_semantics_as_watcher():
    names = ["低空经济", "AI应用", "AI概念精选", "算力"]
    assert bt.match_board_name("AI应用", names) == "AI应用"  # 精确优先
    assert bt.match_board_name("AI", names) == "AI应用"  # 双向包含取最短
    assert bt.match_board_name("低空经济概念", names) == "低空经济"  # 板块名包含于标签
    assert bt.match_board_name("量子科技", names) is None  # 匹配不到不冒充
    assert bt.match_board_name("算力", ["", "算力"]) == "算力"  # 空名过滤


# ---------------------------------------------------------------- 板块日 K 视图


def test_day_view_pct_vr_and_forward_window():
    bars = _bars(
        ("d0", None, 100.0, 1000.0),
        ("d1", 102.0, 102.0, 2000.0),
        ("d2", 100.0, 101.0, 1000.0),
        ("d3", None, 100.0, 1000.0),
        ("d4", None, 103.0, 1000.0),
    )
    v = bt.day_view(bars, 1)
    assert v["date"] == "d1"
    assert v["pct"] == 2.0  # 102/100-1
    assert v["vr"] == 2.0  # turnover 2000/1000（收盘口径）
    assert v["fwd"] == 3.0  # T+1 open(d2)=100 → T+3 close(d4)=103
    # 边界：i<1 / 越界 / fwd 出窗 / 缺收盘 / 缺成交额
    assert bt.day_view(bars, 0) is None
    assert bt.day_view(bars, 5) is None
    assert bt.day_view(bars, 2)["fwd"] is None  # T+3 越出 K 线尾部，pct/vr 仍可算
    assert bt.day_view(_bars(("d0", None, 100.0, 1000.0), ("d1", None, None, 1000.0)), 1) is None
    assert bt.day_view(_bars(("d0", None, 100.0, 1000.0), ("d1", 101.0, 101.0, None)), 1)["vr"] is None


# ---------------------------------------------------------------- 样本装配


def test_build_samples_assembly_and_promo_percentile():
    pools = {
        "2026-01-05": [Rec("1", reason="AI应用", consecutive_boards=2, change_pct=10.0)],
        "2026-01-06": [Rec("2", reason="AI应用", consecutive_boards=3, change_pct=10.0)],
    }
    bars = _bars(
        ("2026-01-05", None, 100.0, 1000.0),
        ("2026-01-06", 102.0, 102.0, 2000.0),
        ("2026-01-07", 100.0, 101.0, 1000.0),
        ("2026-01-08", None, 100.0, 1000.0),
        ("2026-01-09", None, 103.0, 1000.0),
    )
    rows = [
        {"date": "2026-01-05", "promo_1to2": 30.0},
        {"date": "2026-01-06", "promo_1to2": 50.0},
    ]
    samples, stats = bt.build_samples(
        pools_by_day=pools, bars_by_board={"AI应用": bars},
        board_names=["AI应用"], metric_rows=rows,
    )
    assert [s["date"] for s in samples] == ["2026-01-06"]  # 首日只作 T-1
    s = samples[0]
    assert s["tag"] == "AI应用" and s["board"] == "AI应用"
    assert s["inputs"]["theme_pct"] == 2.0
    assert s["inputs"]["volume_ratio"] == 2.0
    assert s["inputs"]["theme_limit_up"] == 1
    assert s["inputs"]["leader_pct"] == 10.0
    # promo 分位：T 日值 50 在 {30, 50}（截止 T 含 T）中的分位 = (1+0.5)/2 = 75
    assert s["inputs"]["promo_percentile"] == 75.0
    assert s["inputs"]["phase"] is None  # 回放边界：phase 不回放
    assert s["fwd"] == 3.0
    assert stats["days"] == 1 and stats["promo_cover"] == 0


def test_build_samples_counts_unmatched_and_missing_bars():
    # 匹配不到板块 → unmatched，涨幅/量比如实 unknown
    pools = {
        "2026-01-05": [Rec("1", reason="量子科技", consecutive_boards=1, change_pct=10.0)],
        "2026-01-06": [Rec("2", reason="量子科技", consecutive_boards=1, change_pct=10.0)],
    }
    samples, stats = bt.build_samples(
        pools_by_day=pools, bars_by_board={}, board_names=["AI应用"], metric_rows=[],
    )
    assert len(samples) == 1
    s = samples[0]
    assert s["board"] is None and s["fwd"] is None
    assert s["inputs"]["theme_pct"] is None
    assert stats["unmatched"] == 1
    assert stats["no_board_bars"] == 0  # 没匹配到板块不算"板块缺K线"

    # 匹配到但无 K 线 → no_board_bars，confirm 输入仍 unknown
    pools2 = {
        "2026-01-05": [Rec("1", reason="AI应用", consecutive_boards=1, change_pct=10.0)],
        "2026-01-06": [Rec("2", reason="AI应用", consecutive_boards=1, change_pct=10.0)],
    }
    samples2, stats2 = bt.build_samples(
        pools_by_day=pools2, bars_by_board={}, board_names=["AI应用"], metric_rows=[],
    )
    assert samples2[0]["board"] == "AI应用"
    assert samples2[0]["inputs"]["theme_pct"] is None
    assert stats2["no_board_bars"] == 1 and stats2["unmatched"] == 0


# ---------------------------------------------------------------- 网格与建议


def test_run_grid_baselines_partition_and_excess():
    samples = [
        _sample(2.0, 3.0),  # 涨幅/量比全过 → 所有组合都触发
        _sample(-1.0, 1.2, vr=1.4),  # 只在 (1.0, 1.3) 触发
        _sample(1.0, 2.2, vr=1.0),  # 量比不过任何线 → 永不触发
        _sample(0.0, 2.6, vr=1.6),  # 过 1.3/1.5 量比线，不过 2.0
        _sample(None, 3.0),  # fwd 缺失 → 不进任何统计
    ]
    rows = bt.run_grid(samples)
    assert len(rows) == len(bt.GRID_PCT) * len(bt.GRID_VR)
    base = rows[0]["baseline_all"]
    assert base["n"] == 4 and base["mean"] == 0.5  # [2, -1, 1, 0]
    for r in rows:
        assert r["baseline_all"] == base  # 基线对每组阈值一致
        assert r["triggered"]["n"] + r["untriggered"]["n"] == 4  # 触发+未触发=全样本
    # (2.0, 2.0)：只有 s1 过双线，excess = 2.0 - 0.5
    r22 = next(r for r in rows if r["pct_thr"] == 2.0 and r["vr_thr"] == 2.0)
    assert r22["triggered"]["n"] == 1 and r22["triggered"]["mean"] == 2.0
    assert r22["untriggered"]["n"] == 3 and r22["untriggered"]["mean"] == 0.0
    assert r22["excess"] == 1.5
    # (1.0, 1.3)：s1/s2/s4 触发
    r11 = next(r for r in rows if r["pct_thr"] == 1.0 and r["vr_thr"] == 1.3)
    assert r11["triggered"]["n"] == 3


def test_run_grid_missing_vr_uses_degraded_confirm():
    """量比缺失的样本走降级确认（同一份规则代码）——不过量比线也视同触发。"""
    rows = bt.run_grid([_sample(5.0, 3.0, vr=None)], pct_grid=(2.5,), vr_grid=(2.0,))
    assert rows[0]["triggered"]["n"] == 1
    assert rows[0]["excess"] == 0.0  # 全样本只有它自己 → 超额 0


def test_best_combo_requires_min_triggered():
    samples = [_sample(2.0, 3.0), _sample(-1.0, 1.2, vr=1.4)]
    assert bt.best_combo(bt.run_grid(samples)) is None  # 触发数 <30 不给建议

    def _row(n: int, excess):
        return {"pct_thr": 2.0, "vr_thr": 1.5, "triggered": {"n": n},
                "untriggered": {"n": 0}, "baseline_all": {"n": n}, "excess": excess}

    rows = [_row(25, 9.9), _row(40, 0.5), _row(35, 1.2), _row(40, None)]
    best = bt.best_combo(rows)
    assert best is not None and best["excess"] == 1.2  # 达标里超额最高；25 个不入选


# ---------------------------------------------------------------- 报告


def test_format_report_required_sections_and_overfit_gate():
    samples = [_sample(2.0, 3.0), _sample(-1.0, 1.2, vr=1.4)]
    rows = bt.run_grid(samples)
    stats = {"days": 130, "unmatched": 2, "no_board_bars": 1, "promo_cover": 3}
    report = bt.format_report(rows, samples, stats, window=("2026-01-01", "2026-06-30"))
    for seg in ("## 数据边界（诚实降级）", "## 网格结果", "## 建议", "## 防过拟合声明"):
        assert seg in report, f"报告缺少必备区 {seg}"
    assert "2026-01-01 ~ 2026-06-30" in report
    assert "130 个交易日" in report and "满足 ≥120 交易日协议" in report
    assert "匹配失败 2 条" in report and "缺当日数据 1 条" in report
    assert "promo 分位缺失日 3 天" in report
    assert "不给调参建议" in report  # 样本不足 → 宁缺毋滥
    assert "报告不自动回写常量" in report

    short = bt.format_report(
        rows, samples, {"days": 5, "unmatched": 0, "no_board_bars": 0, "promo_cover": 0},
        window=None,
    )
    assert "不足 120 交易日协议" in short
    assert "回测窗口：未知" in short


# ---------------------------------------------------------------- collect（IO 层）


def test_collect_skips_identical_pool_and_fetches_needed_boards():
    d0, d1, d2 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    pools = {
        d0: [Rec("600001", reason="AI应用", consecutive_boards=1, change_pct=10.0)],
        d1: [Rec("600002", reason="AI应用+算力", consecutive_boards=2, change_pct=10.0)],
        # d2 与 d1 涨停池集合完全相同 → 哨兵判定数据源回退，样本全跳
        d2: [Rec("600002", reason="AI应用+算力", consecutive_boards=2, change_pct=10.0)],
    }
    fetched: list[str] = []

    async def fake_pool_fetcher(d):
        return pools[d]

    async def fake_bars_fetcher(code, span):
        fetched.append(code)
        if code == "885000":
            raise RuntimeError("ths 打嗝")
        return _bars(
            ("2026-01-05", None, 100.0, 1000.0),
            ("2026-01-06", 102.0, 102.0, 2000.0),
            ("2026-01-07", 100.0, 100.0, 1000.0),
            ("2026-01-08", None, 101.0, 1000.0),
            ("2026-01-09", None, 103.0, 1000.0),
        )

    samples, stats, bars_by_board = asyncio.run(bt.collect(
        trade_days=[d0, d1, d2],
        pool_fetcher=fake_pool_fetcher,
        bars_fetcher=fake_bars_fetcher,
        catalog=[{"code": "885000", "name": "算力"}, {"code": "885001", "name": "AI应用"}],
        metric_rows=[{"date": "2026-01-06", "promo_1to2": 50.0}],
        gap=0,
    ))

    # 哨兵生效：d2 被跳过 → 样本只有 d1；且 top_tags 取 T-1（d0）池，只有 AI应用
    assert [s["date"] for s in samples] == ["2026-01-06"]
    assert stats["days"] == 1
    # needed 覆盖 T 日池里的题材（AI应用+算力）→ 两个板块各拉一次；失败吞异常落空列表
    assert sorted(fetched) == ["885000", "885001"]
    assert bars_by_board["算力"] == []
    ai = samples[0]
    assert ai["tag"] == "AI应用"
    assert ai["inputs"]["theme_pct"] == 2.0 and ai["fwd"] == 3.0
    assert stats["no_board_bars"] == 0 and stats["unmatched"] == 0
