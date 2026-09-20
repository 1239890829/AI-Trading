"""盘后方向对照与提醒收益回算回归测试（选股 2.0 批次 C）。

锁五类失效：
1. **四分类优先级**——证伪 > 发酵 > 半发酵 > 无波动（终态口径）；
   确认后证伪按证伪归档，确认事实由 confirmed/阈值过敏保留。
2. **收盘快照口径**——量比恒 unknown 不得让发酵永不可达
   （closing_confirmed：无明确不满足 + 至少一项可判定满足）。
3. **误判分类**——环境突变 > 阈值过敏 > 数据缺失误导 > 逻辑失效，
   顺序错会把环境造成的回撤记成"阈值过敏"，调参方向就错了。
4. **收益回算**——参考价=提醒日收盘价；T 日停牌（无 K 线）留空不冒充；
   周末/节假日跳过取 T+1/T+3 交易日。
5. **调度幂等**——already_reviewed 读简报文件（持久化），不是内存变量
   （review_scheduler 的教训：重启清内存 + 时间已过 = 每次重启都重跑）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.models.watchlist import Base
from app.picks import review_intraday as ri
from app.picks.opportunity_learning import archive_records, build_notification_records
from app.schemas.market import Kline


# ---------------------------------------------------------------- 纯函数：分类


def test_classify_outcome_precedence():
    # 证伪最高优先（终态口径）
    assert ri.classify_outcome(falsified=True, tracker_confirmed=True,
                               closing_confirmed=True, met_count=5) == ri.OUTCOME_FALSIFIED
    # 发酵：tracker 严格口径或收盘快照口径任一成立
    assert ri.classify_outcome(falsified=False, tracker_confirmed=True,
                               closing_confirmed=False, met_count=0) == ri.OUTCOME_FERMENT
    assert ri.classify_outcome(falsified=False, tracker_confirmed=False,
                               closing_confirmed=True, met_count=4) == ri.OUTCOME_FERMENT
    # 半发酵：满足 ≥3 项
    assert ri.classify_outcome(falsified=False, tracker_confirmed=False,
                               closing_confirmed=False, met_count=3) == ri.OUTCOME_HALF
    assert ri.classify_outcome(falsified=False, tracker_confirmed=False,
                               closing_confirmed=False, met_count=2) == ri.OUTCOME_FLAT
    assert ri.classify_outcome(falsified=False, tracker_confirmed=False,
                               closing_confirmed=False, met_count=None) == ri.OUTCOME_FLAT


def test_closing_confirmed_tolerates_unknown_volume():
    """量比恒 unknown：4 项满足 + 1 unknown = 收盘口径发酵（不阻塞）。"""
    conf = {"unmet_count": 0, "met_count": 4, "unknown_count": 1,
            "checks": [{"key": "theme_pct", "met": True}] * 4 + [{"key": "volume_ratio", "met": None}]}
    assert ri.closing_confirmed(conf) is True
    # 有明确不满足 → 不是发酵
    conf_unmet = {"unmet_count": 1, "met_count": 3, "unknown_count": 1,
                  "checks": [{"key": "theme_pct", "met": True}] * 3
                  + [{"key": "theme_height", "met": False}, {"key": "volume_ratio", "met": None}]}
    assert ri.closing_confirmed(conf_unmet) is False
    # 只有环境项满足（全方向共享，不构成方向证据）+ 其余全 unknown → 无证据不冒充发酵
    conf_env_only = {"unmet_count": 0, "met_count": 1, "unknown_count": 4,
                     "checks": [{"key": "environment", "met": True}]
                     + [{"key": k, "met": None} for k in ("theme_pct", "theme_height", "leader_pct", "volume_ratio")]}
    assert ri.closing_confirmed(conf_env_only) is False
    # 全 unknown（连板块都没匹配到）→ 无证据
    conf_all_unknown = {"unmet_count": 0, "met_count": 0, "unknown_count": 5,
                        "checks": [{"key": k, "met": None} for k in
                                   ("theme_pct", "theme_height", "leader_pct", "volume_ratio", "environment")]}
    assert ri.closing_confirmed(conf_all_unknown) is False


def test_classify_failure_priority():
    # 环境触发器最高（即使确认过又证伪，也是环境突变不是阈值过敏）
    assert ri.classify_failure_direction(
        confirmed=True, falsified=True, falsify_keys=["environment"],
        missing_ratio=0.0, actual_pct=-1.0) == ri.FAILURE_ENV
    # 确认后证伪（回撤）→ 阈值过敏
    assert ri.classify_failure_direction(
        confirmed=True, falsified=True, falsify_keys=["drawdown"],
        missing_ratio=0.0, actual_pct=0.5) == ri.FAILURE_OVERSENSITIVE
    # 未确认未证伪 + 缺数据拍占比高 → 数据缺失误导
    assert ri.classify_failure_direction(
        confirmed=False, falsified=False, falsify_keys=[],
        missing_ratio=0.4, actual_pct=1.0) == ri.FAILURE_DATA_GAP
    # 缺数据占比低但收盘连晚盘确认线都没碰到 → 逻辑失效
    assert ri.classify_failure_direction(
        confirmed=False, falsified=False, falsify_keys=[],
        missing_ratio=0.1, actual_pct=0.8) == ri.FAILURE_LOGIC
    # 收盘涨幅过线（≥晚盘阈值）但其他项没跟上 → 半发酵，不算误判
    assert ri.classify_failure_direction(
        confirmed=False, falsified=False, falsify_keys=[],
        missing_ratio=0.0, actual_pct=3.0) is None
    # 缺失线以下不算数据缺失误导
    assert ri.classify_failure_direction(
        confirmed=False, falsified=False, falsify_keys=[],
        missing_ratio=0.29, actual_pct=5.0) is None


def test_next_trade_dates():
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
            date(2026, 9, 4), date(2026, 9, 7)]
    assert ri.next_trade_dates(days, date(2026, 9, 2), 3) == [
        date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7)]
    assert ri.next_trade_dates(days, date(2026, 9, 4), 3) == [date(2026, 9, 7)]  # 不足给余量
    assert ri.next_trade_dates(days, date(2026, 9, 7), 3) == []
    assert ri.next_trade_dates([], date(2026, 9, 1), 3) == []


def test_compute_alert_returns():
    d0 = date(2026, 9, 1)
    t1 = date(2026, 9, 2)
    t3 = date(2026, 9, 4)
    closes = {d0: 10.0, t1: 11.0, date(2026, 9, 3): 10.5, t3: 9.0}
    r = ri.compute_alert_returns(closes, d0=d0, t1=t1, t3=t3)
    assert r["ref_price"] == 10.0
    assert r["t1_return"] == 10.0
    assert r["t3_return"] == -10.0
    assert r["complete"] is True
    # D0 无 K 线（停牌/新股）→ 全留空
    r2 = ri.compute_alert_returns({t1: 11.0}, d0=d0, t1=t1, t3=t3)
    assert r2["ref_price"] is None and r2["t1_return"] is None and r2["complete"] is False
    # T3 未到/停牌 → t1 有值、t3 留空
    r3 = ri.compute_alert_returns(closes, d0=d0, t1=t1, t3=t3)
    r3.pop("t3_return"), r3.pop("complete")
    partial = ri.compute_alert_returns({d0: 10.0, t1: 11.0}, d0=d0, t1=t1, t3=t3)
    assert partial["t1_return"] == 10.0 and partial["t3_return"] is None
    assert partial["complete"] is False


def test_should_run_review_persistent_dedup():
    now = datetime(2026, 9, 2, 15, 36)
    assert ri.should_run_review(now, run_hour=15, run_minute=35,
                                brief_exists=True, already_reviewed=False) is True
    # 当日已 schedule 复盘 → 跳过（重启后依然跳过：读文件不读内存）
    assert ri.should_run_review(now, run_hour=15, run_minute=35,
                                brief_exists=True, already_reviewed=True) is False
    # 时间未到 / 无简报
    assert ri.should_run_review(datetime(2026, 9, 2, 15, 34), run_hour=15, run_minute=35,
                                brief_exists=True, already_reviewed=False) is False
    assert ri.should_run_review(now, run_hour=15, run_minute=35,
                                brief_exists=False, already_reviewed=False) is False
    # 深夜不补跑
    assert ri.should_run_review(datetime(2026, 9, 2, 23, 5), run_hour=15, run_minute=35,
                                brief_exists=True, already_reviewed=False) is False


def test_d0_path_retry_slot_is_bounded_after_initial_review():
    kw = {"run_hour": 15, "run_minute": 35}
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 15, 49), **kw) is None
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 15, 50), **kw) == 1
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 16, 4), **kw) == 1
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 16, 5), **kw) == 2
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 16, 35), **kw) == 4
    assert ri.d0_path_retry_slot(datetime(2026, 9, 2, 16, 36), **kw) is None


# ---------------------------------------------------------------- 单方向复盘


def test_review_direction_with_tracker():
    """真实 confirm_signal 路径：收盘 +3.2%/3 家涨停/环境齐备（量比与龙头 unknown）→ 发酵。"""
    theme = {"pct": 3.2, "limit_up": 3, "max_boards": 4,
             "leader_symbol": "600001", "leader_name": "龙头股"}
    tracker = {"beats": 200, "missing_beats": 10, "confirmed": False, "falsified": False,
               "falsify_triggers": []}
    rv = ri.review_direction({"direction": "粮食"}, tracker, theme, {"phase": "发酵"})
    assert rv["outcome"] == ri.OUTCOME_FERMENT       # 3 项满足 0 项不满足 → 收盘口径发酵
    assert rv["source"] == "tracker"
    assert rv["actual_pct"] == 3.2
    assert rv["actual_leader"] == "600001 龙头股"
    assert rv["missing_ratio"] == 0.05
    assert rv["failure_class"] is None               # 走强了，不算误判


def test_review_direction_tracker_lost_uses_closing_snapshot():
    rv = ri.review_direction({"direction": "粮食"}, None, None, {"phase": None})
    assert rv["source"] == "closing_snapshot"
    assert "收盘快照口径" in rv["note"]
    # 全 unknown（无收盘题材）→ 无波动，且不冒充发酵
    assert rv["outcome"] == ri.OUTCOME_FLAT
    assert rv["closing_unknown"] == 5


def test_review_direction_falsified_by_environment():
    tracker = {"beats": 100, "missing_beats": 5, "confirmed": True, "falsified": True,
               "falsify_triggers": [{"key": "environment", "detail": "退潮"}]}
    rv = ri.review_direction({"direction": "AI应用"}, tracker,
                             {"pct": -1.5, "limit_up": 0, "max_boards": 1}, {"phase": "退潮"})
    assert rv["outcome"] == ri.OUTCOME_FALSIFIED
    assert rv["failure_class"] == ri.FAILURE_ENV
    assert rv["confirmed"] is True  # 确认过的事实保留


# ---------------------------------------------------------------- 胜率统计


def test_stats_from_payloads_win_rate():
    payloads = [
        {"brief_date": "20260902",
         "directions": [
             {"direction": "粮食", "review": {"outcome": "发酵", "failure_class": None}},
             {"direction": "AI应用", "review": {"outcome": "证伪", "failure_class": ri.FAILURE_ENV}},
             {"direction": "银行"},  # 未复盘
         ],
         "alerts": [
             {"kind": "confirm", "direction": "粮食", "symbol": "600001", "name": "A",
              "meta": {"returns": {"t1_return": 5.0, "t3_return": 8.0}}},
             {"kind": "confirm", "direction": "AI应用", "symbol": "300001", "name": "B",
              "meta": {"returns": {"t1_return": -2.0, "t3_return": None}}},
             {"kind": "falsify", "direction": "AI应用", "symbol": "", "name": "", "meta": {}},
         ]},
        {"brief_date": "20260901",
         "directions": [{"direction": "种业", "review": {"outcome": "半发酵",
                                                          "failure_class": ri.FAILURE_LOGIC}}],
         "alerts": []},
    ]
    s = ri._stats_from_payloads(payloads)
    assert s["directions"]["total"] == 3
    assert s["directions"]["outcomes"] == {"发酵": 1, "半发酵": 1, "证伪": 1, "无波动": 0}
    assert s["directions"]["failures"] == {ri.FAILURE_ENV: 1, ri.FAILURE_LOGIC: 1}
    # T+1：2 条已知，1 胜 1 负 → 50%；盈亏比 5/2=2.5
    assert s["alert_t1"]["n"] == 2 and s["alert_t1"]["win_rate"] == 50.0
    assert s["alert_t1"]["avg_win"] == 5.0 and s["alert_t1"]["avg_loss"] == -2.0
    assert s["alert_t1"]["profit_loss_ratio"] == 2.5
    # T+3：仅 1 条已知
    assert s["alert_t3"]["n"] == 1 and s["alert_t3"]["win_rate"] == 100.0
    # falsify 提醒不进收益统计
    assert len(s["alerts"]) == 2
    assert [d["date"] for d in s["daily"]] == ["20260902", "20260901"]
    assert s["daily"][0]["reviewed"] == 2 and s["daily"][0]["falsified"] == 1


def test_stats_empty():
    s = ri._stats_from_payloads([])
    assert s["daily"] == [] and s["directions"]["total"] == 0
    assert s["alert_t1"]["n"] == 0 and s["alert_t1"]["win_rate"] is None


# ---------------------------------------------------------------- 文件流：run_review / 回填


@pytest.fixture
def brief_dir(tmp_path, monkeypatch):
    d = tmp_path / "briefs"
    d.mkdir()
    monkeypatch.setattr(ri, "BRIEF_DIR", d)
    # morning_brief 的 save_brief/load_brief 读自己模块的 BRIEF_DIR，一并替换
    from app.picks import morning_brief as mb

    monkeypatch.setattr(mb, "BRIEF_DIR", d)
    return d


def _write_brief(d, payload: dict):
    (d / f"{payload['brief_date']}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _brief(date_str: str) -> dict:
    return {
        "brief_date": date_str, "context": "morning_brief",
        "env": {"phase": "发酵"}, "missing": [],
        "directions": [
            {"direction": "粮食", "pool": [], "entry_mode": "追涨"},
            {"direction": "AI应用", "pool": [], "entry_mode": "追涨减半"},
        ],
        "alerts": [],
    }


def test_collect_d0_path_outcomes_uses_tencent_only_and_selected_symbols(tmp_path):
    import asyncio

    engine = create_engine(f"sqlite:///{tmp_path / 'path-collector.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    items = [
        {"symbol": "600001", "name": "甲", "confidence": {"tier": "executable"}},
        {"symbol": "600002", "name": "乙", "confidence": {"tier": "observe"}},
    ]
    hit = {"item": items[0], "price": 10.0, "chg": 2.0}
    run_id, rows = build_notification_records(
        items, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 30),
        hits=[hit], skips=[{"symbol": "600002", "reason": "observe"}],
        dispatch_by_symbol={},
    )
    archive_records(run_id, rows, sf)

    class _Eastmoney:
        name = "eastmoney"
        calls = []

        async def get_kline(self, symbol, timeframe, start=None, end=None):
            self.calls.append((symbol, timeframe))
            raise AssertionError("D0 path must never fall back to Eastmoney minute timestamps")

    class _Tencent:
        name = "tencent"
        calls = []

        async def get_kline(self, symbol, timeframe, start=None, end=None):
            self.calls.append((symbol, timeframe))
            return [
                Kline(
                    symbol=symbol, timeframe="1m",
                    ts=datetime(2026, 9, 16, 2, 31, tzinfo=timezone.utc),
                    open=10.0, close=10.1, high=10.3, low=9.9, volume=1000,
                    source="tencent",
                ),
                Kline(
                    symbol=symbol, timeframe="1m",
                    ts=datetime(2026, 9, 16, 7, 0, tzinfo=timezone.utc),
                    open=10.1, close=10.1, high=10.2, low=10.0, volume=1000,
                    source="tencent",
                ),
            ]

    east, tencent = _Eastmoney(), _Tencent()

    class _Composite:
        providers = [east, tencent]
        up_calls = 0
        break_calls = 0

        async def get_limit_up_pool(self, trade_date):
            self.up_calls += 1
            assert trade_date == date(2026, 9, 16)
            return []

        async def get_limit_break_pool(self, trade_date):
            self.break_calls += 1
            assert trade_date == date(2026, 9, 16)
            return [{"symbol": "600001", "first_seal_time": "10:45:00"}]

    composite = _Composite()
    state = NS(hub=NS(provider=composite))
    result = asyncio.run(ri.collect_d0_path_outcomes(state, "2026-09-16", sf))

    assert result["symbols"] == 1
    assert result["labeled"] == 1 and result["skipped_deferred"] == 1
    assert result["minute_source"] == "tencent"
    assert tencent.calls == [("600001", "1m")]
    assert east.calls == []
    assert composite.up_calls == 1 and composite.break_calls == 1
    assert result["limit_universe_complete"] is True
    assert result["ever_limit_members"] == 1
    with sf() as db:
        selected = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.symbol == "600001")
        ).scalar_one()
        rejected = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.symbol == "600002")
        ).scalar_one()
    assert selected.path_state == "labeled" and selected.mfe_pct == 3.0 and selected.mae_pct == -1.0
    assert selected.limit_state == "hit" and selected.time_to_limit_minutes == 15.0
    assert rejected.path_state == "deferred" and rejected.mfe_pct is None


def test_run_review_no_brief(brief_dir, monkeypatch):
    import asyncio

    monkeypatch.setattr(ri, "beijing_now", lambda: datetime(2026, 9, 2, 15, 36))
    state = NS(hub=None)
    result = asyncio.run(ri.run_review(NS(state=state), trigger="manual"))
    assert result["ok"] is False and result["reason"] == "no_brief"


def test_run_review_rejects_premarket(brief_dir, monkeypatch):
    """09:25 前拒绝复盘：拿"今天的空池"当"全天没动静"是自指污染。"""
    import asyncio

    _write_brief(brief_dir, _brief("20260902"))
    monkeypatch.setattr(ri, "beijing_now", lambda: datetime(2026, 9, 2, 8, 50))
    result = asyncio.run(ri.run_review(NS(state=NS(hub=None)), trigger="manual"))
    assert result["ok"] is False and result["reason"] == "market_not_open"


def test_run_review_end_to_end(brief_dir, monkeypatch):
    """真实 confirm_signal 路径：收盘题材 + tracker 内存态 → 四分类回填。"""
    import asyncio

    _write_brief(brief_dir, _brief("20260902"))
    now = datetime(2026, 9, 2, 15, 36)
    monkeypatch.setattr(ri, "beijing_now", lambda: now)

    class _Pool:
        async def get_limit_up_pool(self, d):
            assert str(d) == "2026-09-02"
            return [NS(symbol="600001", name="龙头股", consecutive_boards=3,
                       change_pct=9.98, reason="粮食")]

    async def _days(provider):
        return [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]

    async def _board_pcts(hub):
        return {"粮食概念": 3.2}, 1

    class _Hub:
        provider = NS()  # get_limit_up_pool 直接挂 _Pool 实例

    hub = _Hub()
    hub.provider = _Pool()

    async def _sentiment(h, s):
        return {"phase": "发酵", "calibration": {"percentile": {"promo_1to2": {"percentile": 60.0}}}}

    monkeypatch.setattr(ri.tc, "trading_days", _days)
    monkeypatch.setattr("app.picks.watcher._board_pcts", _board_pcts)
    import app.services.market_context as mc

    monkeypatch.setattr(mc, "compute_market_sentiment", _sentiment)

    watcher = NS(state=lambda: {"trackers": [
        {"direction": "粮食", "beats": 100, "missing_beats": 5, "confirmed": False,
         "falsified": False, "falsify_triggers": []},
        {"direction": "AI应用", "beats": 100, "missing_beats": 90, "confirmed": False,
         "falsified": False, "falsify_triggers": []},
    ]})
    state = NS(hub=hub, snapshot_service=None, picks_watcher=watcher)
    result = asyncio.run(ri.run_review(NS(state=state), trigger="schedule"))
    assert result["ok"] is True
    by_dir = {r["direction"]: r for r in result["directions"]}
    # 粮食：收盘池 1 家涨停 + 板块 +3.2%（≥晚盘 2.5）→ 可判定项全过 → 发酵
    assert by_dir["粮食"]["outcome"] == ri.OUTCOME_FERMENT
    # AI应用：收盘池无数据（全 unknown）→ 无波动；缺数据拍 90% → 数据缺失误导
    assert by_dir["AI应用"]["outcome"] == ri.OUTCOME_FLAT
    assert by_dir["AI应用"]["failure_class"] == ri.FAILURE_DATA_GAP
    # 回填进文件
    saved = json.loads((brief_dir / "20260902.json").read_text(encoding="utf-8"))
    assert saved["review"]["trigger"] == "schedule"
    assert saved["review"]["outcomes"] == {"粮食": "发酵", "AI应用": "无波动"}
    assert saved["directions"][0]["review"]["actual_pct"] == 3.2
    assert saved["directions"][0]["review"]["source"] == "tracker"


def test_run_review_schedule_idempotent(brief_dir, monkeypatch):
    """已 schedule 复盘过的简报，schedule 触发跳过（持久化幂等）。"""
    import asyncio

    b = _brief("20260902")
    b["review"] = {"trigger": "schedule", "outcomes": {"粮食": "发酵"}}
    _write_brief(brief_dir, b)
    monkeypatch.setattr(ri, "beijing_now", lambda: datetime(2026, 9, 2, 15, 36))
    result = asyncio.run(ri.run_review(NS(state=NS(hub=None)), trigger="schedule"))
    assert result.get("skipped") == "already_reviewed"
    # manual 不受幂等拦截（可强制重算）；hub=None 会失败，这里只验证 schedule 路径的跳过


def test_backfill_alert_returns(brief_dir, monkeypatch):
    """T+1/T+3 回填：参考价=提醒日收盘；已 complete 跳过；无 K 线不落盘。"""
    import asyncio

    d0 = "20260901"
    b = _brief(d0)
    b["alerts"] = [
        {"kind": "confirm", "direction": "粮食", "symbol": "600001", "name": "A", "meta": {}},
        {"kind": "confirm", "direction": "AI应用", "symbol": "300001", "name": "B",
         "meta": {"returns": {"ref_price": 20.0, "t1_return": 1.0, "t3_return": 2.0,
                              "t1_date": "2026-09-02", "t3_date": "2026-09-04",
                              "ref_date": "2026-09-01", "complete": True}}},
        {"kind": "confirm", "direction": "银行", "symbol": "601000", "name": "C", "meta": {}},
    ]
    _write_brief(brief_dir, b)
    monkeypatch.setattr(ri, "beijing_now", lambda: datetime(2026, 9, 5, 15, 40))

    class _Prov:
        name = "tencent"

        async def get_kline(self, symbol, timeframe, start=None, end=None):
            if symbol == "601000":
                raise RuntimeError("kline down")
            # 600001：D0=9/1 收盘 10 → T1 9/2 收 11 (+10%) → T3 9/4 收 9 (-10%)
            bars = {  # ts = 交易日 0 点（naive，_daily_closes 只取 .date()）
                date(2026, 9, 1): 10.0, date(2026, 9, 2): 11.0,
                date(2026, 9, 3): 10.5, date(2026, 9, 4): 9.0,
            }
            return [NS(ts=datetime(d.year, d.month, d.day), close=c)
                    for d, c in bars.items()]

    async def _days(provider):
        return [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
                date(2026, 9, 3), date(2026, 9, 4)]

    monkeypatch.setattr(ri.tc, "trading_days", _days)
    state = NS(hub=NS(provider=_Prov()))
    out = asyncio.run(ri.backfill_alert_returns(state))
    assert out["ok"] is True and out["alerts_updated"] == 1  # complete 的跳过、失败的计数
    assert out["alerts_kline_failed"] == 1
    saved = json.loads((brief_dir / f"{d0}.json").read_text(encoding="utf-8"))
    ret = saved["alerts"][0]["meta"]["returns"]
    assert ret["ref_price"] == 10.0
    assert ret["t1_return"] == 10.0 and ret["t3_return"] == -10.0
    assert ret["complete"] is True
    # complete 的提醒未被覆盖
    assert saved["alerts"][1]["meta"]["returns"]["t1_return"] == 1.0
    # 失败的提醒未落盘（下次续算），不冒充已回算
    assert "returns" not in saved["alerts"][2]["meta"]
