"""HTTP 端点冒烟（结构 + 契约，不测业务细节）。

**共享「模块级」`client` fixture（P1-25，2026-09-10）**：本文件 12 个用例原先各自
`with TestClient(app)`，**每个都重建一次 lifespan**（建库 + 起 QuoteHub/snapshot），
本机实测单次 35–46s、整文件 **7 分 24 秒**。改用 `client` 后整文件降到 **38 秒**
（启动成本只付一次）。作用域刻意停在 **module**——升到 session 会让 lifespan 与
其它模块的 TestClient 并存，常驻调度会真的跑起来（详见 `conftest.py::client` docstring）。

⚠️ **代价与约束**：用例之间**共享同一份进程状态**（内存库 / 自选表 / 模拟盘）。
因此本文件内每个会改状态的用例**必须自己还原现场**（见 `test_watchlist_crud`
删回 601899、`test_paper_fills_and_reset` 末尾 reset）。新增用例若依赖「全新启动
状态」，请自建 TestClient，不要复用本 fixture。

fixture 定义见 `tests/conftest.py::client`。
"""
from __future__ import annotations


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    # 休市日/盘后 hub 会把最近交易日数据标 stale（红线 2）→ degraded 是正确行为；
    # 交易日盘中 → ok。两者均合法，本断言只锁结构与 provider。
    assert body["status"] in ("ok", "degraded")
    assert body["provider"] == "mock"
    assert body["last_success_refresh"] is not None


def test_market_overview_filled_after_initial_refresh(client):
    resp = client.get("/api/market/overview")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["indices"]) == 6
    first = data["indices"][0]
    for key in ("symbol", "name", "price", "change_pct", "source", "quality", "received_at"):
        assert key in first
    # 成交额口径（2026-09-01）：全市场快照求和；测试环境无快照 → None（诚实缺失）
    ta = data["total_amount"]
    assert ta is None or ta > 0
    meta = resp.json()["meta"]
    assert meta["provider"] == "mock"
    assert meta["is_realtime"] is False  # mock 永远不许标记为实时


def test_quotes_endpoint(client):
    resp = client.get("/api/quotes?symbols=600519,000001")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert {q["symbol"] for q in data} == {"600519", "000001"}


def test_quote_not_cached_falls_back_to_live(client):
    # 非自选股走 Provider 实时链（mock 对任意 6 位代码均可生成行情）
    resp = client.get("/api/quotes/999999")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["symbol"] == "999999" and body["source"] == "mock"


def test_kline_endpoint(client):
    resp = client.get("/api/kline/600519?limit=30")
    assert resp.status_code == 200
    bars = resp.json()["data"]["bars"]
    assert len(bars) == 30
    assert bars[0]["ts"] <= bars[-1]["ts"]
    assert bars[0]["source"] == "mock"


def test_order_book_and_trades(client):
    ob = client.get("/api/order-book/600519")
    assert ob.status_code == 200
    body = ob.json()["data"]
    assert body["bids"][0]["price"] < body["asks"][0]["price"]
    tr = client.get("/api/trades/600519?limit=10")
    assert tr.status_code == 200
    assert len(tr.json()["data"]) == 10


def test_limit_up_and_longhu(client):
    zt = client.get("/api/limit-up?date=2026-08-28")
    assert zt.status_code == 200
    pool = zt.json()["data"]["pool"]
    assert pool and pool[0]["consecutive_boards"] >= pool[-1]["consecutive_boards"]
    lh = client.get("/api/longhu?date=2026-08-27")
    assert lh.status_code == 200
    assert len(lh.json()["data"]["records"]) == 5


def test_limit_down_endpoint(client, monkeypatch):
    from datetime import date
    from app.market import trade_calendar

    async def calendar(_provider):
        return [date(2026, 8, 28)]
    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    resp = client.get("/api/limit-down?date=2026-08-28")
    assert resp.status_code == 200
    body = resp.json()["data"]
    pool = body["pool"]
    assert pool and body["trade_date"] == "2026-08-28"
    # 连续跌停天数降序 + mock 镜像语义（跌停为负涨幅）
    assert pool[0]["consecutive_days"] >= pool[-1]["consecutive_days"]
    assert all(r["change_pct"] < 0 for r in pool)


def test_search_endpoint(client):
    resp = client.get("/api/search?q=600519")
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert items and items[0]["symbol"] == "600519"
    assert items[0]["source"] == "mock"


