"""板块异动检测器单测（2026-09-13 第一期）。

锁四类必然再发生的失效（与 test_watcher 同思路）：
1. 聚合口径错——中位/超额/ge3 算错或把缺失成员当 0（三态纪律）。
2. 时段门失效——开盘 warmup 内/尾盘触发（竞价噪音与尾盘偷袭告警）。
3. 去重/上限失效——同一题材每拍刷提醒（MAX_ALERTS_PER_THEME）。
4. 归因匹配错——direction=0 的行混进来（关联待判 ≠ 利好）、长短名互含失败。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.watchlist import Base
from app.models.event import EventCard, EventDirection
from app.picks import board_surge as bs
from app.schemas.market import LimitUpRecord


# ---------------------------------------------------------------- 构造器


def _row(sym: str, cp: float | None, amt: float = 100.0, name: str = "") -> dict:
    return {"symbol": sym, "name": name, "change_pct": cp, "amount": amt}


def _snapshot() -> list[dict]:
    rows = [_row(f"m{i:03d}", -1.0) for i in range(20)]           # 全市场背景 -1%
    rows += [_row(f"t{i:02d}", 4.0, 100.0, "强") for i in range(6)]  # 题材强成员
    rows += [_row(f"s{i:02d}", -1.0, 100.0, "弱") for i in range(4)]  # 题材弱成员
    return rows


def _index() -> dict[str, list[tuple[str, str]]]:
    idx: dict[str, list[tuple[str, str]]] = {}
    for i in range(6):
        idx[f"t{i:02d}"] = [("T1", "测试题材")]
    for i in range(4):
        idx[f"s{i:02d}"] = [("T1", "测试题材")]
    return idx


# ---------------------------------------------------------------- 聚合（E1 计算口径）


def test_compute_momentum_median_and_rel():
    moments, market_med = bs.compute_theme_momentum(_snapshot(), _index())
    m = moments["T1"]
    # 题材成员 = 4×(-1) + 6×4，偶数样本中位 = (xs[4]+xs[5])/2 = 4.0；全市场 -1%；rel = 5.0
    assert m["med"] == pytest.approx(4.0)
    assert market_med == pytest.approx(-1.0)
    assert m["rel"] == pytest.approx(5.0)
    assert m["ge3"] == pytest.approx(0.6)
    assert m["ge5"] == pytest.approx(0.0)
    assert m["n"] == 10
    assert m["amt"] == pytest.approx(1000.0)


def test_compute_momentum_skips_small_themes_and_missing_cp():
    index = dict(_index())
    index["t00"].append(("T2", "小题材"))  # T2 只有 1 个成员 → 低于 MIN_MEMBERS 不产出
    rows = _snapshot() + [_row("x01", None, 500.0)]  # 缺 cp 的成员
    index["x01"] = [("T1", "测试题材")]
    moments, _ = bs.compute_theme_momentum(rows, index)
    assert "T2" not in moments
    # 缺 cp 成员不计入 cps（n 仍 10），NaN 不污染中位
    assert moments["T1"]["n"] == 10
    # 空/全缺快照 → 空结果不崩
    assert bs.compute_theme_momentum([{"symbol": "a", "change_pct": None, "amount": 1.0}], index)[0] == {}


# ---------------------------------------------------------------- 检测器（时段门/触发/去重）


def _detector() -> bs.BoardSurgeDetector:
    d = bs.BoardSurgeDetector()
    d.theme_names = {"T1": "测试题材"}
    return d


def test_warmup_captures_baseline_but_no_alert():
    d = _detector()
    moments, mkt = bs.compute_theme_momentum(_snapshot(), _index())
    assert d.evaluate(9 * 60 + 31, moments, mkt) == []          # 开盘 1 分钟：只记基准
    assert d.baseline["T1"] == pytest.approx(1000.0)
    # 基准建立后、放量到位 → 触发
    big = bs.compute_theme_momentum(
        [r | {"amount": 1500.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    alerts = d.evaluate(10 * 60, big[0], mkt)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "board_surge" and a["direction"] == "测试题材"
    assert a["key"].startswith("board_surge:T1:")
    assert "相对全市场 +5.0pp" in a["text"]
    assert "非买卖建议" in a["text"]          # 红线 3 兜底段
    assert "未经实证" in a["text"]           # 初始阈值声明


def test_dedup_and_cap_two_per_theme():
    d = _detector()
    base = bs.compute_theme_momentum(_snapshot(), _index())
    d.evaluate(9 * 60 + 31, base[0], base[1])
    big = bs.compute_theme_momentum(
        [r | {"amount": 1500.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    assert len(d.evaluate(10 * 60, big[0], big[1])) == 1
    assert d.evaluate(10 * 60 + 1, big[0], big[1]) == []          # 同拍重复 → 去重
    # 30 分钟内但 rel 新高 → 仍被 REARM_MIN_MINUTES 拦住
    bigger = bs.compute_theme_momentum(
        [r | {"change_pct": (8.0 if r["symbol"].startswith("t") else r["change_pct"]),
              "amount": 2000.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    assert d.evaluate(10 * 60 + 20, bigger[0], bigger[1]) == []
    # 30 分钟后 + rel 新高 → 第二条（再警）
    alerts = d.evaluate(10 * 60 + 35, bigger[0], bigger[1])
    assert len(alerts) == 1
    # 已到 MAX_ALERTS_PER_THEME=2 → 之后永不触发
    assert d.evaluate(13 * 60, bigger[0], bigger[1]) == []
    assert d.alert_count["T1"] == 2


def test_amount_gate_blocks_weak_growth():
    d = _detector()
    flat = bs.compute_theme_momentum(_snapshot(), _index())
    d.evaluate(9 * 60 + 31, flat[0], flat[1])                     # 基准 amt=1000
    big = bs.compute_theme_momentum(
        [r | {"amount": 110.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    # rel/ge3 达标但成交仅 ×1.1 < 1.3 → 不触发（amt_ratio 门）
    assert d.evaluate(10 * 60, big[0], big[1]) == []


def test_amount_unknown_still_fires_on_price_signals():
    """amt_ratio 判不出（成交额全 0，基准永不建立）→ 缺 ≠ 否决，价量两项达标仍触发。"""
    d = _detector()
    rows = [_row(sym, cp, 0.0) for sym, cp in
            ([(f"m{i:03d}", -1.0) for i in range(20)]
             + [(f"t{i:02d}", 4.0) for i in range(6)]
             + [(f"s{i:02d}", -1.0) for i in range(4)])]
    moments, mkt = bs.compute_theme_momentum(rows, _index())
    alerts = d.evaluate(10 * 60, moments, mkt)
    assert len(alerts) == 1
    assert "成交额" not in alerts[0]["text"]


def test_cool_tail_no_trigger_after_1455():
    d = _detector()
    base = bs.compute_theme_momentum(_snapshot(), _index())
    d.evaluate(9 * 60 + 31, base[0], base[1])                     # 先建基准
    big = bs.compute_theme_momentum(
        [r | {"amount": 1500.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    assert d.evaluate(14 * 60 + 56, big[0], big[1]) == []         # 14:56 尾盘静默
    assert d.evaluate(10 * 60, big[0], big[1]) != []              # 对照：盘中正常触发


# ---------------------------------------------------------------- 归因两路


def _sf():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_match_news_events_direction_and_containment():
    sf = _sf()
    now = datetime(2026, 9, 11, 14, 0)
    with sf() as db:
        db.add(EventCard(id=1, fingerprint="f1", title="MLCC龙头村田停产部分消费级产品",
                         source="东财快讯", source_tier=2, published_at=now - timedelta(hours=2)))
        db.add(EventDirection(event_id=1, target_type="theme", target="MLCC", direction=1))
        db.add(EventCard(id=2, fingerprint="f2", title="某事件仅关联方向未判",
                         source="x", source_tier=1, published_at=now - timedelta(hours=1)))
        db.add(EventDirection(event_id=2, target_type="theme", target="MLCC", direction=0))
        db.add(EventCard(id=3, fingerprint="f3", title="无关题材利好",
                         source="y", source_tier=1, published_at=now - timedelta(minutes=30)))
        db.add(EventDirection(event_id=3, target_type="theme", target="粮食安全", direction=1))
        db.add(EventCard(id=4, fingerprint="f4", title="MLCC订单待更正",
                         source="东财快讯", source_tier=3,
                         published_at=now - timedelta(minutes=15),
                         revision_pending_at=now - timedelta(minutes=5)))
        db.add(EventDirection(event_id=4, target_type="theme", target="MLCC", direction=1))
        db.commit()
    hits = bs.match_news_events("PCB概念", since=now - timedelta(hours=6), session_factory=sf)
    # 「MLCC」与「PCB概念」不互含 → 本查询用 MLCC 名验证匹配；direction=0 与无关题材被排除
    hits_mlcc = bs.match_news_events("MLCC概念", since=now - timedelta(hours=6), session_factory=sf)
    assert len(hits_mlcc) == 1
    assert "村田" in hits_mlcc[0]["title"] and hits_mlcc[0]["tier"] == 2
    assert hits == []
    # 窗口外（6h 前发布）→ 不命中
    old_sf = _sf()
    with old_sf() as db:
        db.add(EventCard(id=9, fingerprint="f9", title="很早的MLCC消息",
                         source="s", source_tier=1, published_at=now - timedelta(hours=9)))
        db.add(EventDirection(event_id=9, target_type="theme", target="MLCC", direction=1))
        db.commit()
    assert bs.match_news_events("MLCC概念", since=now - timedelta(hours=6), session_factory=old_sf) == []


def test_seal_sequence_orders_by_first_seal():
    pool = [
        {"symbol": "s3", "name": "晚封", "first_seal_time": "13:37", "consecutive_boards": 1},
        {"symbol": "s1", "name": "早封", "first_seal_time": "09:33", "consecutive_boards": 2},
        {"symbol": "other", "name": "外题材", "first_seal_time": "09:00", "consecutive_boards": 5},
        {"symbol": "s2", "name": "中封", "first_seal_time": "10:20", "consecutive_boards": 1},
    ]
    out = bs.seal_sequence(pool, {"s1", "s2", "s3"})
    assert out == ["早封 09:33（2板）", "中封 10:20（1板）", "晚封 13:37（1板）"]
    assert bs.seal_sequence(pool, set()) == []


def test_seal_sequence_accepts_provider_limit_up_records():
    pool = [
        LimitUpRecord(
            symbol="s2", name="中封", trade_date="2026-09-22", source="test",
            first_seal_time="10:20", consecutive_boards=1,
        ),
        LimitUpRecord(
            symbol="s1", name="早封", trade_date="2026-09-22", source="test",
            first_seal_time="09:33", consecutive_boards=2,
        ),
    ]

    assert bs.seal_sequence(pool, {"s1", "s2"}) == [
        "早封 09:33（2板）", "中封 10:20（1板）",
    ]


def test_attach_attribution_appends_with_basis():
    d = _detector()
    base = bs.compute_theme_momentum(_snapshot(), _index())
    d.evaluate(9 * 60 + 31, base[0], base[1])                     # 先建基准
    big = bs.compute_theme_momentum(
        [r | {"amount": 1500.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    alerts = d.evaluate(10 * 60, big[0], big[1])
    d.attach_attribution(alerts[0], {
        "news": [{"time": "09-11 08:45", "title": "村田停产", "source": "东财快讯"}],
        "seals": ["双星新材 09:33（1板）"],
    })
    t = alerts[0]["text"]
    assert "可能诱因（消息面）：09-11 08:45「村田停产" in t.replace("「", "「") or "村田停产" in t
    assert "封板时序：双星新材 09:33（1板）" in t
    empty = {"key": "k", "kind": "board_surge", "direction": "x", "text": "基线", "meta": {}}
    d.attach_attribution(empty, {"news": [], "seals": []})
    assert "归因：时间窗内无匹配" in empty["text"]


# ---------------------------------------------------------------- 落库与 API 出口


def test_persist_beat_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "DATA_DIR", Path(tmp_path) / "theme_momentum")
    import asyncio

    asyncio.run(bs.persist_beat("20260911", "14:12", -2.2, {"T1": {"med": 1.5, "rel": 2.5}}))
    asyncio.run(bs.persist_beat("20260911", "14:13", -2.0, {"T1": {"med": 1.8, "rel": 2.8}}))
    lines = (Path(tmp_path) / "theme_momentum" / "20260911.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = __import__("json").loads(lines[0])
    assert first["ts"] == "14:12" and first["themes"]["T1"]["rel"] == 2.5


def test_todays_state_shape():
    d = _detector()
    base = bs.compute_theme_momentum(_snapshot(), _index())
    d.evaluate(9 * 60 + 31, base[0], base[1])                     # 先建基准
    big = bs.compute_theme_momentum(
        [r | {"amount": 1500.0 if r["symbol"].startswith(("t", "s")) else 100.0} for r in _snapshot()],
        _index(),
    )
    d.evaluate(10 * 60, big[0], big[1])
    state = bs.todays_state(NS(board_surge_detector=d))
    assert state["params"]["calibrated"] is False
    assert state["top_themes"][0]["code"] == "T1"
    assert len(state["alerts"]) == 1


def test_ensure_board_surge_rule_get_or_create():
    sf = _sf()
    rule = bs.ensure_board_surge_rule(sf)
    assert rule.name == "__board_surge__" and rule.condition_type == "board_surge"
    again = bs.ensure_board_surge_rule(sf)
    assert again.id == rule.id
