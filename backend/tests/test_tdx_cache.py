"""tdx_daily_bars 缓存测试：命中/未命中/None 不缓存/失效/浅拷贝隔离。"""
import pytest

from app.market import tdx_kline


@pytest.fixture(autouse=True)
def _clean_cache():
    tdx_kline._BARS_CACHE.invalidate()
    yield
    tdx_kline._BARS_CACHE.invalidate()


def _mk_bars(tag: str) -> list[dict]:
    return [{"ts": f"{tag}-d{i}", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0} for i in range(3)]


def test_cache_hit_avoids_refetch(monkeypatch):
    calls = []

    def fake_fetch(symbol, count):
        calls.append((symbol, count))
        return _mk_bars(tag="a")

    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", fake_fetch)
    b1 = tdx_kline.tdx_daily_bars("600519", 500)
    b2 = tdx_kline.tdx_daily_bars("600519", 500)
    assert len(calls) == 1  # 第二次命中缓存
    assert b1[0]["ts"] == b2[0]["ts"]
    # 不同 count 视为不同键
    tdx_kline.tdx_daily_bars("600519", 300)
    assert len(calls) == 2


def test_none_not_cached(monkeypatch):
    calls = []

    def fake_fetch(symbol, count):
        calls.append(symbol)
        return None

    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", fake_fetch)
    assert tdx_kline.tdx_daily_bars("600519", 500) is None
    assert tdx_kline.tdx_daily_bars("600519", 500) is None
    assert len(calls) == 2  # None 不入缓存 → 两次都回源


def test_use_cache_false_bypasses(monkeypatch):
    calls = []

    def fake_fetch(symbol, count):
        calls.append(symbol)
        return _mk_bars("a")

    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", fake_fetch)
    tdx_kline.tdx_daily_bars("600519", 500)
    tdx_kline.tdx_daily_bars("600519", 500, use_cache=False)
    assert len(calls) == 2


def test_invalidate(monkeypatch):
    calls = []

    def fake_fetch(symbol, count):
        calls.append(symbol)
        return _mk_bars("a")

    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", fake_fetch)
    tdx_kline.tdx_daily_bars("600519", 500)
    tdx_kline.invalidate_bars_cache()
    tdx_kline.tdx_daily_bars("600519", 500)
    assert len(calls) == 2


def test_shallow_copy_isolates_cache(monkeypatch):
    """命中返回浅拷贝：调用方原地改 list 不污染缓存内的共享对象。"""
    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", lambda s, c: _mk_bars("a"))
    b1 = tdx_kline.tdx_daily_bars("600519", 500)
    b1.pop()  # 调用方误改
    b2 = tdx_kline.tdx_daily_bars("600519", 500)
    assert len(b2) == 3
