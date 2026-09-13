"""二期模块单测：龙虎榜归档（E4）+ 辨识度画像（C）+ board_surge 资金面接线（B）。"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import duckdb
import pytest

from app.market import lhb_archive
from app.picks import board_surge as bs
from app.picks import distinctiveness as dv


# ---------------------------------------------------------------- E4 归档


class _FakeProvider:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls = 0

    async def get_longhu_records(self, trade_date):
        self.calls += 1
        from app.schemas.market import LongHuRecord

        return [LongHuRecord(**r) for r in self._rows]


def test_archive_day_writes_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(lhb_archive, "LHB_DIR", tmp_path)
    prov = _FakeProvider([
        {"symbol": "000636", "name": "风华高科", "trade_date": "2026-09-11",
         "net_buy": 1.2, "reason": "日涨幅偏离值达到7%", "source": "eastmoney", "quality": "high"},
    ])
    out = asyncio.run(lhb_archive.archive_day(prov, date(2026, 9, 11)))
    assert out["archived"] is True and out["count"] == 1
    payload = json.loads((tmp_path / "20260911.json").read_text(encoding="utf-8"))
    assert payload["records"][0]["symbol"] == "000636"
    # 幂等：已归档不再打源
    calls = prov.calls
    out2 = asyncio.run(lhb_archive.archive_day(prov, date(2026, 9, 11)))
    assert out2["reason"] == "already_archived" and prov.calls == calls


def test_archive_day_empty_not_written(tmp_path, monkeypatch):
    """发布前的空结果不落盘（三态：空 ≠ 无上榜，留待重试）。"""
    monkeypatch.setattr(lhb_archive, "LHB_DIR", tmp_path)
    prov = _FakeProvider([])
    out = asyncio.run(lhb_archive.archive_day(prov, date(2026, 9, 11)))
    assert out == {"archived": False, "reason": "empty_list", "date": "20260911"}
    assert not (tmp_path / "20260911.json").exists()


def test_count_symbol_hits_scans_window(tmp_path):
    for i, day in enumerate(["20260901", "20260902", "20260903"]):
        payload = {"trade_date": f"2026-09-0{i + 1}", "count": 1,
                   "records": [{"symbol": "000636" if i < 2 else "x", "name": "风华高科"}]}
        (tmp_path / f"{day}.json").write_text(json.dumps(payload), encoding="utf-8")
    out = lhb_archive.count_symbol_hits({"000636"}, base_dir=tmp_path)
    assert out == {"hits": {"000636": 2}, "archived_days": 3}


def test_load_day_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lhb_archive, "LHB_DIR", tmp_path)
    assert lhb_archive.load_day("20260911") is None


# ---------------------------------------------------------------- C 辨识度画像


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    path = tmp_path / "market.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, "
                "high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE)")
    con.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    return path


def _db_rows() -> list[tuple]:
    day0 = int(datetime(2026, 9, 11).timestamp() * 1000) - 59 * dv.DAY_MS
    rows = []
    c = 100.0
    for i in range(60):  # 强票：60 根里 1 根放量大涨（i=45，落在近 20 根窗口）+ 温和上行
        step = 1.07 if (i % 15 == 0 and i > 40) else 1.002
        c *= step
        rows.append(("600519.SH", day0 + i * dv.DAY_MS, c, c, c, c,
                     5000.0 if step > 1.05 else 1000.0))
    c2 = 50.0
    for i in range(60):  # 弱票：阴跌
        c2 *= 0.999
        rows.append(("000001.SZ", day0 + i * dv.DAY_MS, c2, c2, c2, c2, 800.0))
    return rows


def test_score_candidates_orders_and_breakdown(tmp_path):
    db = _make_db(tmp_path, _db_rows())
    sky = tmp_path / "skyrocket.jsonl"
    sky.write_text("\n".join(
        json.dumps({"date": "2026-09-10", "rank": 1, "symbol": "600519", "heat": 9})
        for _ in range(3)
    ) + "\n", encoding="utf-8")
    out = dv.score_candidates(["600519", "000001"], db_path=db,
                              skyrocket_path=sky, lhb_base_dir=tmp_path)
    assert out["available"] is True
    assert out["items"][0]["symbol"] == "600519"
    strong = out["items"][0]
    assert strong["breakdown"]["vol_spike_20d"] >= 1
    assert strong["breakdown"]["skyrocket_60d"] == 3
    assert strong["score"] > out["items"][1]["score"]
    assert out["weights_calibrated"] is False


def test_score_candidates_missing_db_explicit(tmp_path):
    """marketdb 缺仓 = 显式不可用（三态），绝不拿 0 分冒充「无画像」。"""
    out = dv.score_candidates(["600519"], db_path=tmp_path / "no.duckdb",
                              skyrocket_path=tmp_path / "no.jsonl", lhb_base_dir=tmp_path)
    assert out["available"] is False and "marketdb" in (out["marketdb_note"] or "")
    assert out["items"] == []


def test_format_candidates_none_when_unavailable():
    assert dv.format_candidates({"available": False, "items": []}) is None
    line = dv.format_candidates({"available": True, "items": [
        {"symbol": "600519", "score": 82.0}, {"symbol": "000001", "score": None}]},
        names={"600519": "贵州茅台"})
    assert line == "辨识度候选：贵州茅台(82)"


# ---------------------------------------------------------------- B：资金面接线


def test_collect_flows_sums_baseline_and_skip(monkeypatch):
    """净额合计 + 基准只建一次 + 北交所显式 skipped（绝不混入合计）。"""
    import app.market.stock_flow as sfmod

    d = bs.BoardSurgeDetector()
    member_sets = {"T1": {"600519", "000001", "920821"}}

    async def fake_flow(symbols):
        return {"items": {s: {"main": 0.5} for s in symbols}, "no_data": ["920821"]}

    monkeypatch.setattr(sfmod, "get_stock_flow", fake_flow)
    monkeypatch.setattr(sfmod, "stock_secid",
                        lambda s: None if s.startswith("9") else f"1.{s}")

    out = asyncio.run(bs._collect_flows(NS(), d, member_sets, ["T1"]))
    assert out["T1"]["net_sum"] == pytest.approx(1.0)
    assert out["T1"]["skipped"] == 1
    assert d.flow_baseline["T1"] == pytest.approx(1.0)
    # 第二拍：基准不重置（首拍已建），新净额照记
    out2 = asyncio.run(bs._collect_flows(NS(), d, member_sets, ["T1"]))
    assert out2["T1"]["baseline"] == pytest.approx(1.0)
    assert out2["T1"]["net_sum"] == pytest.approx(1.0)


def test_collect_flows_source_failure_theme_absent(monkeypatch):
    """取数失败 → 该题材 flows 缺席（三态：不臆造 0）。"""
    import app.market.stock_flow as sfmod

    d = bs.BoardSurgeDetector()

    async def boom(symbols):
        raise RuntimeError("em down")

    monkeypatch.setattr(sfmod, "get_stock_flow", boom)
    monkeypatch.setattr(sfmod, "stock_secid", lambda s: f"1.{s}")
    out = asyncio.run(bs._collect_flows(NS(), d, {"T1": {"600519"}}, ["T1"]))
    assert out == {}


def test_persist_beat_with_flows(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "DATA_DIR", Path(tmp_path) / "theme_momentum")
    asyncio.run(bs.persist_beat("20260911", "14:12", -2.2, {"T1": {"med": 1.5, "rel": 2.5}},
                                flows={"T1": {"net_sum": 3.2, "n": 40, "skipped": 2,
                                              "baseline": 0.8}}))
    line = json.loads((Path(tmp_path) / "theme_momentum" / "20260911.jsonl").read_text(encoding="utf-8"))
    assert line["flows"]["T1"]["net_sum"] == 3.2
