"""Fail closed on incomplete historical identity and support safe resume."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from scripts.rsh031_baostock_status import collect_status


class Result:
    error_code = "0"

    def __init__(self, rows):
        self.rows = iter(rows)

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return self.current


class Api:
    def __init__(self, by_code):
        self.by_code = by_code
        self.queried = []

    def login(self):
        return SimpleNamespace(error_code="0")

    def logout(self):
        pass

    def query_history_k_data_plus(self, code, *args, **kwargs):
        self.queried.append(code)
        return Result(self.by_code[code])


def test_resume_skips_finished_code_and_keeps_unknown_explicit(tmp_path):
    output = tmp_path / "status.jsonl"
    codes = ["000001.SZ", "600001.SH"]
    api = Api({"sz.000001": [["2025-10-09", "sz.000001", "", "0"]],
               "sh.600001": [["2025-10-09", "sh.600001", "1", "1"]]})
    first = collect_status(api, codes, output, date(2025, 10, 9),
                           date(2025, 10, 9), limit=1, delay=0)
    second = collect_status(api, codes, output, date(2025, 10, 9),
                            date(2025, 10, 9), delay=0)
    assert first == (1, 1) and second == (1, 2)
    assert api.queried == ["sz.000001", "sh.600001"]
    assert [json.loads(line)["rows"][0][2] for line in output.read_text().splitlines()] == ["", "1"]


def test_bad_source_row_does_not_create_completed_checkpoint(tmp_path):
    output = tmp_path / "status.jsonl"
    api = Api({"sz.000001": [["2025-10-09", "sz.000001", "2", "1"]]})
    with pytest.raises(ValueError, match="invalid BaoStock row"):
        collect_status(api, ["000001.SZ"], output,
                       date(2025, 10, 9), date(2025, 10, 9), delay=0)
    assert output.read_text() == ""
