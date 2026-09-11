"""`app/market/commodity_chain.py` 单测（全合成数据，零网络）。

覆盖重点：①方向符号（含实测反向链）②死区内 = 真信息而非"算不了"③四类跳过原因
④前视守卫 ⑤聚合分档与"有效权重不足→未判定"⑥红线 3 文案守卫 ⑦取数编排降级。
外加一条**反漂移守卫**：生产 SPECS 里 weight>0 的链必须带实测依据（不得留"待标定"）。
"""
from __future__ import annotations

import asyncio
from datetime import date

from app.market import commodity_chain as cc

ASOF = date(2026, 9, 11)


def _spec(**over) -> cc.CommoditySpec:
    base = dict(
        code="X0", label="测试品", sw_index="801000", sw_name="测试行业",
        intuition=1, dead_zone=1.0, weight=1.0, sign=1,
        mid_verified=False, mid_edge=None, mid_t=None, mid_n=0, basis="单测",
    )
    base.update(over)
    return cc.CommoditySpec(**base)


def _run(series, specs, asof=ASOF):
    return cc.evaluate(series, asof=asof, specs=specs)


# ---------------------------------------------------------------- 变化量口径

def test_change_of_is_percent():
    assert round(cc.change_of(_spec(), 100.0, 105.0), 4) == 5.0
    assert round(cc.change_of(_spec(), 100.0, 95.0), 4) == -5.0


def test_change_of_zero_prev_is_zero_not_inf():
    assert cc.change_of(_spec(), 0.0, 100.0) == 0.0


# ---------------------------------------------------------------- 方向与死区

def test_bullish_chain_up_beyond_dead_zone():
    s = _spec(dead_zone=1.0, sign=1)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 105.0)]}, (s,))
    row = res["rows"][0]
    assert row["bias"] == 1
    assert row["zone"] == "涨破死区"
    assert row["change"] == 5.0
    assert res["stance"] == "偏多"


def test_bullish_chain_down_beyond_dead_zone():
    s = _spec(dead_zone=1.0, sign=1)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 94.0)]}, (s,))
    assert res["rows"][0]["bias"] == -1
    assert res["stance"] == "偏空"


def test_inverse_chain_sign_flips_direction():
    """实测反向链（成本端，如锂价跌利好电池）：商品涨 → 板块偏空。"""
    s = _spec(dead_zone=1.0, sign=-1, intuition=-1)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 106.0)]}, (s,))
    row = res["rows"][0]
    assert row["zone"] == "涨破死区"
    assert row["bias"] == -1
    assert res["stance"] == "偏空"


def test_dead_zone_gives_zero_bias_and_is_not_a_skip():
    """死区内 = 「确无偏向」的真信息，不得与「算不了」混为一谈（三态纪律）。"""
    s = _spec(dead_zone=3.0, sign=1)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 101.0)]}, (s,))
    row = res["rows"][0]
    assert row["zone"] == "死区内"
    assert row["bias"] == 0
    assert "skip_reason" not in row
    assert res["stance"] == "无偏向"
    assert res["active_weight"] == 1.0


def test_dead_zone_uses_each_commodity_own_scale():
    """同一 2% 变化，死区 1% 触发、死区 5% 不触发 —— 证明死区是按自身尺度而非统一值。"""
    up = {"X0": [("2026-09-09", 100.0), ("2026-09-10", 102.0)]}
    assert _run(up, (_spec(dead_zone=1.0),))["rows"][0]["bias"] == 1
    assert _run(up, (_spec(dead_zone=5.0),))["rows"][0]["bias"] == 0


# ---------------------------------------------------------------- 三态跳过

def test_source_unavailable_marks_skip():
    res = _run({}, (_spec(),))
    row = res["rows"][0]
    assert row["skip_reason"] == "源不可得"
    assert row["change"] is None and row["bias"] is None
    assert res["stance"] is None
    assert "隔夜口径实测不成立" in res["unjudged_reason"]


def test_insufficient_points_marks_skip():
    res = _run({"X0": [("2026-09-10", 100.0)]}, (_spec(),))
    row = res["rows"][0]
    assert row["skip_reason"] == "有效点不足（<2）"
    assert row["bias"] is None


