from __future__ import annotations


from app.main import app
from app.news.router import SummaryRouter
from app.news.rules import RulesSummarizer, _looks_like_table, _strip_title_prefix


def _rules() -> RulesSummarizer:
    return RulesSummarizer()


def test_importance_ranks_earnings_above_routine():
    r = _rules()
    earnings = r.summarize_item({"title": "贵州茅台2026年半年度报告摘要", "type": "半年度报告摘要"},
                                kind="announcement")
    routine = r.summarize_item({"title": "贵州茅台:贵州茅台关于召开2026年半年度业绩说明会的公告"},
                               kind="announcement")
    assert earnings["importance"] == "高"
    assert routine["importance"] == "低"
    assert earnings["importance_score"] > routine["importance_score"]


def test_risk_news_ranked_highest():
    r = _rules()
    risk = r.summarize_item({"title": "公司收到证监会立案告知书"}, kind="news")
    assert risk["importance"] == "高"
    assert any("监管" in x for x in risk["importance_reasons"])


def test_major_event_recognized():
    """"重大事项公告"是通用但确实值得看的标题，不能因为词表漏词就判成普通。"""
    r = _rules()
    out = r.summarize_item({"title": "贵州茅台:贵州茅台重大事项公告"}, kind="announcement")
    assert out["importance"] == "中"
    assert any("重大事项" in x for x in out["importance_reasons"])


def test_sentiment_positive_negative_and_mixed():
    r = _rules()
    assert r.summarize_item({"title": "公司业绩预增，净利润大幅增长"}, kind="news")["sentiment"] == "偏正面"
    assert r.summarize_item({"title": "公司涉嫌违规被立案，股价跌停"}, kind="news")["sentiment"] == "偏负面"
    mixed = r.summarize_item({"title": "业绩预增但股东减持"}, kind="news")
    assert mixed["sentiment"] == "分歧"


def test_table_dump_summary_falls_back_to_title():
    """东财部分 summary 是行情表原文，必须整段丢弃回退到标题，不能当摘要用。"""
    r = _rules()
    table = "计算机 688041 海光信息 -0.47 20875.30 918369.62 1.71 电子 301205 联特科技 2.40 17182.46 215516.51 8.27"
    assert _looks_like_table(table) is True
    out = r.summarize_item(
        {"title": "14股获杠杆资金净买入超亿元", "summary": table}, kind="news"
    )
    assert "海光信息" not in out["digest"]
    assert "杠杆资金" in out["digest"]
    assert "表格" in out["digest_source"]


def test_fullwidth_space_normalized():
    r = _rules()
    out = r.summarize_item(
        {"title": "贵州茅台发布中报", "summary": "公司营业总收入为922.78亿元，\u3000\u3000同比增长1.30%。"},
        kind="news",
    )
    assert "\u3000" not in out["digest"]
    assert out["digest_source"] == "正文"


def test_strip_title_prefix():
    assert _strip_title_prefix("贵州茅台:贵州茅台关于召开2026年半年度业绩说明会的公告") == \
        "关于召开2026年半年度业绩说明会的公告"
    # 尾巴太短时保留原标题，别把正文砍没了
    assert _strip_title_prefix("关于:停牌") == "关于:停牌"


def test_numbers_extracted():
    r = _rules()
    out = r.summarize_item(
        {"title": "中报净利润为445.17亿元、同比较去年同期下降1.95%", "summary": ""}, kind="news"
    )
    assert "-1.95%" in out["numbers"] or "1.95%" in out["numbers"]
    assert "445.17亿元" in out["numbers"]


def test_summary_sorted_by_importance():
    r = _rules()
    news = [
        {"title": "白酒概念下跌0.15%", "summary": ""},
        {"title": "公司收到证监会立案告知书", "summary": ""},
    ]
    out = r.summarize(news, [])
    assert "立案" in out["news"][0]["title"]


def test_router_degrades_explicitly_when_llm_unconfigured():
    """未配 LLM 时必须降级到 rules，且 degraded/reason 显式回传。"""
    rt = SummaryRouter(requested="llm")
    _, usage = rt.summarize([{"title": "业绩预增", "summary": ""}], [])
    assert usage["requested"] == "llm"
    assert usage["actual"] == "rules"
    assert usage["degraded"] is True
    assert "未配置" in usage["reason"]


