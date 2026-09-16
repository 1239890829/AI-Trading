"""猎场头部闸门读时重算（2026-09-16，用户问题 2/7「猎场判断必须动态化」）。

## 病根（实测，非推测）

空仓闸门（`picks/gate.evaluate_stand_aside`）原本只在组合生成时算一次并落库
`meta.gate`，此后**全天定格**；而同一页面紧邻的风格路由是读取时重算的。
2026-09-16 实测同一次 `/api/picks/today` 响应里同时出现：

- 落库：`phase=退潮`、`stand_aside=true/strong`、`strip_buy_range=true`
  （横幅：建议空仓观望，已撤除买入区间）
- 实时：`style_routing.phase=高潮`、`style=题材进攻`

两个相反结论同屏，且定格的是更悲观的那个。当日实际：生成时（09:26）在场
仅 3 只涨停、最高 2 板（`meta.limit_up_count=3` / `market_max_boards=2`），
据此判「退潮」并非判据错——**错在把开盘 3 分钟的瞬时快照当成了全天结论**。
收盘口径：涨停 89 家、最高 6 板、1进2 晋级率 36%（生成时 8%）、炸板率 11%。

## 本文件钉住的契约

1. 读侧能拿到与生成时**同口径**的闸门输入（`sent["gate_inputs"]`，引擎结构化出口）
   ⇒ 读时重算**零额外网络调用**；
2. 读时重算与生成时共用同一个 60s 情绪槽（第六个消费方，不新开槽）；
3. 落库值**原样保留**（复盘归因要靠它，`buy_range` 被撤的原因也在它身上），
   只在它身上加 `gate_source="stored"`；
4. 降级三态：复核不可用时 `gate_source="unavailable"` + 保留生成时刻结论，
   **不覆盖成"未触发"**；连生成时刻结论都没有时**不得凭空造出 `stand_aside=False`**
   （那是把"不知道"伪装成"安全"）。
"""

from __future__ import annotations

import asyncio

import app.services.market_context as mc
from app.api.routes import picks as picks_route


def _run(coro):
    return asyncio.run(coro)


class _Hub:
    def __init__(self):
        self.provider = type("P", (), {"name": "mock", "realtime": True})()
        self.last_success_refresh = None

    def is_stale(self) -> bool:
        return False


HUB = _Hub()


class _State:
    def __init__(self):
        self.snapshot_service = type("Snap", (), {"breadth": {}})()


class _Request:
    def __init__(self, state):
        self.app = type("App", (), {"state": state})()


def _patch_sentiment(monkeypatch, sent):
    """把情绪计算打桩成固定返回值（打模块全局名，命中 `get_cached_sentiment`）。"""
    async def _fake(hub, snapshot_service, **kwargs):
        return sent

    monkeypatch.setattr(mc, "compute_market_sentiment", _fake)
    return _fake


def _patch_pctl(monkeypatch, promo_pctl, break_pctl):
    """把历史分位打桩（真实实现要读整份情绪历史文件；此处只验闸门接线）。"""
    from app.sentiment import metric_history

    def _fake(metric, value):
        if value is None:
            return None
        return {"percentile": promo_pctl if metric == "promo_1to2" else break_pctl}

    monkeypatch.setattr(metric_history, "percentile_of_value", _fake)


#: 生成时刻落库闸门（09:26 快照：仅 3 只涨停、最高 2 板 ⇒ 退潮）
STORED_GATE = {
    "stand_aside": True,
    "level": "strong",
    "reasons": ["情绪相位「退潮」——赚钱效应处于周期低位"],
    "advice": "市场情绪明显转弱，建议空仓观望，切忌盲目出手",
    "phase": "退潮",
    "strip_buy_range": True,
    "signals": {"promotion_1to2": 0.08, "promotion_1to2_pctl": 9.0, "break_rate": None},
}

#: 读取时刻的实时情绪（收盘口径）
LIVE_SENT = {
    "phase": "高潮",
    "phase_unreliable": False,
    "trade_date": "2026-09-16",
    "judged_at": "2026-09-16T10:48:06+00:00",
    "gate_inputs": {
        "promotion_1to2": 0.36,
        "break_rate": 0.11,
        "limit_down": 0,
        "prev_zt_median_pct": 1.06,
        "break_caliber": "pool",
    },
}


