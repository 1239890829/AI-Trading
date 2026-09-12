"""盘中方向跟踪状态机回归测试（选股 2.0 批次 B）。

锁四类必然再发生的失效：
1. **去重失效**——确认信号每拍都为真，若无 (方向,个股) 去重会每分钟刷一条提醒。
2. **证伪不彻底**——证伪后仍继续确认/提醒，等于给用户两面信号。
3. **缺数据误证伪**——某方向某拍没数据（题材没上榜/板块匹配失败）就当"转负"
   计数或证伪，违反三态纪律（缺 ≠ 证伪）。
4. **提醒刷屏**——龙头轮动时每方向无限发确认提醒，必须有每日上限。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from app.picks import morning_brief, watcher
from app.picks.watcher import (
    MAX_ALERTS_PER_DIRECTION,
    DirectionTracker,
    IntradayWatcher,
    match_board_pct,
)

ENV_OK = {"phase": "发酵", "promo_percentile": 60.0}


def _theme(**kw) -> dict:
    base = {
        "pct": None, "limit_up": None, "max_boards": None,
        "leader_symbol": None, "leader_name": None, "leader_pct": None,
        "leader_broke_board": None, "limit_down": None, "volume_ratio": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- 确认与去重


def test_confirm_full_alerts_once():
    """五项全满足 → 确认 + 一条提醒；下一拍同样满足 → 去重不重发。"""
    tr = DirectionTracker(direction="粮食", logic="测试逻辑")
    theme = _theme(pct=2.0, limit_up=3, max_boards=3, leader_pct=7.0,
                   volume_ratio=1.8, leader_symbol="600000", leader_name="龙头股")
    alerts = tr.step(theme, ENV_OK, 590)  # 09:50，早盘线 1.5%
    assert tr.confirmed is True
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "confirm" and a["symbol"] == "600000"
    assert a["key"] == "粮食:600000:confirm"
    assert "【盘中机会】" in a["text"]
    assert "非投资建议" in a["text"]  # 八段式的兜底段
    # 第二拍完全相同 → 去重
    assert tr.step(dict(theme), ENV_OK, 590) == []


def test_unknown_volume_degrades_confirm_to_075_alert():
    """量比缺失（unknown）+ 其余四项全过 → 0.75 档降级确认，提醒真实发出。

    2026-09-02 选 b 放宽（§5.1#4）：旧实现 confirmed 永假、提醒整条死掉。
    缺失项在八段式第 7/8 段如实标注，不冒充满强度。
    """
    tr = DirectionTracker(direction="粮食")
    theme = _theme(pct=2.0, limit_up=3, max_boards=3, leader_pct=7.0,
                   leader_symbol="600000", leader_name="A")
    alerts = tr.step(theme, ENV_OK, 590)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "confirm"
    assert tr.confirmed is True
    assert tr.last_confirm["confirmed"] is True
    assert tr.last_confirm["unknown_count"] == 1
    assert tr.last_confirm["strength"] == 0.75
    # 仓位按 0.75 系数：10% × 0.75 = 7.5%，且文本标注缺失项
    assert "7.5" in alerts[0]["text"]
    assert "数据缺失项" in alerts[0]["text"] and "量能" in alerts[0]["text"]


def test_early_late_threshold_switch():
    """10:00 前 1.5%、后 2.5% 的分段阈值在 tracker 里同样生效。"""
    tr = DirectionTracker(direction="X")
    tr.step(_theme(pct=2.0), ENV_OK, 590)   # 09:50
    checks = {c["key"]: c for c in tr.last_confirm["checks"]}
    assert checks["theme_pct"]["met"] is True
    tr2 = DirectionTracker(direction="X")
    tr2.step(_theme(pct=2.0), ENV_OK, 610)  # 10:10
    checks2 = {c["key"]: c for c in tr2.last_confirm["checks"]}
    assert checks2["theme_pct"]["met"] is False


def test_leader_fallback_to_brief_pool():
    """盘中龙头缺失 → 回退盘前标的池第一只未提醒的。"""
    tr = DirectionTracker(direction="X", pool=[
        {"symbol": "600100", "name": "池内股", "role": "龙头"},
    ])
    alerts = tr.step(_theme(pct=2.0, limit_up=3, max_boards=3, leader_pct=6.0,
                            volume_ratio=2.0), ENV_OK, 590)
    assert len(alerts) == 1 and alerts[0]["symbol"] == "600100"
    assert alerts[0]["name"] == "池内股"


# ---------------------------------------------------------------- 证伪


def test_drawdown_falsify_and_stop():
    """峰值 3.0 → 0.5（回撤 2.5 ≥ 2.0）→ 证伪；之后不再确认也不提醒。"""
    tr = DirectionTracker(direction="X")
    assert tr.step(_theme(pct=3.0), ENV_OK, 600) == []  # 高度/量比缺 → 不确认，但峰值已记
    assert tr.peak_pct == 3.0
    alerts = tr.step(_theme(pct=0.5), ENV_OK, 600)
    assert tr.falsified is True
    assert len(alerts) == 1 and alerts[0]["kind"] == "falsify"
    assert any(t["key"] == "drawdown" for t in alerts[0]["meta"]["triggers"])
    # 证伪后一切静默
    assert tr.step(_theme(pct=5.0, limit_up=5, max_boards=5, leader_pct=9.0,
                          volume_ratio=3.0, leader_symbol="600000"), ENV_OK, 600) == []


def test_negative_streak_needs_15_beats():
    """转负计数：14 拍不证伪，第 15 拍触发（60s/拍 ≈ 15 分钟）。"""
    tr = DirectionTracker(direction="X")
    tr.step(_theme(pct=1.0), ENV_OK, 600)  # 峰值基准
    for _ in range(14):
        alerts = tr.step(_theme(pct=-0.2), ENV_OK, 600)
        assert alerts == []
        assert tr.falsified is False
    alerts = tr.step(_theme(pct=-0.2), ENV_OK, 600)  # 第 15 拍
    assert tr.falsified is True
    assert any(t["key"] == "negative_streak" for t in alerts[0]["meta"]["triggers"])


def test_env_ebb_falsifies_immediately():
    """相位退潮 → 环境触发器首拍即证伪（全方向降级的盘中形态）。"""
    tr = DirectionTracker(direction="X")
    alerts = tr.step(_theme(pct=3.0, limit_up=4, max_boards=4, leader_pct=7.0,
                            volume_ratio=2.0, leader_symbol="600001"),
                     {"phase": "退潮", "promo_percentile": 10.0}, 600)
    assert tr.falsified is True
    assert any(t["key"] == "environment" for t in alerts[0]["meta"]["triggers"])


def test_missing_theme_does_not_falsify():
    """整拍缺数据的方向：missing_beats 计数、峰值/转负计数不动、绝不证伪。"""
    tr = DirectionTracker(direction="X")
    assert tr.step(None, ENV_OK, 600) == []
    assert tr.beats == 1 and tr.missing_beats == 1
    assert tr.peak_pct is None and tr.below_zero_beats == 0
    assert tr.falsified is False
    # 缺数据之前有峰值 → 缺拍不清峰值
    tr2 = DirectionTracker(direction="X")
    tr2.step(_theme(pct=3.0), ENV_OK, 600)
    tr2.step(None, ENV_OK, 600)
    assert tr2.peak_pct == 3.0


# ---------------------------------------------------------------- 刷屏上限与路由


def test_max_alerts_per_direction_cap():
    """龙头轮动：第 4 只不同龙头不再提醒（每方向每日上限 3）。"""
    tr = DirectionTracker(direction="X")
    for i, sym in enumerate(("600001", "600002", "600003", "600004")):
        theme = _theme(pct=3.0, limit_up=3, max_boards=3, leader_pct=6.0,
                       volume_ratio=2.0, leader_symbol=sym, leader_name=f"N{sym}")
        alerts = tr.step(theme, ENV_OK, 600)
        if i < MAX_ALERTS_PER_DIRECTION:
            assert len(alerts) == 1, f"beat {i} 应发一条"
        else:
            assert alerts == [], "超过上限后不得再发"
    assert tr.alerted == {"600001", "600002", "600003"}


def test_watcher_routes_themes_by_direction():
    """beat 的 themes 按方向名路由；无数据的方向记 missing 而不是崩/证伪。"""
    w = IntradayWatcher([{"direction": "A", "logic": "la"}, {"direction": "B", "logic": "lb"}])
    beat = {
        "now_minutes": 590,
        "env": ENV_OK,
        "themes": {"A": _theme(pct=2.0, limit_up=3, max_boards=3, leader_pct=6.0,
                               volume_ratio=2.0, leader_symbol="600000", leader_name="A龙头")},
    }
    alerts = w.step(beat)
    assert len(alerts) == 1 and alerts[0]["direction"] == "A"
    st = w.state()
    by_dir = {t["direction"]: t for t in st["trackers"]}
    assert by_dir["A"]["confirmed"] is True
    assert by_dir["B"]["beats"] == 1 and by_dir["B"]["missing_beats"] == 1
    assert by_dir["B"]["falsified"] is False


# ---------------------------------------------------------------- 板块匹配


def test_match_board_pct_exact_contains_none():
    board = {"粮食": 1.0, "种业概念": 3.0, "转基因种业": 4.0, "地产": 2.0}
    assert match_board_pct("粮食", board) == 1.0        # 精确优先
    assert match_board_pct("种业", board) == 3.0        # 包含关系取板块名最短
    assert match_board_pct("白酒", board) is None       # 匹配不到 = unknown，不冒充
    assert match_board_pct("粮食", {}) is None


# ---------------------------------------------------------------- 一拍取数的纯部件


def test_beat_themes_from_pool_aggregation():
    """涨停池 → 每题材节拍：家数/最高板/龙头展平；pct 与 limit_down 显式 unknown。"""
    from app.picks.watcher import _beat_themes_from_pool

    pool = [
        NS(symbol="600001", name="A", consecutive_boards=3, change_pct=10.02, reason="粮食+种业"),
        NS(symbol="600002", name="B", consecutive_boards=1, change_pct=9.98, reason="粮食"),
        NS(symbol="600003", name="C", consecutive_boards=4, change_pct=10.0, reason="种业"),
    ]
    themes = _beat_themes_from_pool(pool)
    assert themes["粮食"]["limit_up"] == 2 and themes["粮食"]["max_boards"] == 3
    assert themes["粮食"]["leader_symbol"] == "600001"
    assert themes["粮食"]["leader_pct"] == 10.02
    assert themes["种业"]["leader_symbol"] == "600003"   # 4 板 > 3 板，不按出现顺序
    # unknown 字段显式存在（confirm_signal/falsify_signal 按 None 处理）
    assert themes["粮食"]["pct"] is None and themes["粮食"]["limit_down"] is None
    assert themes["粮食"]["volume_ratio"] is None
    # members 按板数降序（量比取最强成员用）
    assert themes["粮食"]["members"] == ["600001", "600002"]


# ---------------------------------------------------------------- 量比接线（§5.1#4 降级口径，2026-09-02）


def test_compute_volume_ratio_basic_and_clamps():
    from app.picks.intraday_rules import compute_volume_ratio as vr

    # 11:30（720 分钟）：已开市 120 分钟，当日量 = 昨日量 → 量比 = 1 / (120/240) = 2.0
    assert vr(1000.0, 1000.0, 720) == 2.0
    # 收盘（900 分钟）：elapsed=1.0 → 直接比值
    assert vr(1500.0, 1000.0, 900) == 1.5
    # 开盘首分钟 clamp：1 分钟量 / (昨日量/240)
    assert vr(10.0, 2400.0, 571) == 1.0
    # 缺任一输入 / 未开市无量 / 昨日量非正 → unknown
    assert vr(None, 1000.0, 600) is None
    assert vr(100.0, None, 600) is None
    assert vr(0.0, 1000.0, 600) is None
    assert vr(100.0, 0.0, 600) is None
    assert vr(100.0, 1000.0, None) is None


def test_attach_volume_ratios_max_of_members_and_daily_cache(monkeypatch):
    """量比 = 题材成员最大值；昨日量按日缓存（None 也缓存，当日不重试）。

    2026-09-04 起当日量优先取 snapshot_service 全市场快照（题材成员任意覆盖）。
    """
    import asyncio

    import app.picks.watcher as w

    def _kline(sym):
        # 昨日量 1000 股（最后一根昨日 bar + 一根今日 bar 应被过滤）
        return [
            NS(volume=1000.0, ts=__import__("datetime").datetime(2026, 9, 1)),
            NS(volume=9999.0, ts=__import__("datetime").datetime(2026, 9, 2)),
        ]

    started = {"n": 0}

    def _prev(s):
        started["n"] += 1
        return _kline(s)[-2].volume  # 昨日 1000

    async def _prev_async(hub_, s):
        return _prev(s)

    monkeypatch.setattr(w, "_prev_day_volume", _prev_async)

    snap_service = NS(snapshot=[
        {"symbol": "600001", "volume": 500.0},
        {"symbol": "600002", "volume": 400.0},
        {"symbol": "600003", "volume": 500.0},
    ])
    # hub.get_quotes 是同步内存读（quote_hub 真实形态）；快照可用时不应被调用
    hub = NS(provider=NS(), get_quotes=lambda syms: (_ for _ in ()).throw(AssertionError("should not be called")))

    themes = {
        "粮食": {"members": ["600001", "600002"]},
        "种业": {"members": ["600003"]},
    }
    cache = {"date": "", "vols": {}}
    # 10:00（600 分钟）已开市 30 分钟：量比 = 500/1000/(30/240) = 4.0
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert themes["粮食"]["volume_ratio"] == 4.0  # max(4.0, 400/1000/(30/240)=3.2)
    assert themes["种业"]["volume_ratio"] == 4.0
    assert started["n"] == 3 and cache["date"] == "20260902"
    # 第二拍：昨日量走缓存不重拉，当日量重取
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert started["n"] == 3


def test_attach_volume_ratios_snapshot_missing_falls_back_to_hub(monkeypatch):
    """全市场快照缺失 → 回退 hub.get_quotes（同步内存读，watchlist 覆盖）。"""
    import asyncio

    import app.picks.watcher as w

    async def prev_ok(hub_, s):
        return 1000.0

    monkeypatch.setattr(w, "_prev_day_volume", prev_ok)

    snap_service = NS(snapshot=[])
    hub_get_quotes_calls: list[list[str]] = []

    def sync_get_quotes(syms):
        hub_get_quotes_calls.append(list(syms))
        return [NS(symbol="600001", volume=500.0)]

    hub = NS(provider=NS(), get_quotes=sync_get_quotes)
    themes = {"粮食": {"members": ["600001"]}}
    cache = {"date": "20260902", "vols": {"600001": 1000.0}}
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert themes["粮食"]["volume_ratio"] == 4.0
    assert hub_get_quotes_calls == [["600001"]]


def test_attach_volume_ratios_prev_none_stays_unknown(monkeypatch):
    """昨日量拉不到（缓存 None）→ 量比 unknown，绝不臆造；换日后重试。"""
    import asyncio

    import app.picks.watcher as w

    calls = {"n": 0}

    async def no_prev(hub_, s):
        calls["n"] += 1
        return None

    monkeypatch.setattr(w, "_prev_day_volume", no_prev)

    snap_service = NS(snapshot=[{"symbol": "600001", "volume": 500.0}])
    hub = NS(provider=NS(), get_quotes=lambda syms: [])
    themes = {"粮食": {"members": ["600001"]}}
    cache = {"date": "", "vols": {}}
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert themes["粮食"]["volume_ratio"] is None
    assert calls["n"] == 1
    # 同日不重试
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert calls["n"] == 1
    # 换日重建缓存 → 重试
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260903", 600))
    assert calls["n"] == 2


def test_attach_volume_ratios_no_snapshot_no_hub_hit_stays_unknown(monkeypatch):
    """快照与 hub 缓存都查无该股 → 量比 unknown（不阻断其他判定项）。"""
    import asyncio

    import app.picks.watcher as w

    async def prev_ok(hub_, s):
        return 1000.0

    monkeypatch.setattr(w, "_prev_day_volume", prev_ok)

    snap_service = NS(snapshot=[{"symbol": "000001", "volume": 500.0}])  # 别的股票
    hub = NS(provider=NS(), get_quotes=lambda syms: [])  # hub 缓存也没有
    themes = {"粮食": {"members": ["600001"]}}
    cache = {"date": "20260902", "vols": {"600001": 1000.0}}
    asyncio.run(w._attach_volume_ratios(hub, snap_service, themes, cache, "20260902", 600))
    assert themes["粮食"]["volume_ratio"] is None


def test_refresh_env_cache_and_fallback(monkeypatch):
    """环境缓存：首拍取值、缓存期内不重取、刷新失败沿用旧值（不猜新值）。"""
    import asyncio

    import app.picks.watcher as w

    calls = {"n": 0}

    async def fake_sent(hub, snap):
        calls["n"] += 1
        return {"phase": "发酵", "calibration": {"percentile": {"promo_1to2": {"percentile": 49.2}}}}

    monkeypatch.setattr("app.services.market_context.compute_market_sentiment", fake_sent)
    state = NS(hub=NS(), snapshot_service=NS())
    cache = {"at": 0.0, "env": None}

    env1 = asyncio.run(w._refresh_env(state, cache, refresh_after=600.0))
    assert env1 == {"phase": "发酵", "promo_percentile": 49.2}
    assert calls["n"] == 1
    # 缓存期内：不重取
    env2 = asyncio.run(w._refresh_env(state, cache, refresh_after=600.0))
    assert calls["n"] == 1 and env2 == env1

    # 刷新失败（抛异常）→ 沿用旧值
    async def broken(hub, snap):
        raise RuntimeError("down")

    monkeypatch.setattr("app.services.market_context.compute_market_sentiment", broken)
    env3 = asyncio.run(w._refresh_env(state, cache, refresh_after=0.0))
    assert env3 == {"phase": "发酵", "promo_percentile": 49.2}
    assert calls["n"] == 1


# ---------------------------------------------------------------- 系统规则 channels

def _rule_factory(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'rule.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_ensure_system_rule_new_row_includes_configured_channels(tmp_path, monkeypatch):
    """新建规则 channels 来自 settings.picks_watcher_channels（默认含 feishu）。"""
    import json

    from app.core.config import settings
    from app.picks.watcher import WATCHER_RULE_NAME, ensure_system_rule

    monkeypatch.setattr(settings, "picks_watcher_channels", "in_app,log,feishu")
    factory = _rule_factory(tmp_path)
    row = ensure_system_rule(factory)
    assert json.loads(row.channels) == ["in_app", "log", "feishu"]
    assert row.name == WATCHER_RULE_NAME


def test_ensure_system_rule_upgrades_v1_default_row(tmp_path, monkeypatch):
    """历史行 channels 跟随配置默认（2026-09-08 用户指令：中间态去 feishu，无需手工迁移）。"""
    import json

    from app.core.config import settings
    from app.models.alert import AlertRule
    from app.picks.watcher import ensure_system_rule

    monkeypatch.setattr(settings, "picks_watcher_channels", "in_app,log,feishu")
    factory = _rule_factory(tmp_path)

    # 旧默认行 → 升级
    with factory() as db:
        db.add(AlertRule(name="__picks_watcher__", enabled=1, condition_type="picks_intraday",
                         scope="all", threshold=0.0, channels='["in_app", "log"]'))
        db.commit()
    row = ensure_system_rule(factory)
    assert json.loads(row.channels) == ["in_app", "log", "feishu"]

    # 历史遗留的其他值 → 同步为配置默认（系统规则由系统管理，非用户定制对象；
    # 2026-09-08 用户明确指令后 DB 固化的 feishu 行靠此收敛）
    with factory() as db:
        r = db.query(AlertRule).filter(AlertRule.name == "__picks_watcher__").one()
        r.channels = '["log"]'
        db.commit()
    row2 = ensure_system_rule(factory)
    assert json.loads(row2.channels) == ["in_app", "log", "feishu"]

    # 配置默认收敛（去 feishu）→ 已固化的 feishu 行在下次 ensure 时同步去掉
    monkeypatch.setattr(settings, "picks_watcher_channels", "in_app,log")
    row3 = ensure_system_rule(factory)
    assert json.loads(row3.channels) == ["in_app", "log"]


# ---------------------------------------------------------------- 板块级资金规则（P1-1）


def test_board_flow_first_beat_only_records_baseline():
    """首拍**无增量基线** → 不报「突增」（否则每次启动都会误报）；但「低吸异动」是
    累计口径条件（净额 + 涨幅），不依赖差分 → 首拍即可成立。两类语义不同，勿混。"""
    w = IntradayWatcher([])
    got = w._step_board_flows({"甲板": {"net": 3.0, "pct": 0.5}})
    assert [a["kind"] for a in got] == ["board_low_absorb"]
    # 第二拍才有增量基线；低吸已当日一次 → 只剩突增
    got2 = w._step_board_flows({"甲板": {"net": 5.0, "pct": 0.5}})
    assert [a["kind"] for a in got2] == ["board_flow_surge"]


def test_board_flow_surge_needs_increment_over_threshold():
    from app.picks.watcher import BOARD_FLOW_SURGE_YI

    w = IntradayWatcher([])
    w._step_board_flows({"甲板": {"net": 0.0, "pct": 5.0}})
    # 增量略低于阈值 → 不报（且涨幅 5% 也不满足低吸）
    assert w._step_board_flows({"甲板": {"net": BOARD_FLOW_SURGE_YI - 0.01, "pct": 5.0}}) == []
    # 恰好越过阈值 → 报（口径含边界）
    got = w._step_board_flows({"甲板": {"net": BOARD_FLOW_SURGE_YI * 2, "pct": 5.0}})
    assert [a["kind"] for a in got] == ["board_flow_surge"]


def test_board_low_absorb_requires_high_net_and_low_pct():
    from app.picks.watcher import BOARD_LOW_ABSORB_PCT, BOARD_LOW_ABSORB_YI

    w = IntradayWatcher([])
    # 净额达标但涨幅也不低 → 不是「资金进价没动」，不报
    assert w._step_board_flows({"甲板": {"net": BOARD_LOW_ABSORB_YI, "pct": BOARD_LOW_ABSORB_PCT}}) == []
    # 净额略低 → 不报
    assert w._step_board_flows({"乙板": {"net": BOARD_LOW_ABSORB_YI - 0.01, "pct": 0.5}}) == []


def test_board_flow_unknown_net_is_not_zero():
    """缺净额（unknown）绝不按 0 处理：不建基线、不触发任何规则。"""
    w = IntradayWatcher([])
    assert w._step_board_flows({"甲板": {"net": None, "pct": 0.1}}) == []
    assert w._step_board_flows({"甲板": {"net": None, "pct": 0.1}}) == []
    assert w.state()["board_flow_tracked"] == 0


def test_board_flow_alerted_once_per_day():
    w = IntradayWatcher([])
    w._step_board_flows({"甲板": {"net": 0.0, "pct": 0.5}})
    assert len(w._step_board_flows({"甲板": {"net": 5.0, "pct": 0.5}})) == 2
    assert w._step_board_flows({"甲板": {"net": 50.0, "pct": 0.5}}) == []


def test_board_flow_caps_alerts_per_beat_and_keeps_biggest():
    """按幅度取 Top（防板块轮动刷屏）；未入选的不标记 → 下一拍仍有机会。"""
    from app.picks.watcher import BOARD_ALERT_PER_BEAT

    w = IntradayWatcher([])
    names = [f"板{i}" for i in range(BOARD_ALERT_PER_BEAT + 3)]
    w._step_board_flows({n: {"net": 0.0, "pct": 5.0} for n in names})
    got = w._step_board_flows({n: {"net": float(i + 1), "pct": 5.0} for i, n in enumerate(names)})
    assert len(got) == BOARD_ALERT_PER_BEAT
    assert {a["direction"] for a in got} == set(names[-BOARD_ALERT_PER_BEAT:])


def test_board_flow_keys_are_kind_tagged():
    w = IntradayWatcher([])
    w._step_board_flows({"甲板": {"net": 0.0, "pct": 0.5}})
    got = w._step_board_flows({"甲板": {"net": 5.0, "pct": 0.5}})
    assert {a["key"] for a in got} == {"board-flow-surge-甲板", "board-low-absorb-甲板"}
    assert all(a["direction"] == "甲板" for a in got)


def test_board_flows_io_reads_net_ratio_pct_code(monkeypatch):
    """`_board_flows` 取四要素（含 `ratio`，2026-09-12 接入）；缺 net/ratio 如实 None
    （不填 0）；无名板块跳过；单 kind 失败降级。"""
    import asyncio

    from app.market import board_flow as bf
    from app.picks import watcher as wt

    async def fake_list(kind):
        if kind == "concept":
            return ([{"name": "甲板", "main_net_yi": 1.5, "main_net_ratio": 3.2,
                      "change_pct": 0.8, "board_code": "BK1"},
                     {"name": "乙板", "main_net_yi": None, "main_net_ratio": None,
                      "change_pct": 2.0, "board_code": "BK2"},
                     {"name": "", "main_net_yi": 9.0, "change_pct": 1.0}], [])
        return (None, ["boom"])

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    out = asyncio.run(wt._board_flows(None))
    assert out["甲板"] == {"net": 1.5, "ratio": 3.2, "pct": 0.8, "code": "BK1"}
    assert out["乙板"]["net"] is None
    assert out["乙板"]["ratio"] is None      # 缺 f184 → 如实 None，不填 0
    assert "" not in out


# ---------------------------------------------------------------- 占比口径 dry-run 计量（P1-2 残余，2026-09-12）


def _boards(n: int, named: dict[str, float] | None = None, pct: float = 5.0,
            base: float = 0.0) -> dict[str, dict]:
    """构造一拍板块字典：`n` 个匿名板块（ratio=`base`）+ 每个具名板块。

    具名板块**永远存在**（即使 ratio 为 0）——否则它在"基线拍"里缺席，
    下一拍就是首拍（无 prev）⇒ 测出来的是"没有基线"而不是被考察的逻辑。

    `base` 用来把匿名板块的 ratio 抬离 0：此时"缺值"与"值为 0"才可区分，
    否则把 unknown 当 0 的错误**测不出来**（2026-09-12 实测：用 0.0 时注入不红）。

    pct 默认 5.0（> BOARD_LOW_ABSORB_PCT）⇒ 只考察 surge 与计量，不掺低吸异动。
    """
    out = {f"板{i}": {"net": 0.0, "ratio": base, "pct": pct} for i in range(n)}
    for name, ratio in (named or {}).items():
        out[name] = {"net": 0.0, "ratio": ratio, "pct": pct}
    return out


def _two_beats_ratio(w, n: int, first: dict[str, float], second: dict[str, float]) -> list[dict]:
    """打两拍：两拍**都含**同一批具名板块，只有 ratio 变；返回第二拍的产出。"""
    w._step_board_flows(_boards(n, {k: first.get(k, 0.0) for k in second}))
    return w._step_board_flows(_boards(n, second))


def test_board_ratio_probe_does_not_trigger_any_alert():
    """**核心不变量**：占比计量**绝不影响触发**。

    即便占比增量给到 50 个百分点这种极端值，只要净额增量不足绝对额阈值，
    就必须**一条告警都不产**——否则 dry-run 就成了事实上的上线。
    """
    w = IntradayWatcher([])
    got = _two_beats_ratio(w, 60, {"猛板": 0.0}, {"猛板": 50.0})
    assert got == []
    assert w.board_ratio_probe["samples"] == 61       # 但计量确实记下了（60 匿名 + 猛板）


def test_board_ratio_probe_accumulates_histogram_and_beats():
    """计量累计正确：样本数 / 计入拍数 / 直方图落箱，且均值可复算。"""
    from app.picks.watcher import BOARD_RATIO_BINS

    w = IntradayWatcher([])
    w._step_board_flows(_boards(60, {"甲板": 0.0, "乙板": 0.0}))   # 首拍无基线 → 0 样本
    w._step_board_flows(_boards(60, {"甲板": 0.03, "乙板": 3.0}))  # 第二拍：+0.03 / +3.0
    p = w._probe_snapshot()
    # 两拍横截面都够宽（62 个板块带回 ratio）⇒ 都计入有效拍；但只有第二拍有 delta
    assert p["beats"] == 2
    assert p["beats_insufficient"] == 0
    assert p["samples"] == 62                        # 60 匿名(Δ0) + 甲板 +0.03 + 乙板 +3.0
    assert p["min_samples"] == 50
    assert p["bins"] == list(BOARD_RATIO_BINS)
    assert sum(p["hist"]) == 62
    assert p["mean"] == round((0.03 + 3.0) / 62, 4)
    # 落箱：bisect_right([0.02,0.05,...], 0.03)=1；bisect_right(..., 3.0)=7；0.0 → 箱 0
    assert p["hist"][1] == 1
    assert p["hist"][7] == 1
    assert p["hist"][0] == 60
    assert p["enabled"] is True


def test_board_ratio_probe_first_beat_is_not_insufficient():
    """首拍全员**无基线**（delta 数为 0）但横截面够宽 ⇒ 仍算**有效拍**、不算样本不足。

    这两件事成因完全不同：前者是"还没有上一拍可比"，后者是"板块数太少"。
    若混记，`beats_insufficient > 0` 会被误读成"横截面经常不够"，进而误判口径可用性。
    """
    w = IntradayWatcher([])
    w._step_board_flows(_boards(60))
    p = w._probe_snapshot()
    assert p["beats"] == 1
    assert p["beats_insufficient"] == 0
    assert p["samples"] == 0                          # 首拍确实没有可比的 delta
    assert p["enabled"] is True


def test_board_ratio_probe_negative_deltas_are_recorded():
    """占比下降（负增量）也要记——分布是否对称是"线画在哪"的依据之一，不能只留右尾。"""
    w = IntradayWatcher([])
    _two_beats_ratio(w, 60, {"降板": 0.0}, {"降板": -5.0})
    p = w._probe_snapshot()
    assert p["hist"][0] == 61                         # 60 匿名(Δ0) + 降板(-5.0) 全在箱 0
    assert p["mean"] == round(-5.0 / 61, 4)
    assert p["samples"] == 61


def test_board_ratio_probe_narrow_beats_keep_samples_but_are_flagged():
    """横截面过窄的拍：样本**照常保留**（单板块 delta 是有效观测，不丢信息），
    但必须被标出来（`beats_insufficient` / `samples_narrow`），不能假装它没问题。"""
    w = IntradayWatcher([])
    got = _two_beats_ratio(w, 10, {"甲板": 0.0}, {"甲板": 3.0})
    assert got == []
    p = w._probe_snapshot()
    assert p["beats"] == 0
    assert p["beats_insufficient"] == 2               # 两拍横截面都只有 11 个板块
    assert p["samples"] == 11                         # 但样本保留（可用于看分布形状）
    assert p["samples_narrow"] == 11                  # 且如实标注为"窄拍样本"
    assert p["enabled"] is False                      # 无有效拍 ⇒ 不宣称可用
    assert p["hist"][0] == 10 and p["hist"][7] == 1   # 分布仍如实落箱


def test_board_ratio_probe_requires_both_beats_known():
    """任一拍缺 ratio ⇒ 该板块本拍**不计入**（unknown ≠ 0，三态纪律）。

    匿名板块的 ratio 抬到 1.0 且缺值板块取 5.0：这样"缺值"与"值为 0/相等"可区分——
    用 0.0 构造时，把 unknown 当 0 的错误**测不出来**（2026-09-12 实测注入不红）。
    """
    w = IntradayWatcher([])
    b1 = _boards(60, {"缺板": 0.0}, base=1.0)
    del b1["缺板"]["ratio"]                            # 首拍缺
    w._step_board_flows(b1)
    w._step_board_flows(_boards(60, {"缺板": 5.0}, base=1.0))
    # 60 个匿名板块 delta=0 计入；缺板 首拍 unknown ⇒ 本拍不计
    assert w._probe_snapshot()["samples"] == 60

    w2 = IntradayWatcher([])
    w2._step_board_flows(_boards(60, {"缺板": 5.0}, base=1.0))
    b2 = _boards(60, {"缺板": 5.0}, base=1.0)
    del b2["缺板"]["ratio"]                            # 次拍缺
    w2._step_board_flows(b2)
    assert w2._probe_snapshot()["samples"] == 60


def test_board_flow_surge_absolute_path_unchanged_by_probe():
    """回归守卫：计量接入后，绝对额口径的 surge 语义必须与改动前**逐字一致**
    （文案 / meta 键集 / 阈值），确保 dry-run 是纯增量、没夹带行为变更。"""
    from app.picks.watcher import BOARD_FLOW_SURGE_YI

    w = IntradayWatcher([])
    w._step_board_flows(_boards(60, {"甲板": 0.0}))
    got = w._step_board_flows({**_boards(60, {"甲板": 0.0}),
                               "甲板": {"net": 1.0, "ratio": 0.0, "pct": 5.0}})
    surges = [a for a in got if a["kind"] == "board_flow_surge"]
    assert [a["direction"] for a in surges] == ["甲板"]
    assert surges[0]["meta"] == {
        "trigger_value": 1.0, "threshold": BOARD_FLOW_SURGE_YI, "cum_net_yi": 1.0,
    }
    assert surges[0]["text"] == (
        "🌊 板块资金突增 甲板：本拍主力净流入 +1.00 亿"
        f"（当日累计 1.00 亿；阈值 {BOARD_FLOW_SURGE_YI} 亿/拍）"
    )
    assert "占比" not in surges[0]["text"]


# ---------------------------------------------------------------- 分发快照（名称留存）

def test_dispatch_alert_snapshot_keeps_name(monkeypatch):
    """提醒快照必须带 name：悬浮球（alert_triage.pending_bubbles）要求 symbol+name
    齐备，缺 name 整条过滤（2026-09-09 用户指令）。

    回归背景（2026-09-10）：此前 snapshot 只落 kind/direction/text，实测库内 14 条
    notify 事件全部 name=None → 悬浮球收不到任何 watcher 个股提醒，而事件确实产生了
    （symbol 列有值），属"数据缺失但无人报错"的静默失效。
    """
    captured: dict = {}

    class _Repo:
        def record_trigger(self, rule_id, symbol, tv, threshold, snapshot=None):
            captured["snapshot"] = snapshot
            captured["symbol"] = symbol
            return NS(id=1)

        def update_event_channels(self, event_id, channels):
            return None

    class _Registry:
        async def dispatch(self, event, rule):
            return ["in_app"]

    monkeypatch.setattr(morning_brief, "brief_for_today", lambda: ("20260910", {"directions": []}))
    monkeypatch.setattr(morning_brief, "append_alert", lambda target, alert: True)
    monkeypatch.setattr(watcher, "get_session_factory", lambda: (lambda: None))
    monkeypatch.setattr(watcher, "get_notifier_registry", lambda: _Registry())

    alert = {
        "key": "flow-surge-600540",
        "kind": "flow_surge",
        "symbol": "",  # 空 symbol 跳过台账登记分支，保持测试无副作用
        "name": "新赛股份",
        "direction": "",
        "text": "💰 大单异动 新赛股份(600540)：主力净流入 1.20 亿",
        "meta": {"trigger_value": 1.2, "threshold": 1.0},
    }
    app = NS(state=NS(alert_repo=_Repo()))
    ok = asyncio.run(
        watcher.dispatch_alert(app, dict(alert), rule_provider=lambda sf: NS(id=1, channels="[]"))
    )
    assert ok is True
    assert captured["snapshot"]["name"] == "新赛股份"
    assert captured["snapshot"]["kind"] == "flow_surge"
    # 无名称时不臆造：落 None 而不是空串（三态：缺失 ≠ 有值）
    alert2 = dict(alert, name="")
    asyncio.run(watcher.dispatch_alert(app, alert2, rule_provider=lambda sf: NS(id=1, channels="[]")))
    assert captured["snapshot"]["name"] is None


# ---------------------------------------------------------------- 价格语义（缺陷回归，2026-09-10）


def _install_dispatch_stubs(monkeypatch):
    """dispatch_alert 的最小桩：只保留被断言的分支，阻断真实 IO 与 DB。"""

    class _Repo:
        def record_trigger(self, rule_id, symbol, tv, threshold, snapshot=None):
            return NS(id=1)

        def update_event_channels(self, event_id, channels):
            return None

    class _Registry:
        async def dispatch(self, event, rule):
            return ["in_app"]

    monkeypatch.setattr(morning_brief, "brief_for_today", lambda: ("20260910", {"directions": []}))
    monkeypatch.setattr(morning_brief, "append_alert", lambda target, alert: True)
    monkeypatch.setattr(watcher, "get_session_factory", lambda: (lambda: None))
    monkeypatch.setattr(watcher, "get_notifier_registry", lambda: _Registry())
    return _Repo()


def test_alert_price_rejects_non_price_trigger_value():
    """`trigger_value` 的两种语义（涨跌幅 / 净额亿元）**都不是价格**，不可当 entry_price。

    回归背景（2026-09-10）：`record_sighting(entry_price=...)` 与
    `maybe_open(price=...)` 曾直接取 `meta["trigger_value"]`，而该字段在本模块有四种
    语义且没有一种表示价格 ——
      · flow_surge / board_flow = 净额（亿元）或板块涨跌幅；
      · falsify / confirm = 个股涨跌幅。
    后果：`watch_ledger` 把「主力净流入 0.32 亿」记成股价 0.32（pnl 22150%，
    verdict 误判 success，09-09 共 71 行）；`paper_order` 以题材涨跌幅 1.82
    当价格开模拟仓（单号 1/2，603330）。取数只认价格语义字段，取不到给 None。
    """
    # flow_surge：净额（亿元）—— 1.20 是钱不是股价
    assert watcher._alert_price({"meta": {"trigger_value": 1.2, "threshold": 1.0}}) is None
    # 09-09 事故原样：alert_event.snapshot =「主力净流入 0.32 亿」
    assert watcher._alert_price({"meta": {"trigger_value": 0.32, "threshold": 0.3}}) is None
    # falsify / confirm：个股涨跌幅
    assert watcher._alert_price({"meta": {"trigger_value": -3.5}}) is None
    # board_flow 的题材涨跌幅（paper_order id=1/2 曾以 1.82「元」开仓）
    assert watcher._alert_price({"meta": {"trigger_value": 1.82}}) is None
    # 明确的价格字段 → 采纳（并优先于 trigger_value）
    assert watcher._alert_price({"meta": {"price": 71.2, "trigger_value": 0.32}}) == 71.2
    # 无价格字段 → 回退快照现价
    assert watcher._alert_price({}, 33.3) == 33.3
    # 两处都取不到 → None（宁可判不出，不可判错）
    assert watcher._alert_price({}) is None
    assert watcher._alert_price({"meta": {}}, None) is None
    assert watcher._alert_price({"meta": None}) is None
    # 非正数不是价格
    assert watcher._alert_price({"meta": {"price": 0}}) is None
    assert watcher._alert_price({"meta": {"price": -1}}) is None
    assert watcher._alert_price({"meta": {"price": 0}}, 5.0) == 5.0
    # 字符串不隐式转换（三态：类型不符 ≠ 可解析）
    assert watcher._alert_price({"meta": {"price": "12.5"}}) is None


def test_snapshot_price_only_accepts_positive_number():
    """快照现价回退：只认正数 price，缺失/0/负都返回 None（缺 ≠ 0）。"""

    class _Svc:
        def __init__(self, rows):
            self.snapshot = rows

    app = NS(snapshot_service=_Svc([{"symbol": "603330", "price": 71.2}]))
    assert watcher._snapshot_price(app, "603330") == 71.2
    assert watcher._snapshot_price(NS(snapshot_service=_Svc([{"symbol": "603330", "price": 0}])), "603330") is None
    assert watcher._snapshot_price(NS(snapshot_service=_Svc([{"symbol": "603330", "change_pct": 3.0}])), "603330") is None
    # 未命中 / 空 symbol / 无服务 / snapshot=None → None
    assert watcher._snapshot_price(app, "000001") is None
    assert watcher._snapshot_price(app, "") is None
    assert watcher._snapshot_price(NS(), "603330") is None
    assert watcher._snapshot_price(NS(snapshot_service=NS(snapshot=None)), "603330") is None


def test_dispatch_alert_price_comes_from_price_semantics(monkeypatch):
    """端到端复刻 09-09 事故：`buy_point` 告警不带 `meta["price"]` 时，
    台账 entry_price 与开仓价都必须回退**快照现价**，绝不可取 trigger_value。

    覆盖两条独立调用链：`record_sighting(entry_price=...)`（用局部 snap_price）
    与 `maybe_open(price=...)`（用 _snapshot_price 独立查快照）。
    """
    from app.picks import position_engine, watch_ledger

    repo = _install_dispatch_stubs(monkeypatch)
    seen: dict = {}
    opened: dict = {}

    monkeypatch.setattr(watch_ledger, "record_sighting", lambda **kw: seen.update(kw) or {"ok": True})

    async def fake_open(app, **kw):
        opened.update(kw)
        return {"opened": False, "reason": "stub"}

    monkeypatch.setattr(position_engine, "maybe_open", fake_open)

    class _Svc:
        snapshot = [{"symbol": "603330", "name": "奥士康", "price": 71.2, "change_pct": 3.0}]

    app = NS(state=NS(alert_repo=repo, snapshot_service=_Svc()))
    alert = {
        "key": "buy-point-603330",
        "kind": "buy_point",
        "symbol": "603330",
        "name": "奥士康",
        "text": "主力净流入 0.32 亿",
        "meta": {"trigger_value": 0.32, "threshold": 0.3},  # 无 price 字段
    }
    ok = asyncio.run(
        watcher.dispatch_alert(app, dict(alert), rule_provider=lambda sf: NS(id=1, channels="[]"))
    )
    assert ok is True
    assert seen, "台账登记分支未走到（异常被 contextlib.suppress 吞掉时会假绿）"
    assert seen["entry_price"] == 71.2
    assert opened["price"] == 71.2
    assert seen["entry_price"] != 0.32 and opened["price"] != 0.32  # 事故值：净额不是价格


def test_dispatch_alert_price_absent_stays_unknown(monkeypatch):
    """meta 无 price、快照也无 price → 两条链都取 None（宁可判不出，不可判错）。

    旧代码此处会取到 trigger_value=0.32，把「净流入 0.32 亿」静默记成 0.32 元成本。
    """
    from app.picks import position_engine, watch_ledger

    repo = _install_dispatch_stubs(monkeypatch)
    seen: dict = {}
    opened: dict = {}

    monkeypatch.setattr(watch_ledger, "record_sighting", lambda **kw: seen.update(kw) or {"ok": True})

    async def fake_open(app, **kw):
        opened.update(kw)
        return {"opened": False, "reason": "stub"}

    monkeypatch.setattr(position_engine, "maybe_open", fake_open)

    class _Svc:
        # 有行情（change_pct 可判定未封板）但**没有价格**——模拟快照字段不齐
        snapshot = [{"symbol": "603330", "name": "奥士康", "change_pct": 3.0}]

    app = NS(state=NS(alert_repo=repo, snapshot_service=_Svc()))
    alert = {
        "key": "buy-point-603330",
        "kind": "buy_point",
        "symbol": "603330",
        "name": "奥士康",
        "meta": {"trigger_value": 0.32, "threshold": 0.3},
    }
    ok = asyncio.run(
        watcher.dispatch_alert(app, dict(alert), rule_provider=lambda sf: NS(id=1, channels="[]"))
    )
    assert ok is True
    assert seen, "台账登记分支未走到（异常被 contextlib.suppress 吞掉时会假绿）"
    assert seen["entry_price"] is None
    assert opened["price"] is None
    assert seen["entry_price"] != 0.32 and opened["price"] != 0.32
