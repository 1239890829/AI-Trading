"""Completeness checks for the bounded public disclosure collector."""
from datetime import date, datetime

import pytest

from app.core.bjtime import BJ_TZ
from scripts.rsh031_cninfo_notices import collect_day


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, pages):
        self.pages = iter(pages)

    def post(self, *args, **kwargs):
        return Response(next(self.pages))


def page(ids, total, more, reported_pages=1):
    source_time = datetime(2025, 10, 9, tzinfo=BJ_TZ)
    return {"announcements": [{"announcementId": item,
                                 "announcementTime": int(source_time.timestamp() * 1000)}
                                for item in ids],
            "totalAnnouncement": total, "totalpages": reported_pages, "hasMore": more}


def test_collects_last_page_even_when_reported_page_count_is_too_small(tmp_path):
    session = Session([page(["a"], 2, True), page(["b"], 2, False)])
    result = collect_day(date(2025, 10, 9), tmp_path / "audit", session)
    assert result["page_count"] == 2
    assert result["announcements"] == 2
    assert (tmp_path / "audit" / "pages.jsonl").read_text().count("\n") == 2


@pytest.mark.parametrize("pages", [
    [page(["a"], 2, False)],
    [page(["a"], 2, True), page(["a"], 2, False)],
    [page(["a"], 2, True), page([], 2, True)],
    [page(["a"], 2, True), page(["b"], 3, False)],
])
def test_rejects_incomplete_or_drifting_pages_without_manifest(tmp_path, pages):
    with pytest.raises(ValueError):
        collect_day(date(2025, 10, 9), tmp_path / "audit", Session(pages))
    assert not (tmp_path / "audit" / "manifest.json").exists()


def test_rejects_source_date_fallback(tmp_path):
    wrong = page(["a"], 1, False)
    wrong["announcements"][0]["announcementTime"] -= 86400000
    with pytest.raises(ValueError, match="another date"):
        collect_day(date(2025, 10, 9), tmp_path / "audit", Session([wrong]))
