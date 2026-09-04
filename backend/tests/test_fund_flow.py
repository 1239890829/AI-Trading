"""fund_flow 纯函数与降级逻辑单测（不外呼——数据源调用一概 monkeypatch/直测纯函数）。"""

from datetime import date, datetime, timezone, timedelta

from app.market import fund_flow as ff

_TZ_BJ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 时间口径

def test_market_progress_minutes_sessions():
    mk = ff._market_progress_minutes
    # 盘前
    assert mk(datetime(2026, 9, 4, 9, 0, tzinfo=_TZ_BJ)) is None
    # 早盘 09:31 → 1 分钟
    assert mk(datetime(2026, 9, 4, 9, 31, tzinfo=_TZ_BJ)) == 1
    # 午休
    assert mk(datetime(2026, 9, 4, 12, 0, tzinfo=_TZ_BJ)) is None
    # 午后 13:01 → 121
    assert mk(datetime(2026, 9, 4, 13, 1, tzinfo=_TZ_BJ)) == 121
    # 收盘后封顶 240
    assert mk(datetime(2026, 9, 4, 15, 30, tzinfo=_TZ_BJ)) == 240
    # naive datetime 视作北京时间
    assert mk(datetime(2026, 9, 4, 9, 31)) == 1


def test_sina_bar_seq_maps_minutes():
    sq = ff._sina_bar_seq
    assert sq(datetime(2026, 9, 3, 9, 35)) == 5
    assert sq(datetime(2026, 9, 3, 11, 30)) == 120
    assert sq(datetime(2026, 9, 3, 11, 35)) is None  # 午休
    assert sq(datetime(2026, 9, 3, 13, 5)) == 125
    assert sq(datetime(2026, 9, 3, 15, 0)) == 240
    assert sq(datetime(2026, 9, 3, 15, 5)) is None  # 收盘外


# ---------------------------------------------------------------- 新浪 bar 聚合

def _bar(day: str, hm: str, amount: float) -> dict:
    """构造 _sina_mk_bars 解析后的 bar 结构。"""
    dt = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
    seq = ff._sina_bar_seq(dt)
    return {"dt": dt, "seq": seq, "amount": amount}


def test_cum_amount_by_seq_accumulates_increment_bars():
    d = date(2026, 9, 3)
    bars = [
        _bar("2026-09-03", "09:35", 100.0),
        _bar("2026-09-03", "09:40", 200.0),
        _bar("2026-09-04", "09:35", 999.0),  # 其他交易日不串
    ]
    total = ff._cum_amount_by_seq(bars, d, max_seq=10)
    assert total == 300.0
    # max_seq 截断
    assert ff._cum_amount_by_seq(bars, d, max_seq=5) == 100.0
    # 无该日数据
    assert ff._cum_amount_by_seq(bars, date(2026, 8, 1)) is None


def test_pair_total_sums_two_markets():
    d = date(2026, 9, 3)
    sh = [_bar("2026-09-03", "09:35", 100e8)]  # 元
    sz = [_bar("2026-09-03", "09:35", 50e8)]
    assert ff._pair_total(sh, sz, d) == 150.0  # 亿元
    assert ff._pair_total(sh, [], d) is None  # 任一市场缺数据 → None 不臆造


# ---------------------------------------------------------------- 实时聚合与降级

def test_rt_from_ulist_aggregates_markets():
    diff = [
        {"f12": "000001", "f62": -100e8, "f66": -30e8, "f72": -70e8,
         "f78": -20e8, "f84": 120e8, "f124": 1788000000},
        {"f12": "399107", "f62": -50e8, "f66": -10e8, "f72": -40e8,
         "f78": -30e8, "f84": 80e8, "f124": 1788000000},
    ]
    out = ff._rt_from_ulist(diff, [])
    assert out["available"] is True
    t = out["items"]["total"]
    assert t["main"] == -150.0
    assert t["super_"] == -40.0
    assert t["small"] == 200.0
    # self-check：main+mid+small ≈ 0
    assert abs(t["main"] + t["mid"] + t["small"]) < 0.01