def test_stale_data_marks_skip():
    s = _spec()
    res = _run({"X0": [("2026-01-02", 100.0), ("2026-01-03", 105.0)]}, (s,))
    row = res["rows"][0]
    assert row["skip_reason"].startswith("数据滞后")
    assert row["change"] == 5.0  # 变化量照给，只是不参与判定
    assert row["bias"] is None


def test_lookahead_guard_excludes_asof_day():
    """asof 当日（盘中未完成 bar）必须被丢弃，否则构成前视。"""
    s = _spec()
    res = _run(
        {"X0": [("2026-09-09", 100.0), ("2026-09-10", 105.0), ("2026-09-11", 130.0)]},
        (s,),
    )
    row = res["rows"][0]
    assert row["date"] == "2026-09-10"
    assert row["change"] == 5.0


def test_dirty_rows_are_dropped_not_fatal():
    """脏行（非数、负价、缺字段）一律丢弃；两种形态（dict / tuple）都要吃。"""
    s = _spec()
    raw = [
        ("2026-09-08", "abc"),
        ("2026-09-09", 100.0),
        {"date": "2026-09-10", "close": 105.0},
        {"date": "2026-09-10", "close": None},
        "garbage",
    ]
    res = _run({"X0": raw}, (s,))
    row = res["rows"][0]
    assert row["change"] == 5.0
    assert row["prev_date"] == "2026-09-09"


def test_all_dirty_rows_equals_source_unavailable():
    res = _run({"X0": ["garbage", ("bad", None)]}, (_spec(),))
    assert res["rows"][0]["skip_reason"] == "源不可得"


# ---------------------------------------------------------------- 聚合与分档

def test_unweighted_spec_does_not_count_into_stance():
    """weight=0.0（实测否决）的链仍出证据行，但绝不参与判定。"""
    dead = _spec(code="X0", weight=0.0)
    live = _spec(code="Y0", weight=1.0, dead_zone=1.0)
    series = {
        "X0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)],  # 本该偏多，但被否决
        "Y0": [("2026-09-09", 100.0), ("2026-09-10", 90.0)],   # 偏空
    }
    res = _run(series, (dead, live))
    assert res["rows"][0]["weighted"] is False
    assert res["rows"][0]["bias"] == 1
    assert res["active_weight"] == 1.0
    assert res["stance"] == "偏空"


def test_stance_none_when_active_weight_below_floor():
    weak = _spec(code="X0", weight=0.25)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)]}, (weak,))
    assert res["active_weight"] == 0.25
    assert res["stance"] is None
    assert "隔夜口径实测不成立" in res["unjudged_reason"]


def test_score_carries_weights_and_range():
    a = _spec(code="A0", weight=1.0, dead_zone=1.0)
    b = _spec(code="B0", weight=0.5, dead_zone=1.0, sign=-1)
    series = {
        "A0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)],  # +1 × 1.0
        "B0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)],  # -1 × 0.5
    }
    res = _run(series, (a, b))
    assert res["score"] == 0.5
    assert res["active_weight"] == 1.5
    assert res["score_range"] == "±1.5"


# ---------------------------------------------------------------- 红线 3 / 防漂移

def test_red_line_three_text_present():
    res = _run({}, (_spec(),))
    assert "不构成买卖建议" in res["disclaimer"]
    assert res["epistemic_note"]
    assert "因果预测" in res["epistemic_note"]
    assert res["stance"] in (None, "偏多", "偏空", "无偏向")


def test_timing_note_states_no_lead_and_resonance():
    """时点结构声明必须出现——这是防「把同日共振读成领先信号」的关键文案。"""
    res = _run({}, (_spec(),))
    assert "共振" in res["timing_note"]
    assert "领先" in res["timing_note"]


# ---------------------------------------------------------------- 中期线索

