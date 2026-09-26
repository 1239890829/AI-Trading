"""Identity audit must account for every requested symbol and actual sample row."""
import json

import duckdb
import pytest

from scripts.rsh031_identity_audit import audit


def _write(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def test_cross_source_status_and_missing_checkpoint(tmp_path):
    codes = _write(tmp_path / "codes.json", ["000001.SZ", "600001.SH"])
    basic = _write(tmp_path / "basic.json", {
        "fields": ["code", "code_name", "ipoDate", "outDate", "type", "status"],
        "rows": [["sz.000001", "A", "1990-01-01", "", "1", "1"],
                 ["sh.600001", "B", "2000-01-01", "", "1", "1"]]})
    status = tmp_path / "status.jsonl"
    status.write_text("".join(json.dumps(x) + "\n" for x in [
        {"thscode": "000001.SZ", "complete": True,
         "rows": [["2025-10-09", "sz.000001", "0", "1"]]},
        {"thscode": "600001.SH", "complete": True,
         "rows": [["2025-10-09", "sh.600001", "1", "0"]]},
    ]), encoding="utf-8")
    pools = tmp_path / "pools.jsonl"
    pools.write_text(json.dumps({"trade_date": "2025-10-09", "pool": "limit-up-pool",
                                  "items": [{"thscode": "600001.SH", "is_st": True}]}) + "\n")
    samples = tmp_path / "samples.parquet"
    con = duckdb.connect(":memory:")
    con.execute(f"COPY (SELECT DATE '2025-10-09' AS trade_date, '000001.SZ' AS thscode "
                "UNION ALL SELECT DATE '2025-10-09', '600001.SH') "
                f"TO '{samples}' (FORMAT PARQUET)")
    con.close()
    result = audit(codes, status, basic, pools, samples, tmp_path / "audit")
    assert result["sample"] == {"stock_days": 2, "missing_status": 0,
                                 "unknown_st": 0, "st_days": 1, "suspended_days": 1}
    assert result["limit_up_pool"]["conflicting_st"] == 0

    status.write_text(status.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="covers 1/2"):
        audit(codes, status, basic, pools, samples, tmp_path / "incomplete")
