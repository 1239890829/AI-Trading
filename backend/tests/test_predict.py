"""新题材预判：评分卡、梯队推演、介入计划、验证四问、落库。

用合成证据包驱动（不依赖网络）。关键不变式：
- 每条非「不预判」的 verdict 必须带失效条件（红线：可证伪）
- 关联判定只认数据面（新闻/标签/名称三路），无证据 → hot_score=0 + gap
- 验证幂等：已验证的报告不再重算
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.predict.collector import guess_context, next_weekday_after
from app.predict.engine import WEIGHTS, judge_theme, plan_entries
from app.predict.schemas import EchelonCandidate, PredictionReport, ThemePrediction
from app.predict.service import maybe_auto_verify, verify_predictions
from app.predict.storage import get_report, hit_stats, list_reports, save_report


# ---------------------------------------------------------------- 合成证据包

def _cand(symbol: str, name: str, rank: int, news: list[str] | None = None, net_buy: float | None = None) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "hot_rank": rank,
        "heat": 1000,
        "news_matched": news or [],
        "news_sample": news or [],
        "tags": [],
        "dragon_net_buy": net_buy,
    }


def _pack(candidates: list[dict], phase: str | None = "分歧", occupied: bool = False) -> dict:
    return {
        "now_cst": datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc).isoformat(),
        "context": "weekend",
        "last_trade_date": "20260828",
        "target_date": "20260831",
        "keywords": ["住房", "地产"],
        "hot_list": [],
        "candidates": candidates,
        "tags_by_symbol_size": 0,
        "theme_tag_freq": {"房地产": 2} if occupied else {},
        "active_themes": [{"theme": "房地产", "score": 5, "tier": "强势"}] if occupied else [],
        "env": {"phase": phase} if phase else {},
        "gaps": [],
    }


# ---------------------------------------------------------------- 评分卡

KEYWORDS = ["住房", "地产", "房产"]


def test_strong_theme_gets_established_verdict():
    pack = _pack([
        _cand("000560", "我爱我家", 5, news=["住建部：进一步促进住房消费的政策解读"]),
        _cand("600162", "香江控股", 10, news=["多地跟进地产新政，住房市场预期回暖"]),
        _cand("600722", "金牛化工", 6),  # 与题材无关的热榜股
    ])
    p = judge_theme("房地产", KEYWORDS, pack)
    assert p.verdict == "预判成立"
    assert p.score >= 0.72
    assert p.confidence == "high"
    kinds = {e.kind for e in p.evidence}
    assert {"hot_presence", "news_linkage", "policy_level", "freshness", "environment"} <= kinds
    # 龙头候选 = 人气最高的相关标的（#5 我爱我家）
    assert p.echelon[0].symbol == "000560"
    assert p.fail_conditions, "预判成立也必须带失效条件"


def test_no_data_linkage_fails_not_speculated():
    """三路（新闻/标签/名称）都无关联 → 不预判 + gap，绝不臆测。"""
    pack = _pack([
        _cand("600127", "金健米业", 1),  # 无任何题材关联证据
    ])
    p = judge_theme("房地产", KEYWORDS, pack)
    assert p.verdict == "不预判"
    assert p.score < 0.42
    assert any("hot_presence" in g for g in p.data_gaps)


def test_policy_level_from_news_titles():
    pack = _pack([
        _cand("000560", "我爱我家", 5, news=["国务院常务会议部署房地产平稳发展措施"]),
        _cand("600162", "香江控股", 10, news=["地产板块周末消息发酵"]),
    ])
    p = judge_theme("房地产", KEYWORDS, pack)
    pol = next(e for e in p.evidence if e.kind == "policy_level")
    assert "国务院" in pol.content
    assert pol.contribution == pytest.approx(WEIGHTS["policy_level"])


def test_old_theme_occupied_halves_freshness():
    pack = _pack([
        _cand("000560", "我爱我家", 5, news=["住房新政发布"]),
    ], occupied=True)
    p = judge_theme("房地产", KEYWORDS, pack)
    fresh = next(e for e in p.evidence if e.kind == "freshness")
    assert "旧题材" in fresh.content


def test_heuristic_auto_theme_capped_confidence():
    pack = _pack([
        _cand("000560", "我爱我家", 5, news=["国务院发布住房新政"]),
        _cand("600162", "香江控股", 10, news=["住房新政发酵"]),
    ])
    p = judge_theme("住房", ["住房"], pack, heuristic=True)
    assert p.confidence != "high", "自动发现的主题不允许 high 置信度"


# ---------------------------------------------------------------- 梯队与介入

def test_echelon_ranking_prefers_rank_and_news():
    cands = [
        _cand("600162", "香江控股", 3, news=[]),  # 相关（标签路），但无新闻命中
        _cand("000560", "我爱我家", 5, news=["住房新政", "楼市解读"]),
    ]
    cands[0]["tags"] = ["地产"]
    ech = judge_theme("房地产", KEYWORDS, _pack(cands)).echelon
    assert ech[0].symbol == "000560"  # #3 无新闻命中 vs #5 双命中 → 后者综合分更高
    assert ech[0].role == "龙头候选"
    assert ech[1].role == "中军候选"  # 2 个候选：龙头+中军，无跟风


def test_echelon_note_always_honest():
    p = judge_theme("房地产", KEYWORDS, _pack([_cand("000560", "我爱我家", 5, news=["住房"])]))
    assert "走出来" in p.echelon_note


def test_plan_entries_weak_verdict_only_first_board():
    plans = plan_entries("弱预期", "分歧", "low")
    timings = {p.timing for p in plans}
    assert timings == {"D1盘中首板"}
    plans_strong = plan_entries("预判成立", "冰点", "high")
    assert "D2分歧低吸" in {p.timing for p in plans_strong}
    assert "一字次日追高(禁)" in {p.timing for p in plans_strong}


def test_ebb_phase_discounts_success_rate():
    normal = plan_entries("预判成立", "冰点", "high")
    ebb = plan_entries("预判成立", "退潮", "high")
    n_auction = next(p for p in normal if p.timing == "D1竞价")
    e_auction = next(p for p in ebb if p.timing == "D1竞价")
    assert e_auction.est_success != n_auction.est_success  # 退潮 -10pp


# ---------------------------------------------------------------- 语境与目标日

def test_context_and_target_date():
    assert guess_context(datetime(2026, 8, 30, 10, 0)) == "weekend"   # 周日
    assert guess_context(datetime(2026, 8, 31, 8, 0)) == "pre_market"  # 周一盘前
    assert guess_context(datetime(2026, 8, 28, 20, 0)) == "evening"    # 周五盘后
    nxt = next_weekday_after(date(2026, 8, 30))  # 周日 → 周一
    assert nxt.weekday() == 0 and nxt == date(2026, 8, 31)


# ---------------------------------------------------------------- 验证四问 + 落库

from app.core.db import get_engine, get_session_factory  # noqa: E402
from app.models.watchlist import Base  # noqa: E402
from app.schemas.market import LimitUpRecord  # noqa: E402


def _report(target: str = "20991201", outcome_ready: bool = True) -> PredictionReport:
    return PredictionReport(
        prediction_id=f"PR-{target}-TEST",
        created_at=datetime.now(timezone.utc).isoformat(),
        context="weekend",
        target_date=target,
        trigger="test",
        predictions=[ThemePrediction(
            theme="房地产",
            keywords=KEYWORDS,
            verdict="预判成立",
            score=0.8,
            confidence="high",
            echelon=[EchelonCandidate(symbol="000560", name="我爱我家", role="龙头候选", hot_rank=5, basis="热榜第5", confidence="medium")],
            fail_conditions=["D1 涨停 <3 作废"],
        )],
        summary="test",
    )


class _FakeProvider:
    def __init__(self, pool: list[LimitUpRecord], hot: list[dict]):
        self._pool, self._hot = pool, hot

    async def get_limit_up_pool(self, d):
        return self._pool

    async def get_hot_stock_list_history(self, d):
        return self._hot


class _FakeHub:
    def __init__(self, provider):
        self.provider = provider


def _lu(symbol: str, name: str, boards: int, reason: str) -> LimitUpRecord:
    return LimitUpRecord(
        symbol=symbol, name=name, trade_date=date(2099, 12, 1),
        consecutive_boards=boards, reason=reason,
        source="ths", quality="high",
    )


@pytest.fixture()
def sf():
    Base.metadata.create_all(get_engine())
    return get_session_factory()


def test_verify_hit_partial_miss_and_idempotent(sf):
    target = "20991201"
    save_report(sf, _report(target))
    pool = [
        _lu("000560", "我爱我家", 3, "住房政策+房产中介"),
        _lu("600162", "香江控股", 1, "住房政策+地产"),
        _lu("000017", "深中华A", 1, "住房政策+深圳本地"),
    ]
    hub = _FakeHub(_FakeProvider(pool, hot=[{"rank": 3, "symbol": "000560", "name": "我爱我家"}]))

    v = asyncio.run(verify_predictions(hub, None, sf, target))
    t = v["themes"][0]
    assert t["outcome"] == "hit"
    assert t["formed"] and t["limit_up_count"] == 3
    assert t["leader_hit"] and t["leader_actual"].startswith("000560")

    # 幂等：再验返回同一结果
    v2 = asyncio.run(verify_predictions(hub, None, sf, target))
    assert v2 == v

    # 落库行同步
    stats = hit_stats(sf)
    assert stats["by_verdict"]["预判成立"]["hit"] >= 1


def test_verify_miss_when_theme_not_formed(sf):
    target = "20991202"
    save_report(sf, _report(target))
    pool = [_lu("600127", "金健米业", 2, "粮食安全")]  # 无题材关联涨停
    hub = _FakeHub(_FakeProvider(pool, hot=[]))
    import asyncio

    v = asyncio.run(verify_predictions(hub, None, sf, target))
    assert v["themes"][0]["outcome"] == "miss"


def test_verify_partial_when_leader_wrong(sf):
    target = "20991203"
    save_report(sf, _report(target))
    pool = [
        _lu("600162", "香江控股", 4, "住房政策+地产"),
        _lu("000017", "深中华A", 1, "住房政策"),
        _lu("000034", "深信服", 1, "住房政策+数字化"),
    ]
    hub = _FakeHub(_FakeProvider(pool, hot=[]))
    import asyncio

    v = asyncio.run(verify_predictions(hub, None, sf, target))
    t = v["themes"][0]
    assert t["outcome"] == "partial"
    assert not t["leader_hit"]


def test_storage_overwrite_same_target(sf):
    target = "20991204"
    r1 = _report(target)
    r1.prediction_id = "PR-OLD"
    save_report(sf, r1)
    r2 = _report(target)
    r2.prediction_id = "PR-NEW"
    r2.predictions[0].verdict = "可能成立"
    save_report(sf, r2)
    got = get_report(sf, target)
    assert got.prediction_id == "PR-NEW"
    items = list_reports(sf, limit=50)
    assert all(i["target_date"] != target or i["prediction_id"] == "PR-NEW" for i in items)


def test_maybe_auto_verify_hook(sf):
    target = "20991205"
    save_report(sf, _report(target))
    pool = [_lu("000560", "我爱我家", 2, "住房政策"), _lu("600162", "香江控股", 1, "住房"), _lu("000017", "深中华A", 1, "住房")]
    hub = _FakeHub(_FakeProvider(pool, hot=[]))
    import asyncio

    note = asyncio.run(maybe_auto_verify(hub, None, sf, date(2099, 12, 5)))
    assert note and "hit" in note
    # 已验证 → 钩子返回 None（幂等）
    assert asyncio.run(maybe_auto_verify(hub, None, sf, date(2099, 12, 5))) is None
