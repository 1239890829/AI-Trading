from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["provider"] == "mock"
        assert body["is_stale"] is False
        assert body["last_success_refresh"] is not None


def test_market_overview_filled_after_initial_refresh():
    with TestClient(app) as client:
        resp = client.get("/api/market/overview")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["indices"]) == 6
        first = data["indices"][0]
        for key in ("symbol", "name", "price", "change_pct", "source", "quality", "received_at"):
            assert key in first
        assert data["total_amount"] > 0
        meta = resp.json()["meta"]
        assert meta["provider"] == "mock"
        assert meta["is_realtime"] is False  # mock 永远不许标记为实时


def test_quotes_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/quotes?symbols=600519,000001")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert {q["symbol"] for q in data} == {"600519", "000001"}


def test_quote_not_cached_falls_back_to_live():
    # 非自选股走 Provider 实时链（mock 对任意 6 位代码均可生成行情）
    with TestClient(app) as client:
        resp = client.get("/api/quotes/999999")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["symbol"] == "999999" and body["source"] == "mock"


def test_kline_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/kline/600519?limit=30")
        assert resp.status_code == 200
        bars = resp.json()["data"]["bars"]
        assert len(bars) == 30
        assert bars[0]["ts"] <= bars[-1]["ts"]
        assert bars[0]["source"] == "mock"


def test_order_book_and_trades():
    with TestClient(app) as client:
        ob = client.get("/api/order-book/600519")
        assert ob.status_code == 200
        body = ob.json()["data"]
        assert body["bids"][0]["price"] < body["asks"][0]["price"]
        tr = client.get("/api/trades/600519?limit=10")
        assert tr.status_code == 200
        assert len(tr.json()["data"]) == 10


def test_limit_up_and_longhu():
    with TestClient(app) as client:
        zt = client.get("/api/limit-up?date=2026-08-28")
        assert zt.status_code == 200
        pool = zt.json()["data"]["pool"]
        assert pool and pool[0]["consecutive_boards"] >= pool[-1]["consecutive_boards"]
        lh = client.get("/api/longhu?date=2026-08-27")
        assert lh.status_code == 200
        assert len(lh.json()["data"]["records"]) == 5


def test_search_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/search?q=600519")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert items and items[0]["symbol"] == "600519"
        assert items[0]["source"] == "mock"


def test_watchlist_crud():
    with TestClient(app) as client:
        listed = client.get("/api/watchlist").json()["data"]
        assert {i["symbol"] for i in listed} >= {"600519", "000001", "300750", "601318"}

        added = client.post("/api/watchlist", json={"symbol": "601899", "name": "紫金矿业"})
        assert added.status_code == 201
        assert client.post("/api/watchlist", json={"symbol": "601899"}).status_code == 201  # 幂等

        assert client.delete("/api/watchlist/601899").status_code == 200
        assert client.delete("/api/watchlist/601899").status_code == 404
        assert client.post("/api/watchlist", json={"symbol": "abc"}).status_code == 422


def test_websocket_snapshot_and_ping():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["data"] and snap["data"][0]["symbol"] == "600519"
            assert "seq" in snap and "ts" in snap
            ws.send_json({"action": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"


def test_paper_fills_and_reset():
    """成交记录带费用字段；重置后持仓/委托清空、资金回到初始额度。"""
    with TestClient(app) as client:
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
