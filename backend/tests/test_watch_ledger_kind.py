"""台账五类归因（KB-TRADE-13 落地）自证（§6.20 W2）。

钉三件事：
1. `classify_kind` 推导规则：显式 kind 映射 / 临板雷达层 / 题材推导 / 诚实缺失；
2. 登记链写 kind 后，`tracking_review_stats.by_kind` 的分桶与胜率正确；
3. 资金类 kind（flow_surge）不硬归五类——宁缺毋滥。
"""
from __future__ import annotations

import sys
from datetime import datetime

sys.path.insert(0, ".")

from app.core.db import get_engine, get_session_factory
from app.models.watch_ledger import WatchLedger
from app.picks.watch_ledger import classify_kind, record_sighting, settle_day, tracking_review_stats


def _reset():
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        db.query(WatchLedger).delete()
        db.commit()


def test_classify_kind_rules():
    # 显式 kind → 五类（与 attribution_winrate 的 KIND_LABEL 同源）
    assert classify_kind({"kind": "news"}, None) == "时事/消息"
    assert classify_kind({"kind": "theme"}, None) == "题材共振"
    assert classify_kind({"kind": "fundamental"}, None) == "基本面"
    # 临板雷达层 → 技术面（即使 kind 未映射，如 kind="pre_limit"）
    assert classify_kind({"kind": "pre_limit", "gate": "pre_limit"}, "pre_limit") == "技术面"
    assert classify_kind({"gate": "pre_limit"}, "pre_limit") == "技术面"
    # 资金类不硬归五类：kind 不在映射表且无题材 → 未标注（空串）
    assert classify_kind({"kind": "flow_surge"}, "today_strongest") == ""
    # 候选链历史行（无显式 kind，有 theme）→ 题材共振
    assert classify_kind({"theme": "算力", "gate": "pre_limit pct=7"}, "today_strongest") == "题材共振"
    # 全缺 → 未标注
    assert classify_kind({}, "quiet_starting") == ""


def test_by_kind_aggregation(monkeypatch):
    # 固定窗口和样本的相对位置；真实日期跨过五天后不应让分桶测试失效。
    monkeypatch.setattr("app.picks.watch_ledger.beijing_now", lambda: datetime.fromisoformat("2026-09-12T15:00:00+08:00"))
    _reset()
    sf = get_session_factory()
    today = "2026-09-12"
    # 三条链各一笔：watcher 新闻（时事/消息）、临板雷达（技术面）、候选链历史行（题材共振推导）
    record_sighting(trade_date=today, symbol="600519", name="A",
                    reason={"kind": "news", "text": "利好"}, entry_price=10.0,
                    session_factory=sf)
    record_sighting(trade_date=today, symbol="000001", name="B",
                    reason={"kind": "technical", "gate": "pre_limit"}, entry_price=20.0,
                    session_factory=sf)
    record_sighting(trade_date=today, symbol="300750", name="C",
                    reason={"theme": "算力", "gate": "pre_limit pct=7"}, entry_price=30.0,
                    session_factory=sf)
    record_sighting(trade_date=today, symbol="601318", name="D",
                    reason={"kind": "flow_surge"}, entry_price=40.0,
                    session_factory=sf)
    # 清算：A 涨（success）B 跌超 2%（fail）C 微亏≤2%（flat）D 涨（success）
    settle_day(today, {"600519": 10.5, "000001": 19.0, "300750": 29.9, "601318": 41.0},
               session_factory=sf)

    stats = tracking_review_stats(5, session_factory=sf)
    buckets = {b["key"]: b for b in stats["by_kind"]}
    assert buckets["时事/消息"]["judged"] == 1 and buckets["时事/消息"]["win_rate"] == 1.0
    assert buckets["技术面"]["judged"] == 1 and buckets["技术面"]["win_rate"] == 0.0
    assert buckets["题材共振"]["n"] == 1 and buckets["题材共振"]["judged"] == 0  # flat 不计胜率
    assert buckets["（未标注）"]["n"] == 1  # flow_surge 诚实未标注
    # flat 行仍在桶内计数（n=1）但不进 judged——与 day_stats 口径一致
