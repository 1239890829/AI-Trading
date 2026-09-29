import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from scripts.rsh031_forward_capture import fetch_pool, prepare_morning, validate_clock, write_once


def test_morning_snapshot_keeps_stale_st_and_bad_price_as_explicit_unknowns():
    now = datetime(2026, 9, 30, 1, 57, tzinfo=timezone.utc)
    members = [{"thscode": f"00000{i}.SZ", "industry": "示例行业"} for i in range(4)]
    rows = []
    for index, member in enumerate(members):
        rows.append({"thscode": member["thscode"], "received_at": now.isoformat(),
                     "source_at": (now - timedelta(seconds=30 if index != 1 else 200)).isoformat(),
                     "price": 11.0 if index != 3 else None, "prev_close": 10.0,
                     "name": "ST示例" if index == 2 else "示例", "source": "tencent"})
    result, coverage = prepare_morning(rows, members, "2026-09-30", now)
    assert [row["exclusion"] for row in result] == [None, "missing_or_stale_quote",
                                                     "st_name", "invalid_price"]
    assert result[0]["early_return"] == pytest.approx(0.1)
    assert [row["early_return"] for row in result[1:]] == [None, None, None]
    assert coverage["usable"] == 1
    assert coverage["complete_for_research"] is False


def test_quote_fresh_when_received_can_be_stale_at_decision():
    received = datetime(2026, 9, 30, 1, 55, tzinfo=timezone.utc)
    decision = received + timedelta(minutes=3)
    rows = [{"thscode": "000001.SZ", "received_at": received.isoformat(),
             "source_at": received.isoformat(), "price": 11.0,
             "prev_close": 10.0, "name": "平安银行", "source": "tencent"}]
    result, coverage = prepare_morning(
        rows, [{"thscode": "000001.SZ", "industry": "银行"}], "2026-09-30", decision)
    assert result[0]["exclusion"] == "missing_or_stale_quote"
    assert result[0]["early_return"] is None
    assert coverage["usable"] == 0


def test_capture_clock_requires_short_beijing_windows():
    validate_clock(datetime(2026, 9, 30, 1, 56, tzinfo=timezone.utc), "morning")
    validate_clock(datetime(2026, 9, 30, 7, 30, tzinfo=timezone.utc), "close")
    with pytest.raises(ValueError, match="outside"):
        validate_clock(datetime(2026, 9, 30, 7, 30, tzinfo=timezone.utc), "morning")
    with pytest.raises(ValueError, match="timezone"):
        validate_clock(datetime(2026, 9, 30, 9, 56), "morning")


def test_close_pool_rejects_wrong_day_or_truncated_page():
    class Provider:
        def __init__(self, data):
            self.data = data

        async def _get_json(self, _url, _params):
            return self.data

    with pytest.raises(ValueError, match="date mismatch"):
        asyncio.run(fetch_pool(Provider({"rc": 0, "data": {"qdate": "20260929",
                                                          "tc": 0, "pool": []}}),
                               "2026-09-30", "limit-up-pool"))
    with pytest.raises(ValueError, match="count mismatch"):
        asyncio.run(fetch_pool(Provider({"rc": 0, "data": {"qdate": "20260930",
                                                          "tc": 1, "pool": []}}),
                               "2026-09-30", "limit-up-pool"))


def test_research_evidence_never_overwrites(tmp_path):
    path = tmp_path / "day" / "morning.json"
    write_once(path, "first")
    with pytest.raises(FileExistsError):
        write_once(path, "replacement")
    assert path.read_text() == "first"