# ---------------------------------------------------------------- 重算正确性


def test_live_gate_clears_when_live_phase_is_strong(monkeypatch):
    """今天的主场景：实时相位「高潮」+ 晋级率 36% ⇒ 闸门不该触发（生成时是退潮）。"""
    state = _State()
    _patch_sentiment(monkeypatch, dict(LIVE_SENT))
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    out = _run(picks_route._live_gate(_Request(state), HUB, dict(STORED_GATE)))

    assert out["gate_source"] == "live"
    assert out["stand_aside"] is False
    assert out["level"] == "none"
    assert out["reasons"] == []
    assert out["phase"] == "高潮"
    # 对照面：前端凭它写「生成时（09:26）按退潮判…，按当前盘面复核已解除」
    assert out["recheck"]["stored_phase"] == "退潮"
    assert out["recheck"]["phase"] == "高潮"
    assert out["recheck"]["phase_changed"] is True
    assert out["recheck"]["inputs_source"] == "sentiment.gate_inputs"


def test_live_gate_triggers_when_live_phase_is_adverse(monkeypatch):
    """反向：实时相位真的转弱时必须触发（动态化不是"只会放宽"）。"""
    state = _State()
    sent = {
        "phase": "退潮",
        "phase_unreliable": False,
        "trade_date": "2026-09-16",
        "judged_at": "2026-09-16T06:00:00+00:00",
        "gate_inputs": {
            "promotion_1to2": 0.08,
            "break_rate": None,
            "limit_down": 0,
            "prev_zt_median_pct": 0.0,
            "break_caliber": "missing",
        },
    }
    _patch_sentiment(monkeypatch, sent)
    _patch_pctl(monkeypatch, promo_pctl=9.0, break_pctl=None)

    out = _run(picks_route._live_gate(_Request(state), HUB, None))

    assert out["stand_aside"] is True
    assert out["level"] == "strong"  # 相位级 + 单指标异常 = 2 条理由
    assert out["strip_buy_range"] is True  # 相位 ∈ {退潮,冰点} ⇒ 撤区间
    assert out["gate_source"] == "live"


def test_live_gate_uses_signals_for_caliber_traceability(monkeypatch):
    """输入留痕必须能回答"为什么这天触发"——分位口径与原始值都要在。"""
    state = _State()
    _patch_sentiment(monkeypatch, dict(LIVE_SENT))
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    out = _run(picks_route._live_gate(_Request(state), HUB, dict(STORED_GATE)))

    sig = out["signals"]
    assert sig["promotion_1to2"] == 0.36
    assert sig["promotion_1to2_pctl"] == 62.0
    assert sig["break_rate"] == 0.11
    assert sig["prev_zt_median_pct"] == 1.06
    assert sig["promo_caliber"] == "percentile"
    assert out["recheck"]["break_caliber"] == "pool"


def test_live_gate_percentile_missing_falls_back_and_says_so(monkeypatch):
    """分位不可用时回落绝对经验值，且**在理由里写明**用的是哪个口径（不静默）。"""
    state = _State()
    sent = {
        "phase": "修复",
        "phase_unreliable": False,
        "trade_date": "2026-09-16",
        "judged_at": "2026-09-16T06:00:00+00:00",
        "gate_inputs": {"promotion_1to2": 0.20, "break_rate": None,
                        "limit_down": 0, "prev_zt_median_pct": 0.5,
                        "break_caliber": "missing"},
    }
    _patch_sentiment(monkeypatch, sent)
    _patch_pctl(monkeypatch, promo_pctl=None, break_pctl=None)

    out = _run(picks_route._live_gate(_Request(state), HUB, None))

    assert out["stand_aside"] is True  # 20% < PROMO_FLOOR 30%
    assert any("历史分位不可用" in r for r in out["reasons"])
    assert out["signals"]["promo_caliber"] == "absolute"


# ---------------------------------------------------------------- 共享槽（不额外回源）


