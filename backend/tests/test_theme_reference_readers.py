"""Current directory references never rewrite saved decisions or review facts."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.api.routes import picks, picks_intraday, review


class Directory:
    revision = 0

    def __init__(self, *, fail_industry=False):
        self.fail_industry = fail_industry
        self.calls = []

    async def read_board_catalog(self, kind):
        self.calls.append(kind)
        if kind == "industry" and self.fail_industry:
            raise RuntimeError("source unavailable")
        code, name = (("885517.TI", "机器人概念") if kind == "concept"
                      else ("881126.TI", "汽车零部件"))
        return {"kind": kind, "source": "ths_official", "total": 1,
                "catalog_state": "ready", "synced_at": "2026-10-27T08:00:00+00:00",
                "items": [{"code": code, "name": name, "kind": kind,
                           "source": "ths_official"}]}


def _report_reader(tmp_path, monkeypatch):
    original = {"trade_date": "20261023", "generated_at": "2026-10-23T16:00:00+08:00",
                "data": {"market": {"themes": [], "reported_values": {"themes": [
                    {"theme": "汽车零部件", "raw_tags": ["汽车零部件", "“羊”字辈", "机器人概念"],
                     "board": {"change_pct": None}, "strength_score": 23.5}]},
                    "provenance": {"themes": {"state": "unverified"}}}},
                "dimensions": [], "action_items": [], "summary": "原记录"}
    saved = tmp_path / "original-review.json"
    saved.write_text(json.dumps(original, ensure_ascii=False))
    before = hashlib.sha256(saved.read_bytes()).hexdigest()
    monkeypatch.setattr(review, "_service", lambda _: SimpleNamespace(session_factory=object()))
    monkeypatch.setattr(review, "get_report", lambda *_: SimpleNamespace(
        model_dump=lambda: json.loads(saved.read_text())))
    monkeypatch.setattr(review, "read_metric_history_reference", lambda *_: {"state": "missing"})
    return original, saved, before


@pytest.mark.parametrize("fail_industry", [False, True])
def test_review_reference_is_separate_from_old_unverified_report(client, tmp_path, monkeypatch, fail_industry):
    original, saved, before = _report_reader(tmp_path, monkeypatch)
    directory = Directory(fail_industry=fail_industry)
    monkeypatch.setattr(client.app.state, "theme_catalog", directory, raising=False)
    response = client.get("/api/review/reports/20261023")
    assert response.status_code == 200
    report = response.json()["data"]
    assert report["data"] == original["data"]
    assert report["generated_at"] == original["generated_at"]
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == before
    reference = report["current_theme_catalog"]
    assert reference["scope"] == "current_directory_reference"
    assert [row["theme"] for row in reference["items"]] == ["汽车零部件", "“羊”字辈", "机器人概念"]
    assert sorted(directory.calls) == ["concept", "industry"]
    rows = {row["theme"]: row for row in reference["items"]}
    if fail_industry:
        assert all(row["catalog_mapping_state"] == "unavailable" and row["exact_catalog"] is None
                   for row in rows.values())
    else:
        assert rows["汽车零部件"]["exact_catalog"]["code"] == "881126.TI"
        assert rows["汽车零部件"]["exact_catalog"]["kind"] == "industry"
        assert rows["机器人概念"]["exact_catalog"]["kind"] == "concept"
        assert rows["“羊”字辈"]["catalog_mapping_state"] == "unmapped"
    assert all("change_pct" not in row and "members" not in row for row in rows.values())


def test_daily_reference_does_not_change_original_selection_or_score(client, monkeypatch):
    original = {"date": "2026-10-23", "stale": True, "items": [
        {"symbol": "600001", "theme": "汽车零部件", "score": 61.2},
        {"symbol": "600002", "theme": "“羊”字辈", "score": 59.5}], "meta": {"gate": {"stand_aside": True}}}
    frozen = deepcopy(original)
    monkeypatch.setattr(picks, "beijing_today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(picks, "_read_today_selection", lambda *_: deepcopy(original))
    async def style(*_):
        return {"phase_source": "unavailable"}
    async def gates(_request, _hub, meta):
        return meta
    monkeypatch.setattr(picks, "_live_style_routing", style)
    monkeypatch.setattr(picks, "_attach_gates", gates)
    monkeypatch.setattr(picks, "_attach_latest_execution", lambda items, *_: items)
    directory = Directory()
    monkeypatch.setattr(client.app.state, "theme_catalog", directory, raising=False)
    response = client.get("/api/picks/today")
    assert response.status_code == 200
    data = response.json()["data"]
    assert original == frozen and data["items"] == frozen["items"]
    assert data["date"] == "2026-10-23" and data["stale"] is True
    assert data["meta"]["gate"] == frozen["meta"]["gate"]
    reference = data["current_theme_catalog"]
    assert reference["scope"] == "current_directory_reference"
    assert reference["items"][0]["exact_catalog"]["code"] == "881126.TI"
    assert reference["items"][1]["exact_catalog"] is None
    assert sorted(directory.calls) == ["concept", "industry"]


def test_missing_review_does_not_collect_a_directory(client, monkeypatch):
    directory = Directory()
    monkeypatch.setattr(client.app.state, "theme_catalog", directory, raising=False)
    monkeypatch.setattr(review, "_service", lambda _: SimpleNamespace(session_factory=object()))
    monkeypatch.setattr(review, "get_report", lambda *_: None)
    assert client.get("/api/review/reports/20261023").status_code == 404
    assert directory.calls == []


def test_intraday_cards_reuse_their_parent_reference_without_another_directory_read(client, monkeypatch):
    from app.picks import intraday_opportunity
    from app.services import official_match, tradingagents_research

    reference = {"scope": "current_directory_reference", "catalog_version": "a" * 64,
                 "observed_at": "2026-10-27T08:00:00+00:00", "items": [
                     {"theme": "汽车零部件", "catalog_mapping_state": "exact", "exact_catalog": {
                         "name": "汽车零部件", "code": "881126.TI", "kind": "industry",
                         "source": "ths_official", "basis": "exact_name", "catalog_version": "a" * 64,
                         "observed_at": "2026-10-27T08:00:00+00:00"}}]}
    parent = {"data": {"themes": [], "current_theme_catalog": reference}}
    original = deepcopy(parent)
    async def day(_hub):
        return date(2026, 10, 27)
    async def build(_request, _trade_date, _top_themes, _stocks_per_theme):
        return parent
    async def forbidden(_holder, _names, *, resolution=None):
        pytest.fail("The intraday child must reuse its parent mapping, not read a second catalog")
    monkeypatch.setattr(picks_intraday, "default_trade_date", day)
    monkeypatch.setattr(picks_intraday, "_build_opportunities", build)
    monkeypatch.setattr(intraday_opportunity, "top_watch_stocks", lambda _payload, *, limit=None: {
        "items": [{"symbol": "600001", "theme": "汽车零部件"}], "reference_items": []})
    monkeypatch.setattr(tradingagents_research, "attach_saved_summaries", lambda items, *, session_factory=None: items)
    monkeypatch.setattr(official_match, "current_catalog_reference", forbidden)
    response = client.get("/api/picks/intraday-top")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current_theme_catalog"] == reference
    assert data["items"] == [{"symbol": "600001", "theme": "汽车零部件"}]
    assert parent == original
