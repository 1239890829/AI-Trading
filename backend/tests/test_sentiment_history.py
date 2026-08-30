"""情绪周期序列测试（retro #17）：落库幂等 / 报告回填 / 周期定位纯函数。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.market.sentiment_history import (
    SentimentHistoryRow,
    backfill_from_reports,
    extract_from_review_payload,
    get_history,
    locate_cycle,
    upsert_if_absent,
)
from app.models.watchlist import Base
from app.review.models import ReviewReportRow

# import 即注册进 Base.metadata（create_all 需要）；显式引用避免未使用告警
_REGISTERED = (SentimentHistoryRow, ReviewReportRow)


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _entry(date: str, phase: str, temp: float = 60.0) -> dict:
    return {
        "trade_date": date, "phase": phase, "temperature": temp,
        "confidence": "中", "phase_unreliable": False, "source": "review",
        "detail": {"phase": phase},
    }


class TestUpsert:
    def test_insert_then_skip(self, sf):
        assert upsert_if_absent(sf, _entry("20260828", "分歧")) is True
        # 同日再写：跳过不覆盖（历史是"当时判了什么"的记录）
        assert upsert_if_absent(sf, _entry("20260828", "高潮", 88.0)) is False
        h = get_history(sf, days=10)
        assert len(h) == 1
        assert h[0]["phase"] == "分歧" and h[0]["temperature"] == 60.0

    def test_ordering_and_limit(self, sf):
        for d, p in [("20260826", "退潮"), ("20260828", "分歧"), ("20260827", "退潮")]:
            upsert_if_absent(sf, _entry(d, p))
        h = get_history(sf, days=2)
        assert [x["trade_date"] for x in h] == ["20260827", "20260828"]
        full = get_history(sf, days=10)
        assert [x["trade_date"] for x in full] == ["20260826", "20260827", "20260828"]


class TestBackfill:
    def test_from_review_report(self):
        """save_report 钩子的等价路径：payload 里的情绪块被提取落库。"""
        from sqlalchemy import create_engine as ce
        from sqlalchemy.orm import sessionmaker as sm

        engine = ce("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sm(bind=engine, expire_on_commit=False)
        db = Session()

        payload = {
            "trade_date": "20260828",
            "data": {"market": {"sentiment": {
                "phase": "分歧", "temperature": 64.2, "confidence": "中",
                "trade_date": "20260828",
            }}},
        }
        db.add(ReviewReportRow(
            review_id="RV-20260828-test", trade_date="20260828",
            payload=json.dumps(payload, ensure_ascii=False),
        ))
        db.commit()
        db.close()

        added = backfill_from_reports(Session)
        assert added == 1
        h = get_history(Session, days=5)
        assert h[0]["phase"] == "分歧" and h[0]["temperature"] == 64.2
        assert h[0]["source"] == "review"
        # 幂等：再跑一遍不重复
        assert backfill_from_reports(Session) == 0
        engine.dispose()

    def test_payload_without_sentiment_ignored(self, sf):
        assert extract_from_review_payload({"data": {"market": {}}}) is None
        assert extract_from_review_payload({}) is None


class TestLocateCycle:
    def test_empty(self):
        assert locate_cycle([])["start_date"] is None

    def test_single_entry(self):
        r = locate_cycle([{"trade_date": "20260828", "phase": "高潮"}])
        assert r["start_date"] == "20260828" and r["days"] == 1
        assert r["current_group"] == "strong"

    def test_group_switch_detection(self):
        seq = [
            {"trade_date": "20260818", "phase": "高潮"},   # strong
            {"trade_date": "20260819", "phase": "分歧"},   # neutral
            {"trade_date": "20260820", "phase": "退潮"},   # weak
            {"trade_date": "20260821", "phase": "冰点"},   # weak（延续）
            {"trade_date": "20260822", "phase": "回暖"},   # strong ← 切换点
            {"trade_date": "20260825", "phase": "升温"},   # strong（延续）
        ]
        r = locate_cycle(seq)
        assert r["start_date"] == "20260822"
        assert r["start_phase"] == "回暖"
        assert r["days"] == 2
        assert r["current_group"] == "strong"
        # strong / neutral / weak(两日延续) / strong = 4 段
        assert len(r["segments"]) == 4
        assert r["segments"][2]["phases"] == ["退潮", "冰点"]

    def test_neutral_does_not_break_strong_run(self):
        seq = [
            {"trade_date": "20260818", "phase": "回暖"},
            {"trade_date": "20260819", "phase": "分歧"},   # 中段是独立分段
            {"trade_date": "20260820", "phase": "升温"},
        ]
        r = locate_cycle(seq)
        # 最近一次切换是 19 日（strong→neutral）……不，20 日 neutral→strong 又切回
        assert r["start_date"] == "20260820"
        assert r["current_group"] == "strong"
