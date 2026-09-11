"""情绪历史指标库（metric_history）回归测试（P0-3b 数据底座）。

锁三类已发生/必然再发生的失效：
1. **盘中回补污染**——今天的涨停池是半截数据（家数还在涨、炸板未定型），
   入库会把分位分布往盘中口径拉偏。`backfill` 必须永远排除今天。
2. **数据源日期回退**——东财 push2ex 的前科：非交易日静默返回最近交易日。
   两天涨停池集合完全相同即疑似回退，必须剔除而非入库。
3. **库损坏静默爆炸**——JSON 损坏/缺字段时读库必须返回空库而非抛异常，
   让上层走"样本不足→经验值"的显式降级路径。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.sentiment import metric_history as mh


# ---------------------------------------------------------------- fixtures


class _Row:
    """最小涨停池行：只需 symbol / consecutive_boards。"""

    def __init__(self, symbol: str, boards: int = 1):
        self.symbol = symbol
        self.consecutive_boards = boards


class FakeProvider:
    """按日期脚本化返回涨停池/炸板池，未脚本化的日期抛错（暴露隐式回退）。"""

    def __init__(self, pools: dict[date, list], breaks: dict[date, list] | None = None):
        self.pools = pools
        self.breaks = breaks or {}
        self.calls: list[date] = []

    async def get_limit_up_pool(self, d: date):
        self.calls.append(d)
        pool = self.pools.get(d)
        if pool is None:
            raise AssertionError(f"unexpected pool fetch for {d}")
        return pool

    async def get_limit_break_pool(self, d: date):
        brk = self.breaks.get(d)
        if brk is None:
            raise RuntimeError("break pool unavailable")
        return brk


def _backfill(provider, days, **kw):
    """项目无 pytest-asyncio：异步入口统一 asyncio.run 包一层。"""
    return asyncio.run(mh.backfill(provider, days, **kw))


def _pool(*syms: str) -> list:
    return [_Row(s) for s in syms]


@pytest.fixture()
def store(tmp_path):
    return tmp_path / "metrics.json"


# ---------------------------------------------------------------- daily_metrics


def test_daily_metrics_matches_engine_promotion():
    """口径对齐：与 engine.promotion_rates 完全一致（校准和线上必须同源）。"""
    pool_prev = _pool("A", "B", "C", "D")  # 4 只首板
    pool_today = [_Row("A", 2), _Row("B", 2), _Row("X", 1)]  # A/B 晋级 2 板
    m = mh.daily_metrics(pool_prev, pool_today, break_pool=_pool("Y", "Z"))
    assert m["limit_up"] == 3
    assert m["max_board"] == 2
    assert m["promo_1to2"] == pytest.approx(2 / 4)
    assert m["break_rate"] == pytest.approx(2 / 5)
    assert m["n_break"] == 2


def test_daily_metrics_empty_pools_give_none_not_zero():
    m = mh.daily_metrics([], [], [])
    assert m["limit_up"] == 0
    assert m["max_board"] == 0
    assert m["break_rate"] is None  # 0/0 不该伪造出 0% 炸板率


# ---------------------------------------------------------------- backfill


def test_backfill_excludes_today_intraday_pollution(store):
    """盘中回补绝不能把今天写进库——今天的池是半截数据。"""
    # days 首元素只作锚点：指标覆盖 8/31 与 9/1 两天，9/2（今天）必须被剔除
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A", "B"),
        date(2026, 9, 1): _pool("C", "D"),
    })
    stats = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    # 9/2 是"今天"，绝不能出现在库里
    assert date(2026, 9, 2).isoformat() not in stats["days"]
    assert stats["total"] == 2


def test_backfill_incremental_skips_existing(store):
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A", "B"),
        date(2026, 9, 1): _pool("C", "D"),
    })
    first = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    assert first["added"] == 2
    calls_after_first = len(provider.calls)

    second = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    assert second["added"] == 0
    assert second["skipped"] == 2
    assert len(provider.calls) == calls_after_first  # 增量：一个请求都没发


def test_backfill_same_pool_sentinel_drops_day(store):
    """两天涨停池完全相同 = 疑似数据源日期回退，剔除不入库（宁可少样本）。"""
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    same = _pool("A", "B", "C")
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): same,
        date(2026, 9, 1): same,
    })
    stats = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    assert stats["suspicious"] == 1
    # 8/31（池与前一日不同）正常入库，9/1（池与 8/31 完全相同）被剔除
    assert stats["total"] == 1


def test_backfill_break_pool_failure_does_not_kill_day(store):
    """炸板池挂了只影响 break_rate 一项（置 None），不该拖垮整天的入库。"""
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A"),
        date(2026, 9, 1): _pool("B"),
    })  # breaks 全空 → get_limit_break_pool 抛错
    stats = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    assert stats["added"] == 2
    rows = mh.history(store_path=store)
    assert all(r["break_rate"] is None for r in rows)


# ---------------------------------------------------------------- load/history


def test_load_corrupt_json_returns_empty_store(store):
    store.write_text("{not valid json", encoding="utf-8")
    loaded = mh.load(store_path=store)
    assert loaded == {"updated_at": None, "lookback": None, "days": {}}


def test_load_missing_schema_returns_empty_store(store):
    store.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    assert mh.load(store_path=store)["days"] == {}


def test_history_is_date_ascending(store):
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A"),
        date(2026, 9, 1): _pool("B"),
        date(2026, 9, 2): _pool("C"),
    })
    _backfill(provider, days, store_path=store, today=date(2026, 9, 3))
    rows = mh.history(store_path=store, limit=2)
    assert [r["date"] for r in rows] == ["2026-09-01", "2026-09-02"]


# ---------------------------------------------------------------- resolve_bands


def _seed_store(store, n_days: int, end: date) -> None:
    rows = []
    for i in range(n_days):
        d = end - __import__("datetime").timedelta(days=n_days - 1 - i)
        rows.append({
            "date": d.isoformat(), "limit_up": 50 + i, "max_board": 4,
            "break_rate": 0.2, "promo_1to2": 0.12, "promo_2to3": 0.06,
        })
    store.write_text(json.dumps({"days": {r["date"]: r for r in rows}}), encoding="utf-8")


def test_resolve_bands_calibrated_with_window_and_stale(tmp_path, monkeypatch):
    """校准生效时窗口首尾 + 交易日口径的 stale 必须随结论返回。"""
    from datetime import timedelta

    from app.core.config import settings
    from app.services import market_context as mcx

    store = tmp_path / "metrics.json"
    _seed_store(store, 45, date(2026, 8, 10))
    monkeypatch.setattr(mh, "_STORE_PATH", store)
    monkeypatch.setattr(settings, "sentiment_calibrate", True)

    # 日历覆盖到 8/13：库尾 8/10 → stale = 3 个交易日
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(4)]
    heat, earning, source, meta = mcx.resolve_bands(trade_days=days)
    assert source == "calibrated"
    assert meta["window"]["end"] == "2026-08-10"
    assert meta["window"]["stale_days"] == 3
    assert "3 个交易日未更新" in meta["stale_reason"]
    assert meta["basis"]["heat"]["limit_up"]["calibrated"] is True


def test_resolve_bands_switch_off_returns_defaults(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import market_context as mcx

    store = tmp_path / "metrics.json"
    _seed_store(store, 45, date(2026, 8, 10))
    monkeypatch.setattr(mh, "_STORE_PATH", store)
    monkeypatch.setattr(settings, "sentiment_calibrate", False)

    _, _, source, meta = mcx.resolve_bands(trade_days=[date(2026, 8, 11)])
    assert source == "defaults"
    assert "关闭历史分位校准" in meta["reason"]


# ---------------------------------------------------------------- 当日分位 + 类型归一（2026-09-10）


def test_backfill_accepts_iso_string_days(store):
    """回归（2026-09-10 事故）：日历传 **ISO 字符串** 也必须能回补。

    真实事故：调度侧直取 provider 原始日历（字符串）喂给 backfill，而 `d < today`
    是 date 比较 → `TypeError: '<' not supported between 'str' and 'date'`；
    异常被调度器的 `except Exception` 收成一条日志 ⇒ **库静默停在 2026-09-01、
    连续 6 个交易日没更新**，界面照旧写着"按近 241 个交易日分位校准"。
    日历元素归一后，传错类型不再是一个沉默数周的数据缺陷。
    """
    str_days = ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A", "B"),
        date(2026, 9, 1): _pool("C", "D"),
    })
    stats = _backfill(provider, str_days, store_path=store, today=date(2026, 9, 2))
    assert stats["added"] == 2  # 8/31 与 9/1；9/2 是今天被剔除
    assert sorted(stats["days"]) == ["2026-08-31", "2026-09-01"]


def test_backfill_dedupes_repeated_days(store):
    """重复日期先去重：否则相邻两天会被当成"同一天"参与 _is_same_pool 判定。"""
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 8, 31), date(2026, 9, 1)]
    provider = FakeProvider({
        date(2026, 8, 30): _pool("P"),
        date(2026, 8, 31): _pool("A", "B"),
        date(2026, 9, 1): _pool("C", "D"),
    })
    stats = _backfill(provider, days, store_path=store, today=date(2026, 9, 2))
    assert stats["added"] == 2 and stats["suspicious"] == 0


def test_percentile_of_value_is_about_the_given_value(store):
    """当日分位：算的是**传进来的那个值**的位置，不是"历史最后一行"的位置。

    这是 `describe()` 与 `percentile_of_value()` 的关键区别：库里最后一行是
    **上一个交易日**（backfill 刻意不回补今天），拿它当"今天的分位"用，
    等于每天用昨天的位置描述今天——库一停更就变成用上个月的位置。
    """
    vals = [0.05, 0.10, 0.15, 0.20]
    rows = [
        {"date": f"2026-08-0{i + 1}", "promo_1to2": v, "break_rate": 0.2,
         "limit_up": 60, "max_board": 5, "promo_2to3": 0.05}
        for i, v in enumerate(vals)
    ]
    store.write_text(json.dumps({"days": {r["date"]: r for r in rows}}), encoding="utf-8")

    # 0.30 高于全部历史 → 100 分位（而不是历史末行 0.20 的 87.5）
    assert mh.percentile_of_value("promo_1to2", 0.30, store)["percentile"] == 100.0
    assert mh.percentile_of_value("promo_1to2", 0.05, store)["percentile"] == 12.5
    # 传末行同值时与 describe（末行口径）一致 → 证明差异只来自"喂了哪个值"
    from app.sentiment.calibration import describe

    assert (
        mh.percentile_of_value("promo_1to2", 0.20, store)["percentile"]
        == describe(mh.history(store))["promo_1to2"]["percentile"]
    )
    # 值缺失/库为空 → None（调用方回落绝对阈值，不臆造分位）
    assert mh.percentile_of_value("promo_1to2", None, store) is None
