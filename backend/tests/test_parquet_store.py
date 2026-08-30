from __future__ import annotations

import polars as pl
from pathlib import Path

from app.services.parquet_store import read_latest_in_dir, read_latest_snapshot


def _mk_snapshot(day_dir: Path, name: str, rows: list[dict]) -> Path:
    day_dir.mkdir(parents=True, exist_ok=True)
    p = day_dir / f"{name}.parquet"
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(p)
    return p


def _corrupt(path: Path) -> None:
    """原地写坏文件，但保持大小不变——实测中的损坏就是这个形态（完整大小、内容坏）。"""
    size = path.stat().st_size
    path.write_bytes(b"\x00" * size)


def test_skips_corrupt_latest_and_falls_back(tmp_path: Path):
    """最新一份损坏时必须回退到更早的可读快照，而不是整个崩掉。"""
    day = tmp_path / "snapshots" / "20260830"
    _mk_snapshot(day, "090000", [{"symbol": "600519", "ticktime": "09:00"}])
    good = _mk_snapshot(day, "100000", [{"symbol": "000001", "ticktime": "10:00"}])
    bad = _mk_snapshot(day, "110000", [{"symbol": "300750", "ticktime": "11:00"}])
    _corrupt(bad)

    read = read_latest_snapshot(tmp_path)
    assert read.ok
    assert read.path == good  # 跳过 110000，落到 100000
    assert len(read.skipped) == 1
    assert read.skipped[0].endswith("110000.parquet")
    assert read.df["symbol"].to_list() == ["000001"]


def test_all_corrupt_reports_honest_error(tmp_path: Path):
    """全损坏时必须报错，且错误要说明是本地快照问题（不是上游源问题）。"""
    day = tmp_path / "snapshots" / "20260830"
    for name in ("090000", "100000"):
        _corrupt(_mk_snapshot(day, name, [{"symbol": "600519"}]))

    read = read_latest_snapshot(tmp_path)
    assert not read.ok
    assert read.df is None
    assert "不可读" in (read.error or "")
    assert len(read.skipped) == 2


def test_missing_dir(tmp_path: Path):
    read = read_latest_snapshot(tmp_path / "nope")
    assert not read.ok
    assert "不存在" in (read.error or "")


def test_read_latest_in_dir_respects_columns(tmp_path: Path):
    """market.py 用 columns 窄读，损坏时同样要能回退。"""
    day = tmp_path / "20260830"
    _mk_snapshot(day, "090000", [{"symbol": "600519", "change_pct": 1.0}])
    _corrupt(_mk_snapshot(day, "100000", [{"symbol": "000001", "change_pct": 2.0}]))

    read = read_latest_in_dir(day, columns=["symbol", "change_pct"])
    assert read.ok
    assert read.df.columns == ["symbol", "change_pct"]
    assert read.df["symbol"].to_list() == ["600519"]


def test_empty_dir(tmp_path: Path):
    day = tmp_path / "20260830"
    day.mkdir(parents=True)
    read = read_latest_in_dir(day)
    assert not read.ok
    assert "无快照文件" in (read.error or "")


def test_snapshot_write_is_atomic_and_leaves_no_temp(tmp_path: Path):
    """快照写入必须是原子的：产物可读，且不留 .tmp 残留。"""
    from app.services.snapshot_service import MarketSnapshotService

    svc = MarketSnapshotService(poll_interval=60, save_interval=0, parquet_dir=tmp_path)
    svc.snapshot = [{"symbol": "600519", "change_pct": 1.23}]

    for _ in range(3):
        svc._maybe_save()
        svc._last_save = None  # 绕开 save_interval 节流，连续写多份

    day_dirs = list((tmp_path / "snapshots").iterdir())
    assert day_dirs, "快照目录未创建"
    files = sorted(day_dirs[0].glob("*.parquet"))
    assert files, "未产生快照文件"
    assert not list(day_dirs[0].glob("*.tmp")), "临时文件残留，原子写没生效"

    # 每一份都必须可读（不然后续读取方仍会踩到损坏文件）
    for f in files:
        assert pl.read_parquet(f).height == 1


def test_write_parquet_atomic_leaves_no_temp(tmp_path: Path):
    from app.services.parquet_store import write_parquet_atomic

    target = tmp_path / "sub" / "600519.parquet"
    write_parquet_atomic(pl.DataFrame([{"symbol": "600519", "p": 1.0}]), target)

    assert target.exists()
    assert not list((tmp_path / "sub").glob("*.tmp")), "临时文件残留"
    assert pl.read_parquet(target).height == 1


def test_read_parquet_safe_handles_missing_and_corrupt(tmp_path: Path):
    from app.services.parquet_store import read_parquet_safe

    miss = tmp_path / "nope.parquet"
    df, err = read_parquet_safe(miss)
    assert df is None and "不存在" in (err or "")

    good = tmp_path / "ok.parquet"
    pl.DataFrame([{"a": 1}]).write_parquet(good)
    df, err = read_parquet_safe(good)
    assert df is not None and err is None

    bad = tmp_path / "bad.parquet"
    pl.DataFrame([{"a": 1}]).write_parquet(bad)
    _corrupt(bad)
    df, err = read_parquet_safe(bad)
    assert df is None and err, "损坏文件应返回 (None, 错误说明) 而不是抛异常"


def test_load_symbol_returns_empty_on_corrupt_instead_of_raising(tmp_path: Path):
    """分钟缓存损坏时按无数据处理，不能把 /api/minute-line 整个打挂。"""
    from app.market.minute_backfill import load_symbol

    sym_dir = tmp_path
    p = sym_dir / "600519.parquet"
    pl.DataFrame([{"ts": "2026-08-28T01:30:00", "price": 10.0, "cum_volume": 100}]) \
        .write_parquet(p)

    assert len(load_symbol(sym_dir, "600519")) == 1

    _corrupt(p)
    assert load_symbol(sym_dir, "600519") == [], "损坏文件应退化为空列表"
    assert load_symbol(sym_dir, "000001") == [], "不存在的文件同样返回空"

