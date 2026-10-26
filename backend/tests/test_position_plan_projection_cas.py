"""Saved plan facts survive concurrent writers and async projection waits."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier, Event, get_ident

import pytest

from app.picks import exit_engine, position_engine as pe


@pytest.fixture
def plan_dir(tmp_path, monkeypatch):
    folder = tmp_path / "plans"
    monkeypatch.setattr(pe, "_PLAN_DIR", folder)
    return folder


def _plan(**values):
    return {"date": pe.beijing_now().date().isoformat(), "decisions": [], "exits": [], "peaks": {}, **values}


def _exit(order_id=114):
    return {"order_id": order_id, "symbol": "600000", "name": "fixture", "ts": "10:05:00",
            "qty": 100, "price": 10, "reason": "frozen exit fact"}


def test_no_drift_json_is_identical_and_plain_dict_retains_explicit_replace_semantics(plan_dir):
    original = _plan(peaks={"600000": 10})
    pe.save_plan(original)
    path = plan_dir / f"{original['date']}.json"
    before = path.read_bytes()
    snapshot = pe.load_plan()
    pe.save_plan(snapshot)
    assert path.read_bytes() == before
    assert json.loads(before) == original and "base" not in json.loads(before)
    replacement = _plan(decisions=[{"historical": "explicit replacement"}])
    pe.save_plan(replacement)
    assert dict(pe.load_plan()) == replacement


def test_anonymous_history_duplicates_and_independent_appends_are_not_folded(plan_dir):
    old = {"symbol": "600000", "reason": "legacy without source ID"}
    initial = _plan(decisions=[deepcopy(old), deepcopy(old)])
    pe.save_plan(initial)
    first, second = pe.load_plan(), pe.load_plan()
    same_new = {"symbol": "000001", "reason": "anonymous new observation"}
    first["decisions"].append(deepcopy(same_new))
    second["decisions"].append(deepcopy(same_new))
    pe.save_plan(first)
    pe.save_plan(second)
    assert pe.load_plan()["decisions"] == [old, old, same_new, same_new]


def test_source_conflict_does_not_attach_old_projection_receipt(plan_dir):
    pe.save_plan(_plan(exits=[_exit()]))
    stale, latest = pe.load_plan(), pe.load_plan()
    stale["exits"][0].update(event_id=17, event_projected=True)
    latest["exits"][0]["price"] = 12
    pe.save_plan(latest)
    with pytest.raises(pe.PlanConflict, match="source facts"):
        pe.save_plan(stale)
    final = pe.load_plan()["exits"][0]
    assert final["price"] == 12 and "event_id" not in final


@pytest.mark.parametrize("initial,completion", [
    ({"trigger_event_id": 31, "order_id": None}, {"order_id": 114}),
    ({"order_id": 114}, {"trigger_event_id": 31}),
])
def test_exact_trigger_order_identity_can_complete_during_unrelated_append(plan_dir, initial, completion):
    pe.save_plan(_plan(decisions=[{"symbol": "600000", "action": "open_unknown", **initial}]))
    pending, other = pe.load_plan(), pe.load_plan()
    pending["decisions"][0].update(completion, action="open")
    other["decisions"].append({"order_id": 200, "symbol": "000001", "action": "open"})
    pe.save_plan(other)
    pe.save_plan(pending)
    rows = pe.load_plan()["decisions"]
    assert len(rows) == 2
    assert rows[0]["order_id"] == 114 and rows[0]["trigger_event_id"] == 31 and rows[0]["action"] == "open"
    assert rows[1]["order_id"] == 200


def test_same_trigger_conflicting_orders_cannot_be_joined(plan_dir):
    pe.save_plan(_plan(decisions=[{"trigger_event_id": 31, "symbol": "600000", "action": "open_unknown"}]))
    first, second = pe.load_plan(), pe.load_plan()
    first["decisions"][0].update(order_id=114, action="open")
    second["decisions"][0].update(order_id=115, action="open")
    pe.save_plan(first)
    with pytest.raises(pe.PlanConflict, match="identities"):
        pe.save_plan(second)
    assert pe.load_plan()["decisions"][0]["order_id"] == 114


def test_filled_order_survives_unrelated_peak_conflict(plan_dir):
    pe.save_plan(_plan(peaks={"600000": 10}))
    stale, latest = pe.load_plan(), pe.load_plan()
    stale["peaks"]["600000"] = 11
    stale["exits"].append(_exit())
    latest["peaks"]["600000"] = 12
    pe.save_plan(latest)
    with pytest.raises(pe.PlanConflict, match="peak"):
        pe.save_plan(stale)
    pe.preserve_filled_exit(stale["date"], _exit())
    pe.preserve_filled_exit(stale["date"], _exit())
    final = pe.load_plan()
    assert final["peaks"] == {"600000": 12} and final["exits"] == [_exit()]


def test_two_thread_writers_share_short_file_owner_without_lost_append_or_tmp_collision(plan_dir):
    pe.save_plan(_plan())
    barrier = Barrier(2)

    def write(order_id):
        snapshot = pe.load_plan()
        snapshot["exits"].append(_exit(order_id))
        barrier.wait(timeout=3)  # No file lock survives load_plan().
        pe.save_plan(snapshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, order_id) for order_id in (114, 115)]
        for future in futures:
            future.result(timeout=5)
    assert {row["order_id"] for row in pe.load_plan()["exits"]} == {114, 115}
    assert not list(plan_dir.glob("*.tmp"))


@pytest.mark.parametrize("collection", ["decisions", "exits"])
@pytest.mark.parametrize("source_drift", [False, True])
def test_async_reconcile_copies_only_projection_and_merges_latest_identity(plan_dir, monkeypatch,
                                                                         collection, source_drift):
    row = _exit()
    if collection == "decisions":
        row.update(action="open", trigger="buy_point", decision_version="fixture-v1", total_cap=.3, weight=.3)
    pe.save_plan(_plan(**{collection: [row]}))
    entered, release = Event(), Event()
    loop_thread = get_ident()
    original_write = Path.write_text

    def loop_file_write(path, *args, **kwargs):
        if path.parent == plan_dir:
            assert get_ident() == loop_thread, "the projection worker attempted plan file IO"
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", loop_file_write)

    def publisher(copied_plan, copied_row):
        assert get_ident() != loop_thread
        assert copied_row is not row
        assert copied_plan[collection][0] is not copied_row
        entered.set()
        assert release.wait(3)
        copied_row.update(event_id=17, event_projected=True)
        return 17, True

    monkeypatch.setattr(pe if collection == "decisions" else exit_engine,
                        "_publish_open_decision" if collection == "decisions" else "_publish_paper_exit", publisher)

    async def run():
        reconcile = pe.reconcile_position_open_events_async if collection == "decisions" else exit_engine.reconcile_paper_exit_events_async
        running = asyncio.create_task(reconcile())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            current = pe.load_plan()
            current["peaks"]["000001"] = 23.5
            current["decisions"].append({"action": "open", "order_id": 200, "symbol": "000001"})
            if source_drift:
                current[collection][0]["price"] = 12
            pe.save_plan(current)
            release.set()
            assert await running == (0 if source_drift else 1)
        finally:
            release.set()
            if not running.done():
                await running

    asyncio.run(run())
    final = pe.load_plan()
    assert final["peaks"] == {"000001": 23.5}
    assert any(item.get("order_id") == 200 for item in final["decisions"])
    target = final[collection][0]
    if source_drift:
        assert target["price"] == 12 and "event_id" not in target
    else:
        assert target["event_id"] == 17 and target["event_projected"] is True