def test_watchlist_crud(client):
    listed = client.get("/api/watchlist").json()["data"]
    assert {i["symbol"] for i in listed} >= {"600519", "000001", "300750", "601318"}

    added = client.post("/api/watchlist", json={"symbol": "601899", "name": "紫金矿业"})
    assert added.status_code == 201
    assert client.post("/api/watchlist", json={"symbol": "601899"}).status_code == 201  # 幂等

    assert client.delete("/api/watchlist/601899").status_code == 200
    assert client.delete("/api/watchlist/601899").status_code == 404
    assert client.post("/api/watchlist", json={"symbol": "abc"}).status_code == 422


def test_websocket_snapshot_and_ping(client):
    with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["data"] and snap["data"][0]["symbol"] == "600519"
        assert "seq" in snap and "ts" in snap
        ws.send_json({"action": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"


def test_paper_fills_and_reset(client):
    """成交记录带费用字段；重置后持仓/委托清空、资金回到初始额度。"""
    before = client.get("/api/paper/account").json()["data"]
    assert before["cash"] > 0

    quote = client.get("/api/quotes/600519").json()["data"]
    price = quote["price"]
    placed = client.post("/api/paper/orders", json={
        "symbol": "600519", "side": "buy", "price": price + 1, "quantity": 100,
    })
    assert placed.status_code == 200, placed.text
    assert placed.json()["data"]["status"] == "filled"

    fills = client.get("/api/paper/fills?symbol=600519").json()["data"]
    assert fills, "成交后应有 fills 记录"
    top = fills[0]
    for key in ("symbol", "date", "side", "price", "quantity", "fee"):
        assert key in top

    positions = client.get("/api/paper/positions").json()["data"]
    assert any(p["symbol"] == "600519" for p in positions)

    reset = client.post("/api/paper/reset", json={})
    assert reset.status_code == 200, reset.text
    body = reset.json()["data"]
    assert body["market_value"] == 0
    assert body["total_pnl"] == 0

    assert client.get("/api/paper/positions").json()["data"] == []
    assert client.get("/api/paper/fills?symbol=600519").json()["data"] == []


def test_minute_signals_and_decisions_contract(client):
    """P1-24 做 T 决策库的读路径契约（结构 + 三态字段，不锁值——值随分时数据变）。

    `degraded` 必须是**字符串列表**而非空值：缺昨日量/缺波动率时如实上报，
    不假装满配（三态纪律）。`signals` 允许为空列表——那是「确无信号」的真信息。
    """
    sig = client.get("/api/market/minute-signals/600519")
    assert sig.status_code == 200, sig.text
    body = sig.json()["data"]
    assert body["symbol"] == "600519"
    assert isinstance(body["signals"], list)          # [] = 确无信号，不等于 None
    assert isinstance(body["degraded"], list)
    assert all(isinstance(d, str) for d in body["degraded"])
    assert "observed" in body and "recorded" in body

    dec = client.get("/api/market/minute-decisions?limit=5")
    assert dec.status_code == 200, dec.text
    payload = dec.json()["data"]
    assert set(payload) >= {"items", "settled", "outcomes", "open_count", "note"}
    assert isinstance(payload["items"], list)
    assert isinstance(payload["outcomes"], dict)
    # 有记录时逐字段核验（本文件共享进程状态，别的用例可能已落库）
    if payload["items"]:
        it = payload["items"][0]
        for key in ("decision_id", "symbol", "trigger_ts", "signal_price", "bias",
                    "score", "triggered", "outcome"):
            assert key in it, key
        assert it["outcome"] is None or it["outcome"] in (
            "correct", "wrong", "invalid", "expired"
        )


def test_daily_picks_attach_latest_execution_is_read_only(monkeypatch):
    """IMP-006：每日精选只读挂最新执行事实，不自行生成 decision。"""
    from app.api.routes import picks as route
    import app.picks.opportunity_learning as ol

    contract = {
        "decision_id": "OD-demo",
        "decision_version": "ODV-demo",
        "reference_entry": {"price": 9.8},
        "executable_snapshot": {"price": 10.0, "state": "ready"},
        "gate_decision": "passed",
    }
    calls = []
    monkeypatch.setattr(
        ol, "latest_notification_execution",
        lambda day, sf=None: calls.append(day) or {"600001": contract},
    )
    src = [{"symbol": "600001", "name": "甲"}, {"symbol": "600002", "name": "乙"}]
    out = route._attach_latest_execution(src, "2026-09-21")
    assert calls == ["2026-09-21"]
    assert out[0]["execution"]["decision_version"] == "ODV-demo"
    assert out[1]["execution"] is None
    assert "execution" not in src[0], "读侧不得回写 DailyPickSet 原始 items"
