"""相位对账测试：预测目标集解析 / 四态判定 / sentiment_history 读取。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.market.sentiment_history import SentimentHistoryRow
from app.sentiment.reconcile import load_prev_entry, parse_predicted_targets, reconcile


class TestParse:
    def test_targets_extracted_in_order(self):
        text = "1进2 ≥40% 且中位 >4% 且涨停 ≥80 家 → 高潮；1进2 <25% → 分歧"
        assert parse_predicted_targets(text) == ["高潮", "分歧"]

    def test_empty_or_noise(self):
        assert parse_predicted_targets(None) == []
        assert parse_predicted_targets("") == []
        assert parse_predicted_targets("无箭头文本") == []


class TestReconcile:
    def test_transition_confirmed(self):
        prev = {"phase": "发酵", "switch_conditions": "…→ 高潮；…→ 分歧"}
        out = reconcile(prev, {"phase": "高潮"})
        assert out["verdict"] == "transition_confirmed"
        assert out["verdict_label"] == "切换兑现"

    def test_held_is_not_fake_hit(self):
        """延续是「条件未触发」的如实标注，不冒充命中。"""
        prev = {"phase": "发酵", "switch_conditions": "…→ 高潮；…→ 分歧"}
        out = reconcile(prev, {"phase": "发酵"})
        assert out["verdict"] == "held"
        assert out["verdict_label"] == "相位延续"

    def test_off_path(self):
        prev = {"phase": "发酵", "switch_conditions": "…→ 高潮；…→ 分歧"}
        out = reconcile(prev, {"phase": "冰点"})
        assert out["verdict"] == "off_path"
        assert "冰点" in out["reason"]

    def test_unavailable_without_prev(self):
        out = reconcile(None, {"phase": "发酵"})
        assert out["verdict"] == "unavailable"
        assert out["predicted_targets"] == []

    def test_unavailable_when_today_phase_missing(self):
        """今日相位缺失（采集 gap）：无法对账，绝不把「缺失」判成「偏离」。"""
        prev = {"phase": "发酵", "switch_conditions": "…→ 高潮；…→ 分歧"}
        out = reconcile(prev, {"phase": None})
        assert out["verdict"] == "unavailable"
        assert "缺失" in out["reason"]

    def test_prev_without_switch_text_still_reconciles(self):
        """昨日存档缺 switch_conditions（detail 损坏降级）：延续可判，切换即 off_path。"""
        out = reconcile({"phase": "发酵", "switch_conditions": None}, {"phase": "发酵"})
        assert out["verdict"] == "held"
        out2 = reconcile({"phase": "发酵", "switch_conditions": None}, {"phase": "高潮"})
        assert out2["verdict"] == "off_path"
        assert "（空）" in out2["reason"]


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:")
    from app.models.watchlist import Base

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _entry(trade_date: str, phase: str, switch: str) -> dict:
    return {
        "trade_date": trade_date, "phase": phase, "switch_conditions": switch,
        "temperature": 60.0, "confidence": "高", "phase_unreliable": False,
    }


class TestLoadPrev:
    def test_takes_latest_before_today(self, sf):
        for td, ph in (("20260901", "发酵"), ("20260902", "高潮")):
            e = _entry(td, ph, "…→ 分歧")
            with sf() as db:
                db.add(SentimentHistoryRow(
                    trade_date=e["trade_date"], phase=e["phase"],
                    temperature=e["temperature"], confidence=e["confidence"],
                    phase_unreliable=0, source="review",
                    detail=json.dumps(e, ensure_ascii=False),
                ))
                db.commit()
        prev = load_prev_entry(sf, "20260903")
        assert prev["phase"] == "高潮"

    def test_no_row_returns_none(self, sf):
        assert load_prev_entry(sf, "20260903") is None

    def test_corrupt_detail_degrades_to_row_phase(self, sf):
        with sf() as db:
            db.add(SentimentHistoryRow(
                trade_date="20260901", phase="修复", temperature=None,
                confidence=None, phase_unreliable=0, source="review", detail="{broken",
            ))
            db.commit()
        prev = load_prev_entry(sf, "20260902")
        assert prev == {"phase": "修复", "switch_conditions": None}