def test_live_gate_shares_the_sentiment_slot(monkeypatch):
    """读时重算**不得**新开缓存槽：与风格路由同一次计算（P1-3 纪律的第六个消费方）。"""
    state, counter = _State(), {"n": 0}

    async def _fake(hub, snapshot_service, **kwargs):
        counter["n"] += 1
        return dict(LIVE_SENT)

    monkeypatch.setattr(mc, "compute_market_sentiment", _fake)
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    req = _Request(state)
    _run(picks_route._live_style_routing(req, HUB, None))
    _run(picks_route._live_gate(req, HUB, dict(STORED_GATE)))

    assert counter["n"] == 1, f"两次消费应共用一次计算，实际 {counter['n']} 次"
    assert not hasattr(state, "_ttl_cache_picks.live_gate"), "不得为此新开缓存槽"


# ---------------------------------------------------------------- 降级三态


def test_live_gate_degradation_keeps_stored_conclusion(monkeypatch):
    """复核不可用：保留生成时刻结论 + 说明原因，**不覆盖成未触发**。"""
    from app.services.market_context import CalendarUnavailable

    state = _State()

    async def _boom(hub, snapshot_service, **kwargs):
        raise CalendarUnavailable("全市场快照尚未就绪")

    monkeypatch.setattr(mc, "compute_market_sentiment", _boom)

    out = _run(picks_route._live_gate(_Request(state), HUB, dict(STORED_GATE)))

    assert out["gate_source"] == "unavailable"
    assert "全市场快照尚未就绪" in out["gate_note"]
    # 关键：结论仍是生成时刻的（不是被覆盖成"未触发"）
    assert out["stand_aside"] is True
    assert out["phase"] == "退潮"


def test_live_gate_degradation_without_stored_does_not_invent_false(monkeypatch):
    """连生成时刻结论都没有时，**不得**凭空造出 `stand_aside=False`。

    这是最贵的一条：`False` 会被前端读成"闸门未触发"，把"未复核"伪装成"安全"。
    """
    async def _boom(hub, snapshot_service, **kwargs):
        raise RuntimeError("boom")

    state = _State()
    monkeypatch.setattr(mc, "compute_market_sentiment", _boom)

    out = _run(picks_route._live_gate(_Request(state), HUB, None))

    assert out["gate_source"] == "unavailable"
    assert "stand_aside" not in out
    assert "boom" in out["gate_note"]


def test_live_style_routing_and_live_gate_degrade_independently(monkeypatch):
    """两者降级互不牵连：一个失败不把另一个也拖成不可用。"""
    state = _State()
    _patch_sentiment(monkeypatch, dict(LIVE_SENT))
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    routed = _run(picks_route._live_style_routing(_Request(state), HUB, None))
    gate = _run(picks_route._live_gate(_Request(state), HUB, dict(STORED_GATE)))

    assert routed["phase_source"] == "live"
    assert gate["gate_source"] == "live"


# ---------------------------------------------------------------- 落库值不被改写


def test_attach_gates_marks_stored_source_without_overwriting(monkeypatch):
    """`_attach_gates` 只加来源标注，落库内容逐字保留（复盘归因靠它）。"""
    state = _State()
    _patch_sentiment(monkeypatch, dict(LIVE_SENT))
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    meta = {"gate": dict(STORED_GATE), "generated_at": "2026-09-16T09:26:35+08:00"}
    out = _run(picks_route._attach_gates(_Request(state), HUB, meta))

    stored = out["gate"]
    assert stored["gate_source"] == "stored"
    assert stored["stand_aside"] is True
    assert stored["phase"] == "退潮"
    assert stored["reasons"] == STORED_GATE["reasons"]
    assert stored["strip_buy_range"] is True
    assert stored["signals"] == STORED_GATE["signals"]
    # 实时面单独挂在 gate_live，不覆盖 gate
    assert out["gate_live"]["gate_source"] == "live"
    assert out["gate_live"]["stand_aside"] is False


def test_attach_gates_without_stored_gate_still_provides_live(monkeypatch):
    """旧行无 gate 时也挂实时面（meta 里没 key 不是"没有风险"）。"""
    state = _State()
    _patch_sentiment(monkeypatch, dict(LIVE_SENT))
    _patch_pctl(monkeypatch, promo_pctl=62.0, break_pctl=30.0)

    out = _run(picks_route._attach_gates(_Request(state), HUB, {"weights": {}}))

    assert "gate" not in out
    assert out["gate_live"]["gate_source"] == "live"
    assert out["gate_live"]["stand_aside"] is False
