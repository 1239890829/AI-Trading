"""事件驱动最小闭环测试（architecture-design §1 E1）。网络全 mock。"""

from __future__ import annotations


import pytest

from app.core.db import get_engine, get_session_factory
from app.events.extract import (
    build_event,
    classify_category,
    classify_certainty,
    classify_source_tier,
    extract_board_direction,
    extract_directions,
    title_fingerprint,
)
from app.events.store import EventStore


@pytest.fixture(autouse=True)
def _create_tables():
    from app.models.watchlist import Base as _B

    _B.metadata.create_all(get_engine())
    yield


def _store() -> EventStore:
    return EventStore(get_session_factory())


def _isolated_store(tmp_path) -> EventStore:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'event-lineage.db'}")
    Base.metadata.create_all(engine)
    return EventStore(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


# ---------------------------------------------------------------- 抽取规则


def test_classify_source_tier_announcement_and_fallback():
    assert classify_source_tier(None, is_announcement=True) == 5
    assert classify_source_tier("财联社快讯") == 4
    assert classify_source_tier("eastmoney") == 3
    assert classify_source_tier("某个未知渠道") == 2


def test_classify_certainty_and_category():
    # 传闻三档联动：rumor 词同时决定 fact_kind 与 certainty
    assert classify_certainty("据悉某大厂拟收购存储厂") == ("rumor", "rumor")
    # 拟议：草案未落地
    assert classify_certainty("美国拟禁止芯片经东南亚转口")[1] == "proposed"
    # 已落地事实
    assert classify_certainty("长鑫 LPDDR6 全球首发量产") == ("fact", "done")
    assert classify_category("美国拟禁止中国 AI 企业采购芯片") == "policy"
    assert classify_category("沃什鹰派发言") == "statement"
    assert classify_category("长鑫 LPDDR6 全球首发量产") == "corporate"


def test_title_fingerprint_dedupes_punctuation():
    a = title_fingerprint("长鑫 LPDDR6 全球首发量产！")
    b = title_fingerprint("长鑫LPDDR6全球首发量产")
    c = title_fingerprint("另一条完全不同的新闻")
    assert a == b != c


def test_extract_directions_alias_and_forbidden_flip():
    names = ["存储芯片", "算力", "信创"]
    # 利好词 + 别名表命中（LPDDR → 存储芯片）
    rows = extract_directions("长鑫 LPDDR6 全球首发量产", names)
    assert [(r["target"], r["direction"]) for r in rows] == [("存储芯片", 1)]
    assert rows[0]["matched_by"] == "alias"
    assert "利好" in rows[0]["basis"] and ("首发" in rows[0]["basis"] or "量产" in rows[0]["basis"])

    # 禁令 + 国产替代题材 → 对冲为利好；禁令 + 租赁属性题材 / 普通算力 → 利空
    rows = extract_directions("美国拟禁止算力租赁及海外算力转口，利好国产信创替代", ["算力", "信创", "算力租赁"])
    by = {r["target"]: r["direction"] for r in rows}
    assert by["算力租赁"] == -1
    assert by["算力"] == -1, "无对冲属性的题材命中利空词 → 利空"
    assert by["信创"] == 1, "禁令对国产替代题材对冲为利好"

    # 无动词命中 → 关联待判（不猜方向）
    rows = extract_directions("粮食概念盘中异动", ["粮食概念"])
    assert rows[0]["direction"] == 0


def test_summary_participates_in_judgement():
    """P0-4：标题被截断时，摘要参与 category / 方向 / 传导链判定。"""
    # 标题丢了「印发规划」政策主体词 → 只用标题判不出 policy
    assert classify_category("北京发布新一轮行动方案") == "other"
    assert (
        classify_category("北京发布新一轮行动方案", summary="印发《商业航天产业规划》，加快发展商业航天")
        == "policy"
    )
    # 题材词与方向词落在摘要里 → 命中且判出利好
    names = ["商业航天", "算力"]
    rows = extract_directions("北京发布新一轮行动方案", names, summary="印发商业航天产业规划，加快发展")
    by = {r["target"]: r["direction"] for r in rows}
    assert by.get("商业航天") == 1, f"摘要题材词应命中并判利好，实际 {rows}"
    assert "算力" not in by, "摘要未提「算力」不应命中"
    # 摘要就是标题复述 → 子串判据跳过，行为一致不报错
    assert classify_category("长鑫量产", summary="长鑫量产") == "corporate"
    # 空摘要 → 退化为仅标题，不报错
    assert classify_category("沃什鹰派发言", summary=None) == "statement"


def test_build_event_completes_all_fields():
    e = build_event("沃什鹰派发言致美股黄金大跌", source="财联社")
    assert e["category"] == "statement" and e["half_life_hours"] == 48
    assert e["source_tier"] == 4 and e["fact_kind"] == "fact"
    assert e["fingerprint"]


def test_extract_board_direction():
    """P0-3 后半：板块代码映射的题材方向行（matched_by=board）。"""
    row = extract_board_direction("大力发展绿色能源产业", "绿色电力", "绿色电力", "BK1024")
    assert row["target"] == "绿色电力" and row["direction"] == 1
    assert row["matched_by"] == "board" and row["target_type"] == "theme"
    assert "BK1024" in row["basis"] and "绿色电力" in row["basis"]
    # 无方向词 → direction=0（关联待判，不猜）
    neutral = extract_board_direction("某板块午后异动", "光伏概念", "光伏概念", "BK0588")
    assert neutral["direction"] == 0


def test_build_event_board_themes():
    """build_event 接收 board_themes → 生成 matched_by=board 行（retro P0-3 后半）。"""
    ev = build_event(
        "某板块午后异动", theme_names=["绿色电力"],
        board_themes=[{"theme_name": "绿色电力", "board_name": "绿色电力", "board_code": "BK1024"}],
    )
    board_rows = [d for d in ev["directions"] if d["matched_by"] == "board"]
    assert len(board_rows) == 1 and board_rows[0]["target"] == "绿色电力"


def test_build_event_dedupes_board_row_colliding_with_text_hit():
    """回归（2026-09-10 事故）：板块映射的题材与**文本命中**的题材相同时只留一行。

    真实样本：「南非七月份黄金产量同比下降7.4%」经 name-stem 命中「黄金概念」，
    东财板块「黄金」又映射到同一个「黄金概念」→ 两行 target 相同 →
    `event_direction` 撞 UNIQUE(event_id,target_type,target) → 事件整条丢失
    （每轮快讯轮询重试都失败）。断言：只剩 1 行，且保留的是**文本证据**那一行。
    """
    ev = build_event(
        "南非七月份黄金产量同比下降7.4%",
        theme_names=["黄金概念"],
        board_themes=[{"theme_name": "黄金概念", "board_name": "黄金", "board_code": "BK1617"}],
    )
    golden = [d for d in ev["directions"] if d["target"] == "黄金概念"]
    assert len(golden) == 1
    # 先到者胜：文本命中排在前（优先级 文本证据 > 板块代码推断）
    assert golden[0]["matched_by"] == "name-stem"
    keys = [(d["target_type"], d["target"]) for d in ev["directions"]]
    assert len(keys) == len(set(keys))  # 整包无重复键（= 不会撞唯一约束）


def test_build_event_keeps_board_row_when_no_text_hit():
    """只有板块代码、没有文本命中时板块行照常生效（P0-3 的 ~35% 快讯不能被去重误伤）。"""
    ev = build_event(
        "某板块午后异动",
        theme_names=["黄金概念"],
        board_themes=[{"theme_name": "黄金概念", "board_name": "黄金", "board_code": "BK1617"}],
    )
    golden = [d for d in ev["directions"] if d["target"] == "黄金概念"]
    assert len(golden) == 1 and golden[0]["matched_by"] == "board"


def test_dedupe_directions_prefers_first_occurrence():
    """纯函数契约：按 (target_type,target) 精确去重、保留先到者；
    target_type 不同不算重复（题材行与个股行可同名）。"""
    from app.events.extract import dedupe_directions

    rows = [
        {"target_type": "theme", "target": "黄金概念", "matched_by": "name-stem"},
        {"target_type": "theme", "target": "黄金概念", "matched_by": "board"},
        {"target_type": "symbol", "target": "600547", "matched_by": "source"},
    ]
    out = dedupe_directions(rows)
    assert [r["matched_by"] for r in out] == ["name-stem", "source"]


# ---------------------------------------------------------------- 存储


def test_store_dedupes_by_fingerprint():
    store = _store()
    title = "某公司签订重大合同中标金额创新高"
    r1, c1 = store.register(title, source="东财")
    r2, c2 = store.register(title + "。", source="东财")
    assert c1 is True and c2 is False
    assert r1.id == r2.id


def test_source_revision_keeps_original_interpretation_and_pauses_consumers(tmp_path):
    store = _isolated_store(tmp_path)
    first = build_event("液冷服务器订单落地", source="东财快讯", summary="订单已签署",
                        source_symbol="301468", theme_names=["液冷服务器"])
    first.update(source_item_id="flash-42", source_symbols=["301468"])
    row, created = store.add_event(first)
    assert created and store.is_active(row)
    original_directions = [(d.target, d.direction, d.observation_id) for d in store.directions_of(row.id)]
    assert original_directions and original_directions[0][2] is not None

    corrected = build_event("液冷服务器订单落地", source="东财快讯", summary="订单尚未签署",
                            source_symbol="301468", theme_names=["液冷服务器"])
    corrected.update(source_item_id="flash-42", source_symbols=["301468"])
    same_row, created = store.add_event(corrected)
    assert not created and same_row.id == row.id
    assert same_row.summary == "订单已签署"
    assert same_row.revision_pending_at is not None
    assert [(d.target, d.direction, d.observation_id) for d in store.directions_of(row.id)] == original_directions
    assert row.id not in {active.id for active in store.list_events(active_only=True, limit=100)}
    observations = store.observations_of(row.id)
    assert [o.change_kind for o in observations] == ["initial", "revision"]
    assert [o.summary for o in observations] == ["订单已签署", "订单尚未签署"]
    assert observations[0].available_at <= observations[1].available_at
    assert observations[1].source_item_id == "flash-42"
    store.add_event(corrected)
    assert len(store.observations_of(row.id)) == 2, "重拉相同修订应幂等"


def test_reviewed_revision_replays_exact_visible_versions(tmp_path):
    import json
    from datetime import timedelta

    store = _isolated_store(tmp_path)
    original = build_event("液冷服务器订单落地", source="东财快讯", summary="订单已签署",
                           theme_names=["液冷服务器"])
    original["source_item_id"] = "revision-1"
    row, _ = store.add_event(original)
    first = store.interpretations_of(row.id)[0]
    assert first.state == "active"
    assert store.interpretation_at(row.id, first.effective_at - timedelta(microseconds=1)) is None

    correction = build_event("液冷服务器订单尚未落地", source="东财快讯", summary="合同尚未签署",
                             theme_names=["液冷服务器"])
    correction["source_item_id"] = "revision-1"
    store.add_event(correction)
    pending = store.interpretations_of(row.id)[1]
    assert pending.state == "pending"
    assert json.loads(pending.payload_json)["directions"] == []
    assert store.interpretation_at(row.id, pending.effective_at).id == pending.id
    assert store.interpretation_at(row.id, first.effective_at).id == first.id

    latest = store.observations_of(row.id)[-1]
    with pytest.raises(ValueError, match="观察版本已变化"):
        store.review_revision(row.id, expected_observation_id=latest.id - 1,
                              action="retain", note="错误版本")
    reviewed = store.review_revision(
        row.id, expected_observation_id=latest.id, action="adopt", note="核对原文与合同公告",
        interpretation={
            "source_tier": 3, "category": "corporate", "fact_kind": "fact",
            "certainty": "done", "half_life_hours": 48,
            "directions": [{"target_type": "theme", "target": "液冷服务器", "direction": -1,
                            "strength": 1, "chain": "未签合同，原订单催化失效", "basis": "人工核对更正",
                            "matched_by": "manual"}],
        },
    )
    assert reviewed.state == "active"
    assert json.loads(reviewed.payload_json)["directions"][0]["direction"] == -1
    assert json.loads(first.payload_json)["directions"][0]["direction"] == 1
    assert store.interpretation_at(row.id, pending.effective_at).state == "pending"
    assert store.get_event(row.id).revision_pending_at is None
    assert store.get_event(row.id).summary == "合同尚未签署"
    assert store.directions_of(row.id)[0].observation_id == latest.id
    assert next(r for r in store.list_events(active_only=True) if r.id == row.id).interpretation_ref["version_id"] == reviewed.id
    with pytest.raises(ValueError, match="无需复核"):
        store.review_revision(row.id, expected_observation_id=latest.id,
                              action="retain", note="重复提交")


def test_review_can_retain_or_withdraw_without_rewriting_prior_version(tmp_path):
    store = _isolated_store(tmp_path)
    event = build_event("某公司公告订单已签署", source="东财快讯", summary="订单已签署")
    event["source_item_id"] = "review-2"
    row, _ = store.add_event(event)
    original = store.interpretations_of(row.id)[0]
    changed = build_event(event["title"], source="东财快讯", summary="合同原文补充说明")
    changed["source_item_id"] = "review-2"
    store.add_event(changed)
    obs = store.observations_of(row.id)[-1]
    retained = store.review_revision(row.id, expected_observation_id=obs.id,
                                     action="retain", note="原判断仍成立")
    assert retained.state == "active" and retained.review_note == "原判断仍成立"
    assert store.get_event(row.id).summary == "订单已签署"
    assert store.interpretations_of(row.id)[0].payload_json == original.payload_json

    withdrawn = build_event(event["title"], source="东财快讯", summary="来源撤回订单消息")
    withdrawn["source_item_id"] = "review-2"
    store.add_event(withdrawn)
    obs = store.observations_of(row.id)[-1]
    result = store.review_revision(row.id, expected_observation_id=obs.id,
                                   action="withdraw", note="来源原文已撤回")
    assert result.state == "withdrawn"
    assert not store.is_active(store.get_event(row.id))
    assert store.interpretation_at(row.id, result.effective_at).state == "withdrawn"


def test_new_source_id_requires_explicit_withdrawal_link_and_preserves_history(tmp_path):
    store = _isolated_store(tmp_path)
    original = build_event("某公司订单已签署", source="东财快讯", summary="订单已签署")
    original["source_item_id"] = "old-1"
    old, _ = store.add_event(original)
    prior = store.interpretations_of(old.id)[-1]
    target = store.observations_of(old.id)[-1]

    notice = build_event("更正：某公司订单并未签署", source="东财快讯", summary="原消息撤回")
    notice["source_item_id"] = "new-2"
    new, _ = store.add_event(notice)
    evidence = store.observations_of(new.id)[-1]
    assert old.id != new.id and store.is_active(store.get_event(old.id))
    assert store.withdrawal_links_of(old.id) == [], "新 ID 本身没有稳定上游关联"

    kwargs = dict(target_observation_id=target.id, notice_observation_id=evidence.id,
                  expected_interpretation_id=prior.id, note="人工核对新旧来源原文：新 ID 明确撤回旧消息")
    link, linked_notice = store.link_withdrawal(old.id, **kwargs)
    versions = store.interpretations_of(old.id)
    assert len(versions) == 2 and versions[-1].state == "withdrawn"
    assert link.prior_interpretation_id == prior.id
    assert link.withdrawn_interpretation_id == versions[-1].id
    assert linked_notice.id == evidence.id
    assert store.interpretation_at(old.id, prior.effective_at).state == "active"
    assert not store.is_active(store.get_event(old.id))
    assert store.get_event(new.id).status == "active", "通知本身保留独立来源身份"
    assert store.withdrawal_links_of(old.id)[0][1].source_item_id == "new-2"
    assert store.link_withdrawal(old.id, **kwargs)[0].id == link.id
    assert len(store.interpretations_of(old.id)) == 2, "超时重试不得重复撤回版本"


def test_withdrawal_link_rejects_wrong_source_stale_version_and_pending_revision(tmp_path):
    store = _isolated_store(tmp_path)
    old_data = build_event("某公司订单已签署", source="东财快讯", summary="订单已签署")
    old_data["source_item_id"] = "old-1"
    old, _ = store.add_event(old_data)
    target = store.observations_of(old.id)[-1]
    prior = store.interpretations_of(old.id)[-1]

    foreign_data = build_event("更正：订单并未签署", source="财联社", summary="来源撤回")
    foreign_data["source_item_id"] = "new-2"
    foreign, _ = store.add_event(foreign_data)
    foreign_obs = store.observations_of(foreign.id)[-1]
    args = dict(target_observation_id=target.id, notice_observation_id=foreign_obs.id,
                expected_interpretation_id=prior.id, note="人工核对")
    with pytest.raises(ValueError, match="同来源"):
        store.link_withdrawal(old.id, **args)
    assert store.get_event(old.id).status == "active"

    notice_data = build_event("更正：某公司订单并未签署", source="东财快讯", summary="原消息撤回")
    notice_data["source_item_id"] = "new-3"
    notice, _ = store.add_event(notice_data)
    args["notice_observation_id"] = store.observations_of(notice.id)[-1].id
    with pytest.raises(ValueError, match="已变化"):
        store.link_withdrawal(old.id, **{**args, "expected_interpretation_id": prior.id + 999})
    changed = build_event(old_data["title"], source="东财快讯", summary="原文又有修订")
    changed["source_item_id"] = "old-1"
    store.add_event(changed)
    with pytest.raises(ValueError, match="待复核"):
        store.link_withdrawal(old.id, **args)
    assert store.withdrawal_links_of(old.id) == []
    assert store.get_event(old.id).status == "active"


def test_withdrawal_link_api_exposes_exact_notice_and_rejects_conflicting_retry(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route
    from app.core.config import settings

    store = _isolated_store(tmp_path)
    old_data = build_event("某公司订单已签署", source="东财快讯")
    old_data["source_item_id"] = "api-old"
    old, _ = store.add_event(old_data)
    notice_data = build_event("更正：某公司订单未签署", source="东财快讯")
    notice_data.update(source_item_id="api-new", url="https://example.test/notice")
    notice, _ = store.add_event(notice_data)
    target = store.observations_of(old.id)[-1]
    evidence = store.observations_of(notice.id)[-1]
    prior = store.interpretations_of(old.id)[-1]
    body = {
        "target_observation_id": target.id,
        "notice_observation_id": evidence.id,
        "expected_interpretation_id": prior.id,
        "note": "人工核对两条来源原文，确认新 ID 撤回旧消息",
    }
    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: store
    monkeypatch.setattr(settings, "api_token", "withdrawal-test-token")
    with TestClient(app) as api:
        path = f"/api/events/{old.id}/link-withdrawal"
        assert api.post(path, json=body).status_code == 401
        assert store.get_event(old.id).status == "active"
        headers = {"X-API-Token": "withdrawal-test-token"}
        result = api.post(path, json=body, headers=headers)
        assert result.status_code == 200, result.text
        link = result.json()["data"]
        assert link["notice_event_id"] == notice.id
        assert link["notice_source_item_id"] == "api-new"
        assert link["notice_url"] == "https://example.test/notice"
        detail = api.get(f"/api/events/{old.id}").json()["data"]
        assert detail["withdrawal_links"] == [link]
        assert detail["interpretations"][-1]["state"] == "withdrawn"
        assert api.post(path, json=body, headers=headers).json()["data"]["id"] == link["id"]
        conflict = api.post(path, json={**body, "note": "另一段依据"}, headers=headers)
        assert conflict.status_code == 409
        assert len(store.interpretations_of(old.id)) == 2


def test_later_corroboration_does_not_displace_pending_review_target(tmp_path):
    store = _isolated_store(tmp_path)
    first = build_event("某公司公告订单已签署", source="东财快讯", summary="订单已签署")
    first["source_item_id"] = "late-1"
    row, _ = store.add_event(first)
    correction = build_event(first["title"], source="东财快讯", summary="订单尚未签署")
    correction["source_item_id"] = "late-1"
    store.add_event(correction)
    correction_id = store.observations_of(row.id)[-1].id
    corroboration = build_event(first["title"], source="财联社", summary="订单已签署")
    corroboration["source_item_id"] = "late-2"
    store.add_event(corroboration)
    assert store.observations_of(row.id)[-1].change_kind == "corroboration"
    assert store.pending_observation_of(row.id).id == correction_id
    version = store.review_revision(row.id, expected_observation_id=correction_id,
                                    action="withdraw", note="更正经原来源核对成立")
    assert version.observation_id == correction_id and version.state == "withdrawn"


def test_backfill_and_status_changes_keep_point_in_time_history(tmp_path):
    import json

    store = _isolated_store(tmp_path)
    row, _ = store.add_event(build_event("某板块午后异动", source="东财快讯"))
    initial = store.interpretations_of(row.id)[0]
    assert json.loads(initial.payload_json)["category"] == "other"
    store.backfill_event(row.id, category="corporate", half_life_hours=36)
    updated = store.interpretations_of(row.id)[-1]
    assert json.loads(updated.payload_json)["category"] == "corporate"
    assert json.loads(initial.payload_json)["category"] == "other"
    store.set_status(row.id, "resolved")
    withdrawn = store.interpretations_of(row.id)[-1]
    assert withdrawn.state == "withdrawn"
    assert store.interpretation_at(row.id, withdrawn.effective_at).state == "withdrawn"


def test_active_event_hit_carries_the_visible_interpretation_id(tmp_path):
    from app.services.picks_pipeline import _build_event_hits_index

    store = _isolated_store(tmp_path)
    event = build_event("某公司订单落地", source="东财快讯", source_symbol="600001")
    event.update(source_item_id="hit-1", source_symbols=["600001"])
    row, _ = store.add_event(event)
    version = store.interpretations_of(row.id)[0]
    hit = _build_event_hits_index(store.list_events(active_only=True))["600001"]
    assert hit[5][0]["event_id"] == row.id
    assert hit[5][0]["version_id"] == version.id
    assert hit[5][0]["observation_id"] == version.observation_id
    second = build_event("某公司获得新订单", source="财联社", source_symbol="600001")
    second.update(source_item_id="hit-2", source_symbols=["600001"])
    row2, _ = store.add_event(second)
    all_hits = _build_event_hits_index(store.list_events(active_only=True))["600001"]
    assert {ref["event_id"] for ref in all_hits[5]} == {row.id, row2.id}, "所有计分事件都须留下版本引用"
    changed = build_event(event["title"], source="东财快讯", summary="订单内容更正",
                          source_symbol="600001")
    changed.update(source_item_id="hit-1", source_symbols=["600001"])
    store.add_event(changed)
    remaining = _build_event_hits_index(store.list_events(active_only=True))["600001"]
    assert {ref["event_id"] for ref in remaining[5]} == {row2.id}


def test_multi_symbol_source_is_visible_for_every_stock_without_imputed_score(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route
    from app.services.picks_pipeline import _build_event_hits_index

    store = _isolated_store(tmp_path)
    event = build_event("甲乙两家公司签约合作", source="东财快讯",
                        source_symbols=["600001", "000002"])
    event["source_item_id"] = "multi-1"
    row, _ = store.add_event(event)
    assert {d.target for d in store.directions_of(row.id) if d.target_type == "symbol"} == {
        "600001", "000002"}
    hits = _build_event_hits_index(store.list_events(active_only=True))
    assert hits["600001"][:2] == (0, 0)
    assert hits["000002"][:2] == (0, 0)
    assert hits["000002"][4] == 1

    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: store
    with TestClient(app) as api:
        detail = api.get(f"/api/events/{row.id}").json()["data"]
        assert detail["observations"][0]["source_symbols"] == ["600001", "000002"]
        second = api.get("/api/events/symbol/000002").json()["data"]
        assert any(it["id"] == row.id and it["match_reason"] == "source"
                   for it in second["items"])
    correction = build_event(event["title"], source="东财快讯", summary="合作条款更正",
                             source_symbols=["600001", "000002"])
    correction["source_item_id"] = "multi-1"
    store.add_event(correction)
    target_id = store.pending_observation_of(row.id).id
    store.review_revision(row.id, expected_observation_id=target_id, action="adopt",
                          note="核对更正后逐股方向仍未知", interpretation={
                              "source_tier": 3, "category": "corporate", "fact_kind": "fact",
                              "certainty": "done", "half_life_hours": 48, "directions": [],
                          })
    assert {(d.target, d.direction) for d in store.directions_of(row.id) if d.target_type == "symbol"} == {
        ("600001", 0), ("000002", 0)}


def test_multi_symbol_manual_review_versions_only_one_stock(tmp_path):
    import json

    from app.services.picks_pipeline import _build_event_hits_index

    store = _isolated_store(tmp_path)
    event = build_event("甲乙公司合作公告", source="东财快讯",
                        source_symbols=["600001", "000002"])
    event["source_item_id"] = "joint-1"
    row, _ = store.add_event(event)
    original = store.interpretations_of(row.id)[0]
    observation = store.observations_of(row.id)[0]
    version = store.review_symbol_direction(
        row.id, expected_observation_id=observation.id,
        expected_interpretation_id=original.id, symbol="600001",
        direction=1, strength=2, chain="已签订单仅对应甲公司收入",
        basis="人工核对来源原文：甲公司承担供货，乙公司仅为框架合作方",
        note="核对甲乙双方公告，乙公司影响仍未知",
    )
    before = json.loads(original.payload_json)["directions"]
    after = json.loads(version.payload_json)["directions"]
    assert {(d["target"], d["direction"]) for d in before if d["target_type"] == "symbol"} == {
        ("600001", 0), ("000002", 0)}
    assert {(d["target"], d["direction"]) for d in after if d["target_type"] == "symbol"} == {
        ("600001", 1), ("000002", 0)}
    assert next(d for d in after if d["target"] == "600001")["matched_by"] == "manual"
    assert version.observation_id == observation.id
    assert store.interpretation_at(row.id, original.effective_at).id == original.id
    hits = _build_event_hits_index(store.list_events(active_only=True))
    assert hits["600001"][0] > 0 and hits["000002"][:2] == (0, 0)
    assert hits["000002"][4] == 1
    with pytest.raises(ValueError, match="版本不匹配"):
        store.review_symbol_direction(
            row.id, expected_observation_id=observation.id,
            expected_interpretation_id=original.id, symbol="000002",
            direction=-1, strength=1, chain="待核", basis="旧版本", note="旧版本",
        )
    assert len(store.interpretations_of(row.id)) == 2
    second = store.review_symbol_direction(
        row.id, expected_observation_id=observation.id,
        expected_interpretation_id=version.id, symbol="000002",
        direction=-1, strength=1, chain="乙公司承担新增成本",
        basis="人工核对乙公司公告的费用条款", note="逐股核对乙公司",
    )
    assert {(d["target"], d["direction"]) for d in json.loads(second.payload_json)["directions"]
            if d["target_type"] == "symbol"} == {("600001", 1), ("000002", -1)}
    assert json.loads(version.payload_json)["directions"] == after
    correction = build_event(event["title"], source="东财快讯", summary="合作范围更正",
                             source_symbols=["600001", "000002"])
    correction["source_item_id"] = "joint-1"
    store.add_event(correction)
    assert not any(r.id == row.id for r in store.list_events(active_only=True))
    assert store.interpretation_at(row.id, second.effective_at).id == second.id
    assert store.interpretations_of(row.id)[-1].state == "pending"


def test_multi_symbol_manual_review_requires_exact_current_source_and_no_pending(tmp_path):
    store = _isolated_store(tmp_path)
    event = build_event("甲乙公司合作公告", source="东财快讯",
                        source_symbols=["600001", "000002"])
    event["source_item_id"] = "joint-2"
    row, _ = store.add_event(event)
    original = store.interpretations_of(row.id)[0]
    observation = store.observations_of(row.id)[0]
    kwargs = dict(expected_observation_id=observation.id,
                  expected_interpretation_id=original.id, symbol="600001",
                  direction=1, strength=2, chain="甲公司获益", basis="核对原公告",
                  note="人工核验")
    with pytest.raises(ValueError, match="未列于"):
        store.review_symbol_direction(row.id, **{**kwargs, "symbol": "300003"})
    other, _ = store.add_event(build_event("另一事件", source="财联社",
                                           source_symbols=["300003", "300004"]))
    with pytest.raises(ValueError, match="不属于"):
        store.review_symbol_direction(row.id, **{**kwargs,
                                                  "expected_observation_id": store.observations_of(other.id)[0].id})
    correction = build_event(event["title"], source="东财快讯", summary="合作范围更正",
                             source_symbols=["600001", "000002"])
    correction["source_item_id"] = "joint-2"
    store.add_event(correction)
    with pytest.raises(ValueError, match="待复核"):
        store.review_symbol_direction(row.id, **kwargs)
    assert len(store.interpretations_of(row.id)) == 2  # initial + pending, no review


def test_multi_symbol_manual_review_preserves_proposed_strength_cap(tmp_path):
    store = _isolated_store(tmp_path)
    event = build_event("甲乙公司拟签约合作", source="东财快讯",
                        source_symbols=["600001", "000002"])
    row, _ = store.add_event(event)
    assert row.certainty == "proposed"
    kwargs = dict(expected_observation_id=store.observations_of(row.id)[0].id,
                  expected_interpretation_id=store.interpretations_of(row.id)[0].id,
                  symbol="600001", direction=1, strength=2,
                  chain="拟议合作可能增收", basis="核对拟议公告", note="只确认拟议性质")
    with pytest.raises(ValueError, match="强度不得超过 1"):
        store.review_symbol_direction(row.id, **kwargs)
    assert len(store.interpretations_of(row.id)) == 1
    version = store.review_symbol_direction(row.id, **{**kwargs, "strength": 1})
    assert version.state == "active"


def test_multi_symbol_manual_review_api_requires_write_token(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route
    from app.core.config import settings

    store = _isolated_store(tmp_path)
    event = build_event("甲乙公司合作公告", source="东财快讯",
                        source_symbols=["600001", "000002"])
    row, _ = store.add_event(event)
    body = {
        "expected_observation_id": store.observations_of(row.id)[0].id,
        "expected_interpretation_id": store.interpretations_of(row.id)[0].id,
        "symbol": "000002", "direction": -1, "strength": 2,
        "chain": "乙公司承担额外成本", "basis": "核对乙公司公告成本条款",
        "note": "人工确认乙公司单独受损",
    }
    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: store
    monkeypatch.setattr(settings, "api_token", "symbol-review-test-token")
    with TestClient(app) as api:
        path = f"/api/events/{row.id}/review-symbol-direction"
        assert api.post(path, json=body).status_code == 401
        headers = {"X-API-Token": "symbol-review-test-token"}
        assert api.post("/api/events/999999/review-symbol-direction",
                        json=body, headers=headers).status_code == 404
        response = api.post(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        version = response.json()["data"]
        assert any(d["target"] == "000002" and d["direction"] == -1
                   for d in version["payload"]["directions"])
        detail = api.get(f"/api/events/{row.id}").json()["data"]
        assert {(d["target"], d["direction"]) for d in detail["directions"] if d["target_type"] == "symbol"} == {
            ("600001", 0), ("000002", -1)}
        assert next(d for d in detail["directions"] if d["target"] == "000002")["matched_by"] == "manual"
        assert detail["judge_status"] == "judged"
        assert detail["judged_at"] is None and "人工逐股审定" in detail["judge_reason"]
        assert api.post(path, json=body, headers=headers).status_code == 409
        assert len(store.interpretations_of(row.id)) == 2


def test_same_source_id_changed_title_and_late_publication_stay_linked(tmp_path):
    from datetime import datetime

    store = _isolated_store(tmp_path)
    original = build_event("某公司宣布完成收购", source="东财快讯")
    original.update(source_item_id="flash-99", source_published_at=datetime(2026, 9, 1, 9, 0))
    row, _ = store.add_event(original)
    revised = build_event("某公司否认完成收购", source="东财快讯")
    revised.update(source_item_id="flash-99", source_published_at=datetime(2026, 9, 1, 9, 0))
    linked, created = store.add_event(revised)
    assert not created and linked.id == row.id and linked.revision_pending_at is not None
    observations = store.observations_of(row.id)
    assert len(observations) == 2
    assert observations[0].source_published_at < observations[0].received_at
    assert observations[0].available_at == observations[0].received_at


def test_same_title_multiple_sources_preserves_both_observations(tmp_path):
    store = _isolated_store(tmp_path)
    event = build_event("某公司公告中标项目", source="东财快讯", summary="已中标")
    event.update(source_item_id="east-1")
    row, _ = store.add_event(event)
    other = build_event("某公司公告中标项目", source="财联社", summary="已中标")
    other.update(source_item_id="cls-1")
    again, created = store.add_event(other)
    assert not created and again.id == row.id and again.revision_pending_at is None
    assert {o.source for o in store.observations_of(row.id)} == {"东财快讯", "财联社"}


def test_store_survives_duplicate_direction_rows():
    """回归（2026-09-10 事故）：事件 dict 内方向行 (target_type,target) 重复时，
    入库必须**成功**而不是把整条事件丢掉（旧行为：撞 UNIQUE → 兜底查不到 fingerprint
    → raise → 事件丢失 + 每轮重试都失败）。

    这里绕过 build_event 直接构造 event dict，验证 store 层的最后一道防线
    （别的生产者直接构造时也不会丢数据）。
    """
    store = _store()
    ev = build_event("某板块午后异动", theme_names=["黄金概念"])
    ev["directions"] = [
        {"target_type": "theme", "target": "黄金概念", "matched_by": "name"},
        {"target_type": "theme", "target": "黄金概念", "matched_by": "board"},
    ]
    row, created = store.add_event(ev)
    assert created is True
    dirs = store.directions_of(row.id)
    assert len(dirs) == 1 and dirs[0].target == "黄金概念"


def test_store_list_active_respects_half_life():
    from datetime import timedelta
    from app.core.db import utcnow

    store = _store()
    # 半衰期 2h 的传闻事件，发布于 5h 前 → 过期
    old = store.register("据悉某公司酝酿重大资产重组", source="东财")[0]
    with store._sf() as db:
        from app.models.event import EventCard

        row = db.get(EventCard, old.id)
        row.published_at = utcnow() - timedelta(hours=5)
        row.half_life_hours = 2
        db.commit()
    assert store.is_active(store.get_event(old.id)) is False
    assert store.get_event(old.id) not in store.list_events(active_only=True)
    # 人工裁决 resolved 也算非活跃
    fresh = store.register("另一家公司发布业绩预增公告", source="东财")[0]
    store.set_status(fresh.id, "resolved")
    assert store.is_active(store.get_event(fresh.id)) is False


# ---------------------------------------------------------------- API


def test_events_api_lifecycle(client, monkeypatch: pytest.MonkeyPatch):
    # 目录服务替换为可控实例（注册时用于实体匹配）
    from app.services.theme_catalog_service import ThemeCatalogService

    svc = ThemeCatalogService(get_session_factory(), api_key="t")

    async def fake_catalog():
        # 用独立代码 990001.TI：886042.TI 会被 test_theme_catalog 的 stale 用例使用，
        # 共享内存库下避免跨文件污染（本测试的 stocks 懒同步会给它写入成分）
        return [{"code": "990001.TI", "name": "存储芯片"}]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    client.app.state.theme_catalog = svc
    client.portal.call(svc.sync_catalog)

    # 手动注册：方向抽取依赖目录名
    r = client.post("/api/events", json={"title": "长鑫 LPDDR6 全球首发量产", "source": "财联社"})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["created"] is True
    assert data["category"] == "corporate"
    assert data["directions"][0]["target"] == "存储芯片"
    assert data["directions"][0]["direction"] == 1
    eid = data["id"]
    detail = client.get(f"/api/events/{eid}").json()["data"]
    assert len(detail["observations"]) == 1
    assert detail["directions"][0]["observation_id"] == detail["observations"][0]["id"]

    # 重复注册 → created=False
    r2 = client.post("/api/events", json={"title": "长鑫 LPDDR6 全球首发量产"})
    assert r2.json()["data"]["created"] is False

    # 列表
    r = client.get("/api/events")
    assert any(i["id"] == eid for i in r.json()["data"]["items"])

    # 标的池：成分未同步 → 懒同步（mock fetch_members）→ 池返回
    async def fake_members(code):
        return [{"symbol": "000019", "name": "深粮控股"}] if False else [
            {"symbol": "600171", "name": "上海贝岭"}
        ]

    monkeypatch.setattr(svc, "fetch_members", fake_members)
    r = client.get(f"/api/events/{eid}/stocks")
    pools = r.json()["data"]["pools"]
    assert pools[0]["target"] == "存储芯片"
    assert any(s["symbol"] == "600171" for s in pools[0]["stocks"])

    # 人工裁决（2026-09-08 P0-4 起 /events/{id}/review 端点已删——
    # store.set_status 是唯一裁决路径，store 层语义由上面的 storage 用例锚定）
    store = client.app.state.event_store
    store.set_status(eid, "rejected")
    assert store.get_event(eid).status == "rejected"
    r = client.get("/api/events")
    assert all(i["id"] != eid for i in r.json()["data"]["items"]), "rejected 不再出现在活跃列表"

    # 非法状态在 store 层拒绝
    import pytest as _pytest

    with _pytest.raises(ValueError):
        store.set_status(eid, "whatever")


def test_revision_detail_is_visible_while_opportunity_pool_is_paused(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route
    from app.api.deps import require_write_token

    store = _isolated_store(tmp_path)
    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: store
    app.dependency_overrides[require_write_token] = lambda: None
    original = build_event("某云厂商液冷机柜订单落地", source="东财快讯", summary="订单已签署",
                           source_symbol="301468")
    original.update(source_item_id="detail-1", source_symbols=["301468"])
    row, _ = store.add_event(original)
    corrected = build_event("某云厂商液冷机柜订单落地", source="东财快讯", summary="订单尚未签署",
                            source_symbol="301468")
    corrected.update(source_item_id="detail-1", source_symbols=["301468", "688496"])
    store.add_event(corrected)

    with TestClient(app) as api:
        detail = api.get(f"/api/events/{row.id}").json()["data"]
        assert detail["revision_pending_at"] and not detail["is_active"]
        assert detail["judge_status"] == "unknown"
        assert [o["summary"] for o in detail["observations"]] == ["订单已签署", "订单尚未签署"]
        assert detail["observations"][1]["source_symbols"] == ["301468", "688496"]
        assert detail["pending_review_observation_id"] == detail["observations"][-1]["id"]
        assert [v["state"] for v in detail["interpretations"]] == ["active", "pending"]
        pending_at = detail["interpretations"][-1]["effective_at"]
        replay = api.get(f"/api/events/{row.id}/interpretation", params={"as_of": pending_at})
        assert replay.status_code == 200 and replay.json()["data"]["state"] == "pending"
        pool = api.get(f"/api/events/{row.id}/stocks").json()
        assert pool["data"]["pools"] == []
        assert "未复核" in pool["meta"]["note"]
        review = api.post(f"/api/events/{row.id}/review-revision", json={
            "expected_observation_id": detail["observations"][-1]["id"],
            "action": "retain", "note": "核对后原结论仍成立",
        })
        assert review.status_code == 200 and review.json()["data"]["state"] == "active"
        replay = api.get(f"/api/events/{row.id}/interpretation", params={"as_of": pending_at})
        assert replay.json()["data"]["state"] == "pending", "复核不能改写过去的待审窗口"
        repeated = api.post(f"/api/events/{row.id}/review-revision", json={
            "expected_observation_id": detail["observations"][-1]["id"],
            "action": "retain", "note": "重复",
        })
        assert repeated.status_code == 409


def test_events_for_symbol_api(client, monkeypatch: pytest.MonkeyPatch):
    """E2：详情页事件标签——按官方归属题材 + source_symbol 两条路径匹配。"""
    from app.services.theme_catalog_service import ThemeCatalogService

    svc = ThemeCatalogService(get_session_factory(), api_key="t")

    async def fake_catalog():
        return [{"code": "990001.TI", "name": "存储芯片"}]

    async def fake_members(code):
        if code == "990001.TI":
            return [{"symbol": "600171", "name": "上海贝岭"}]
        return []

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    client.app.state.theme_catalog = svc
    client.portal.call(svc.sync_catalog)
    client.portal.call(svc.sync_members, "990001.TI")

    # 事件 A：方向命中 600171 的归属题材（标题用变体，避免与前面用例指纹去重）
    r = client.post("/api/events", json={"title": "长鑫 LPDDR6 量产全面爬坡"})
    assert r.json()["data"]["created"] is True
    # 事件 B：来源个股即 600171（source_symbol 路径）
    r = client.post("/api/events", json={"title": "上海贝岭发布业绩预增公告", "source_symbol": "600171"})
    assert r.json()["data"]["created"] is True
    # 事件 C：题材无关（不应命中）
    client.post("/api/events", json={"title": "某机场旅客吞吐量创新高"})

    r = client.get("/api/events/symbol/600171")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["themes"] == ["存储芯片"]
    ids = {i["id"] for i in data["items"]}
    assert len(ids) == 2, "题材命中 + source_symbol 两条路径各命中一个"
    reasons = {i["match_reason"] for i in data["items"]}
    assert reasons == {"theme", "source"}

    # 非法代码
    assert client.get("/api/events/symbol/xyz").status_code == 400


# ---------------------------------------------------------------- G3 方向词典 v2（hotspot-pipeline §3 固定测试集子集）

def test_g3_benefits_word_drives_direction():
    """「受益」进 _POSITIVE：厄尔尼诺→电力/化肥 实测 dir=0 的修复。"""
    names = ["电力", "磷肥及磷化工"]
    rows = extract_directions("厄尔尼诺超强推升粮价，化肥电力受益", names)
    assert rows, "题材必须命中"
    assert all(r["direction"] == 1 for r in rows), "「受益」应给出利好方向"


def test_g3_paper_level_proposed():
    """「论文/实验室」进 _PROPOSED：长存学术消息 certainty=proposed（研究阶段非落地）。"""
    fact_kind, certainty = classify_certainty("长存 3D NAND 论文披露绕过 EUV 的实验室路径")
    assert (fact_kind, certainty) == ("fact", "proposed")


def test_g3_progress_word():
    """「进展」进 _POSITIVE：取得进展类消息方向可判。"""
    rows = extract_directions("国产存储芯片技术取得进展", ["存储芯片"])
    assert rows and rows[0]["direction"] == 1