def test_router_rules_mode_not_marked_degraded():
    rt = SummaryRouter(requested="rules")
    _, usage = rt.summarize([{"title": "业绩预增", "summary": ""}], [])
    assert usage["actual"] == "rules"
    assert usage["degraded"] is False


_NEWS_FIXTURE = [
    {
        "symbol": "600519",
        "title": "贵州茅台2026年半年度报告摘要",
        "date": "2026-08-15 10:11",
        # 含全角空格的正常正文
        "summary": "公司营业总收入为922.78亿元，\u3000\u3000同比增长1.30%。",
        "url": "https://example.com/1",
        "source": "eastmoney",
    },
    {
        "symbol": "600519",
        "title": "14股获杠杆资金净买入超亿元",
        "date": "2026-08-27 09:29",
        # 东财常见：summary 直接塞行情表，必须整段丢弃
        "summary": "计算机 688041 海光信息 -0.47 20875.30 918369.62 1.71 电子 301205 联特科技 2.40",
        "url": "https://example.com/2",
        "source": "eastmoney",
    },
]

_ANN_FIXTURE = [
    {
        "symbol": "600519",
        "title": "贵州茅台:贵州茅台关于召开2026年半年度业绩说明会的公告",
        "date": "2026-08-15",
        "type": "其他",
        "url": "https://example.com/a1",
        "source": "eastmoney",
    }
]


def test_news_digest_api(client, monkeypatch):
    """端到端验证路由与序列化。数据源必须 mock——CI 无外网，真打会 502。"""

    async def fake_news(symbol: str, limit: int = 10):
        return [dict(r) for r in _NEWS_FIXTURE]

    async def fake_ann(symbol: str, limit: int = 10):
        return [dict(r) for r in _ANN_FIXTURE]

    # 测试环境 hub.provider 是 MockProvider（没有这两个方法），raising=False 才允许挂载
    monkeypatch.setattr(app.state.hub.provider, "get_news", fake_news, raising=False)
    monkeypatch.setattr(
        app.state.hub.provider, "get_announcements", fake_ann, raising=False
    )

    resp = client.get("/api/news/digest/600519?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["symbol"] == "600519"
    assert data["model"]["actual"] == "rules"
    assert data["model"]["degraded"] is False

    news = data["news"]
    assert len(news) == 2
    # 中报（高）排在资金流（普通）前面
    assert "半年度报告" in news[0]["title"]
    assert news[0]["importance"] == "高"
    for item in news:
        assert item["importance"] in {"高", "中", "普通", "低"}
        assert item["sentiment"] in {"偏正面", "偏负面", "分歧", "中性"}
        assert item["digest"]
        assert item["digest_source"]

    # 表格型正文被丢弃，回退到标题
    table_item = next(n for n in news if "杠杆资金" in n["title"])
    assert "海光信息" not in table_item["digest"]
    assert "表格" in table_item["digest_source"]

    # 公告："贵州茅台:贵州茅台关于…" 的双层前缀应被剥掉
    ann = data["announcements"][0]
    assert ann["digest"].startswith("关于召开")
    # 正常路径：双侧数据源错误字段必须为 None（三态显式）
    assert data["news_error"] is None
    assert data["announcements_error"] is None


def test_news_digest_degrades_when_news_source_fails(client, monkeypatch):
    """新闻源失败 → 公告照常返回 + news_error 显式透出（不整体 502）；
    双侧都挂才 502。东财搜索接口有间歇软封锁（2026-09-04 实测）。
    注意 digest 有 60s TTL 缓存（键=symbol+limit），换标的避免撞上一个用例的缓存。"""

    async def fail_news(symbol: str, limit: int = 10):
        raise RuntimeError("empty reply from search-api (可能被限流)")

    async def fake_ann(symbol: str, limit: int = 10):
        return [dict(r) for r in _ANN_FIXTURE]

    monkeypatch.setattr(app.state.hub.provider, "get_news", fail_news, raising=False)
    monkeypatch.setattr(app.state.hub.provider, "get_announcements", fake_ann, raising=False)

    resp = client.get("/api/news/digest/000001?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["news"] == []
    assert "可能被限流" in data["news_error"]
    assert data["announcements_error"] is None
    assert len(data["announcements"]) == 1


def test_news_digest_502_when_both_sources_fail(client, monkeypatch):
    async def fail(symbol: str, limit: int = 10):
        raise RuntimeError("down")

    monkeypatch.setattr(app.state.hub.provider, "get_news", fail, raising=False)
    monkeypatch.setattr(app.state.hub.provider, "get_announcements", fail, raising=False)

    resp = client.get("/api/news/digest/000002?limit=5")
    assert resp.status_code == 502
    assert "均失败" in resp.json()["detail"]


def _direct_digest(provider, state, *, symbol="600519", limit=5):
    from types import SimpleNamespace
    from app.api.routes.news import news_digest

    request = SimpleNamespace(app=SimpleNamespace(state=state))
    return news_digest(symbol, request, limit, SimpleNamespace(provider=provider))


def test_digest_actual_provider_missing_both_capabilities_returns_502():
    import asyncio
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from app.api.deps import get_hub
    from app.api.routes import news as route
    from app.data_providers.mock import MockProvider

    provider = MockProvider()
    assert not hasattr(provider, "get_news") and not hasattr(provider, "get_announcements")
    api = FastAPI()
    api.include_router(route.router, prefix="/api")
    api.dependency_overrides[get_hub] = lambda: SimpleNamespace(provider=provider)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api, raise_app_exceptions=False),
                                     base_url="http://isolated") as client:
            response = await client.get("/api/news/digest/600519?limit=5")
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "get_news" in detail and "get_announcements" in detail

    asyncio.run(scenario())