def test_mid_signal_only_for_verified_chain():
    v = _spec(code="V0", mid_verified=True, mid_edge=0.381, mid_t=3.389, mid_n=4214)
    u = _spec(code="U0", mid_verified=False)
    series = {
        "V0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)],
        "U0": [("2026-09-09", 100.0), ("2026-09-10", 110.0)],
    }
    res = _run(series, (v, u))
    assert len(res["mid_signals"]) == 1
    sig = res["mid_signals"][0]
    assert sig["code"] == "V0"
    assert sig["edge_pct"] == 0.381 and sig["edge_t"] == 3.389 and sig["sample_n"] == 4214
    assert res["rows"][0]["mid_signal"] is True
    assert res["rows"][0]["mid_bias"] == 1
    assert res["rows"][1]["mid_signal"] is False
    assert res["rows"][1]["mid_bias"] is None
    assert "未通过实证" in res["rows"][1]["note"]


def test_mid_signal_requires_actual_move():
    """已验证的链也要「本次真有异动」才成立——死区内不给中期线索（三态纪律）。"""
    v = _spec(dead_zone=5.0, mid_verified=True, mid_edge=0.5, mid_t=3.0, mid_n=100)
    res = _run({"X0": [("2026-09-09", 100.0), ("2026-09-10", 101.0)]}, (v,))
    assert res["rows"][0]["mid_signal"] is False
    assert res["mid_signals"] == []
    assert res["rows"][0]["note"] == "变化在死区内，不给方向"


# ---------------------------------------------------------------- 反漂移守卫

def test_production_specs_have_no_overnight_weight():
    """实证结论守卫：隔夜口径实测不成立 ⇒ 任何链都不得带隔夜权重。

    若后人「觉得应该有」而把权重打开，此断言会立刻失败并指向模块 docstring。
    """
    offenders = [s.code for s in cc.SPECS if s.weight != 0.0]
    assert offenders == [], f"隔夜口径已实测否决，不得赋权重：{offenders}"


def test_mid_verified_specs_carry_measured_edge():
    """中期线索必须有实测数字支撑；所有链都必须写明依据（含否决依据）。"""
    for s in cc.SPECS:
        assert s.basis and "待标定" not in s.basis, f"{s.code} 依据未回填"
        if s.mid_verified:
            assert s.mid_edge is not None and s.mid_t is not None and s.mid_n > 0
            assert abs(s.mid_t) >= 2.0, f"{s.code} 中期 t 值不足却标为已验证"
            assert "t=" in s.basis, f"{s.code} 已验证却未写明实测 t 值"


def test_production_specs_are_unique_per_industry_and_unique_codes():
    codes = [s.code for s in cc.SPECS]
    industries = [s.sw_index for s in cc.SPECS]
    assert len(codes) == len(set(codes))
    assert len(industries) == len(set(industries))


# ---------------------------------------------------------------- 取数编排

class _FakeExt:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()

    async def commodity_daily(self, code: str):
        if code in self.fail:
            raise RuntimeError("boom")
        return [{"date": "2026-09-09", "close": 100.0},
                {"date": "2026-09-10", "close": 110.0}]


def _patch_ext(monkeypatch, fake):
    import app.services.akshare_ext as ext_mod

    monkeypatch.setattr(ext_mod, "get_akshare_ext", lambda: fake)


def test_collect_handles_partial_failure(monkeypatch):
    _patch_ext(monkeypatch, _FakeExt(fail=set()))
    specs = (_spec(code="X0"), _spec(code="Y0"))
    monkeypatch.setattr(cc, "SPECS", specs)
    res = asyncio.run(cc.collect(ASOF))
    assert res is not None
    assert res["stance"] == "偏多"
    assert all(r["bias"] == 1 for r in res["rows"])


def test_collect_single_dead_source_is_skipped_only(monkeypatch):
    _patch_ext(monkeypatch, _FakeExt(fail={"Y0"}))
    specs = (_spec(code="X0"), _spec(code="Y0"))
    monkeypatch.setattr(cc, "SPECS", specs)
    res = asyncio.run(cc.collect(ASOF))
    assert res is not None
    assert res["rows"][1]["skip_reason"] == "源不可得"
    assert res["stance"] == "偏多"  # X0 仍可判定


def test_collect_returns_none_when_all_sources_dead(monkeypatch):
    _patch_ext(monkeypatch, _FakeExt(fail={"X0", "Y0"}))
    specs = (_spec(code="X0"), _spec(code="Y0"))
    monkeypatch.setattr(cc, "SPECS", specs)
    assert asyncio.run(cc.collect(ASOF)) is None
