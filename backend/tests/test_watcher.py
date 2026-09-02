"""盘中方向跟踪状态机回归测试（选股 2.0 批次 B）。

锁四类必然再发生的失效：
1. **去重失效**——确认信号每拍都为真，若无 (方向,个股) 去重会每分钟刷一条提醒。
2. **证伪不彻底**——证伪后仍继续确认/提醒，等于给用户两面信号。
3. **缺数据误证伪**——某方向某拍没数据（题材没上榜/板块匹配失败）就当"转负"
   计数或证伪，违反三态纪律（缺 ≠ 证伪）。
4. **提醒刷屏**——龙头轮动时每方向无限发确认提醒，必须有每日上限。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

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


def test_unknown_volume_blocks_confirm_but_keeps_strength():
    """量比缺失（unknown）→ 不确认、不发提醒；但满足率 4/5 → strength 0.75 留档。"""
    tr = DirectionTracker(direction="粮食")
    theme = _theme(pct=2.0, limit_up=3, max_boards=3, leader_pct=7.0,
                   leader_symbol="600000", leader_name="A")
    alerts = tr.step(theme, ENV_OK, 590)
    assert alerts == []
    assert tr.confirmed is False
    assert tr.last_confirm["confirmed"] is False
    assert tr.last_confirm["unknown_count"] == 1
    assert tr.last_confirm["strength"] == 0.75


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
