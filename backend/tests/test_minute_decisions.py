"""做 T 决策链测试：记录去重 / 三分类结算 / leave-one-out 归因 / 执行链自动关联。"""

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.core.db import get_engine, get_session_factory
from app.market.minute_decisions import (
    list_decisions,
    record_signals,
    settle_decision,
    settle_due,
)
from app.models.paper import PaperOrder
from app.models.watchlist import Base
from app.review.models import MinuteDecisionRow

Base.metadata.create_all(get_engine())

T0 = "2026-08-28T01:30:00+00:00"  # 北京 09:30


def _pts(prices_after_trigger, trigger_price=10.0):
    """触发时刻 + 窗口内分钟点（每分钟 0.02 元步进由调用方给 prices）。"""
    t0 = datetime.fromisoformat(T0)
    pts = [{"ts": T0, "price": trigger_price, "volume": 1000.0,
            "cum_amount": 10000.0, "cum_volume": 1000, "avg": 10.0, "source": "t"}]
    for i, p in enumerate(prices_after_trigger):
        ts = (t0 + timedelta(minutes=i + 1)).isoformat()
        pts.append({"ts": ts, "price": p, "volume": 1000.0,
                    "cum_amount": 10000.0 + (i + 1) * p * 1000,
                    "cum_volume": 1000 + (i + 1) * 1000, "avg": 10.0, "source": "t"})
    return pts


def _sig(ts=T0, price=10.0, score=-0.61, bias="低吸偏向"):
    return {
        "ts": ts, "signal_price": price, "bias": bias, "score": score,
        "confidence": "medium",
        "triggered": [
            {"key": "avg_dev", "name": "均价线偏离", "weight": 0.30, "direction": -1,
             "trigger_value": -2.2, "threshold": -1.5, "evidence": "偏离 -2.2%"},
            {"key": "vol_div", "name": "量价背离", "weight": 0.25, "direction": -1,
             "trigger_value": 0.2, "threshold": 0.4, "evidence": "底背离"},
        ],
        "invalidate_condition": "跌破均价 3%",
    }


def test_record_dedup_by_symbol_and_ts():
    sf = get_session_factory()
    assert record_signals(sf, "000001", [_sig()]) == 1
    assert record_signals(sf, "000001", [_sig()]) == 0  # 同 ts 重复 → 去重
    assert record_signals(sf, "600519", [_sig()]) == 1  # 不同 symbol 不去重


def _mk_row(ts=T0, price=10.0, score=-0.61, bias="低吸偏向", symbol="000001"):
    sf = get_session_factory()
    record_signals(sf, symbol, [_sig(ts=ts, price=price, score=score, bias=bias)])
    db = sf()
    row = db.query(MinuteDecisionRow).order_by(MinuteDecisionRow.id.desc()).first()
    db.close()
    return row


def test_settle_low_buy_correct_when_price_rises():
    row = _mk_row(symbol="600519")
    prices = [10.005] * 9 + [10.05] + [10.06] * 20  # 窗口内有 ≥0.08% 的有利价差
    assert settle_decision(row, _pts(prices), fills=[]) is True
    assert row.outcome == "correct"
    assert row.optimal_spread_pct >= 0.08
    assert row.best_price == max(prices)
    assert row.executed == 0  # 无成交 → 执行链未关联
    assert "无需归因" in json_load(row.error_attribution)["counter_evidence"]


def json_load(s):
    import json
    return json.loads(s)


def test_settle_low_buy_wrong_with_leave_one_out_attribution():
    """价格一路下杀（反向 ≥8bp 无有利价差）→ wrong；
    双指标里剔除 avg_dev 后 score 跌出信号区 → avg_dev 为归因主因。"""
    row = _mk_row(symbol="300750")
    prices = [9.95] * 30  # -0.5% 一路走低
    assert settle_decision(row, _pts(prices), fills=[]) is True
    assert row.outcome == "wrong"
    att = json_load(row.error_attribution)
    assert att["pivotal"] is True
    assert att["primary_cause"] == "avg_dev"
    # 剔除 avg_dev(-0.30) 后 score = -0.61*0.9+0.30)/0.9 = -0.278，跌出信号区
    assert abs(att["score_without"] + 0.278) < 0.01
    assert row.optimal_spread_pct < 0.08


def test_settle_invalid_when_window_flat():
    """窗口内价差不足阈值 → invalid（消耗注意力但没钱）。"""
    row = _mk_row(symbol="601318")
    prices = [10.001] * 30  # ±1bp
    assert settle_decision(row, _pts(prices), fills=[]) is True
    assert row.outcome == "invalid"


def test_settle_expired_when_window_truncated():
    """触发点太靠后、窗口数据不足 10 根 → expired 不判定。"""
    row = _mk_row(symbol="603269")
    assert settle_decision(row, _pts([10.0] * 3), fills=[]) is False  # 数据未到期
    # 补足"数据末端已过窗口"的场景：末点已过触发+30min 但窗口内仅 3 根
    t0 = datetime.fromisoformat(T0)
    far = (t0 + timedelta(hours=2)).isoformat()
    pts = _pts([10.0] * 3) + [{"ts": far, "price": 10.0, "volume": 0, "cum_amount": 1,
                               "cum_volume": 1, "avg": 10.0, "source": "t"}]
    assert settle_decision(row, pts, fills=[]) is True
    assert row.outcome == "expired"


def test_executed_autolink_with_paper_fill():
    """窗口内同 symbol、方向匹配的首笔 paper 成交自动关联执行链。"""
    sf = get_session_factory()
    db = sf()
    db.add(PaperOrder(symbol="002855", side="buy", price=10.02, quantity=100,
                      status="filled", filled_price=10.02,
                      created_at=datetime.fromisoformat(T0) + timedelta(minutes=5)))
    db.commit()
    db.close()
    row = _mk_row(symbol="002855")
    prices = [10.005] * 9 + [10.05] + [10.06] * 20
    assert settle_decision(row, _pts(prices), fills=list(sf().query(PaperOrder).all())) is True
    assert row.executed == 1
    assert row.executed_price == 10.02
    assert row.realized_spread_pct is not None and row.realized_spread_pct > 0


def test_settle_due_only_touches_due_records():
    """未到 30 分钟窗口的记录不被结算。"""
    future_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fresh = _mk_row(ts=future_ts, price=10.0, symbol="688111")
    due = _mk_row(symbol="688222")  # T0 是 2026-08-28，早已超窗
    sf = get_session_factory()

    def fetch(sym):
        return _pts([10.05] * 30)

    settle_due(sf, fetch)
    db = sf()
    fresh_row = db.get(MinuteDecisionRow, fresh.id)
    due_row = db.get(MinuteDecisionRow, due.id)
    assert fresh_row.outcome is None  # 未到期不动
    assert due_row.outcome is not None  # 到期的已结算
    db.close()


def test_list_decisions_payload():
    row = _mk_row(symbol="688333")
    settle_decision(row, _pts([10.05] * 30), fills=[])
    rows = list_decisions(get_session_factory(), symbol="000001")
    assert rows and rows[0]["decision_id"].startswith("MD-")
    assert rows[0]["triggered"][0]["key"] in {"avg_dev", "vol_div"}