def test_digest_single_missing_capability_keeps_available_side_and_error():
    import asyncio
    from types import SimpleNamespace

    async def news(*_args):
        return [dict(row) for row in _NEWS_FIXTURE]

    async def ann(*_args):
        return [dict(row) for row in _ANN_FIXTURE]

    async def scenario():
        # Deliberately absent attributes, not methods that simulate failure.
        for available, missing, rows_key, error_key in [
            (SimpleNamespace(get_announcements=ann), "get_news", "announcements", "news_error"),
            (SimpleNamespace(get_news=news), "get_announcements", "news", "announcements_error"),
        ]:
            result = await _direct_digest(available, SimpleNamespace())
            assert result["data"][rows_key]
            assert missing in result["data"][error_key]
            other_rows = "news" if rows_key == "announcements" else "announcements"
            other_error = "news_error" if error_key == "announcements_error" else "announcements_error"
            assert result["data"][other_rows] == []
            assert result["data"][other_error] is None

    asyncio.run(scenario())


def test_digest_concurrent_cold_requests_share_sources_and_summary(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.api.routes import news as route
    from app.core import ttl_cache

    calls = {"news": 0, "ann": 0, "summary": 0}
    original = route.SummaryRouter.summarize

    def summarize(self, news, ann):
        calls["summary"] += 1
        return original(self, news, ann)

    monkeypatch.setattr(route.SummaryRouter, "summarize", summarize)

    async def scenario():
        gate, started = asyncio.Event(), asyncio.Event()

        async def news(_symbol, _limit):
            calls["news"] += 1
            started.set()
            await gate.wait()
            return [dict(r) for r in _NEWS_FIXTURE]

        async def ann(_symbol, _limit):
            calls["ann"] += 1
            return [dict(r) for r in _ANN_FIXTURE]

        provider = SimpleNamespace(get_news=news, get_announcements=ann)
        state = SimpleNamespace()
        tasks = [asyncio.create_task(_direct_digest(provider, state)) for _ in range(4)]
        await asyncio.wait_for(started.wait(), 1)
        gate.set()
        results = await asyncio.gather(*tasks)
        assert calls == {"news": 1, "ann": 1, "summary": 1}
        assert all(result["data"] == results[0]["data"] for result in results)
        assert (await _direct_digest(provider, state))["meta"]["cached"] is True
        assert calls == {"news": 1, "ann": 1, "summary": 1}
        # Patch the cache clock only; the event loop clock remains real.
        clock = ttl_cache.monotonic
        monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock() + 301)
        await _direct_digest(provider, state)
        assert calls == {"news": 2, "ann": 2, "summary": 2}
        await _direct_digest(provider, state, limit=6)
        await _direct_digest(provider, state, symbol="000001", limit=6)
        assert calls == {"news": 4, "ann": 4, "summary": 4}

    asyncio.run(scenario())


