"""盘前简报回归测试（选股 2.0 批次 B）。

锁四类失效：
1. **池日期自指**——盘前 09:25 前当日涨停池必然是空的，若取"最近交易日"
   （今天在日历里就返回今天），题材动能全体归零且界面看不出是口径错。
2. **排序/上限失效**——方向数必须 cap 3，标的池 cap 10，score 降序。
3. **条件缺失**——每方向必须带触发/证伪条件（它们是盘中 watcher 的判定依据）。
4. **落盘去重失效**——盘中 append_alert 同 key 必须去重；文件损坏走"无简报"
   路径而不是抛异常。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from app.picks import morning_brief as mb


def _rec(symbol: str, name: str, boards: int, pct: float | None, reason: str) -> NS:
    return NS(symbol=symbol, name=name, consecutive_boards=boards,
              change_pct=pct, reason=reason)


# ---------------------------------------------------------------- 池聚合与打分


def test_theme_stats_aggregation():
    pool = [
        _rec("600001", "A", 3, 9.98, "粮食+种业"),
        _rec("600002", "B", 1, 9.97, "粮食"),
        _rec("600003", "C", 1, 10.0, "种业"),
        _rec("600004", "D", 1, None, "粮食"),  # 无涨幅也计入家数
    ]
    stats = mb._theme_stats_from_pool(pool)
    assert set(stats) == {"粮食", "种业"}
    assert stats["粮食"]["limit_up"] == 3 and stats["粮食"]["max_boards"] == 3
    assert stats["粮食"]["leader"]["symbol"] == "600001"
    assert stats["种业"]["limit_up"] == 2
    assert stats["粮食"]["avg_change"] == pytest.approx((9.98 + 9.97) / 2, abs=0.01)
    assert stats["粮食"]["stage"] in ("启动", "发酵", "高潮", "分歧", "退潮")


def test_momentum_echelon_bounds():
    assert mb.momentum_score(None) == 0.0
    assert mb.echelon_score(None) == 0.0
    big = {"limit_up": 99, "max_boards": 9, "avg_change": 9.9, "completeness": 1.0}
    assert mb.momentum_score(big) == 100.0   # 封顶
    assert mb.echelon_score(big) == 100.0
    empty = {"limit_up": 0, "max_boards": 0, "avg_change": None, "completeness": None}
    assert mb.momentum_score(empty) == 0.0
    # 完整度缺失按 0.5 中性处理，不臆造满值
    assert mb.echelon_score({"limit_up": 0, "max_boards": 0, "completeness": None}) == 30.0


def test_evidence_pool_date_avoids_self_reference():
    """N3（2026-09-04 定案）：任何时点生成的盘前简报都建立在上一交易日完整
    收盘数据上——anchor==today 一律回退 prev，并返回口径标注 basis。"""
    days = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31),
            date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 4)]
    # 交易日盘前 08:40（简报调度时刻）→ 昨日
    assert mb._evidence_pool_date(days, datetime(2026, 9, 2, 8, 40)) == (date(2026, 9, 1), "prev_trade_date")
    # 交易日开盘后（原缺陷路径：09:25 后生成拿当日盘中池冒充上一交易日）→ 仍取昨日
    assert mb._evidence_pool_date(days, datetime(2026, 9, 2, 10, 0)) == (date(2026, 9, 1), "prev_trade_date")
    # 周六任意时刻 → 日历内 <= 周六 的最近交易日（周五 9/4），basis=last_trade_date
    assert mb._evidence_pool_date(days, datetime(2026, 9, 5, 9, 0)) == (date(2026, 9, 4), "last_trade_date")
    # 边界：09:25 整也回退（竞价刚出价、池未定稿）
    assert mb._evidence_pool_date(days, datetime(2026, 9, 2, 9, 25)) == (date(2026, 9, 1), "prev_trade_date")
    assert mb._evidence_pool_date([], datetime(2026, 9, 2, 8, 40)) == (None, None)


# ---------------------------------------------------------------- 组装


def _evidence(**over) -> dict:
    ev = {
        "brief_date": "20260902",
        "generated_at": "2026-09-02T08:40:00",
        "is_trading_day": True,
        "phase": "发酵",
        "promo_percentile": 49.2,
        "bands_source": "calibrated",
        "pool_date": "2026-09-01",
        "themes": {
            "粮食": {"limit_up": 5, "max_boards": 3, "avg_change": 7.2,
                     "stage": "发酵", "stage_basis": [], "completeness": 0.8,
                     "records": [{"symbol": f"60000{i}", "name": f"股{i}", "boards": 3 - i // 3, "change_pct": 9.9} for i in range(1, 9)]},
            "银行": {"limit_up": 2, "max_boards": 1, "avg_change": 2.0,
                     "stage": "启动", "stage_basis": [], "completeness": 0.5,
                     "records": [{"symbol": "601000", "name": "银行股", "boards": 1, "change_pct": 9.9}]},
            "AI应用": {"limit_up": 8, "max_boards": 5, "avg_change": 8.0,
                       "stage": "高潮", "stage_basis": [], "completeness": 0.9,
                       "records": [{"symbol": "300001", "name": "AI股", "boards": 5, "change_pct": 19.9}]},
        },
        "event_strength": {"粮食": 2.4, "AI应用": 0.6, "数据中心": 1.2},
        "event_counts": {"粮食": {"bull": 2, "bear": 0}, "AI应用": {"bull": 1, "bear": 0}, "数据中心": {"bull": 1, "bear": 0}},
        "event_symbols": {"粮食": ["600100"], "数据中心": ["600200"]},
        "event_symbol_names": {"600100": "事件股A", "600200": "事件股B"},
        "missing": [],
    }
    ev.update(over)
    return ev


def test_assemble_brief_caps_and_conditions():
    payload = mb.assemble_brief(_evidence())
    assert payload["context"] == "morning_brief"
    assert payload["engine_version"] == "brief_v1"
    assert 1 <= len(payload["directions"]) <= 3  # cap 3
    scores = [d["score"] for d in payload["directions"]]
    assert scores == sorted(scores, reverse=True)
    for d in payload["directions"]:
        assert len(d["trigger_conditions"]) == 5
        assert len(d["falsify_conditions"]) == 4
        assert d["entry_mode"] in ("追涨", "追涨减半", "潜伏", "观望")
        assert len(d["pool"]) <= 10
        assert d["logic"]
    # 事件强度方向（数据中心无涨停池证据）也参排，logic 里能看到事件净强度
    names = {d["direction"] for d in payload["directions"]}
    assert "粮食" in names  # 事件 + 池双证据，必然靠前
    grain = next(d for d in payload["directions"] if d["direction"] == "粮食")
    assert "近24h事件净强度 +2.40" in grain["logic"]
    # 标的池：涨停池成员 + 事件个股，cap 10
    assert len(grain["pool"]) == 9  # 8 条 records + 1 事件个股
    assert grain["pool"][0]["role"] == "龙头"
    assert any(p["symbol"] == "600100" and p["role"] == "事件标的" for p in grain["pool"])


def test_assemble_brief_defensive_flag():
    # 银行是防守方向（可能在 top3 外，构造证据单独验证）
    ev = _evidence(themes={"银行": _evidence()["themes"]["银行"]},
                   event_strength={}, event_counts={}, event_symbols={})
    payload2 = mb.assemble_brief(ev)
    assert len(payload2["directions"]) == 1
    d = payload2["directions"][0]
    assert d["defensive"] is True
    assert "防守方向" in d["logic"]


def test_assemble_brief_excludes_performance_tags():
    """业绩/财报结果型题材不作为方向（无对应板块指数，confirm 永远拿不到
    板块涨幅，占坑稀释有效方向——2026-09-02 回测 unmatched 28.6% 根因）；
    被排除的名单落 performance_skipped 如实呈现，不是静默丢弃。"""
    base = _evidence()
    ev = _evidence(
        themes={
            **base["themes"],
            "业绩增长": {"limit_up": 9, "max_boards": 1, "avg_change": 9.9,
                         "stage": "发酵", "stage_basis": [], "completeness": 0.8,
                         "records": []},
            "半年报预增": {"limit_up": 4, "max_boards": 2, "avg_change": 9.9,
                           "stage": "发酵", "stage_basis": [], "completeness": 0.8,
                           "records": []},
        },
        event_strength={**base["event_strength"], "业绩增长": 1.0},
    )
    payload = mb.assemble_brief(ev)
    names = {d["direction"] for d in payload["directions"]}
    assert "业绩增长" not in names and "半年报预增" not in names
    assert set(payload["performance_skipped"]) == {"业绩增长", "半年报预增"}


def test_assemble_brief_events_only_and_missing():
    """涨停池完全不可用：只剩事件强度一条腿，missing 显式呈现，不崩不冒充。"""
    ev = _evidence(themes={}, missing=["涨停池不可用（超时）"])
    payload = mb.assemble_brief(ev)
    assert payload["missing"] == ["涨停池不可用（超时）"]
    names = {d["direction"] for d in payload["directions"]}
    assert names == {"粮食", "AI应用", "数据中心"}
    for d in payload["directions"]:
        assert d["pool"] == [] or all(p["role"] == "事件标的" for p in d["pool"])


def test_assemble_brief_macro_fields_passthrough():
    """P1-8：宏观日历字段按三态透传（None=源不可得 / [] =当日无高信号事件）。"""
    ev = _evidence()
    ev["macro_note"] = "宏观日历：今晚 20:30 美国9月非农公布。"
    ev["macro_events"] = [{"line": "09:30 中国·CPI（公布 0.8）　中国8月CPI年率(%)"}]
    payload = mb.assemble_brief(ev)
    assert payload["macro_note"].startswith("宏观日历")
    assert payload["macro_events"][0]["line"].startswith("09:30")
    # 缺字段时给 None（不是 [] —— 空列表是「确无事件」的真信息，两者不可混）
    bare = mb.assemble_brief(_evidence())
    assert bare["macro_note"] is None and bare["macro_events"] is None


# ---------------------------------------------------------------- 落盘与去重


@pytest.fixture()
def brief_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "BRIEF_DIR", tmp_path)
    return tmp_path


def test_save_load_roundtrip(brief_dir):
    payload = mb.assemble_brief(_evidence())
    path = mb.save_brief(payload)
    assert path.exists()
    loaded = mb.load_brief("20260902")
    assert loaded["brief_date"] == "20260902"
    assert len(loaded["directions"]) == len(payload["directions"])
    assert mb.load_brief("19990101") is None


def test_load_corrupt_json_returns_none(brief_dir):
    (brief_dir / "20260902.json").write_text("{broken", encoding="utf-8")
    assert mb.load_brief("20260902") is None


def test_append_alert_dedup(brief_dir):
    payload = mb.assemble_brief(_evidence())
    mb.save_brief(payload)
    a1 = {"key": "粮食:600001:confirm", "kind": "confirm", "direction": "粮食",
          "symbol": "600001", "name": "股1", "text": "t", "at": "2026-09-02T10:00:00", "meta": {}}
    assert mb.append_alert("20260902", a1) is True
    assert mb.append_alert("20260902", dict(a1)) is False          # 同 key 去重
    a2 = {**a1, "key": "粮食:600002:confirm", "symbol": "600002"}
    assert mb.append_alert("20260902", a2) is True                  # 不同个股放行
    assert mb.append_alert("20260902", {**a1, "key": "粮食:falsify", "kind": "falsify"}) is True
    alerts = mb.load_brief("20260902")["alerts"]
    assert [a["key"] for a in alerts] == ["粮食:600001:confirm", "粮食:600002:confirm", "粮食:falsify"]
    # 简报不存在 → 不落盘返回 False（调用方留日志）
    assert mb.append_alert("19990101", a1) is False


def test_saved_payload_is_valid_json_file(brief_dir):
    payload = mb.assemble_brief(_evidence())
    path = mb.save_brief(payload)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["phase"] == "发酵"
    assert data["alerts"] == []


# ---------------------------------------------------------------- 调度幂等（持久化）
# 2026-09-02 实测事故：premarket_scheduler 的 last_run 是内存态，12:00 前重启后端
# 清零 → due 分支无条件重新生成当日简报，把盘前方向/alerts 整体覆盖。


def _tick_app() -> NS:
    return NS(state=NS(hub=NS()))


def _tick(brief_dir, monkeypatch, *, now, last_run, trading=True):
    """跑单步调度；返回 (last_run, build_and_save 是否被调用)。

    `trading` 是**三态**（F7）：`True` / `False` / `None`（= 日历未覆盖或源不可用，
    「未判定」）。桩直接把值原样返回，因此 `trading=None` 走到 `_premarket_tick`
    的未判定分支。
    """
    import asyncio

    calls: list[str] = []

    async def fake_trading(hub, d):
        return trading

    async def fake_build(app_state, *, trigger):
        calls.append(trigger)
        payload = mb.assemble_brief(_evidence(brief_date=now.strftime("%Y%m%d")))
        mb.save_brief(payload)  # 与真实 build_and_save 同行为：生成即落盘
        return payload

    monkeypatch.setattr(mb, "_trade_day_state", fake_trading)
    monkeypatch.setattr(mb, "build_and_save", fake_build)
    new_last = asyncio.run(
        mb._premarket_tick(
            _tick_app(), now=now, last_run=last_run, run_hour=8, run_minute=40
        )
    )
    return new_last, bool(calls)


def test_scheduler_persisted_idempotent_no_overwrite_on_restart(brief_dir, monkeypatch):
    """重启模拟：内存 last_run=None，但磁盘已有当日简报 → 跳过，不覆盖。"""
    payload = mb.assemble_brief(_evidence())
    mb.save_brief(payload)
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 9, 49), last_run=None
    )
    assert built is False  # 简报未被重新生成
    assert new_last == "20260902"
    # 原简报内容未被覆盖（仍是盘前证据的方向，不是盘中池的方向）
    assert mb.load_brief("20260902")["env"]["pool_date"] == "2026-09-01"


def test_scheduler_generates_when_no_brief(brief_dir, monkeypatch):
    """无当日简报 + 交易日 + due → 正常生成。"""
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 8, 41), last_run=None
    )
    assert built is True
    assert new_last == "20260902"
    assert mb.load_brief("20260902") is not None


def test_scheduler_skip_non_trading_day(brief_dir, monkeypatch):
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 5, 8, 41), last_run=None,
        trading=False,
    )
    assert built is False
    assert new_last == "20260905"  # 置位防整分钟重试，即便非交易日


def test_scheduler_defers_when_calendar_unknown(brief_dir, monkeypatch):
    """**未判定（日历未覆盖今天 / 源不可用）→ 不生成、且不置位 last_run**（F7 定点守卫）。

    2026-09-14 实测事故：原实现把「日历覆盖不到今天」与「源不可用」都塌缩成
    `False`（非交易日），而跳过分支**照常 `return key`** ⇒ 当日 `last_run == key`，
    在 08:40–12:00 的历时时窗内**不再重试**，简报最终缺失
    （`data/picks/briefs/20260914.json` 不存在，最新停在 09-11）。
    本测试钉住两条：① 不触发生成；② 返回值**不等于当日 key**（= 未置位 ⇒ 可重试）。

    *回退即红*：把 `_trade_day_state` 改回二态（`None` 当 `False`）⇒ `new_last`
    变成 `"20260902"`，本测试立刻变红。
    """
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 8, 41), last_run=None,
        trading=None,
    )
    assert built is False
    assert new_last == "", "未判定不得置位 last_run（否则当日不再重试）"
    assert mb.load_brief("20260902") is None


def test_scheduler_unknown_does_not_advance_existing_last_run(brief_dir, monkeypatch):
    """未判定时也不得把 `last_run` **推进**到当日（同族：丧失重试的另一种写法）。"""
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 8, 41), last_run="20260901",
        trading=None,
    )
    assert built is False
    assert new_last == "20260901"  # 保留原值，不推进到当日


def test_scheduler_not_due_after_noon(brief_dir, monkeypatch):
    """12:00 后不再补跑（过时证据无意义）。"""
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 12, 30), last_run=None
    )
    assert built is False
    assert new_last == ""  # last_run 不置位，语义上是"从未到期"


def test_scheduler_same_day_inmemory_idempotent(brief_dir, monkeypatch):
    """同进程内已跑过（last_run==key）→ 不重复生成（原有行为保持）。"""
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 8, 42), last_run="20260902"
    )
    assert built is False
    assert new_last == "20260902"


def test_scheduler_corrupt_brief_falls_through_to_generate(brief_dir, monkeypatch):
    """磁盘简报损坏 → load_brief None → 视同无简报，正常生成（不因坏文件卡死当日）。"""
    (brief_dir / "20260902.json").write_text("{broken", encoding="utf-8")
    new_last, built = _tick(
        brief_dir, monkeypatch, now=datetime(2026, 9, 2, 8, 41), last_run=None
    )
    assert built is True
    assert new_last == "20260902"


def test_assemble_brief_climate_passthrough():
    """P1-32：气候相位按三态透传（None=源不可得 → 前端整块不渲染）。"""
    ev = _evidence()
    ev["climate"] = {"state": "neutral", "alert": "el_nino", "consecutive": 3}
    payload = mb.assemble_brief(ev)
    assert payload["climate"]["alert"] == "el_nino"
    bare = mb.assemble_brief(_evidence())
    assert bare["climate"] is None


def test_climate_failure_is_swallowed_and_not_added_to_missing(monkeypatch):
    """气候是月更慢变量：取不到就整块不渲染，**不得**让简报顶部天天挂 ⚠。"""
    import asyncio

    from app.market import climate as cl

    async def boom(asof=None):
        raise RuntimeError("noaa down")

    monkeypatch.setattr(cl, "collect", boom)
    assert asyncio.run(mb.collect_climate_safe(date(2026, 9, 11))) is None


def test_climate_safe_helper_passes_through_payload(monkeypatch):
    import asyncio

    from app.market import climate as cl

    async def ok(asof=None):
        return {"state": "neutral", "alert": "el_nino", "consecutive": 3, "asof": str(asof)}

    monkeypatch.setattr(cl, "collect", ok)
    out = asyncio.run(mb.collect_climate_safe(date(2026, 9, 11)))
    assert out["alert"] == "el_nino"
    assert out["asof"] == "2026-09-11"


# ---------------------------------------------------------------- 三态口径（同族收口）

class _FakeProvider:
    """只需 `get_limit_up_pool`；其余路径都在 `collect_evidence` 的 try 内降级。"""

    async def get_limit_up_pool(self, _d):  # noqa: ANN001
        return []


def _hermetic_state():
    """构造一个**零外呼**的 state：所有会打网络的入口都换成本地桩。

    必要性：`collect_evidence` 里每路 IO 都包在 try 内 ⇒ 真实网络只在失败时
    变慢/报错，测试会「能过但随机慢」。这里显式断掉，测试才是确定的。
    """
    return NS(hub=NS(provider=_FakeProvider()), event_store=None, snapshot_service=None)


@pytest.fixture
def hermetic(monkeypatch):
    """把 collect_evidence 的每个外部依赖都钉死为本地行为。"""
    from app.events import chains as chains_mod
    from app.services import akshare_ext as ak_mod
    from app.services import market_context as mc_mod

    async def _no_climate(_d):  # noqa: ANN001  —— 是被 await 的协程，桩必须可 await
        return None

    monkeypatch.setattr(mb, "collect_climate_safe", _no_climate)
    monkeypatch.setattr(chains_mod, "macro_calendar_note", lambda _d: None)
    monkeypatch.setattr(ak_mod, "get_akshare_ext", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    monkeypatch.setattr(mc_mod, "compute_market_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(mb.tc, "prev_trade_date", lambda days, anchor: None)
    return monkeypatch


def _cover(days_iso: list[str]):
    return [date.fromisoformat(d) for d in days_iso]


def test_evidence_is_trading_day_is_unknown_when_calendar_does_not_cover_today(hermetic):
    """日历**未覆盖**今天 ⇒ `is_trading_day` 必须是 `None`，**不得**写成 `false`。

    回退即红：旧写法 `(beijing_today() in days)` 在未覆盖时得 `False` ⇒
    把「未判定」断言成「今天不是交易日」，而这个字段会随每日简报
    **永久归档**（`data/picks/briefs/<date>.json`）⇒ 事后翻记录也看不出
    是判错还是真休市（属「错了也看不出来」）。
    """
    hermetic.setattr(mb, "beijing_today", lambda: date(2026, 9, 14))
    # 尾随窗口：末日停在上一交易日，不含 09-14
    async def _days(_p):  # noqa: ANN001
        return _cover(["2026-09-10", "2026-09-11"])
    hermetic.setattr(mb.tc, "trading_days", _days)

    import asyncio

    ev = asyncio.run(mb.collect_evidence(_hermetic_state()))

    assert ev["is_trading_day"] is None, "未判定被写成 false ⇒ 归档里留下一条假事实"
    assert not any("非交易日" in m for m in ev["missing"]), \
        "在真实交易日追加「非交易日」说明 ⇒ 简报面板出现假警示"


def test_evidence_is_trading_day_false_only_when_calendar_confirms_closed(hermetic):
    """日历**已覆盖**今天且今天不是交易日 ⇒ `False`（这才可断言「休市」）。

    与上一条**成对**：只测未覆盖那一侧，会把「任何情况都返回 None」的实现判绿
    （KB-ENG-65 假绿守卫——判据必须能区分三态，而不是只压一个方向）。
    """
    hermetic.setattr(mb, "beijing_today", lambda: date(2026, 9, 11))
    # 覆盖 09-11（末日 >= 09-11）但 09-11 不在集合内 = 日历明确休市
    async def _days(_p):  # noqa: ANN001
        return _cover(["2026-09-09", "2026-09-10", "2026-09-14"])
    hermetic.setattr(mb.tc, "trading_days", _days)

    import asyncio

    ev = asyncio.run(mb.collect_evidence(_hermetic_state()))

    assert ev["is_trading_day"] is False
    assert any("非交易日" in m for m in ev["missing"]), \
        "确认休市却不再提示 ⇒ 用户以为简报漏生成"
