"""板块资金徽标端点契约（P1-4 自选行 / P1-5 题材卡，2026-09-10）。

两条纪律的回归防线：
- **三态**：取不到 → 该标的/该字段不出现在返回里，或如实 None；绝不臆造 0 或默认板块。
- **口径分离**：题材卡上「合力（ths 成分快照聚合）」与「板块资金（东财 f62）」是两套
  口径，必须并列且各自标注——跨源混算是红线。

直调路由函数（绕过 Depends），桩掉上游，只验编排与契约。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import market_flow as flow_route
from app.api.routes import theme_catalog as tc_route


# ---------------------------------------------------------------- P1-4 自选行


class _ProfileProvider:
    """按 symbol 返回受控 F10 资料；`boom` 集合里的 symbol 抛错（模拟资料取不到）。"""

    name = "chain(mock)"

    def __init__(self, groups_by_symbol, codes_by_symbol, boom=()):
        self._groups = groups_by_symbol
        self._codes = codes_by_symbol
        self._boom = set(boom)
        self.calls = 0

    async def get_company_profile(self, symbol):
        self.calls += 1
        if symbol in self._boom:
            raise RuntimeError("F10 down")
        return {
            "board_groups": self._groups.get(symbol, {}),
            "board_codes": self._codes.get(symbol, {}),
        }


def _hub(provider):
    return SimpleNamespace(provider=provider, name="chain(mock)", last_success_refresh=None,
                           is_stale=lambda: False)


def _request():
    # cache_on 只会 getattr/setattr，SimpleNamespace 足够
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _patch_board_list(monkeypatch, rows_by_kind):
    from app.market import board_flow as bf

    async def fake_list(kind):
        return rows_by_kind.get(kind, []), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)


def test_by_symbols_uses_industry_l2_and_attaches_fund(monkeypatch):
    import asyncio

    _patch_board_list(monkeypatch, {
        "industry": [{"board_code": "BK1277", "name": "白酒Ⅱ", "kind": "industry",
                      "change_pct": -2.52, "main_net_yi": -12.26, "main_net_ratio": -3.1}],
        "concept": [],
    })
    from app.market import board_flow as bf
    monkeypatch.setattr(bf, "get_board_streaks", lambda m: {"BK1277": 3})

    provider = _ProfileProvider(
        {"600519": {"industry": ["食品饮料", "白酒Ⅱ", "白酒Ⅲ"], "concept": ["味蕾经济"]}},
        {"600519": {"白酒Ⅱ": "1277", "味蕾经济": "1653"}},
    )
    payload = asyncio.run(
        flow_route.market_board_fund_by_symbols(
            request=_request(), symbols="600519", hub=_hub(provider)
        )
    )
    b = payload["data"]["boards"]["600519"]
    # 主板块取行业 L2，而不是概念段首个（「味蕾经济」）
    assert b["board_name"] == "白酒Ⅱ"
    assert b["board_code"] == "BK1277"
    assert b["level"] == "industry"
    assert b["main_net_yi"] == -12.26
    assert b["streak"] == 3
    # 代码规范化生效：F10 的 "1277" 必须变成榜里的 "BK1277"（否则按 code 直取全 miss）
    assert provider.calls == 1


def test_by_symbols_failed_profile_is_absent_not_fabricated(monkeypatch):
    """资料取不到 → 该 symbol 不出现在返回里（三态），note 如实说明。"""
    import asyncio

    _patch_board_list(monkeypatch, {"industry": [], "concept": []})
    provider = _ProfileProvider({}, {}, boom={"600519"})
    payload = asyncio.run(
        flow_route.market_board_fund_by_symbols(
            request=_request(), symbols="600519", hub=_hub(provider)
        )
    )
    assert payload["data"]["boards"] == {}
    assert payload["data"]["note"]


def test_by_symbols_board_not_listed_keeps_profile_identity(monkeypatch):
    """资金榜缺行不抹掉已知归属；资金与榜单分类如实未知，不臆造 0。"""
    import asyncio

    _patch_board_list(monkeypatch, {"industry": [], "concept": []})
    provider = _ProfileProvider(
        {"600519": {"industry": ["食品饮料", "白酒Ⅱ"]}},
        {"600519": {"白酒Ⅱ": "1277"}},
    )
    payload = asyncio.run(
        flow_route.market_board_fund_by_symbols(
            request=_request(), symbols="600519", hub=_hub(provider)
        )
    )
    assert payload["data"]["boards"]["600519"] == {
        "board_name": "白酒Ⅱ", "board_code": "BK1277", "level": "industry",
        "kind": None, "change_pct": None, "main_net_yi": None,
        "main_net_ratio": None, "streak": None,
    }


def test_by_symbols_missing_code_keeps_known_name_without_inventing_code(monkeypatch):
    """资料有概念名称但无代码：保留明确口径，不按名字猜代码或资金。"""
    import asyncio

    _patch_board_list(monkeypatch, {
        "concept": [{"board_code": "BK1071", "name": "跨境支付", "kind": "concept",
                     "change_pct": 1.0, "main_net_yi": 2.0, "main_net_ratio": 3.0}],
    })
    provider = _ProfileProvider({"000001": {"concept": ["跨境支付"]}}, {})
    payload = asyncio.run(flow_route.market_board_fund_by_symbols(
        request=_request(), symbols="000001", hub=_hub(provider)
    ))
    assert payload["data"]["boards"]["000001"] == {
        "board_name": "跨境支付", "board_code": None, "level": "concept",
        "kind": None, "change_pct": None, "main_net_yi": None,
        "main_net_ratio": None, "streak": None,
    }


def test_by_symbols_empty_board_ref_is_absent(monkeypatch):
    """资料取到了但判不出主板块，仍缺省，不从资金榜补造归属。"""
    import asyncio

    _patch_board_list(monkeypatch, {"industry": [], "concept": []})
    payload = asyncio.run(flow_route.market_board_fund_by_symbols(
        request=_request(), symbols="600519", hub=_hub(_ProfileProvider({}, {}))
    ))
    assert payload["data"]["boards"] == {}
    assert payload["data"]["note"]


@pytest.mark.parametrize("initial", ["http_error", "network_error", "empty"])
def test_unproven_profile_ref_recovers_after_batch_ttl_not_six_hours(monkeypatch, initial):
    """Optional source failures and genuine empty groups have no distinct proof."""
    import asyncio
    import httpx
    from app.core import ttl_cache
    from app.data_providers.eastmoney import EastmoneyProvider
    from app.services import theme_service

    clock = [ttl_cache.monotonic()]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    counts = {"basic": 0, "boards": 0}
    stage = [initial]

    def response(request):
        if "datacenter-web" in request.url.host:
            counts["basic"] += 1
            return httpx.Response(200, json={"result": {"data": [{
                "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "fixture",
            }]}})
        counts["boards"] += 1
        if stage[0] == "network_error":
            raise httpx.ConnectError("isolated unavailable", request=request)
        if stage[0] == "http_error":
            return httpx.Response(503, json={})
        if stage[0] == "empty":
            return httpx.Response(200, json={"ssbk": [], "hxtc": []})
        return httpx.Response(200, json={"ssbk": [
            {"BOARD_NAME": "食品饮料", "BOARD_CODE": "0438", "BOARD_RANK": 1},
            {"BOARD_NAME": "白酒Ⅱ", "BOARD_CODE": "1277", "BOARD_RANK": 2},
        ]})

    async def no_fund_rows(_codes):
        return {}

    monkeypatch.setattr(theme_service, "board_rows_for_codes", no_fund_rows)
    request = _request()

    async def scenario():
        provider = EastmoneyProvider.__new__(EastmoneyProvider)
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:
            hub = _hub(provider)
            first = await flow_route.market_board_fund_by_symbols(request, "600519", hub)
            assert first["data"]["boards"] == {} and first["data"]["note"]
            stage[0] = "ready"
            assert (await flow_route.market_board_fund_by_symbols(request, "600519", hub))["data"] == first["data"]
            assert counts == {"basic": 1, "boards": 1}  # Existing batch cache stays 60s.
            clock[0] += 61
            recovered = await flow_route.market_board_fund_by_symbols(request, "600519", hub)
            assert recovered["data"]["boards"]["600519"] == {
                "board_name": "白酒Ⅱ", "board_code": "BK1277", "level": "industry",
                "kind": None, "change_pct": None, "main_net_yi": None,
                "main_net_ratio": None, "streak": None,
            }
            assert counts == {"basic": 2, "boards": 2}
            clock[0] += 61
            stage[0] = "network_error"
            again = await flow_route.market_board_fund_by_symbols(request, "600519", hub)
            assert again["data"] == recovered["data"]
            assert counts == {"basic": 2, "boards": 2}  # Proven ref retains its 6h cache.
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_genuine_empty_ref_remains_unknown_and_only_retries_after_batch_ttl(monkeypatch):
    import asyncio
    from app.core import ttl_cache

    clock = [ttl_cache.monotonic()]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    _patch_board_list(monkeypatch, {"industry": [], "concept": []})
    provider = _ProfileProvider({}, {})
    request, hub = _request(), _hub(provider)

    async def scenario():
        for _ in range(2):
            result = await flow_route.market_board_fund_by_symbols(request, "600519", hub)
            assert result["data"]["boards"] == {} and result["data"]["note"]
        assert provider.calls == 1
        clock[0] += 61
        result = await flow_route.market_board_fund_by_symbols(request, "600519", hub)
        assert result["data"]["boards"] == {} and result["data"]["note"]
        assert provider.calls == 2

    asyncio.run(scenario())


def test_unknown_ref_short_cache_is_shared_across_symbol_batches(monkeypatch):
    import asyncio
    from app.core import ttl_cache

    clock = [ttl_cache.monotonic()]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    _patch_board_list(monkeypatch, {"industry": [], "concept": []})
    provider = _ProfileProvider({"000001": {"industry": ["金融", "银行Ⅱ"]}},
                                {"000001": {"银行Ⅱ": "0475"}})
    request, hub = _request(), _hub(provider)

    async def scenario():
        for batch in ("600519", "600519,000001", "000001,600519"):
            payload = await flow_route.market_board_fund_by_symbols(request, batch, hub)
            assert "600519" not in payload["data"]["boards"]
        assert provider.calls == 2  # Both symbols read once despite three batch keys.
        provider._groups["600519"] = {"industry": ["食品饮料", "白酒Ⅱ"]}
        provider._codes["600519"] = {"白酒Ⅱ": "1277"}
        clock[0] += 61
        result = await flow_route.market_board_fund_by_symbols(request, "600519,000001", hub)
        assert set(result["data"]["boards"]) == {"600519", "000001"}
        assert provider.calls == 3  # Only the unknown ref expires; the positive stays 6h.

    asyncio.run(scenario())


def test_by_symbols_partial_fund_rows_keep_identity_and_reuse_cache(monkeypatch):
    """同一批有效资金与缺资金归属并存；身份复用，动态读取原资金缓存入口。"""
    import asyncio

    from app.services import theme_service as ts

    fund_calls = []

    async def fake_rows(codes):
        fund_calls.append(codes)
        return {"BK0475": {"board_code": "BK0475", "name": "银行", "kind": "industry",
                           "change_pct": 0.0, "main_net_yi": 0.0, "main_net_ratio": 0.0,
                           "streak": 0}}

    monkeypatch.setattr(ts, "board_rows_for_codes", fake_rows)
    provider = _ProfileProvider(
        {"600519": {"industry": ["食品饮料", "白酒Ⅱ"]},
         "000001": {"industry": ["金融", "银行Ⅱ"]}},
        {"600519": {"白酒Ⅱ": "1277"}, "000001": {"银行Ⅱ": "475"}},
    )
    request = _request()
    hub = _hub(provider)

    async def read_twice():
        first = await flow_route.market_board_fund_by_symbols(
            request=request, symbols="600519,000001,600000", hub=hub
        )
        second = await flow_route.market_board_fund_by_symbols(
            request=request, symbols="600519,000001,600000", hub=hub
        )
        assert second["data"] == first["data"]  # 信封 generated_at 每次读取照常更新
        return first

    boards = asyncio.run(read_twice())["data"]["boards"]
    assert set(boards) == {"600519", "000001"}  # 无 ref 的 600000 仍缺省
    assert boards["600519"]["board_name"] == "白酒Ⅱ"
    assert boards["600519"]["main_net_yi"] is None
    assert boards["000001"] == {
        "board_name": "银行", "board_code": "BK0475", "level": "industry",
        "kind": "industry", "change_pct": 0.0, "main_net_yi": 0.0,
        "main_net_ratio": 0.0, "streak": 0,
    }
    assert provider.calls == 3
    # Each response reads the original board cache; the route adds no second TTL.
    assert fund_calls == [["BK1277", "BK0475"], ["BK1277", "BK0475"]]


def test_by_symbols_rejects_empty_and_oversized():
    import asyncio

    import pytest
    from fastapi import HTTPException

    for bad in ("", " , "):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(flow_route.market_board_fund_by_symbols(
                request=_request(), symbols=bad, hub=_hub(_ProfileProvider({}, {}))
            ))
        assert ei.value.status_code == 422

    too_many = ",".join(f"{600000 + i}" for i in range(51))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(flow_route.market_board_fund_by_symbols(
            request=_request(), symbols=too_many, hub=_hub(_ProfileProvider({}, {}))
        ))
    assert ei.value.status_code == 422


def test_by_symbols_concept_fallback_is_level_labelled(monkeypatch):
    """无行业段时回落概念首个，level 如实标 concept（不冒充行业）。"""
    import asyncio

    _patch_board_list(monkeypatch, {
        "industry": [],
        "concept": [{"board_code": "BK1071", "name": "跨境支付", "kind": "concept",
                     "change_pct": -0.75, "main_net_yi": -6.23, "main_net_ratio": -2.0}],
    })
    from app.market import board_flow as bf
    monkeypatch.setattr(bf, "get_board_streaks", lambda m: {})

    provider = _ProfileProvider(
        {"000001": {"industry": [], "concept": ["跨境支付", "区块链"]}},
        {"000001": {"跨境支付": "1071"}},
    )
    payload = asyncio.run(
        flow_route.market_board_fund_by_symbols(
            request=_request(), symbols="000001", hub=_hub(provider)
        )
    )
    b = payload["data"]["boards"]["000001"]
    assert b["level"] == "concept"
    assert b["board_name"] == "跨境支付"
    assert b["streak"] is None  # 未落盘 → None，不是 0


# ---------------------------------------------------------------- P1-5 题材卡


class _FakeSvc:
    """题材目录服务桩。带调用计数——P0-2 的验收就是「查库次数」，不看行为看次数。"""

    def __init__(self, catalog, members):
        self._catalog = catalog
        self._members = members
        self.catalog_calls = 0
        self.member_calls = 0  # 单条 get_members（P0-2 后应为 0，只许走 bulk）
        self.bulk_calls = 0

    def get_catalog(self, search=None, limit=500):
        self.catalog_calls += 1
        return [SimpleNamespace(code=c, name=n) for c, n in self._catalog.items()]

    def get_members(self, code):
        self.member_calls += 1
        return [SimpleNamespace(symbol=s) for s in self._members.get(code, [])]

    def member_symbols_bulk(self, codes):
        """镜像真实实现：只返回有成分的题材，且**按 symbol 定序**（[:200] 截断依赖次序）。"""
        self.bulk_calls += 1
        out: dict[str, list[str]] = {}
        for c in codes:
            syms = sorted(self._members.get(c, []))
            if syms:
                out[c] = syms
        return out


def _patch_quotes(monkeypatch, quotes):
    from app.services import quote_enrich

    async def fake_batched(hub, symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fake_batched)


def _q(symbol, name, pct, amount=1e8):
    return SimpleNamespace(symbol=symbol, name=name, price=10.0, change_pct=pct, amount=amount)


def test_strength_payload_carries_board_alongside_force(monkeypatch):
    """board 与合力**并存**：合力字段照旧，board 单独挂在同一题材条目上。"""
    import asyncio

    from app.services import theme_service as ts

    async def fake_rows(names):
        assert "绿色电力" in names
        return {"绿色电力": {"board_code": "BK1024", "name": "绿色电力", "kind": "concept",
                             "change_pct": 0.19, "main_net_yi": 32.33, "main_net_ratio": 5.1,
                             "streak": 4}}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)
    _patch_quotes(monkeypatch, {"600105": _q("600105", "永鼎股份", 5.2)})

    svc = _FakeSvc({"881156.TI": "绿色电力"}, {"881156.TI": ["600105"]})
    payload = asyncio.run(tc_route.theme_strength(
        request=_request(), codes="881156.TI", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    row = payload["data"]["themes"]["881156.TI"]
    assert row["up"] == 1                       # 合力字段（ths 成分快照聚合）照旧存在
    assert row["board"]["main_net_yi"] == 32.33  # 东财 f62 口径单独挂载
    assert row["board"]["streak"] == 4
    # 两套口径各自标注，避免混算
    assert "合力" in payload["meta"]["basis"]
    assert "不可相加" in payload["meta"]["board_basis"]


def test_strength_board_absent_when_unmatched(monkeypatch):
    """题材匹配不到东财板块 → board 为 None（不臆造一个板块顶上）。"""
    import asyncio

    from app.services import theme_service as ts

    async def fake_rows(names):
        return {}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)
    _patch_quotes(monkeypatch, {"600105": _q("600105", "永鼎股份", 5.2)})

    svc = _FakeSvc({"881156.TI": "绿色电力"}, {"881156.TI": ["600105"]})
    payload = asyncio.run(tc_route.theme_strength(
        request=_request(), codes="881156.TI", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    assert payload["data"]["themes"]["881156.TI"]["board"] is None


def test_strength_board_failure_does_not_break_force(monkeypatch):
    """板块资金取数异常 → 只丢 board，合力照常返回（增强项不拖垮主链路）。"""
    import asyncio

    from app.services import theme_service as ts

    async def boom(names):
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(ts, "board_rows_for_names", boom)
    _patch_quotes(monkeypatch, {"600105": _q("600105", "永鼎股份", 5.2)})

    svc = _FakeSvc({"881156.TI": "绿色电力"}, {"881156.TI": ["600105"]})
    payload = asyncio.run(tc_route.theme_strength(
        request=_request(), codes="881156.TI", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    row = payload["data"]["themes"]["881156.TI"]
    assert row["up"] == 1
    assert row["board"] is None


# ------------------------------------------------- P0-2 查库次数（缓存前移 + 批量成分）


def _patch_board_rows(monkeypatch):
    from app.services import theme_service as ts

    async def fake_rows(names):
        return {}

    monkeypatch.setattr(ts, "board_rows_for_names", fake_rows)


def test_strength_default_path_uses_bulk_not_n_plus_1(monkeypatch):
    """默认全量路径：成分只查一次（bulk），**不再逐题材单查**（原为 N+1，实测约 390 次）。"""
    import asyncio

    _patch_board_rows(monkeypatch)
    _patch_quotes(monkeypatch, {"600105": _q("600105", "永鼎股份", 5.2)})

    catalog = {f"88{i:04d}.TI": f"题材{i}" for i in range(30)}
    members = {"880003.TI": ["600105"]}  # 只有 1 个题材有成分
    svc = _FakeSvc(catalog, members)
    payload = asyncio.run(tc_route.theme_strength(
        request=_request(), codes="", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    assert svc.bulk_calls == 1, "成分必须走一次批量查询"
    assert svc.member_calls == 0, "不得回落到逐题材单查（N+1）"
    assert list(payload["data"]["themes"]) == ["880003.TI"], "无成分的题材不进结果"


def test_strength_cache_hit_performs_zero_member_queries(monkeypatch):
    """缓存命中路径必须**零查库**——改造前命中也要照付 391 次查询（P0-2 的真问题）。"""
    import asyncio

    _patch_board_rows(monkeypatch)
    _patch_quotes(monkeypatch, {"600105": _q("600105", "永鼎股份", 5.2)})

    svc = _FakeSvc({"881156.TI": "绿色电力"}, {"881156.TI": ["600105"]})
    request = _request()  # 同一个 request ⇒ 同一份 app.state 缓存
    first = asyncio.run(tc_route.theme_strength(
        request=request, codes="881156.TI", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    assert svc.bulk_calls == 1 and svc.catalog_calls == 1

    second = asyncio.run(tc_route.theme_strength(
        request=request, codes="881156.TI", hub=_hub(_ProfileProvider({}, {})), svc=svc
    ))
    assert second == first, "二次请求必须拿到同一份缓存载荷"
    assert svc.bulk_calls == 1, "缓存命中不得再查成分"
    assert svc.catalog_calls == 1, "缓存命中不得再查目录"
    assert svc.member_calls == 0
