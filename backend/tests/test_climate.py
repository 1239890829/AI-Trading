"""P1-32 气候一阶源（ENSO/ONI）单测。

覆盖四类高危点：
1. **季 → 月映射**（DJF 锚 1 月、末月 2 月；NDJ 锚 12 月、末月次年 1 月）——
   错了会把值挂到错误月份，且完全不报错；
2. **前视守卫**：只有「季末月已结束」的季才算已知；
3. **官方口径**：连续 **5** 季才叫「确立」，不满 5 季只能叫预警；
4. **拉尼娜不给方向**——「取反」正是 KB-DEC-018 禁止的直觉链。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.market import climate as cl

HEADER = "  SEAS  YR   TOTAL   ANOM"


def _pt(season: str, year: int, anom: float) -> cl.OniPoint:
    return cl.OniPoint(season=season, year=year, total=0.0, anom=anom)


def _seq(*anoms: float, start_year: int = 2020, start_idx: int = 0) -> list[cl.OniPoint]:
    """按 SEASONS 顺序（从 start_idx 起）连续造点，每个点给一个 ANOM。"""
    out: list[cl.OniPoint] = []
    y, i = start_year, start_idx
    for a in anoms:
        out.append(_pt(cl.SEASONS[i % 12], y, a))
        i += 1
        if i % 12 == 0:
            y += 1
    return out


# ---------------------------------------------------------------- 解析

def test_parse_oni_skips_header_and_dirty_rows():
    text = "\n".join([
        HEADER,
        "  DJF 1950  25.01  -1.32",
        "  JFM 1950  25.36  -1.20",
        "",
        "  XXX 1950  25.36  -1.20",   # 未知季节代号 → 跳过（宁可少点不可错挂）
        "  FMA 1950  abc   -1.12",    # TOTAL 脏 → 跳过
        "  MAM 1950  26.24  nope",    # ANOM 脏 → 跳过
        "  AMJ 1950 26.35 -1.10",     # 空格数不同但 split() 能处理
    ])
    pts = cl.parse_oni(text)
    assert [p.season for p in pts] == ["DJF", "JFM", "AMJ"]
    assert pts[0].year == 1950 and pts[0].anom == pytest.approx(-1.32)


def test_parse_oni_sorted_by_time_not_file_order():
    text = "\n".join([
        "  JFM 1951  25.00  0.10",
        "  DJF 1950  25.00  -1.00",
        "  NDJ 1950  25.00  -0.90",
    ])
    pts = cl.parse_oni(text)
    assert [(p.year, p.season) for p in pts] == [
        (1950, "DJF"), (1950, "NDJ"), (1951, "JFM"),
    ]


def test_parse_oni_empty_is_empty():
    assert cl.parse_oni("") == []
    assert cl.parse_oni(HEADER) == []


# ---------------------------------------------------------------- 季 → 月

def test_season_anchor_and_end_month():
    assert _pt("DJF", 2020, 0).anchor_month == 1
    assert _pt("DJF", 2020, 0).end_month == 2
    assert _pt("NDJ", 2020, 0).anchor_month == 12
    assert _pt("NDJ", 2020, 0).end_month == 1
    assert _pt("SON", 2020, 0).anchor_month == 10
    assert _pt("SON", 2020, 0).end_month == 11


def test_season_end_crosses_year_boundary():
    # NDJ 2020 = 2020-11 / 2020-12 / 2021-01 → 末月 2021-01
    assert cl.season_end(_pt("NDJ", 2020, 0)) == date(2021, 1, 31)
    # DJF 2020 = 2019-12 / 2020-01 / 2020-02 → 末月 2020-02
    assert cl.season_end(_pt("DJF", 2020, 0)) == date(2020, 2, 29)  # 闰年
    assert cl.season_end(_pt("JJA", 2021, 0)) == date(2021, 8, 31)


# ---------------------------------------------------------------- 判定

def test_classify_empty_series_is_unjudged_not_neutral():
    """三态纪律：算不了 → None + 具名原因，**不得**伪造 neutral。"""
    res = cl.classify([], asof=date(2026, 9, 11))
    assert res["state"] is None
    assert res["alert"] is None
    assert "数据不足" in res["unjudged_reason"]
    assert res["candidate_links"] == []


def test_lookahead_guard_excludes_unfinished_season():
    """JJA 2026 的季末是 2026-08-31：asof=2026-08-15 时它还没走完，不得纳入。"""
    pts = [_pt("AMJ", 2026, 0.95), _pt("MJJ", 2026, 1.39), _pt("JJA", 2026, 1.80)]
    early = cl.classify(pts, asof=date(2026, 8, 15))
    assert early["latest"]["season"] == "MJJ"       # JJA 被守卫剔除
    late = cl.classify(pts, asof=date(2026, 9, 11))
    assert late["latest"]["season"] == "JJA"


def test_established_requires_five_consecutive_seasons():
    """官方口径：连续 5 季越线才算「厄尔尼诺」，4 季只能算预警。"""
    four = _seq(0.6, 0.7, 0.8, 0.9, start_year=2023, start_idx=2)   # FMA~JJA，季末 08-31
    r4 = cl.classify(four, asof=date(2023, 10, 31))
    assert r4["state"] == "neutral"
    assert r4["alert"] == "el_nino"
    assert r4["consecutive"] == 4

    five = _seq(0.6, 0.7, 0.8, 0.9, 1.6, start_year=2023, start_idx=2)
    r5 = cl.classify(five, asof=date(2023, 10, 31))
    assert r5["state"] == "el_nino"
    assert r5["alert"] is None
    assert r5["consecutive"] == 5
    assert r5["strength"] == "strong"


def test_run_breaks_on_non_crossing_season():
    """中间夹一个未越线季 → 连续段从此断开（不是「历史上出现过就算」）。"""
    pts = [_pt("MAM", 2026, 0.46), _pt("AMJ", 2026, 0.95),
           _pt("MJJ", 2026, 1.39), _pt("JJA", 2026, 1.80)]
    r = cl.classify(pts, asof=date(2026, 9, 11))
    assert r["consecutive"] == 3
    assert r["alert"] == "el_nino"
    assert r["state"] == "neutral"


def test_la_nina_side_detected():
    pts = _seq(-0.6, -0.8, -1.1, -1.6, -2.1, start_year=2024, start_idx=4)  # AMJ~ASO，季末 09-30
    r = cl.classify(pts, asof=date(2024, 11, 30))
    assert r["state"] == "la_nina"
    assert r["strength"] == "very_strong"
    assert r["peak_abs"] == pytest.approx(2.1)


def test_neutral_is_a_real_verdict():
    pts = _seq(0.1, -0.2, 0.3, start_year=2026, start_idx=2)  # FMA~AMJ，季末 06-30
    r = cl.classify(pts, asof=date(2026, 9, 11))
    assert r["state"] == "neutral"
    assert r["alert"] is None
    assert r["unjudged_reason"] is None
    assert r["strength"] is None


def test_stale_series_is_unjudged():
    """源停更 60 天以上 → 未判定 + 具名原因（不拿旧相位冒充当前）。"""
    pts = [_pt("DJF", 2019, 0.9), _pt("JFM", 2020, 0.9)]
    r = cl.classify(pts, asof=date(2026, 9, 11))
    assert r["state"] is None
    assert "滞后" in r["unjudged_reason"]


def test_classify_accepts_string_asof():
    pts = _seq(0.6, 0.7, 0.8, 0.9, 1.0, start_year=2024, start_idx=0)  # DJF~AMJ，季末 06-30
    r = cl.classify(pts, asof="2024-08-31")
    assert r["as_of"] == "2024-08-31"
    assert r["state"] == "el_nino"


# ---------------------------------------------------------------- 候选链

def test_candidate_links_only_on_el_nino_side():
    """拉尼娜**不给**候选行：取反就是直觉链（KB-DEC-018 明令禁止）。"""
    assert cl.candidate_links("la_nina", None) == []
    assert cl.candidate_links("la_nina", "la_nina") == []
    assert cl.candidate_links("neutral", None) == []
    assert cl.candidate_links(None, None) == []
    assert len(cl.candidate_links("el_nino", None)) > 0
    assert len(cl.candidate_links("neutral", "el_nino")) > 0


def test_candidate_links_are_the_single_source_from_chains():
    """链条内容必须来自 chains（唯一真相源）——复制第二份必然漂移。"""
    from app.events.chains import el_nino_rows

    assert cl.candidate_links("el_nino", None) == el_nino_rows()
    # 返回的是拷贝：调用方改 basis 不会污染 chains 表
    rows = el_nino_rows()
    rows[0]["basis"] = "MUTATED"
    assert el_nino_rows()[0]["basis"] != "MUTATED"


def test_classify_attaches_candidate_links_for_established_el_nino():
    pts = _seq(0.6, 0.7, 0.8, 0.9, 1.6, start_year=2023, start_idx=2)
    r = cl.classify(pts, asof=date(2023, 10, 31))
    assert r["state"] == "el_nino"
    assert {row["target"] for row in r["candidate_links"]} >= {"磷化工", "化肥", "农业种植"}


# ---------------------------------------------------------------- 反漂移守卫

def test_threshold_and_persistence_match_official_definition():
    """这两个数字是 NOAA 官方定义，不是可调参数——改了就不是 ONI 口径了。"""
    assert cl.ONI_THRESHOLD == 0.5
    assert cl.PERSIST_SEASONS == 5


def test_chain_caveat_is_carried_in_output():
    """传导链未经验证的定性必须随输出走——否则读的人会当成结论。"""
    pts = _seq(0.6, 0.7, 0.8, 0.9, 1.6, start_year=2023, start_idx=2)
    r = cl.classify(pts, asof=date(2023, 10, 31))
    assert "候选假设" in r["chain_caveat"]
    assert "未构成领先" in r["timing_note"] or "不构成领先" in r["timing_note"]
    assert "不构成买卖建议" in r["disclaimer"]


# ---------------------------------------------------------------- 取数编排

def test_collect_returns_none_when_source_unavailable(monkeypatch):
    async def _boom() -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(cl, "fetch_oni_text", _boom)
    assert asyncio.run(cl.collect(date(2026, 9, 11))) is None


def test_collect_returns_none_when_text_unparsable(monkeypatch):
    async def _junk() -> str:
        return "not an oni file"

    monkeypatch.setattr(cl, "fetch_oni_text", _junk)
    assert asyncio.run(cl.collect(date(2026, 9, 11))) is None


def test_collect_parses_and_classifies(monkeypatch):
    text = "\n".join([
        HEADER,
        "  AMJ 2026  28.74   0.95",
        "  MJJ 2026  29.02   1.39",
        "  JJA 2026  29.09   1.80",
    ])

    async def _ok() -> str:
        return text

    monkeypatch.setattr(cl, "fetch_oni_text", _ok)
    r = asyncio.run(cl.collect(date(2026, 9, 11)))
    assert r is not None
    assert r["latest"]["season"] == "JJA"
    assert r["alert"] == "el_nino"
    assert r["state"] == "neutral"
    assert [s["season"] for s in r["series"]] == ["AMJ", "MJJ", "JJA"]
