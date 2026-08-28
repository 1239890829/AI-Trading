from __future__ import annotations

from app.schemas.market import LimitUpRecord
from app.sentiment.engine import compute_sentiment


def _rec(symbol: str, boards: int) -> LimitUpRecord:
    return LimitUpRecord(symbol=symbol, name="某股", trade_date="2026-08-28", consecutive_boards=boards, source="test")


def _row(symbol: str, pct: float, name: str = "某股") -> dict:
    return {"symbol": symbol, "name": name, "price": 10.0, "change_pct": pct, "amount": 1e8}


def _breadth(limit_up=90, up=3000, down=2400):
    return {"total": 5550, "up": up, "down": down, "flat": 100, "suspended": 4,
            "limit_up": limit_up, "limit_down": 3, "limit_up_ratio": 0.03, "total_amount": 2e11}


def _snapshot_for(pool_yesterday):
    return [_row(r.symbol, 5.0) for r in pool_yesterday]


def test_ice_phase():
    """涨停少 + 高度低 + 昨涨停大跌 → 冰点"""
    pool_y = [_rec(f"60000{i}", 1) for i in range(5)]
    snap = _snapshot_for(pool_y)
    for i, r in enumerate(snap):
        r["change_pct"] = -4.0 - i * 0.1
    b = _breadth(limit_up=12, up=800, down=4500)
    s = compute_sentiment(b, [], pool_y, snap)
    assert s["phase"] == "冰点"
    assert s["confidence"] in {"中", "低"}  # 样本<10 → 低
    assert s["temperature"] < 45
    assert s["reasons"] and s["switch_conditions"]
    assert any("样本不足" in c for c in s["misjudge_caveats"])


def test_climax_phase():
    """高度≥5 + 昨涨停强 + 涨停多 → 高潮"""
    pool_y = [_rec(f"60010{i}", 2) for i in range(20)]
    snap = _snapshot_for(pool_y)
    for i, r in enumerate(snap):
        r["change_pct"] = 5.0 + i * 0.2
    pool_t = [_rec("600099", 6)] + [_rec(f"00020{i}", 2) for i in range(10)] + [_rec(f"00030{i}", 1) for i in range(50)]
    s = compute_sentiment(_breadth(limit_up=95), pool_t, pool_y, snap)
    assert s["phase"] == "高潮"
    assert s["temperature"] > 55
    assert s["confidence"] == "高"
    assert "5板+" in s["ladder"]


def test_retreat_phase():
    """昨涨停大跌但高度仍在 → 退潮（断板亏钱）"""
    pool_y = [_rec(f"60020{i}", 3) for i in range(30)]
    snap = _snapshot_for(pool_y)
    for i, r in enumerate(snap):
        r["change_pct"] = -4.5
    pool_t = [_rec("600098", 4)] + [_rec(f"00040{i}", 1) for i in range(50)]
    s = compute_sentiment(_breadth(limit_up=60), pool_t, pool_y, snap)
    assert s["phase"] == "退潮"
    assert any("退潮" in str(r) or "断板" in str(r) for r in s["reasons"])


def test_indicators_shape():
    s = compute_sentiment(_breadth(), [_rec("600001", 2)], [_rec("600002", 1)], [_row("600002", 3.0)])
    names = {i["name"] for i in s["indicators"]}
    assert {"涨停家数", "连板高度", "昨日涨停今日均值", "炸板率(近似)"} <= names
    assert s["judged_at"] and s["phase"] in {"冰点", "修复", "发酵", "高潮", "分歧", "退潮"}
