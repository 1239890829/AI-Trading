"""Historical Jev usage report is metadata-only and deterministic."""
from __future__ import annotations

import json

from app.core.jev_client import historical_usage_summary
from scripts import jev_usage_report


def _write(path):
    rows = [
        {"at_utc":"2026-09-19T00:00:00+00:00","purpose":"alert","status":"ok","model":"jev-1.13.0","input_tokens":100,"output_tokens":10,"latency_ms":500,"reason":None},
        {"at_utc":"2026-09-19T00:01:00+00:00","purpose":"event","status":"failed","model":"jev-1.13.0","input_tokens":0,"output_tokens":0,"latency_ms":100,"reason":"http_503"},
        {"at_utc":"2026-09-19T00:02:00+00:00","purpose":"event","status":"skipped","model":"jev-latest","input_tokens":0,"output_tokens":0,"latency_ms":0,"reason":"disabled"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nBROKEN\n")


def test_historical_usage_summary(tmp_path):
    path=tmp_path/"usage.jsonl"; _write(path)
    out=historical_usage_summary(path)
    assert out["calls"] == 3
    assert out["ok"] == 1 and out["failed"] == 1 and out["skipped"] == 1
    assert out["input_tokens"] == 100 and out["output_tokens"] == 10
    assert out["avg_latency_ms"] == 200.0
    assert out["malformed_lines"] == 1
    assert out["purposes"] == {"alert":1,"event":2}


def test_cli_prints_summary_without_network(tmp_path, capsys):
    path=tmp_path/"usage.jsonl"; _write(path)
    assert jev_usage_report.main(["--path", str(path)]) == 0
    data=json.loads(capsys.readouterr().out)
    assert data["calls"] == 3 and data["models"]["jev-1.13.0"] == 2