def test_digest_starts_independent_sources_before_either_finishes():
    import asyncio
    from types import SimpleNamespace

    async def scenario():
        news_started, ann_started = asyncio.Event(), asyncio.Event()
        release = asyncio.Event()

        async def news(_symbol, _limit):
            news_started.set()
            await release.wait()
            return []

        async def ann(_symbol, _limit):
            ann_started.set()
            await release.wait()
            return []

        task = asyncio.create_task(_direct_digest(
            SimpleNamespace(get_news=news, get_announcements=ann), SimpleNamespace()))
        try:
            await asyncio.wait_for(news_started.wait(), 1)
            await asyncio.wait_for(ann_started.wait(), 0.1)
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_digest_same_failed_flight_is_shared_and_next_request_recovers(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api.routes import news as route

    calls = {"news": 0, "ann": 0}

    def forbidden_summary(*_args, **_kwargs):
        raise AssertionError("Both source failures must not reach summary")

    original = route.SummaryRouter.summarize
    monkeypatch.setattr(route.SummaryRouter, "summarize", forbidden_summary)

    async def scenario():
        gate, started = asyncio.Event(), asyncio.Event()
        failing = True

        async def read(kind):
            calls[kind] += 1
            started.set()
            await gate.wait()
            if failing:
                raise RuntimeError(f"{kind} unavailable")
            return []

        provider = SimpleNamespace(get_news=lambda *_: read("news"),
                                   get_announcements=lambda *_: read("ann"))
        state = SimpleNamespace()
        tasks = [asyncio.create_task(_direct_digest(provider, state)) for _ in range(4)]
        await asyncio.wait_for(started.wait(), 1)
        gate.set()
        errors = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(error, HTTPException) and error.status_code == 502 for error in errors)
        assert all("news unavailable" in error.detail and "ann unavailable" in error.detail for error in errors)
        assert calls == {"news": 1, "ann": 1}
        failing = False
        monkeypatch.setattr(route.SummaryRouter, "summarize", original)
        recovered = await _direct_digest(provider, state)
        assert recovered["data"]["news_error"] is None
        assert recovered["data"]["announcements_error"] is None
        assert calls == {"news": 2, "ann": 2}

    asyncio.run(scenario())


def test_digest_cancelled_waiter_does_not_duplicate_or_stop_summary_thread(monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    import pytest
    from app.api.routes import news as route

    started, release = threading.Event(), threading.Event()
    jobs = []
    original = route.SummaryRouter.summarize

    def summarize(self, news, ann):
        jobs.append("started")
        started.set()
        assert release.wait(timeout=2)
        result = original(self, news, ann)
        jobs.append("drained")
        return result

    monkeypatch.setattr(route.SummaryRouter, "summarize", summarize)

    async def rows(*_args):
        return []

    async def wait_started():
        while not started.is_set():
            await asyncio.sleep(0)

    async def scenario():
        provider = SimpleNamespace(get_news=rows, get_announcements=rows)
        state = SimpleNamespace()
        owner = asyncio.create_task(_direct_digest(provider, state))
        survivor = None
        try:
            await asyncio.wait_for(wait_started(), 1)
            survivor = asyncio.create_task(_direct_digest(provider, state))
            await asyncio.sleep(0)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert jobs == ["started"]  # The actual thread has not drained.
            release.set()
            result = await survivor
            assert result["data"]["model"]["actual"] == "rules"
            assert (await _direct_digest(provider, state))["meta"]["cached"] is True
            assert jobs == ["started", "drained"]
        finally:
            release.set()
            await asyncio.gather(owner, *([survivor] if survivor else []), return_exceptions=True)

    asyncio.run(scenario())
