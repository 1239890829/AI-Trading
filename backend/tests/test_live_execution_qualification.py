"""Original selection facts survive a macro mask; only fresh, bound rechecks may act."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from app.core.bjtime import BJ_TZ
from app.core.freshness import MAX_FUTURE_SKEW_SECONDS
from app.picks.execution_qualification import requalify_item, resolve_execution_item
from app.picks.meta_confidence import DIMENSIONS
from app.picks.opportunity_learning import build_execution_contract

NOW = datetime(2026, 9, 8, 10, 30, tzinfo=BJ_TZ)


def context(**changes):
    value = {"state": "ready", "phase": "发酵", "trade_date": "2026-09-08",
             "judged_at": NOW.isoformat(), "source_as_of": NOW.isoformat(), "source_state": "ready",
             "gate": {"phase": "发酵", "stand_aside": False, "strip_buy_range": False}}
    value.update(changes)
    return value


def original(score=82):
    seed = {"version": 1, "score": score, "sub_scores": dict.fromkeys(DIMENSIONS, score),
            "dimension_evidence": {d: {"state": "success"} for d in DIMENSIONS},
            "vetoes": [], "halt_penalty": 0, "chip_signal": None,
            "buy_range": {"low": 10.2, "high": 10.8}, "observation_only": False, "follow_state": None}
    return {"symbol": "600000", "name": "测试标的", "score": score, "price": 10.5,
            "confidence": {"tier": "observe"}, "observation_only": True, "vetoes": [],
            "execution_seed": seed}


def contract_for(item, when=NOW):
    return build_execution_contract(trade_date="2026-09-08", symbol=item["symbol"], item=item,
        executable_snapshot={"state": "ready", "price": 10.5, "source": "isolated_fixture", "as_of": when.isoformat()},
        gate_decision="passed", gate_reason=None, pick_generated_at="2026-09-08T09:26:00+08:00")


def test_fresh_recheck_preserves_original_score_veto_range_and_selection_bytes():
    raw = original()
    before = deepcopy(raw)
    effective = requalify_item(raw, "source-A", context(), NOW)
    assert raw == before
    assert effective["confidence"]["tier"] == "strong"
    assert effective["buy_range"] == raw["execution_seed"]["buy_range"]
    assert effective["score"] == raw["score"] and effective["vetoes"] == raw["vetoes"]
    restored, ctx, issue = resolve_execution_item(raw, contract_for(effective), "source-A", NOW)
    assert issue is None and restored == effective and ctx == context()


@pytest.mark.parametrize("score", [50.9, 51.7, 51.8, 53.9, 54.1, 55.0, 55.2, 55.9])
def test_observed_daily_scores_still_fail_original_sixty_line_after_macro_recovers(score):
    raw = original(score)
    effective = requalify_item(raw, "A", context(), NOW)
    assert effective["confidence"]["tier"] == "observe"
    assert any("60.0" in reason for reason in effective["confidence"]["reasons"])


@pytest.mark.parametrize("change", [
    {"state": "unavailable"}, {"source_state": "stale"}, {"trade_date": "2026-09-07"},
    {"judged_at": (NOW - timedelta(seconds=61)).isoformat()},
    {"source_as_of": (NOW + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS + 1)).isoformat()}, {"phase": "未核实"},
    {"gate": {"phase": "冰点", "stand_aside": False}},
])
def test_unknown_stale_future_or_mismatched_macro_does_not_upgrade(change):
    item = requalify_item(original(), "A", context(**change), NOW)
    assert item["_execution_qualification"]["state"] == "unavailable"
    assert item["confidence"]["tier"] == "observe" and "buy_range" not in item


def test_red_line_chip_and_original_observation_are_never_cleared():
    for change in ({"vetoes": ["原红线"]}, {"halt_penalty": 5},
                   {"chip_signal": "distribution_warning"}, {"observation_only": True}):
        raw = original()
        raw["execution_seed"].update(change)
        if "vetoes" in change:
            raw["vetoes"] = change["vetoes"]
        effective = requalify_item(raw, "A", context(), NOW)
        assert effective.get("observation_only") or effective["confidence"]["tier"] == "observe"


def test_missing_old_seed_does_not_reconstruct_a_historical_buy_range():
    raw = original()
    del raw["execution_seed"]
    item = requalify_item(raw, "A", context(), NOW)
    assert item["_execution_qualification"]["state"] == "unavailable" and "buy_range" not in item
    assert resolve_execution_item(raw, contract_for(item), "A", NOW)[2]


def test_unknown_original_risk_is_not_filled_with_zero():
    raw = original()
    raw["execution_seed"]["halt_penalty"] = None
    effective = requalify_item(raw, "A", context(), NOW)
    assert raw["execution_seed"]["halt_penalty"] is None
    assert effective["_execution_qualification"]["state"] == "unavailable"
    assert "未知扣分当零" in effective["_execution_qualification"]["reason"]


def test_all_independent_refusals_are_saved_without_turning_ready_into_clock_proof():
    from app.picks.buy_point import evaluate_buy_points, execution_quote_check, _reject_unfresh_execution
    item = original(51.8)
    _, refused = evaluate_buy_points([item], gate_stand=False,
                                    quotes={"600000": {"price": 10.5, "change_pct": 2}})
    states = {c["name"]: c["state"] for c in refused[0]["checks"]}
    assert states["confidence"] == states["score"] == states["macro_gate"] == "fail"
    assert states["buy_range"] == "unknown"
    quote = {"state": "ready", "price": 10.5, "source": "isolated_fixture",
             "as_of": (NOW-timedelta(seconds=61)).isoformat(), "checked_at": NOW.isoformat()}
    assert execution_quote_check(quote)["state"] == "unknown"
    kept, skips = _reject_unfresh_execution([{"item": item}], [], {"600000": quote})
    assert not kept and "60秒" in skips[0]["reason"]


def test_action_rechecks_source_seed_contract_and_clock_without_new_decision_version_for_polling():
    raw = original()
    item = requalify_item(raw, "A", context(), NOW)
    old = contract_for(item)
    assert resolve_execution_item(raw, old, "B", NOW)[2]
    assert resolve_execution_item(raw, old, "A", NOW + timedelta(seconds=61))[2]
    wrong = deepcopy(old)
    wrong["gate_inputs"]["confidence_tier"] = "observe"
    assert resolve_execution_item(raw, wrong, "A", NOW)[2]
    changed = deepcopy(raw)
    changed["execution_seed"]["buy_range"]["low"] = 10.3
    assert resolve_execution_item(changed, old, "A", NOW)[2]
    later = requalify_item(raw, "A", context(judged_at=(NOW+timedelta(seconds=1)).isoformat(),
                                               source_as_of=(NOW+timedelta(seconds=1)).isoformat()), NOW+timedelta(seconds=1))
    assert contract_for(later, NOW+timedelta(seconds=1))["decision_version"] == old["decision_version"]


def test_service_rechecks_preserved_candidate_and_archives_before_dispatch(monkeypatch, tmp_path):
    from tests.test_buy_point import _patch_happy_path, _quote, async_ok
    import app.picks.buy_point as bp
    import app.picks.execution_qualification as eq
    from app.models.daily_pick import DailyPickSet
    from app.picks.opportunity_learning import latest_notification_execution
    from app.services.selection_notifications import daily_selection_version

    item = original()
    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path, items=[item], quotes={"600000": _quote()})
    generated = "2026-09-08T09:26:00+08:00"
    version = daily_selection_version("2026-09-08", generated, [item])
    payload = {"date": "2026-09-08", "items": [item], "meta": {"generated_at": generated,
               "selection_version": version, "gate": {"stand_aside": True, "phase": "退潮", "strip_buy_range": True}}}
    with app._test_factory() as db:
        row = db.query(DailyPickSet).filter_by(date="2026-09-08").one()
        row.items, row.meta = json.dumps([item]), json.dumps(payload["meta"])
        db.commit()
    monkeypatch.setattr(bp, "_today_picks_payload", lambda: deepcopy(payload))
    monkeypatch.setattr(eq, "live_execution_context", async_ok(context()))
    hits = asyncio.run(bp.check_and_dispatch(app))
    assert len(hits) == 1 and counters["direct_io"] == 0
    saved = latest_notification_execution("2026-09-08", app._test_factory)["600000"]
    assert saved["gate_decision"] == "passed"
    assert saved["qualification"]["selection_source_version"] == version
    assert saved["reference_entry"]["as_of"] == generated
    with app._test_factory() as db:
        assert json.loads(db.query(DailyPickSet).one().items) == [item]
        from app.models.alert import AlertEvent
        card = json.loads(db.query(AlertEvent).one().snapshot)["card"]
        assert "门控：正常" in json.dumps(card, ensure_ascii=False)
        assert "门控：空仓观察" not in json.dumps(card, ensure_ascii=False)


@pytest.mark.parametrize("change", ["clock", "source"])
def test_dispatch_rechecks_after_await_and_keeps_original_eligible_fact(monkeypatch, tmp_path, change):
    from tests.test_buy_point import _patch_happy_path, _item, _quote
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox, BuyPointConsumption
    from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path, items=[_item()], quotes={"600000": _quote()})
    payload = bp._today_picks_payload()
    clock = {"now": NOW}
    monkeypatch.setattr(bp, "beijing_now", lambda: clock["now"])

    async def delayed(*args):
        clock["now"] = NOW + timedelta(seconds=61 if change == "clock" else 1)
        if change == "source":
            payload["meta"]["selection_version"] = "changed-after-original-archive"
        return {"phase": "发酵"}

    monkeypatch.setattr(bp, "_sent_with_cache", delayed)
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    assert counters["direct_io"] == counters["paper"] == 0
    with app._test_factory() as db:
        assert db.query(AlertEvent).count() == db.query(NotificationOutbox).count() == db.query(BuyPointConsumption).count() == 0
        decisions = db.query(Snapshot).filter_by(stage="notification").order_by(Snapshot.as_of).all()
        assert [d.decision for d in decisions] == ["eligible", "rejected"]
        assert decisions[0].as_of < decisions[1].as_of
