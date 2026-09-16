"""台账五类归因（KB-TRADE-13 落地）自证（§6.20 W2）。

钉三件事：
1. `classify_kind` 推导规则：显式 kind 映射 / 临板雷达层 / 题材推导 / 诚实缺失；
2. 登记链写 kind 后，`tracking_review_stats.by_kind` 的分桶与胜率正确；
3. 资金类 kind（flow_surge）不硬归五类——宁缺毋滥。

🔴 **本文件的时钟纪律（2026-09-17 修正，[[KB-ENG-56]] 形态二）**：`tracking_review_stats(days)`
的窗口是 `beijing_now().date() - (days-1)`——**相对运行日**算的；而夹具日期是**写死的**。
两者一旦脱钩，用例就会在某个日期之后**永久变红**（不是偶发），且表现为
「同一提交，首跑绿、跨过午夜的重跑红」——极易被误读成随机失败。
⇒ 凡在本文件里断言 `tracking_review_stats` 的结果，**必须同时把时钟钉死**
（`monkeypatch.setattr("app.picks.watch_ledger.beijing_now", ...)`，范式与
`tests/test_meta_review.py::test_week_start_bj_is_monday_midnight_naive` 同源）。
"""
from __future__ import annotations

import sys
from datetime import datetime

sys.path.insert(0, ".")

from app.core.bjtime import BJ_TZ
from app.core.db import get_engine, get_session_factory
from app.models.watch_ledger import WatchLedger
from app.picks.watch_ledger import classify_kind, record_sighting, settle_day, tracking_review_stats

#: 本文件统一的「当下」与夹具交易日。两者必须**同源**，见模块 docstring 的时钟纪律。
FROZEN_NOW = datetime(2026, 9, 12, 15, 0, tzinfo=BJ_TZ)
FROZEN_DAY = "2026-09-12"


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
    # 钉死时钟（[[KB-ENG-56]] 形态二）：不钉 ⇒ 夹具日期滑出 5 日窗后，`by_kind` 全空、
    # 下面 `buckets["时事/消息"]` 抛 KeyError（**不写行号——行号会随编辑腐化**）。
    # **实测到期时刻 = 2026-09-17 00:00（北京）**
    # ——CI 重跑 `35114959478` 的后端 job 于 15:35:13Z 启动、断言落在 16:21:06Z
    # （= 北京 09-17 00:21:06，恰在翻页后 21 分钟）⇒ 判红；而同分支首跑
    # `35090781899`（北京 09-16 19:31）仍在窗内 ⇒ 判绿。**同一提交、两种结论，唯一变量是墙钟。**
    monkeypatch.setattr("app.picks.watch_ledger.beijing_now", lambda: FROZEN_NOW)
    _reset()
    sf = get_session_factory()
    today = FROZEN_DAY
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


def test_window_cutoff_expiry_is_pinned(monkeypatch):
    """把「夹具日期哪天过期」钉成可执行契约——复盘 2026-09-17 的 CI 红（[[KB-ENG-56]] 形态二）。

    被测口径（`app/picks/watch_ledger.py:215`）：`cutoff = beijing_now().date() - (days-1)`，
    即「近 5 天」含今天在内、共 5 个自然日。夹具日期 `2026-09-12` 因此只在
    **09-12 ~ 09-16** 可见，**09-17 00:00 起永久不可见**：

    · `2026-09-16 23:59` ⇒ cutoff `09-12` ⇒ 命中 ⇒ **绿**（CI 首跑即此侧）
    · `2026-09-17 00:00` ⇒ cutoff `09-13` ⇒ 滑出 ⇒ **红**（CI 重跑即此侧）

    本用例把两侧都断言下来：窗口口径（`days-1` / 自然日 / 北京口径）若被改动，
    **这里先红**，而不是等到某天数据自己过期、再被误读成随机失败。
    """
    _reset()
    sf = get_session_factory()
    record_sighting(trade_date=FROZEN_DAY, symbol="600519", name="A",
                    reason={"kind": "news"}, entry_price=10.0, session_factory=sf)
    settle_day(FROZEN_DAY, {"600519": 10.5}, session_factory=sf)

    monkeypatch.setattr("app.picks.watch_ledger.beijing_now",
                        lambda: datetime(2026, 9, 16, 23, 59, tzinfo=BJ_TZ))
    assert tracking_review_stats(5, session_factory=sf)["total_settled"] == 1, (
        "2026-09-16 23:59：cutoff=09-12，夹具日期仍在 5 日窗内（含边界当天）"
    )

    monkeypatch.setattr("app.picks.watch_ledger.beijing_now",
                        lambda: datetime(2026, 9, 17, 0, 0, tzinfo=BJ_TZ))
    assert tracking_review_stats(5, session_factory=sf)["total_settled"] == 0, (
        "2026-09-17 00:00：cutoff=09-13 ⇒ 09-12 滑出窗口"
        "（这正是 CI 重跑把 09-16 的绿变成 09-17 的红的时刻）"
    )
