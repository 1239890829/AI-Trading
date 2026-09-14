"""board_flow（板块资金流 L2 枢纽）单元测试——零网络，双域/落盘/三态全锚定。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.market import board_flow as bf
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- fixtures/工具

class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHTTP:
    """按 host 片段脚本化：outcomes 队列，Exception 抛出、dict 作为 JSON 返回。"""

    def __init__(self):
        self.calls: list[str] = []
        self.plan: dict[str, list] = {}

    def add(self, host_frag: str, outcomes: list):
        self.plan[host_frag] = list(outcomes)

    async def get(self, url, params=None, headers=None):
        self.calls.append(url)
        for frag, outcomes in self.plan.items():
            if frag in url:
                if not outcomes:
                    raise AssertionError(f"outcomes exhausted for {frag}")
                out = outcomes.pop(0)
                if isinstance(out, Exception):
                    raise out
                return FakeResp(out)
        raise AssertionError(f"unexpected url: {url}")


def _clist_payload(rows: list[dict], total: int | None = None) -> dict:
    return {"data": {"total": total if total is not None else len(rows), "diff": rows}}


def _em_board(code="BK1650", name="通信技术", **kw) -> dict:
    d = {
        "f12": code, "f14": name, "f3": 1.65, "f6": 1.5e11, "f62": 1.2e10,
        "f66": 9.0e9, "f69": 7.47, "f184": 9.79, "f104": 284, "f105": 86,
        "f128": "美格智能", "f140": "002881", "f164": -4.3e10, "f165": -2.69,
        "f174": -1.9e10, "f175": -0.52,
    }
    d.update(kw)
    return d


@pytest.fixture()
def fresh_memo(monkeypatch, tmp_path):
    """隔离落盘路径与 mtime memo。"""
    monkeypatch.setattr(bf, "_DAILY_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(bf, "_DAYK_STORE", tmp_path / "daykline.json")
    monkeypatch.setattr(bf, "_mem_memo", {"daily": (None, {}), "dayk": (None, {})})
    return tmp_path


def _bj(year=2026, month=9, day=7, hour=15, minute=6):
    from datetime import datetime

    return datetime(year, month, day, hour, minute, tzinfo=BJ_TZ)


# ---------------------------------------------------------------- 解析层

def test_board_rows_parsing_and_ratio_fallback():
    rows = bf._board_rows_from_diff([_em_board(), _em_board(code="BK0714", f184="-")], "concept")
    assert rows[0]["main_net_yi"] == 120.0  # 1.2e10 元 = 120 亿
    assert rows[0]["main_net_ratio"] == 9.79  # 官方 f184
    assert rows[0]["main_net_5d_yi"] == -430.0
    assert rows[0]["main_net_10d_yi"] == -190.0
    assert rows[0]["leader_symbol"] == "002881"
    # f184 缺席 → 同式自算（实测与官方一致）
    assert rows[1]["main_net_ratio"] == round(1.2e10 / 1.5e11 * 100, 2)


def test_board_rows_none_tri_state():
    rows = bf._board_rows_from_diff([_em_board(f62="-", f164="-", f184="-", f128="-", f140="-")], "industry")
    r = rows[0]
    assert r["main_net_yi"] is None and r["main_net_5d_yi"] is None  # 绝不填 0
    assert r["main_net_ratio"] is None
    assert r["leader_name"] is None


def test_clist_failover_push2_to_delay():
    fake = FakeHTTP()
    fake.add("push2.eastmoney", [RuntimeError("waf")])
    fake.add("push2delay.eastmoney", [_clist_payload([_em_board()])])

    async def go():
        return await bf._clist_pages("m:90+t:3", bf._BOARD_LIST_FIELDS)

    bf._HTTP = fake
    try:
        diff, host = _run(go())
    finally:
        bf._HTTP = None
    assert host == bf._HOSTS[1] and len(diff) == 1
    assert any("push2delay" in u for u in fake.calls)


def test_clist_all_hosts_fail():
    fake = FakeHTTP()
    fake.add("push2.eastmoney", [RuntimeError("waf")])
    fake.add("push2delay.eastmoney", [RuntimeError("down")])

    async def go():
        return await bf._clist_pages("m:90+t:3", bf._BOARD_LIST_FIELDS)

    bf._HTTP = fake
    try:
        diff, host = _run(go())
    finally:
        bf._HTTP = None
    assert diff is None and host is None


def test_clist_pagination_by_total():
    fake = FakeHTTP()
    p1 = _clist_payload([_em_board(code=f"BK{i:04d}") for i in range(100)], total=250)
    p2 = _clist_payload([_em_board(code=f"BK1{i:03d}") for i in range(100)], total=250)
    p3 = _clist_payload([_em_board(code=f"BK2{i:03d}") for i in range(50)], total=250)
    fake.add("push2.eastmoney", [RuntimeError("waf")])
    fake.add("push2delay.eastmoney", [p1, p2, p3])

    async def go():
        return await bf._clist_pages("m:90+t:3", bf._BOARD_LIST_FIELDS)

    bf._HTTP = fake
    try:
        diff, _host = _run(go())
    finally:
        bf._HTTP = None
    assert len(diff) == 250


# ---------------------------------------------------------------- 三态计算

def test_streak_bars_include_today():
    bars = [[f"2026-09-0{d}", 1.0 + d, None] for d in range(1, 6)]  # 5 天全正
    assert bf._streak_from_bars(bars, None, "2026-09-05") == 5
    bars[-1][1] = -2.0  # 今日净流出
    assert bf._streak_from_bars(bars, None, "2026-09-05") == 0


def test_streak_bars_stale_appends_f62():
    bars = [["2026-09-03", 2.0, None], ["2026-09-04", 3.0, None]]
    # bars 落后于「今日」：f62=最新交易日实时值 → 追加到尾部
    assert bf._streak_from_bars(bars, 1.5, "2026-09-07") == 3
    assert bf._streak_from_bars(bars, None, "2026-09-07") is None  # 判不出，绝不猜


def test_sum_n_window_semantics():
    bars = [[f"2026-09-{d:02d}", float(d), None] for d in range(1, 11)]  # 1..10，末根 09-10
    # bars 已含「今日」：Σ last 5 = 6+7+8+9+10
    assert bf._sum_n_yi(bars, 5, None, "2026-09-10") == 40.0
    # bars 落后一日：f62(11) + last 4 = 11+7+8+9+10
    assert bf._sum_n_yi(bars, 5, 11.0, "2026-09-11") == 45.0
    # 窗口不足 → None（不凑数）
    assert bf._sum_n_yi(bars[:2], 5, 3.0, "2026-09-11") is None


# ---------------------------------------------------------------- 聚合入口

def _rows_fixture() -> list[dict]:
    return [
        {"board_code": "BK0001", "name": "甲", "kind": "concept", "change_pct": 1.0,
         "main_net_yi": 30.0, "main_net_ratio": 5.0, "super_net_yi": 10.0,
         "main_net_5d_yi": 50.0, "main_net_10d_yi": 80.0,
         "leader_name": "甲股", "leader_symbol": "600001"},
        {"board_code": "BK0002", "name": "乙", "kind": "concept", "change_pct": -1.0,
         "main_net_yi": 50.0, "main_net_ratio": 6.0, "super_net_yi": 20.0,
         "main_net_5d_yi": 40.0, "main_net_10d_yi": 70.0,
         "leader_name": "乙股", "leader_symbol": "600002"},
        {"board_code": "BK0003", "name": "丙", "kind": "concept", "change_pct": 0.5,
         "main_net_yi": None, "main_net_ratio": None, "super_net_yi": None,
         "main_net_5d_yi": None, "main_net_10d_yi": None,
         "leader_name": None, "leader_symbol": None},
    ]


def test_get_board_fund_flow_intraday_rank_delta_and_streak(monkeypatch):
    async def fake_list(kind):
        return _rows_fixture(), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "_yesterday_ranks", lambda kind: ({"BK0001": 3, "BK0002": 1}, "2026-09-04"))
    monkeypatch.setattr(bf, "get_board_streaks", lambda f62: {"BK0001": 4})

    out = _run(bf.get_board_fund_flow("concept", "intraday"))
    assert out["available"] is True
    ranks = [r["board_code"] for r in out["rows"]]
    assert ranks == ["BK0002", "BK0001", "BK0003"]  # None 殿后
    by_code = {r["board_code"]: r for r in out["rows"]}
    assert by_code["BK0002"]["rank"] == 1
    assert by_code["BK0002"]["rank_delta"] == 0  # 昨 1 → 今 1
    assert by_code["BK0001"]["rank_delta"] == 1  # 昨 3 → 今 2，正=上升
    assert by_code["BK0001"]["streak"] == 4
    assert by_code["BK0003"]["streak"] is None
    assert out["rank_basis_date"] == "2026-09-04"


def test_get_board_fund_flow_5d_sort(monkeypatch):
    async def fake_list(kind):
        return _rows_fixture(), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "get_board_streaks", lambda f62: {})
    out = _run(bf.get_board_fund_flow("concept", "5d"))
    assert [r["board_code"] for r in out["rows"]] == ["BK0001", "BK0002", "BK0003"]
    assert all(r["rank_delta"] is None for r in out["rows"])  # 非当日无昨日基线


def test_get_board_fund_flow_20d_zero_store(monkeypatch):
    async def fake_list(kind):
        return _rows_fixture(), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: {})
    out = _run(bf.get_board_fund_flow("concept", "20d"))
    assert out["available"] is False and out["coverage"] == 0
    assert "尚未沉淀" in out["reason"]


def test_get_board_fund_flow_20d_from_store(monkeypatch):
    async def fake_list(kind):
        return _rows_fixture(), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    # bar 日期以真实「今日」收尾（末根含今日 → Σ last20 不再追加 f62）
    from datetime import timedelta

    today = bf.beijing_now().date().isoformat()
    dates = [(bf.beijing_now().date() - timedelta(days=19 - i)).isoformat() for i in range(20)]
    store = {
        "BK0001": {"bars": [[d, float(v), None] for d, v in zip(dates, range(1, 21))]},
        "BK0002": {"bars": [[d, 2.0, None] for d in dates]},
    }
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: store)
    monkeypatch.setattr(bf, "get_board_streaks", lambda f62: {"BK0001": 20, "BK0002": 20})
    out = _run(bf.get_board_fund_flow("concept", "20d"))
    assert out["available"] is True and out["coverage"] == 2
    top = out["rows"][0]
    assert top["board_code"] == "BK0001"
    assert top["main_net_20d_yi"] == sum(range(1, 21))  # Σ last20（bars 含「今日」）
    assert today  # 引用防 lint：today 与 dates 末位一致
    assert dates[-1] == today


# ---------------------------------------------------------------- 收盘快照

def test_snapshot_skipped_before_1505(monkeypatch, fresh_memo):
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj(hour=14, minute=0))
    assert _run(bf.snapshot_daily_if_closed()) is False
    assert not (fresh_memo / "daily.json").exists()


# ------------------------------------------------- F5 交易日闸门（2026-09-14）

def _stub_snapshot_sources(monkeypatch, last_bar: str = "2026-09-06"):
    """桩住三处外呼，让落盘路径只差「闸门判定」一个变量。"""
    async def fake_list(kind):
        rows = _rows_fixture()
        for r in rows:
            r["kind"] = kind
        return rows, []

    async def fake_members(code):
        return {"available": True, "rows": [
            {"symbol": "600001", "name": "甲股", "main_net_yi": 3.0, "main_net_ratio": 12.0}
        ]}

    async def fake_dayk(code):
        return [["2026-09-05", 1.5, 1.1], [last_bar, 2.5, -0.3]]

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "get_board_members", fake_members)
    monkeypatch.setattr(bf, "_fetch_board_dayk", fake_dayk)


def test_snapshot_skipped_on_non_trading_day(monkeypatch, fresh_memo):
    """闸门①：日历明确今天不是交易日（周六/周日/节假日）⇒ 不落盘。

    *回退即红*：删掉 `snapshot_daily_if_closed` 里的 `trade_day is False` 分支，
    本用例变红（实测：还原缺陷后 daily.json 里出现 "2026-09-07" 键）。
    真实事故：daily.json 曾被写入 2026-09-12(六)/2026-09-13(日) 两条，内容与
    09-11 逐字节相同（源在非交易日返回上一交易日终值，键却是今天）—— 后果不止多
    两行：`_yesterday_ranks` 取「< today 的最新一天」，周末那条会被当成"昨日榜位"。
    """
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj(hour=15, minute=6))
    monkeypatch.setattr(bf, "_is_trade_day", lambda _d: False)
    _stub_snapshot_sources(monkeypatch)
    assert _run(bf.snapshot_daily_if_closed()) is False
    assert not (fresh_memo / "daily.json").exists()


def test_snapshot_backstop_skips_when_source_has_no_today_bar(monkeypatch, fresh_memo):
    """闸门②：日历未覆盖今天（`None`）时，用**源数据末日**反推 —— 末日 < 今天 ⇒ 不落盘。

    这是「三态」在写路径上的落地：`unknown` 不许塌缩成「就当是交易日」，
    但也不能只凭"未判定"就拒绝（那会漏掉真实交易日的数据），故用源数据自证。
    """
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj(hour=15, minute=6))
    monkeypatch.setattr(bf, "_is_trade_day", lambda _d: None)
    _stub_snapshot_sources(monkeypatch, last_bar="2026-09-06")  # 源末日 = 上一交易日
    assert _run(bf.snapshot_daily_if_closed()) is False
    assert not (fresh_memo / "daily.json").exists()


def test_snapshot_backstop_allows_when_source_has_today_bar(monkeypatch, fresh_memo):
    """闸门②的**反面**：未判定但源已给出今日 bar ⇒ 放行（不得因「未判定」漏数据）。

    只钉"跳过"那一侧会退化成恒真（把闸门写成 `return False` 也能通过），
    所以这一对必须成对存在 —— 三态纪律要求 unknown 两侧都不塌缩。
    """
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj(hour=15, minute=6))
    monkeypatch.setattr(bf, "_is_trade_day", lambda _d: None)
    _stub_snapshot_sources(monkeypatch, last_bar="2026-09-07")  # 源已含今日 bar
    assert _run(bf.snapshot_daily_if_closed()) is True
    daily = json.loads((fresh_memo / "daily.json").read_text())
    assert "2026-09-07" in daily["days"]


def test_snapshot_happy_path_and_idempotent(monkeypatch, fresh_memo):
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj())
    # F5：闸门必须**显式注入**，否则用例会隐式绑定真实日历（`.data/trade_calendar.json`
    # 的覆盖窗口一变，observed 兜底就会让本用例变红）—— 有注入点就必须用。
    monkeypatch.setattr(bf, "_is_trade_day", lambda _d: True)

    async def fake_list(kind):
        rows = _rows_fixture()
        for r in rows:
            r["kind"] = kind
        return rows, []

    async def fake_members(code):
        return {"available": True, "rows": [
            {"symbol": "600001", "name": "甲股", "main_net_yi": 3.0, "main_net_ratio": 12.0}
        ]}

    async def fake_dayk(code):
        return [["2026-09-05", 1.5, 1.1], ["2026-09-06", 2.5, -0.3]]

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "get_board_members", fake_members)
    monkeypatch.setattr(bf, "_fetch_board_dayk", fake_dayk)

    assert _run(bf.snapshot_daily_if_closed()) is True
    daily = json.loads((fresh_memo / "daily.json").read_text())
    dayk = json.loads((fresh_memo / "daykline.json").read_text())
    day = daily["days"]["2026-09-07"]
    assert day["ranks"]["concept"]["BK0002"] == 1  # 净流入 50 亿居首
    assert day["boards"]["concept"][0] == ["BK0002", "乙", 50.0, 6.0, -1.0]
    assert day["members"]["BK0002"][0] == ["600001", "甲股", 3.0, 12.0]
    assert len(dayk["boards"]["BK0001"]["bars"]) == 2

    # 当日幂等：第二次调用不再写
    assert _run(bf.snapshot_daily_if_closed()) is False


def test_snapshot_daykline_merge_dedup(monkeypatch, fresh_memo):
    """daykline 累计库：旧 bar 保留、同日覆盖、按日期有序。"""
    monkeypatch.setattr(bf, "beijing_now", lambda: _bj())
    monkeypatch.setattr(bf, "_is_trade_day", lambda _d: True)  # F5 闸门注入，同上

    async def fake_list(kind):
        rows = _rows_fixture()[:1]
        for r in rows:
            r["kind"] = kind
        return rows, []

    async def fake_members(code):
        return {"available": False, "rows": []}

    async def fake_dayk(code):
        return [["2026-09-06", 9.9, 0.0], ["2026-09-07", 3.3, 1.0]]  # 覆盖 09-06，新增 09-07

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "get_board_members", fake_members)
    monkeypatch.setattr(bf, "_fetch_board_dayk", fake_dayk)

    assert _run(bf.snapshot_daily_if_closed()) is True
    dayk = json.loads((fresh_memo / "daykline.json").read_text())
    bars = dayk["boards"]["BK0001"]["bars"]
    assert [b[0] for b in bars] == ["2026-09-06", "2026-09-07"]
    assert bars[0][1] == 9.9  # 同日新值覆盖


# ---------------------------------------------------------------- 路由参数校验

def test_route_validation_422():
    from fastapi import HTTPException

    from app.api.routes.market import market_board_fund_flow, market_board_flow_minute

    with pytest.raises(HTTPException) as e:
        _run(market_board_fund_flow(kind="bad", range="intraday"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _run(market_board_fund_flow(kind="concept", range="30d"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _run(market_board_flow_minute("600519"))
    assert e.value.status_code == 422
