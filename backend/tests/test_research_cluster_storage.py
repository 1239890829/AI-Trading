"""Immutable files, concurrent publication and no historical source backfill."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import json

import pytest

from app.services import research_cluster_storage as storage
from app.services.research_clusters import build_research_clusters, canonical


DAY = "2026-10-28"
CLOCK = "2026-10-28T14:42:00+08:00"


def payload(*, observed_at=CLOCK, reason="人形机器人+汽车零部件+“羊”字辈"):
    return build_research_clusters([{"symbol": "600001", "name": "来源公司", "reason": reason,
                                    "source": "ths", "trade_date": DAY, "change_pct": 3.5, "quality": "high"}],
                                   trade_date=DAY, observed_at=observed_at,
                                   resolution={"state": "available", "identities": {}, "ambiguous": []})


@pytest.fixture(autouse=True)
def current_clock(monkeypatch):
    monkeypatch.setattr(storage, "beijing_now", lambda: datetime.fromisoformat("2026-10-28T15:00:00+08:00"))


def test_first_clock_and_original_bytes_preserved_on_same_content_reobservation(tmp_path):
    original = payload()
    before = deepcopy(original)
    first = storage.publish_research_clusters(original, root=tmp_path)
    snapshot_path = tmp_path / DAY / f"{first['version']}.json"
    first_bytes = snapshot_path.read_bytes()
    later = payload(observed_at="2026-10-28T14:50:00+08:00")
    assert later["version"] == first["version"]
    second = storage.publish_research_clusters(later, root=tmp_path)
    assert second["observed_at"] == CLOCK
    assert snapshot_path.read_bytes() == first_bytes
    assert original == before
    saved = storage.read_research_clusters(DAY, root=tmp_path)
    assert saved["reference_mode"] == "saved" and saved["observed_at"] == CLOCK
    assert saved["version"] == first["version"]
    assert len(list((tmp_path / DAY).glob("[0-9a-f]" * 64 + ".json"))) == 1


def test_concurrent_same_content_has_one_complete_immutable_snapshot(tmp_path):
    first = payload()
    def publish(_):
        return storage.publish_research_clusters(first, root=tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(publish, range(12)))
    assert {row["version"] for row in results} == {first["version"]}
    assert storage.read_research_clusters(DAY, root=tmp_path)["version"] == first["version"]
    files = list((tmp_path / DAY).glob("*.json"))
    assert sorted(path.name for path in files) == [f"{first['version']}.json", "latest.json"]
    assert not list((tmp_path / DAY).glob(".publish-*"))


def test_content_change_and_reversion_keep_all_versions_and_explicit_reads(tmp_path):
    old = storage.publish_research_clusters(payload(), root=tmp_path)
    newer = storage.publish_research_clusters(payload(observed_at="2026-10-28T14:43:00+08:00",
                                                       reason="其他源标签"), root=tmp_path)
    assert old["version"] != newer["version"]
    assert storage.read_research_clusters(DAY, root=tmp_path)["version"] == newer["version"]
    assert storage.read_research_clusters(DAY, version=old["version"], root=tmp_path)["version"] == old["version"]
    storage.publish_research_clusters(payload(observed_at="2026-10-28T14:44:00+08:00"), root=tmp_path)
    latest = storage.read_research_clusters(DAY, root=tmp_path)
    assert latest["version"] == old["version"] and latest["observed_at"] == CLOCK
    assert len(list((tmp_path / DAY).glob("*.json"))) == 3


def test_delayed_older_publication_does_not_roll_latest_back(tmp_path):
    newer = storage.publish_research_clusters(payload(observed_at="2026-10-28T14:50:00+08:00",
                                                       reason="新来源标签"), root=tmp_path)
    storage.publish_research_clusters(payload(), root=tmp_path)
    assert storage.read_research_clusters(DAY, root=tmp_path)["version"] == newer["version"]


def test_saved_read_does_not_recompute_or_touch_files(tmp_path, monkeypatch):
    first = storage.publish_research_clusters(payload(), root=tmp_path)
    paths = sorted(path for path in (tmp_path / DAY).iterdir() if path.is_file())
    before = {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
    monkeypatch.setattr(storage, "beijing_now", lambda: datetime.fromisoformat("2026-11-02T15:00:00+08:00"))
    assert storage.read_research_clusters(DAY, root=tmp_path)["version"] == first["version"]
    after = {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
    assert after == before


def test_absent_day_and_missing_explicit_version_are_not_corrupt(tmp_path):
    missing = storage.read_research_clusters(DAY, root=tmp_path)
    assert missing["state"] == "not_recorded" and missing["version"] is None
    assert not tmp_path.joinpath(DAY).exists()
    missing = storage.read_research_clusters(DAY, version="a" * 64, root=tmp_path)
    assert missing["state"] == "not_recorded" and missing["missing"] == ["snapshot_version_missing"]


def test_tampered_snapshot_is_unavailable_and_never_silently_overwritten(tmp_path):
    first = storage.publish_research_clusters(payload(), root=tmp_path)
    path = tmp_path / DAY / f"{first['version']}.json"
    raw = json.loads(path.read_text())
    raw["payload"]["clusters"][0]["stocks"][0]["name"] = "篡改"
    path.write_bytes(canonical(raw))
    corrupted = path.read_bytes()
    assert storage.read_research_clusters(DAY, root=tmp_path)["state"] == "unavailable"
    with pytest.raises(ValueError):
        storage.publish_research_clusters(payload(), root=tmp_path)
    assert path.read_bytes() == corrupted


def test_bad_pointer_and_interrupted_publication_are_not_reported_as_no_record(tmp_path):
    first = storage.publish_research_clusters(payload(), root=tmp_path)
    pointer = tmp_path / DAY / "latest.json"
    pointer.unlink()
    assert storage.read_research_clusters(DAY, root=tmp_path)["missing"] == ["snapshot_pointer_missing"]
    assert storage.read_research_clusters(DAY, version=first["version"], root=tmp_path)["state"] == "partial"
    pointer.write_text('{"version":"../escape"}')
    assert storage.read_research_clusters(DAY, root=tmp_path)["missing"] == ["snapshot_corrupt_or_unreadable"]


@pytest.mark.parametrize("selected_at", ["2026-10-27T14:42:00+08:00", "2026-10-28T14:41:00+08:00",
                                        "2026-10-28T16:00:00+08:00"])
def test_pointer_wrong_day_before_first_observation_or_future_is_corrupt(tmp_path, selected_at):
    storage.publish_research_clusters(payload(), root=tmp_path)
    path = tmp_path / DAY / "latest.json"
    value = json.loads(path.read_text())
    value["selected_at"] = selected_at
    path.write_bytes(canonical(value))
    before = path.read_bytes()
    result = storage.read_research_clusters(DAY, root=tmp_path)
    assert result["state"] == "unavailable" and result["missing"] == ["snapshot_corrupt_or_unreadable"]
    with pytest.raises(ValueError):
        storage.publish_research_clusters(payload(observed_at="2026-10-28T14:50:00+08:00"), root=tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("day,observed", [
    ("2026-10-27", "2026-10-27T14:42:00+08:00"),
    ("2026-10-27", CLOCK),
    (DAY, "2026-10-28T16:00:00+08:00"),
])
def test_no_history_backfill_or_future_observation_even_with_valid_content(tmp_path, day, observed):
    value = build_research_clusters([], trade_date=day, observed_at=observed,
                                    resolution={"state": "available", "identities": {}})
    with pytest.raises(ValueError, match="cannot backfill"):
        storage.publish_research_clusters(value, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("day,version", [("../escape", None), (DAY, "../escape"), (DAY, "a" * 63)])
def test_path_inputs_are_validated_before_filesystem_read(tmp_path, day, version):
    with pytest.raises(ValueError):
        storage.read_research_clusters(day, version=version, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_symlink_snapshot_is_not_read_or_replaced(tmp_path):
    first = storage.publish_research_clusters(payload(), root=tmp_path)
    path = tmp_path / DAY / f"{first['version']}.json"
    original = path.read_bytes()
    external = tmp_path / "outside.json"
    external.write_bytes(original)
    path.unlink()
    path.symlink_to(external)
    assert storage.read_research_clusters(DAY, root=tmp_path)["state"] == "unavailable"
    with pytest.raises(ValueError):
        storage.publish_research_clusters(payload(), root=tmp_path)
    assert external.read_bytes() == original
