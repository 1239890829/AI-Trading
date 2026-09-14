"""**降级契约**回归（2026-09-14 架构审查批次 B4）。

本文件钉住一类共同缺陷：**降级发生时，系统的行为与"事实"脱钩**——读不到数据
被当成没有数据、失败被当成完成、异常被写成数据缺失。它们的共同特征是
「输出照样有、数字照样出，但结论是错的」，因此单看返回值与状态字段发现不了。

四条不变量（各自对应一处修复前的真实缺陷）：
1. **瞬时失败不得变成永久失败**（heatmap 行业映射：`_industry_failed` 一次置位后
   让冷却/重试分支永不可达 ⇒ 本进程内云图整场「未分类」）。
2. **风控方向的降级只能 fail-closed**（position_engine：持仓读失败返回 [] 会让
   只数上限与总敞口闸门同时失效 ⇒ 读不到持仓反而放行开仓）。
3. **写入必须原子、损坏必须留证**（计划台账：非原子写会留半截 JSON，
   而 load_plan 对半截与全损同判 ⇒ 静默清空且残件无从追溯）。
4. **依据文案必须指向真实原因**（技术评分异常被写成「K线数据缺失」，把排查引向错方向）。

⚠️ 每个用例都做过**注入验证**：把修复回退成旧实现时，本文件必须精确变红
（见文末各用例 docstring 的「回退即红」说明）。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


# ---------------------------------------------------------------- 1. heatmap


class _Clock:
    """假时钟：**只推进时间，不手改被测状态**。

    第一版用例是手动把 `_industry_retry_after` 清零来"模拟冷却到期"——结果它把
    被测实现写下的值**覆盖掉**了，于是"失败被写成永久冷却"这个缺陷照样全绿
    （注入验证实测：把冷却改成 1e12 年，用例仍 passed）。改为推进时钟后，
    用例观察的是实现自己算出的冷却是否**有限**。
    """

    def __init__(self, t0: float = 1_000_000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


def _wire_clock(hs, clock, monkeypatch):
    monkeypatch.setattr(hs.time, "time", clock)
    monkeypatch.setattr(hs, "_industry_cache", {})
    monkeypatch.setattr(hs, "_industry_cached_at", 0.0)
    monkeypatch.setattr(hs, "_industry_retry_after", 0.0)


def _failing_then_ok_thread(calls: dict, ok_value: dict):
    async def fake_to_thread(fn, *a, **k):  # noqa: ANN001, ANN002, ANN003
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tdx 抖动（瞬时）")
        return ok_value

    return fake_to_thread


def test_industry_map_retries_after_transient_failure(monkeypatch):
    """失败只应**冷却**，不应**永久放弃**。

    回退即红：把失败写入的冷却改成"永久"（旧实现 `_industry_failed` 标志位的等价物，
    注入实测为 `time.time() + 1e12`）时，第三段「冷却过后必须重取」失败。
    """
    import app.services.heatmap_service as hs

    clock = _Clock()
    _wire_clock(hs, clock, monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(asyncio, "to_thread", _failing_then_ok_thread(calls, {"600519": "白酒"}))

    # 第一次：失败 → 空映射（调用方降级为「未分类」），只尝试一次
    assert asyncio.run(hs.get_industry_map_async()) == {}
    assert calls["n"] == 1
    # 实现自己写下的冷却必须落在 (now, now + _FAIL_COOLDOWN] 内 —— 即**有限**
    remaining = hs._industry_retry_after - clock()
    assert 0 < remaining <= hs._FAIL_COOLDOWN + 1, f"冷却不有限：{remaining}"

    # 冷却期内：不重试（避免每个请求都撞 TDX）
    clock.t += 1.0
    assert asyncio.run(hs.get_industry_map_async()) == {}
    assert calls["n"] == 1, "冷却期内不应重复外呼"

    # 冷却到期：**必须重试**（这一条是本次修复的靶心）
    clock.t += hs._FAIL_COOLDOWN
    assert asyncio.run(hs.get_industry_map_async()) == {"600519": "白酒"}
    assert calls["n"] == 2, "冷却过后未重试 ⇒ 一次瞬时失败被升级成永久失败"


def test_industry_map_keeps_serving_stale_cache_on_refresh_failure(monkeypatch):
    """刷新失败时，旧映射继续服务（行业归属变化慢，'陈旧'远好于整场'未分类'）。

    注意 `_industry_cached_at` 必须落在**假时钟的过去**（此处差 1e6 秒 > TTL），
    否则第一段 TTL 判定会直接返回旧缓存、外呼根本没发生 —— 用例变成空转
    （第一版就是这么写的：注入 `boom` 与不注入结果一样，等于没测）。
    """
    import app.services.heatmap_service as hs

    clock = _Clock()
    monkeypatch.setattr(hs.time, "time", clock)
    monkeypatch.setattr(hs, "_industry_cache", {"600519": "白酒"})
    monkeypatch.setattr(hs, "_industry_cached_at", 0.0)  # 相对假时钟已过期
    monkeypatch.setattr(hs, "_industry_retry_after", 0.0)

    calls = {"n": 0}

    async def boom(fn, *a, **k):  # noqa: ANN001, ANN002, ANN003
        calls["n"] += 1
        raise RuntimeError("tdx 抖动")

    monkeypatch.setattr(asyncio, "to_thread", boom)
    assert asyncio.run(hs.get_industry_map_async()) == {"600519": "白酒"}
    assert calls["n"] == 1, "缓存已过期时应真的尝试刷新（否则本条断言是空转）"


# ---------------------------------------------------------------- 2. 仓位引擎 fail-closed


class _BrokenEngine:
    """持仓读取抛异常（模拟 DB 锁 / 表损坏）。"""

    def positions_with_pnl(self, _quotes):  # noqa: ANN001
        raise RuntimeError("database is locked")

    def ensure_account(self):  # pragma: no cover - 不该被走到
        raise AssertionError("持仓读失败后不应继续算仓位")


class _App:
    def __init__(self, engine):  # noqa: ANN001
        self.state = type("S", (), {"paper": engine})()


def test_open_positions_returns_none_on_read_failure():
    """读失败必须返回 None，而不是伪装成「没有持仓」。"""
    from app.picks import position_engine as pe

    assert pe._open_positions(_BrokenEngine()) is None


def test_maybe_open_refuses_when_holdings_unreadable():
    """**fail-closed**：读不到持仓 ⇒ 拒绝开仓。

    回退即红：旧实现 `except → []` 时本用例会走到 `ensure_account()` 并触发
    其中的 AssertionError（或继续下单），即"读不到反而放行"。
    """
    from app.picks import position_engine as pe

    out = asyncio.run(
        pe.maybe_open(
            _App(_BrokenEngine()),
            symbol="600519", name="贵州茅台", trigger="buy_point", price=1500.0,
        )
    )
    assert out["opened"] is False
    assert "读取失败" in out["reason"]


def test_open_positions_normal_path_still_filters_zero_qty():
    from app.picks import position_engine as pe

    class _E:
        def positions_with_pnl(self, _q):  # noqa: ANN001
            return [{"symbol": "600519", "quantity": 100}, {"symbol": "000001", "quantity": 0}]

    got = pe._open_positions(_E())
    assert [p["symbol"] for p in got] == ["600519"]


# ---------------------------------------------------------------- 3. 计划台账：原子写 + 残件留档


def test_save_plan_writes_temp_then_replaces(tmp_path, monkeypatch):
    """写盘必须是「临时文件 + os.replace」，不得直接写目标文件。

    回退即红：旧实现直接 `target.write_text(...)`，不会调用 os.replace。
    """
    from app.picks import position_engine as pe

    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    seen: dict = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"] = Path(src)
        seen["dst"] = Path(dst)
        seen["src_exists"] = Path(src).exists()
        seen["dst_before"] = Path(dst).exists()
        seen["src_text"] = Path(src).read_text(encoding="utf-8")
        return real_replace(src, dst)

    monkeypatch.setattr(pe.os, "replace", spy)
    pe.save_plan({"date": "2026-09-14", "decisions": [], "exits": [], "peaks": {}})

    assert seen, "save_plan 没有走 os.replace（非原子写）"
    assert seen["src"] != seen["dst"], "原地写 = 没有原子性"
    assert seen["src"].parent == tmp_path and seen["dst"].parent == tmp_path, "临时文件必须同目录（跨分区 rename 不原子）"
    assert seen["src_exists"], "replace 之前临时文件应已完整落盘"
    assert '"date": "2026-09-14"' in seen["src_text"]
    # 收尾干净：不留 .tmp
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_plan_preserves_corrupt_file(tmp_path, monkeypatch):
    """损坏台账要**留档**再重建，不能静默抹掉（否则事后无法判断是写坏还是被改坏）。"""
    from app.picks import position_engine as pe

    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    target = tmp_path / "2026-09-14.json"
    target.write_text('{"date": "2026-09-14", "decisions": [{"s', encoding="utf-8")  # 半截 JSON

    plan = pe.load_plan("2026-09-14")
    assert plan == {"date": "2026-09-14", "decisions": [], "exits": [], "peaks": {}}
    assert not target.exists()
    leftovers = list(tmp_path.glob("2026-09-14.corrupt-*.json"))
    assert len(leftovers) == 1, "残件未留档（旧实现直接覆盖重建）"
    assert leftovers[0].read_text(encoding="utf-8").endswith('"decisions": [{"s')


def test_load_plan_missing_file_is_not_treated_as_corrupt(tmp_path, monkeypatch):
    """文件不存在 ≠ 损坏：不该产生留档文件。"""
    from app.picks import position_engine as pe

    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    assert pe.load_plan("2026-09-15")["decisions"] == []
    assert list(tmp_path.glob("*.corrupt-*.json")) == []


# ---------------------------------------------------------------- 4. 涨跌停口径单点


def test_mock_limit_pools_use_price_rules_single_point(monkeypatch):
    """mock 涨停/跌停池的幅度必须来自 `price_rules.limit_pct` 单点。

    注入法：把 UNIVERSE 换成含**北交所 83 段**的合成标的 —— 旧实现的
    `("300","688")` 硬编码会给出 10%，单点给出 30%，差异即暴露。
    """
    from datetime import date as _date

    from app.data_providers import mock as mock_mod
    from app.market.price_rules import limit_pct

    monkeypatch.setattr(
        mock_mod, "UNIVERSE",
        [("832175", "北交测试", "BJ"), ("300750", "宁德时代", "SZ"), ("600519", "贵州茅台", "SH")],
    )
    p = mock_mod.MockProvider()
    ups = asyncio.run(p.get_limit_up_pool(_date(2026, 9, 14)))
    downs = asyncio.run(p.get_limit_down_pool(_date(2026, 9, 14)))

    for rec in ups:
        assert rec.change_pct == limit_pct(rec.symbol, rec.name), f"{rec.symbol} 涨停幅度未走单点"
    for rec in downs:
        assert rec.change_pct == -limit_pct(rec.symbol, rec.name), f"{rec.symbol} 跌停幅度未走单点"

    assert {r.symbol: r.change_pct for r in ups}["832175"] == 30.0, "北交所应为 30%（硬编码实现会错成 10%）"


def test_theme_member_limit_up_uses_seal_predicate():
    """题材成员的 `limit_up` 判定必须走封板单点（原实现硬编码 9.7）。

    这里直接钉住单点语义：**同一涨幅在不同板块结论不同** —— 硬编码 9.7 时
    「创业板 +10%」会被误判为涨停，而「创业板 +19.8%」反而不算。
    """
    from app.picks.pre_limit_radar import board_limit_pct, is_sealed

    assert is_sealed(9.8, board_limit_pct("600519", "贵州茅台")) is True       # 主板 10%
    assert is_sealed(9.0, board_limit_pct("600519", "贵州茅台")) is False
    assert is_sealed(10.0, board_limit_pct("300750", "宁德时代")) is False     # 创业板 20%（旧硬编码误判为真）
    assert is_sealed(19.8, board_limit_pct("300750", "宁德时代")) is True
    assert is_sealed(29.8, board_limit_pct("832175", "北交测试")) is True      # 北交所 30%
    assert is_sealed(10.0, board_limit_pct("832175", "北交测试")) is False


def _code_text(src: str) -> str:
    """剥掉注释与字符串字面量，只留可执行代码文本。

    ⚠️ 为什么必须剥：这类阈值守卫是**子串判据**，而修复后的注释里往往要
    原样记录旧实现（"此前是 `X if y else Z`"）——裸子串匹配会把**解释**
    判成**违规**，逼后人把注释写含糊。判据应只看代码。
    """
    import io
    import tokenize

    parts: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            parts.append('""')
            continue
        parts.append(tok.string)
    return "".join(parts)


def test_theme_catalog_has_no_hardcoded_limit_threshold():
    """防止再把阈值硬编码回题材链路里（结构性守卫，与上面两条行为断言互补）。

    ⚠️ 2026-09-14 扩面：原判据**只扫路由** `app/api/routes/theme_catalog.py`，
    而第三处硬编码在 `app/services/theme_catalog_service.py`（题材强度聚合的
    `limit_up_count`）——只扫路由的判据对它恒真，等于漏判。这正是 KB-ENG-72
    「守卫的覆盖面与判据同等重要」的又一形态：**判据是对的，扫描面不全**。
    故此处按**文件清单**逐个断言，新增消费方必须显式登记。
    """
    root = Path(__file__).resolve().parents[1]
    scanned = [
        "app/api/routes/theme_catalog.py",
        "app/services/theme_catalog_service.py",
    ]
    for rel in scanned:
        src = (root / rel).read_text(encoding="utf-8")
        assert "is_sealed(" in src, f"{rel} 的封板判定未走单点"
        code = _code_text(src)
        assert ">= 9.7" not in code, f"{rel} 又出现硬编码涨停阈值 9.7"
        assert ">= 9.8" not in code, f"{rel} 又出现硬编码涨停阈值 9.8"
        assert ">= 19.8" not in code, f"{rel} 又出现硬编码 20cm 阈值"
        assert "19.8 if" not in code, f"{rel} 又出现硬编码 20cm 分支"


def test_theme_strength_limit_up_count_uses_board_rule():
    """题材强度的 `limit_up_count` 必须按**板块**判定，不能一套阈值套全板。

    与结构性守卫互补：结构断言只能证明「调了单点」，行为断言才能证明
    **同一涨幅在不同板块结论不同**。同一组输入两套实现的差值：
    旧硬编码 `9.8 / 19.8` 得 3，单点口径得 2 —— 分歧恰好落在北交所两只上。
    """
    from app.services.theme_catalog_service import aggregate_theme_strength

    members = {"T1": ["600000", "300750", "832175", "830001"]}
    quotes = {
        "600000": {"name": "浦发银行", "change_pct": 9.9, "amount": 1.0},    # 主板 10% → 涨停
        "300750": {"name": "宁德时代", "change_pct": 10.0, "amount": 1.0},   # 创业 20% → 非涨停
        "832175": {"name": "北交样本", "change_pct": 9.9, "amount": 1.0},    # 北交所 30% → 非涨停
        "830001": {"name": "北交样本二", "change_pct": 29.8, "amount": 1.0},  # 北交所 30% → 涨停
    }
    row = aggregate_theme_strength(members, quotes)["T1"]
    assert row["limit_up_count"] == 2, (
        f"只应认定主板 9.9% 与北交所 29.8% 两只涨停，实际 {row['limit_up_count']}"
        "（旧硬编码 9.8/19.8 会得 3：把北交所 +9.9% 误判为涨停、且漏判 29.8% 之外的档位）"
    )
    assert row["up"] == 4 and row["missing"] == 0


# ---------------------------------------------------------------- 5. 龙虎榜默认交易日


def test_longhu_default_date_uses_trading_calendar(monkeypatch):
    """默认日期必须取**日历上的最近交易日**，不能只做周末回退。

    注入法：把「今天」钉成节假日（日历里没有），日历上的最近交易日是上一天 ——
    旧实现（只处理 Sat/Sun）在周中节假日会返回今天这个非交易日，龙虎榜必然空。
    """
    from datetime import date as _date

    from app.api.routes import market as market_route

    holiday = _date(2026, 10, 1)  # 国庆（周四，周中节假日）
    monkeypatch.setattr(market_route, "beijing_today", lambda: holiday)

    async def fake_days(_provider):  # noqa: ANN001
        return [_date(2026, 9, 29), _date(2026, 9, 30)]

    monkeypatch.setattr(market_route, "trading_days", fake_days)
    hub = type("H", (), {"provider": object()})()
    assert asyncio.run(market_route._latest_trade_date(hub)) == _date(2026, 9, 30)


def test_longhu_default_date_falls_back_when_calendar_unavailable(monkeypatch):
    """日历不可用时退到周末规则（不 500、不静默）。"""
    from datetime import date as _date

    from app.api.routes import market as market_route
    from app.services import market_snapshot

    # ⚠️ 回退函数 `default_trade_date_weekend_fallback` 读的是**它自己模块**的
    # beijing_today（不是路由命名空间那个）—— 第一版测试 patch 错了对象，
    # 于是拿到真实当天、断言失败。这正是"钉住外部时钟要钉到真正读时钟的那一层"。
    monkeypatch.setattr(market_snapshot, "beijing_today", lambda: _date(2026, 9, 12))  # 周六

    async def boom(_provider):  # noqa: ANN001
        raise RuntimeError("calendar down")

    monkeypatch.setattr(market_route, "trading_days", boom)
    hub = type("H", (), {"provider": object()})()
    assert asyncio.run(market_route._latest_trade_date(hub)) == _date(2026, 9, 11)


def test_longhu_route_wires_the_calendar_date(monkeypatch):
    """钉住**接线**而非仅 helper：默认日期必须真的走日历。

    第一版只调 `_latest_trade_date(hub)`，于是把路由里那一行改回
    `default_trade_date_weekend_fallback()` 的注入**不会变红**（实测如此）——
    测试与修复点错位。本用例改从路由入口进，观察 provider 实际收到的日期。
    """
    from datetime import date as _date

    from app.api.routes import market as market_route

    holiday = _date(2026, 10, 1)  # 国庆（周中节假日）
    monkeypatch.setattr(market_route, "beijing_today", lambda: holiday)
    monkeypatch.setattr(market_route, "_meta", lambda _hub: {})  # 本用例只关心日期接线

    async def fake_days(_provider):  # noqa: ANN001
        return [_date(2026, 9, 29), _date(2026, 9, 30)]

    monkeypatch.setattr(market_route, "trading_days", fake_days)

    seen: dict = {}

    class _Provider:
        name = "stub"

        async def get_longhu_detail(self, symbol, trade_date):  # noqa: ANN001
            seen["date"] = trade_date
            return {"symbol": symbol, "trade_date": trade_date.isoformat()}

        async def get_longhu_history(self, symbol):  # noqa: ANN001
            return []

    hub = type("H", (), {"provider": _Provider()})()
    out = asyncio.run(market_route.longhu_detail("600519", None, hub))
    assert seen["date"] == _date(2026, 9, 30), "默认日期没走交易日历（接线断了）"
    assert out["data"]["detail"]["trade_date"] == "2026-09-30"


# ---------------------------------------------------------------- 6. 资金流水落盘原子性


def test_fund_flow_store_write_is_atomic(tmp_path, monkeypatch):
    """日流水库写盘必须原子（半截 JSON 会让历史成交额对比整段丢失）。"""
    from app.market import fund_flow as ff

    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "daily.json")
    seen: dict = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"], seen["dst"] = Path(src), Path(dst)
        seen["src_exists"] = Path(src).exists()
        return real_replace(src, dst)

    monkeypatch.setattr(ff.os, "replace", spy)
    ff._write_store_atomic({"updated_at": "x", "days": {"2026-09-14": {"sh": 1, "sz": 2}}})

    assert seen.get("src") != seen.get("dst"), "未走 tmp + os.replace"
    assert seen["src_exists"]
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------- 7. 写鉴权 limit 校验


def test_alert_events_limit_is_validated(client):
    """`limit` 必须带边界校验（原实现裸 `int = 50`：limit=-1 / 100000 都能进）。"""
    assert client.get("/api/alerts/events", params={"limit": 0}).status_code == 422
    assert client.get("/api/alerts/events", params={"limit": -1}).status_code == 422
    assert client.get("/api/alerts/events", params={"limit": 10_000}).status_code == 422
    assert client.get("/api/alerts/events", params={"limit": 20}).status_code == 200


# ---------------------------------------------------------------- 8. 情绪历史回填闸门


def test_sentiment_backfill_gate_only_closes_on_success(client, monkeypatch):
    """回填失败**不得**把一次性闸门关上（旧实现无论成败都置 done）。

    回退即红：旧实现下 `done` 会变成 True。
    """
    from app.api.routes import market as market_route
    from app.market import sentiment_history as sh

    monkeypatch.setattr(market_route, "_sent_hist_backfilled", {"done": False, "retry_after": 0.0})

    def boom(_sf):  # noqa: ANN001
        raise RuntimeError("db busy")

    monkeypatch.setattr(sh, "backfill_from_reports", boom)
    r = client.get("/api/market/sentiment-history", params={"days": 10})
    assert r.status_code == 200, r.text
    assert market_route._sent_hist_backfilled["done"] is False, "失败被记成完成 ⇒ 本进程内永不重试"
    assert market_route._sent_hist_backfilled["retry_after"] > 0, "失败后未设退避"


def test_sentiment_backfill_gate_closes_on_success(client, monkeypatch):
    from app.api.routes import market as market_route
    from app.market import sentiment_history as sh

    monkeypatch.setattr(market_route, "_sent_hist_backfilled", {"done": False, "retry_after": 0.0})
    monkeypatch.setattr(sh, "backfill_from_reports", lambda _sf: 2)
    r = client.get("/api/market/sentiment-history", params={"days": 10})
    assert r.status_code == 200, r.text
    assert market_route._sent_hist_backfilled["done"] is True
    assert market_route._sent_hist_backfilled["retry_after"] == 0.0


# ---------------------------------------------------------------- 8b. 惰性补录的交易日闸门（三态）


def _patch_lazy_capture(monkeypatch, days_list):
    """把 15:05 后惰性补录路径的输入全部钉死，返回记录容器。

    生产的取数路径：`market.py` 在函数内 `from app.market.trade_calendar import
    ...`，故 monkeypatch **模块属性**即可生效（不是补丁函数内名字）。
    """
    import datetime as dt

    from app.api.routes import market as market_route
    from app.market import sentiment_history as sh
    from app.market import trade_calendar as tc
    from app.services import market_context as mc

    # 周一 15:30（北京）——已过 15:05 闸门
    monkeypatch.setattr(market_route, "beijing_now_naive", lambda: dt.datetime(2026, 9, 14, 15, 30))
    monkeypatch.setattr(market_route, "_sent_hist_backfilled", {"done": True, "retry_after": 0.0})

    async def _days(_provider):  # noqa: ANN001
        return days_list

    monkeypatch.setattr(tc, "trading_days", _days)

    seen: dict[str, object] = {"upserts": [], "captures": 0}
    monkeypatch.setattr(sh, "get_history", lambda _sf, days=10: [])

    def _upsert(_sf, entry):  # noqa: ANN001
        seen["upserts"].append(entry)
        return True

    monkeypatch.setattr(sh, "upsert_if_absent", _upsert)

    async def _sent(_app, _hub):  # noqa: ANN001
        seen["captures"] += 1
        return {"phase": "分歧", "temperature": 50.0, "confidence": 0.5}

    monkeypatch.setattr(mc, "get_cached_sentiment", _sent)
    return seen


def test_lazy_capture_skips_on_confirmed_holiday(client, monkeypatch):
    """日历**已覆盖**今天且今天不是交易日（节假日）⇒ 不补录。三态里的 `False` 才是"可拦截"。"""
    import datetime as dt

    seen = _patch_lazy_capture(monkeypatch, [dt.date(2026, 9, 8), dt.date(2026, 9, 15)])
    r = client.get("/api/market/sentiment-history", params={"days": 10})
    assert r.status_code == 200, r.text
    assert seen["upserts"] == [], "确认非交易日却仍补录 ⇒ 会写出一个不存在的交易日"
    assert seen["captures"] == 0


def test_lazy_capture_runs_when_calendar_does_not_cover_today(client, monkeypatch):
    """日历**未覆盖**今天（尾随窗口 + 限频旧快照）⇒ `unknown` **不得**当「非交易日」用。

    回退即红（账本 §6.6 行 14）：旧实现 `is_trade_day = now_bj.date() in days_list`
    在日历未覆盖今天时返回 `False` ⇒ 整段补录被静默跳过，**且不产生任何告警**
    （数字出不来，用户无从判断是"本就无数据"还是"闸门判错了"）。

    与上一条**成对**：三态的价值就在于把「确认不是」与「判不出来」分开——
    只测一侧会把 `None` 塌缩成 `False` 的写法一起判绿（KB-ENG-65 假绿守卫）。
    """
    import datetime as dt

    seen = _patch_lazy_capture(monkeypatch, [dt.date(2026, 9, 10), dt.date(2026, 9, 11)])
    r = client.get("/api/market/sentiment-history", params={"days": 10})
    assert r.status_code == 200, r.text
    assert seen["captures"] == 1, "日历未覆盖今天 ⇒ 应乐观补录，而不是静默跳过"
    assert [e["trade_date"] for e in seen["upserts"]] == ["20260914"]