def test_rt_from_ulist_missing_market_degrades():
    diff = [{"f12": "000001", "f62": -1e8, "f66": 0, "f72": 0, "f78": 0, "f84": 0}]
    out = ff._rt_from_ulist(diff, [])
    assert out["available"] is False


def test_rt_fallback_chain_marks_delay(monkeypatch):
    async def fail_ulist():
        return None

    async def fake_kline(secid):
        if secid == "1.000001":
            return [("14:00", {"main": -10.0, "small": 5.0, "mid": 3.0, "big": -8.0, "super_": -2.0})]
        return [("14:00", {"main": -5.0, "small": 3.0, "mid": 1.0, "big": -4.0, "super_": -1.0})]

    monkeypatch.setattr(ff, "_em_ulist", fail_ulist)
    monkeypatch.setattr(ff, "_em_fflow_kline", fake_kline)
    import asyncio
    out = asyncio.run(ff._fund_flow_rt_uncached())
    assert out["available"] is True
    assert "15 分钟延迟" in " ".join(out["degraded"])
    assert out["items"]["total"]["main"] == -15.0


def test_rt_fallback_chain_full_failure(monkeypatch):
    import asyncio

    async def fail(*a, **k):
        return None

    monkeypatch.setattr(ff, "_em_ulist", fail)
    monkeypatch.setattr(ff, "_em_fflow_kline", fail)
    out = asyncio.run(ff._fund_flow_rt_uncached())
    assert out["available"] is False  # 绝不填 0


# ---------------------------------------------------------------- 收盘快照

def test_snapshot_today_if_closed_persists_once(monkeypatch, tmp_path):
    store_file = tmp_path / "daily.json"
    monkeypatch.setattr(ff, "_FLOW_STORE", store_file)
    # 14:00 不落盘
    monkeypatch.setattr(ff, "_now_bj", lambda: datetime(2026, 9, 4, 14, 0, tzinfo=_TZ_BJ))
    rt = {"available": True, "items": {"sh": {"main": -1.0}, "sz": {"main": -2.0}}}
    ff._snapshot_today_if_closed(rt)
    assert not store_file.exists()
    # 15:06 落盘
    monkeypatch.setattr(ff, "_now_bj", lambda: datetime(2026, 9, 4, 15, 6, tzinfo=_TZ_BJ))
    ff._snapshot_today_if_closed(rt)
    import json
    data = json.loads(store_file.read_text())
    assert "2026-09-04" in data["days"]
    assert data["days"]["2026-09-04"]["sh"]["main"] == -1.0
    # 幂等：已有当日不覆盖
    ff._snapshot_today_if_closed({**rt, "items": {"sh": {"main": -9.0}, "sz": {"main": -9.0}}})
    assert json.loads(store_file.read_text())["days"]["2026-09-04"]["sh"]["main"] == -1.0


def test_flow_total_yi_merges_markets():
    rec = {"sh": {"main": -1.0, "small": 2.0, "mid": None, "big": -0.5, "super_": -0.5},
           "sz": {"main": -2.0, "small": 3.0, "mid": 1.0, "big": None, "super_": None}}
    out = ff._flow_total_yi(rec)
    assert out["main"] == -3.0
    assert out["mid"] is None  # 任一市场缺失 → None（三态，不填 0）
    assert out["close_pct"] is None


# ---------------------------------------------------------------- 回源节流

def test_backfill_throttle(monkeypatch):
    monkeypatch.setattr(ff, "_last_backfill_ts", [0.0])
    assert ff._backfill_throttled() is False  # 首次放行
    assert ff._backfill_throttled() is True  # 5 分钟内节流


def test_flow_store_read_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "nope.json")
    assert ff._read_flow_store() is None
