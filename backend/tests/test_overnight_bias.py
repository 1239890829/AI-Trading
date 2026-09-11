"""P1-34 隔夜海外输入 → 大盘方向偏向：规则层单测。

重点守卫三件事（都是「看起来对但其实错」的高危点）：
① 美债 10Y **权重必须为 0**（实测否决项，最容易被"顺手补上权重"）；
② 三态：源不可得 / 数据滞后 / 前视对齐异常 一律记 missing，**绝不臆造 0 或沿用旧值**；
③ bp 换算（收益率源以百分数报价，Δ=0.03 表示 3bp）——曾因漏 ×100 使美债恒判「平」。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.market import overnight_bias as ob


def series(pairs: list[tuple[str, float]]) -> list[dict]:
    return [{"date": d, "close": v} for d, v in pairs]


BRIEF = date(2026, 9, 10)


def base_inputs(*, nas="up", sox="up", cnh="down", tnx="up") -> dict:
    """构造四路输入。up/down 决定变化方向，flat 决定落在死区内。"""
    # 各序列按自身死区取足够大的变化
    nas_v = {"up": (20000.0, 20200.0), "down": (20000.0, 19800.0), "flat": (20000.0, 20010.0)}[nas]
    sox_v = {"up": (5000.0, 5100.0), "down": (5000.0, 4900.0), "flat": (5000.0, 5005.0)}[sox]
    cnh_v = {"up": (7.10, 7.13), "down": (7.13, 7.10), "flat": (7.10, 7.101)}[cnh]
    tnx_v = {"up": (4.80, 4.95), "down": (4.95, 4.80), "flat": (4.80, 4.805)}[tnx]
    return {
        "nasdaq": series([("2026-09-08", nas_v[0]), ("2026-09-09", nas_v[1])]),
        "sox": series([("2026-09-08", sox_v[0]), ("2026-09-09", sox_v[1])]),
        "usdcnh": series([("2026-09-08", cnh_v[0]), ("2026-09-09", cnh_v[1])]),
        "us10y": series([("2026-09-08", tnx_v[0]), ("2026-09-09", tnx_v[1])]),
    }


# ---------------------------------------------------------------- ① 基本判定


def test_all_bullish_is_up_stance():
    res = ob.evaluate(base_inputs(), brief_date=BRIEF)
    assert res["stance"] == "走强"
    assert res["score"] == 3.0
    assert res["available_weight"] == 3.0
    assert res["missing"] == []


def test_all_bearish_is_down_stance():
    res = ob.evaluate(base_inputs(nas="down", sox="down", cnh="up"), brief_date=BRIEF)
    assert res["stance"] == "承压"
    assert res["score"] == -3.0


def test_dead_zone_is_neutral():
    res = ob.evaluate(base_inputs(nas="flat", sox="flat", cnh="flat"), brief_date=BRIEF)
    assert res["stance"] == "中性"
    assert res["score"] == 0.0
    zones = {e["key"]: e["zone"] for e in res["evidence"]}
    assert zones["nasdaq"] == "平"
    assert all(e["bullish"] is None for e in res["evidence"] if e["zone"] == "平")


def test_single_conflict_yields_neutral():
    """3 路中 2 路同向 = ±2 才判方向；1 路单飞必须落在中性带。"""
    res = ob.evaluate(base_inputs(nas="up", sox="flat", cnh="flat"), brief_date=BRIEF)
    assert res["score"] == 1.0
    assert res["stance"] == "中性"


# ---------------------------------------------------------------- ② 高危点守卫


def test_us10y_weight_is_zero_even_on_huge_move():
    """美债 10Y 实测否决 → 权重 0。给它 +50bp 也不得改变方向或分数。"""
    quiet = ob.evaluate(base_inputs(nas="flat", sox="flat", cnh="flat", tnx="flat"), brief_date=BRIEF)
    loud = ob.evaluate(base_inputs(nas="flat", sox="flat", cnh="flat", tnx="up"), brief_date=BRIEF)
    assert quiet["score"] == loud["score"] == 0.0
    spec = next(s for s in ob.SPECS if s.key == "us10y")
    assert spec.weight == 0.0
    row = next(e for e in loud["evidence"] if e["key"] == "us10y")
    assert row["weighted"] is False
    assert row["change"] == pytest.approx(15.0)  # 4.95-4.80 = 0.15 个百分点 = 15bp


def test_change_of_converts_percent_to_bp():
    """收益率源以百分数报价：Δ0.03 必须读作 3bp（漏 ×100 会让美债恒判『平』）。"""
    spec = next(s for s in ob.SPECS if s.key == "us10y")
    assert ob.change_of(spec, 4.80, 4.83) == pytest.approx(3.0)
    pct = next(s for s in ob.SPECS if s.key == "nasdaq")
    assert ob.change_of(pct, 100.0, 101.0) == pytest.approx(1.0)


def test_usdcnh_rising_is_bearish():
    """离岸人民币数值上升 = 贬值 = 对 A 股不利（rising_bullish=False）。"""
    res = ob.evaluate(base_inputs(nas="flat", sox="flat", cnh="up"), brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "usdcnh")
    assert row["zone"] == "升"
    assert row["bullish"] is False


# ---------------------------------------------------------------- ③ 三态


def test_missing_source_is_not_coerced_to_zero():
    inputs = base_inputs()
    inputs["nasdaq"] = []
    res = ob.evaluate(inputs, brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "nasdaq")
    assert row["change"] is None and row["bullish"] is None
    assert row["skip_reason"] == "源不可得"
    assert any("纳斯达克指数：源不可得" in m for m in res["missing"])
    # 其余两路仍参与 → available_weight 2.0，仍可判定
    assert res["available_weight"] == 2.0
    assert res["stance"] == "走强"


def test_insufficient_inputs_is_unjudged_not_neutral():
    inputs = base_inputs()
    inputs["nasdaq"] = []
    inputs["sox"] = []
    res = ob.evaluate(inputs, brief_date=BRIEF)
    assert res["stance"] is None
    assert res["score"] is None
    assert "未判定" in res["unjudged_reason"]


def test_single_point_is_insufficient():
    inputs = base_inputs()
    inputs["sox"] = series([("2026-09-09", 5000.0)])
    res = ob.evaluate(inputs, brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "sox")
    assert "有效点不足" in row["skip_reason"]


def test_lookahead_guard_excludes_same_day_data():
    """隔夜数据日期 ≥ 报告日 = 前视，必须排除（不得用当天收盘当隔夜）。"""
    inputs = base_inputs()
    inputs["nasdaq"] = series([("2026-09-09", 20000.0), ("2026-09-10", 20200.0)])
    res = ob.evaluate(inputs, brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "nasdaq")
    assert "对齐异常" in row["skip_reason"]
    assert any("对齐异常" in m for m in res["missing"])


def test_stale_data_beyond_gap_is_excluded():
    inputs = base_inputs()
    inputs["sox"] = series([("2026-08-01", 5000.0), ("2026-09-09", 5100.0)])
    res = ob.evaluate(inputs, brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "sox")
    assert "数据滞后" in row["skip_reason"]


def test_invalid_rows_are_dropped_not_guessed():
    inputs = base_inputs()
    inputs["sox"] = [
        {"date": "bad", "close": 1.0},
        {"date": "2026-09-08", "close": None},
        {"date": "2026-09-08", "close": 5000.0},
        {"date": "2026-09-09", "close": 5100.0},
    ]
    res = ob.evaluate(inputs, brief_date=BRIEF)
    row = next(e for e in res["evidence"] if e["key"] == "sox")
    assert row["change"] == pytest.approx(2.0)


# ---------------------------------------------------------------- ④ 红线 3 守卫


def test_output_carries_disclaimer_and_invalidation():
    res = ob.evaluate(base_inputs(), brief_date=BRIEF)
    assert "不构成买卖建议" in res["disclaimer"]
    assert len(res["invalidation"]) >= 3
    assert "开盘" in res["horizon_note"]
    assert res["as_of"]["nasdaq"] == "2026-09-09"
    assert res["score_range"] == "±3"


# ---------------------------------------------------------------- ⑤ 取数编排


class _FakeExt:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()

    async def us_index_daily(self, symbol: str):
        key = "nasdaq" if symbol == ".IXIC" else "sox"
        if key in self.fail:
            raise RuntimeError("boom")
        return base_inputs()[key]

    async def usdcnh_daily(self):
        if "usdcnh" in self.fail:
            raise RuntimeError("boom")
        return base_inputs()["usdcnh"]

    async def us_treasury_10y_daily(self):
        if "us10y" in self.fail:
            raise RuntimeError("boom")
        return base_inputs()["us10y"]


def test_collect_survives_single_source_failure(monkeypatch):
    """任一路失败只记 missing，不拖垮其余（三态：源不可得 ≠ 中性 ≠ 0）。"""
    from app.services import akshare_ext

    # 挂 2 路（纳指/费半），只剩权重 1 的 CNH → 有效权重不足 → 未判定（而非硬给方向）
    monkeypatch.setattr(akshare_ext, "get_akshare_ext",
                        lambda: _FakeExt({"nasdaq", "sox"}))
    res = asyncio.run(ob.collect(BRIEF))
    assert res["stance"] is None
    assert res["available_weight"] == 1.0
    assert any("源不可得" in m for m in res["missing"])


def test_collect_assembles_all_inputs(monkeypatch):
    from app.services import akshare_ext

    monkeypatch.setattr(akshare_ext, "get_akshare_ext", lambda: _FakeExt())
    res = asyncio.run(ob.collect(BRIEF))
    assert res["stance"] == "走强"
    assert res["missing"] == []


def test_collect_handles_partial_failure_with_two_live_inputs(monkeypatch):
    """挂 1 路（纳指）后剩 2 路有效权重 → 仍可判定（不因单点故障整体失能）。"""
    from app.services import akshare_ext

    monkeypatch.setattr(akshare_ext, "get_akshare_ext", lambda: _FakeExt({"nasdaq"}))
    res = asyncio.run(ob.collect(BRIEF))
    assert res["stance"] == "走强"
    assert res["available_weight"] == 2.0
    assert any("源不可得" in m for m in res["missing"])
