"""每日进化议程单测（零网络）：预算/红线/频率闸/A 类自动生效/停机开关/解析校验。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

import app.services.evolution as evo
from app.models.agent import AgentAgenda
from app.models.watchlist import Base
from app.core.bjtime import beijing_now, BJ_TZ  # S2-8 时区收敛


def _factory(tmp_path, name="evo.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # 全部被本测试消费的模型先注册，不能依赖其它测试先导入探针。
    from app.models.alert import AlertEvent, AlertRule
    from app.models.watch_ledger import WatchLedger
    assert all(model.__table__.metadata is Base.metadata for model in (AlertEvent, AlertRule, WatchLedger))

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def enable_autonomy_for_enabled_path_tests(monkeypatch):
    """Enabled-path tests model an administrator's explicit opt-in."""
    monkeypatch.setattr(evo.settings, "agent_autonomy_enabled", True)


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    import app.services.agent_params as ap
    import app.services.agent_tasks as at2

    monkeypatch.setattr(evo, "get_session_factory", lambda: factory)
    monkeypatch.setattr(ap, "get_session_factory", lambda: factory)
    monkeypatch.setattr(at2, "get_session_factory", lambda: factory)
    monkeypatch.setattr(evo, "_collect_review_improvements",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    monkeypatch.setattr(evo, "_collect_signal_health",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    monkeypatch.setattr(evo, "_collect_triage_stats",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    # B 类日报写入必须隔离到 tmp（KB-ENG-19：_EVOLUTION_DIR 锚定真实仓库根，
    # 不隔离则每次 pytest 都向真实 docs/evolution/当天.md 追加测试条目）
    evo_dir = tmp_path / "evolution"
    evo_dir.mkdir()
    monkeypatch.setattr(evo, "_EVOLUTION_DIR", evo_dir)
    yield factory
    from app.picks.style_router import set_override_provider

    set_override_provider(None)


VALID = '{"发酵": {"echelon": 0.04}}'

LLM_OK = json.dumps({"items": [
    {"class": "A", "finding": "发酵期 fundamental 偏移拖累胜率",
     "evidence": {"sample_days": 30, "win_rate": 0.41},
     "action": "将发酵期 fundamental 偏移调回 -0.03",
     "param": {"key": "picks_style_offsets_json",
               "after": {"发酵": {"echelon": 0.04, "fundamental": -0.03}}},
     "expected_effect": "胜率回升", "verification": "30 日对比", "priority": 1},
    {"class": "B", "finding": "watcher 阈值偏松",
     "summary": "本周 confirm 事件中 60% 在 10 分钟内证伪", "priority": 2},
]})


def _fake_llm(monkeypatch, payload):
    async def fake(fn):
        return payload

    monkeypatch.setattr(evo, "_llm_call", fake)
    monkeypatch.setattr("app.core.llm_client.chat_completion", lambda *a, **k: payload)


def test_full_cycle_a_class_shadow_then_applied(sf, monkeypatch):
    """A 类完整生命周期（P1-4 影子语义）：议程入影子 → 影子评估转正 →
    覆盖层真实生效（免重启）+ 30 日实验挂账。"""
    _fake_llm(monkeypatch, LLM_OK)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["status"] == "executed"
    statuses = {i["class"]: i["status"] for i in agenda["items"]}
    assert statuses["A"] == "executed"  # executed = 已入影子队列
    assert statuses["B"] == "executed"  # B 类写进化日报
    # 影子阶段：不生效（provider 仍读旧偏移）
    from app.picks.style_router import route_style

    assert route_style("发酵")["offsets"]["echelon"] == pytest.approx(0.06)

    # 影子评估（权重漂移温和）→ 自动转正
    from app.services.experiments import evaluate_and_promote_shadow

    results = evaluate_and_promote_shadow(sf)
    assert results and results[0]["verdict"] == "promoted", results
    # 转正后覆盖层真实生效
    assert route_style("发酵")["offsets"]["echelon"] == pytest.approx(0.04)
    # 变更单带证据落库 + 30 日实验挂账
    rows = ap_rows(sf)
    assert rows[0]["evidence"]["sample_days"] == 30
    assert any(e["change_id"] == rows[0]["id"] for e in
               __import__("app.services.experiments", fromlist=["list_experiments"])
               .list_experiments(session_factory=sf))


def ap_rows(sf):
    from sqlalchemy import select

    from app.models.agent import AgentParamChange as C

    with sf() as db:
        return [{
            "id": r.id, "key": r.key, "status": r.status,
            "evidence": json.loads(r.evidence) if r.evidence else {},
        } for r in db.execute(select(C)).scalars().all()]


def test_autonomy_off_generates_but_never_executes(sf, monkeypatch):
    """停机开关：议程照常生成，但一项都不执行（降级为建议清单）。"""
    _fake_llm(monkeypatch, LLM_OK)
    monkeypatch.setattr(evo, "autonomy_enabled", lambda: False)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    # autonomy 关闭：议程照常生成（ready），但执行层一项不动（后置由 execute_agenda 的开关拦截）
    assert agenda["status"] == "ready"
    assert all(i["status"] == "pending" for i in agenda["items"])
    assert ap_rows(sf) == []  # 没有任何参数变更
    # 直接调 execute_agenda：开关拦截 → skipped（证明不是靠"没调执行"碰巧安全）
    executed = evo.execute_agenda(agenda, sf)
    assert executed["status"] == "skipped"
    assert executed["error"]["code"] == "AutonomyOff"
    assert ap_rows(sf) == []


def test_redline_param_rejected(sf, monkeypatch):
    """红线参数（风控阈值）被提议 → rejected 并留痕，即使 LLM 输出了。"""
    payload = json.dumps({"items": [
        {"class": "A", "finding": "gap 阈值太严",
         "evidence": {}, "action": "放宽禁买线",
         "param": {"key": "picks_gate_block_gap", "after": 12.0},
         "priority": 1},
    ]})
    _fake_llm(monkeypatch, payload)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["items"][0]["status"] == "rejected"
    assert "红线" in agenda["items"][0]["result"]


def test_frequency_gate_defers_second_change(sf, monkeypatch):
    """同参数 24h 内只允许一次自动变更（第二单 deferred）。"""
    _fake_llm(monkeypatch, LLM_OK)

    async def main():
        first = await evo.run_evolution_now(sf)
        # 手动清掉今日议程，模拟次日同参数再次被提出
        # （日期源必须与 generate_agenda 一致用北京日期——UTC/北京午夜前后会差一天）
        with sf() as db:
            row = db.query(AgentAgenda).filter(
                AgentAgenda.date == evo.beijing_now().date().isoformat()).one()
            db.delete(row)
            db.commit()
        second = await evo.run_evolution_now(sf)
        return first, second

    first, second = asyncio.run(main())
    assert first["items"][0]["status"] == "executed"
    a_items = [i for i in second["items"] if i["class"] == "A"]
    assert a_items and a_items[0]["status"] == "deferred"
    assert "频率闸" in a_items[0]["result"]


def test_budget_exhausted_blocks_llm(sf, monkeypatch):
    """LLM 预算耗尽 → 议程 skipped（不烧钱），显式原因。"""
    monkeypatch.setattr(evo.settings, "agent_daily_llm_budget", 0)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["status"] == "skipped"
    assert "预算" in agenda["error"]["message"]


# ---------------------------------------------------------------- R11 议程状态机

def _seed_agenda(sf, status, **kw):
    today = evo.beijing_now().date().isoformat()
    with sf() as db:
        db.add(AgentAgenda(date=today, status=status, **kw))
        db.commit()
    return today


def _counting_llm(monkeypatch, payload):
    calls = {"n": 0}

    async def fake(fn):
        calls["n"] += 1
        return payload

    monkeypatch.setattr(evo, "_llm_call", fake)
    monkeypatch.setattr("app.core.llm_client.chat_completion", lambda *a, **k: payload)
    return calls


def test_failed_agenda_retries_in_place_without_unique_violation(sf, monkeypatch):
    """R11-1：`failed` 议程重试必须**原地复用该行**。

    `AgentAgenda.date` 是 unique，旧实现对 `failed` 是「不 return，继续往下走
    `db.add(新行)`」⇒ 重试必抛 IntegrityError：当日报错后再也无法自愈，
    而"失败就重试"正是调度分支与手动触发的共同路径。
    回退即红：本用例会以 IntegrityError 收场（而不是断言失败）。
    """
    _counting_llm(monkeypatch, json.dumps({"items": []}))
    today = _seed_agenda(sf, "failed", error='{"code":"Boom","message":"上一轮炸了"}')

    # 直接测 generate_agenda：run_evolution_now 在 autonomy 开启时会继续
    # execute_agenda，把 status 改成 executed（那是另一段职责）。
    agenda = asyncio.run(evo.generate_agenda(sf))

    assert agenda["status"] == "ready"
    assert agenda["error"] is None, "重试成功后必须清掉上一轮的 error"
    assert agenda["finished_at"] is not None
    with sf() as db:
        rows = db.query(AgentAgenda).filter(AgentAgenda.date == today).all()
    assert len(rows) == 1, f"重试必须复用原行，实际 {len(rows)} 行（撞唯一键的近因）"


def test_stale_generating_agenda_is_retried(sf, monkeypatch):
    """R11-2：进程被杀留下的 `generating` 必须可重跑，不能永久返回空快照。

    旧判据 `status not in ("failed",)` 把 generating 视为"正在进行"⇒ 当天议程
    永久缺失，`get_agenda` 永远回同一份空 items，界面一直显示"生成中"。
    回退即红：返回值会是那行残留的 generating，而不是 ready。
    """
    _counting_llm(monkeypatch, json.dumps({"items": []}))
    today = _seed_agenda(sf, "generating")

    agenda = asyncio.run(evo.generate_agenda(sf))

    assert agenda["status"] == "ready" and agenda["date"] == today
    assert agenda["finished_at"] is not None


def test_inflight_agenda_is_not_generated_twice(sf, monkeypatch):
    """互斥：本进程已有一轮在跑 ⇒ 不重复消耗 LLM 预算，返回当前快照。

    这是「可重跑」的反向约束——没有它，修复 R11-2 就会变成调度器与 API
    双触发各跑一遍（每次都是真实 LLM 调用）。
    """
    calls = _counting_llm(monkeypatch, json.dumps({"items": []}))
    today = _seed_agenda(sf, "generating")
    evo._AGENDA_INFLIGHT.add(today)
    try:
        out = asyncio.run(evo.generate_agenda(sf))
    finally:
        evo._AGENDA_INFLIGHT.discard(today)

    assert out["status"] == "generating" and out["date"] == today
    assert calls["n"] == 0, "在跑期间不得再次调用 LLM"


def test_ready_agenda_is_reused_idempotently(sf, monkeypatch):
    """反向对照：已就绪的议程照旧复用（不重跑、不重复花钱）。"""
    calls = _counting_llm(monkeypatch, json.dumps({"items": []}))
    _seed_agenda(sf, "ready", items='[{"class":"B","finding":"x","status":"executed"}]')

    agenda = asyncio.run(evo.generate_agenda(sf))

    assert agenda["status"] == "ready" and len(agenda["items"]) == 1
    assert calls["n"] == 0


def test_parse_items_drops_garbage_and_c_class_deferred(sf, monkeypatch):
    """非法项丢弃；C 类不静默忽略而是 deferred 留痕。"""
    monkeypatch.setattr(evo.settings, "agent_code_change_enabled", True)
    payload = json.dumps({"items": [
        {"class": "A", "finding": "缺 param 字段", "action": "x", "priority": 1},
        {"class": "C", "finding": "补一个边界校验", "action": "改 engine.py", "priority": 2},
        {"class": "B", "finding": "沉淀今日结论", "summary": "……", "priority": 3},
        "not-a-dict",
    ]})
    _fake_llm(monkeypatch, payload)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    by_class = {i["class"]: i for i in agenda["items"]}
    assert by_class["A"]["status"] == "rejected"  # 缺 param/非法值被校验拒绝
    # C 类已接入执行器（P1-⑤）：缺 files → 预检 rejected（不再 deferred）
    assert by_class["C"]["status"] == "rejected" and "目标文件" in by_class["C"]["result"]
    assert by_class["B"]["status"] == "executed"


def test_get_and_list_agendas(sf, monkeypatch):
    _fake_llm(monkeypatch, json.dumps({"items": []}))

    async def main():
        await evo.run_evolution_now(sf)
        return evo.get_agenda(session_factory=sf), evo.list_agendas(session_factory=sf)

    today, rows = asyncio.run(main())
    assert today is not None and rows[0]["date"] == today["date"]


# ---------------------------------------------------------------- 数据健康哨兵（P2-②）

def test_data_health_structure(sf):
    """哨兵输出结构稳定：checks 全带 name/ok/detail，issues 与 not-ok 集合一致。

    不断言具体 ok 值（依赖宿主机文件状态），只锚定契约——语义异常的判断在议程 LLM。
    """
    out = evo._collect_data_health(sf)
    assert out["available"] is True
    names = {c["name"] for c in out["checks"]}
    assert {
        "trade_calendar", "snapshot_parquet", "marketdb", "alert_pipeline",
        # 2026-09-10 纳入：回补调度静默失败 → 分位校准窗口漂移 6 个交易日无人察觉
        "sentiment_metrics",
    } <= names
    for c in out["checks"]:
        assert isinstance(c["ok"], bool) and c["detail"]
    not_ok = [c["name"] for c in out["checks"] if not c["ok"]]
    assert out["n_issues"] == len(not_ok)
    # issues 每条 = "<name>：<detail>"，且与 not_ok 集合一一对应
    assert sorted(i.split("：", 1)[0] for i in out["issues"]) == sorted(not_ok)


# ---------------------------------------------------------------- 调度器复现（2026-09-09 15:45 议程未触发事故）


def test_scheduler_fires_when_clock_crosses_window(sf, monkeypatch):
    """复现 09-09 事故：真实 evolution_scheduler 循环跨过 15:45 窗口必须生成议程。

    用受控时钟 + 真实持久化交易日历驱动真实调度循环；窗口前不生成、跨过后生成。
    若此测试挂，说明调度逻辑本身有 bug；若过，则当日未触发是进程环境问题，
    须靠调度器 WARNING 日志与 /api/agent/agenda meta 的 liveness 现场取证。
    """
    from datetime import datetime

    from app.market import trade_calendar as tc


    today = evo.beijing_now().date()

    # 真实持久化日历（与线上同一份数据），保证交易日守卫用的是真实口径
    real_days = tc._load_persisted()
    assert real_days, "持久化交易日历必须可读"

    # ⚠️ 2026-09-12 修复：受控时钟锚到**日历里真实存在的交易日**（末元素），不再取"真实今天"。
    # 原写法是 `assert real_days[-1] >= 今天` + 时钟 = 真实今天，这在**周末与法定节假日必然失败**：
    # 日历只装交易日，周六运行时末元素是周五，于是 ①前提断言挂；②即便绕过前提，
    # 调度器的交易日守卫也会（正确地）判非交易日而不生成议程——一年里约 1/3 的日子必挂。
    # 本测试的主题是「时钟跨过 15:45 窗口是否触发」，与"运行日恰为交易日"无关，故把二者解耦：
    # 锚定日仍取自**真实日历**（保留真实口径），只是不再绑定当天的日历位置。
    probe_day = real_days[-1]
    assert probe_day in real_days, "夹具前提：锚定日必须来自真实日历"
    assert tc.last_trade_date(real_days, asof=probe_day) == probe_day, (
        "夹具前提：锚定日必须是交易日，否则调度器守卫会（正确地）拒绝生成议程"
    )
    today = probe_day

    async def fake_trading_days(provider, lookback_days: int = 120):
        return list(real_days)

    monkeypatch.setattr(tc, "trading_days", fake_trading_days)
    monkeypatch.setattr(evo, "autonomy_enabled", lambda: False)  # 只验证生成，不执行
    _fake_llm(monkeypatch, json.dumps({"items": []}))

    class _Hub:
        provider = None

    class _State:
        hub = _Hub()

    class _App:
        state = _State()

    clock = {"now": datetime(probe_day.year, probe_day.month, probe_day.day, 14, 44,
                             tzinfo=BJ_TZ)}
    monkeypatch.setattr(evo, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(evo, "_MIN_TICK_INTERVAL_SEC", 0.01)  # 生产行为不变（60s 下限），测试提速

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(
            evo.evolution_scheduler(_App(), stop, run_hour=15, run_minute=45,
                                    check_interval_seconds=0.02),
        )
        try:
            await asyncio.sleep(0.15)  # 窗口前若干 tick
            assert evo.get_agenda(today.isoformat(), sf) is None, "窗口前不应生成议程"
            clock["now"] = clock["now"].replace(hour=15, minute=46)
            # ⚠️ 必须等**终态**，不能等"row 存在"：generate_agenda 先落一行
            # status="generating" 再继续异步跑，所以"行已存在"是个中间态。
            # 2026-09-11 才暴露——此前 collect_inputs 是同步调用、不让出事件循环，
            # 测试主协程只能在整段跑完后才被唤醒，于是这个脆弱的等待条件
            # "侥幸通过"；它改走 to_thread 让步点变多后立刻现形（进程侧日志显示
            # 议程确实完成、status=ready，是断言读早了，不是产品缺陷）。
            final = ("ready", "skipped", "failed", "executed")
            for _ in range(60):
                await asyncio.sleep(0.05)
                row = evo.get_agenda(today.isoformat(), sf)
                if row is not None and row["status"] in final:
                    break
            row = evo.get_agenda(today.isoformat(), sf)
            assert row is not None, "跨过 15:45 窗口后调度器必须生成今日议程"
            assert row["status"] in final, f"议程未在窗口内进入终态：{row['status']}"
            # liveness 面：tick 必须刷新（否则 data=null 时无法区分"调度死了"与"真没有"）
            st = evo.scheduler_status()
            assert st["last_tick_at"], "调度器 tick 必须刷新 liveness"
            assert st["last_tick_age_sec"] is not None and st["last_tick_age_sec"] < 5
        finally:
            stop.set()
            with __import__("contextlib").suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=2)

    asyncio.run(main())


def test_scheduler_skips_non_trading_day(sf, monkeypatch):
    """非交易日跨过 15:45 窗口**不得**生成议程——守卫的另一侧定点回归。

    2026-09-12 补：原有覆盖只测"交易日必须触发"，**"非交易日必须不触发"无人守**。
    而 09-12（周六）跑全量时正是这一侧暴露的——当时失败被误读为"测试挂了"，
    实为测试断言写宽（把"日历覆盖交易日"写成了"日历覆盖今天"）。
    锚定日取真实日历末交易日 **+1 天**（必为周末或隔日，确定性、与运行日无关）。
    """
    import contextlib

    from app.market import trade_calendar as tc

    real_days = tc._load_persisted()
    assert real_days, "持久化交易日历必须可读"
    probe_day = real_days[-1] + timedelta(days=1)
    assert tc.last_trade_date(real_days, asof=probe_day) != probe_day, (
        "夹具前提：锚定日必须**不是**交易日"
    )

    async def fake_trading_days(provider, lookback_days: int = 120):
        return list(real_days)

    monkeypatch.setattr(tc, "trading_days", fake_trading_days)
    monkeypatch.setattr(evo, "autonomy_enabled", lambda: False)
    monkeypatch.setattr(evo, "_MIN_TICK_INTERVAL_SEC", 0.01)
    # 中和周五的元评估分支（与本测试主题无关，且会走真实文件 IO）
    monkeypatch.setattr(evo, "_LAST_META_WEEK", probe_day.isocalendar()[:2])

    class _Hub:
        provider = None

    class _State:
        hub = _Hub()

    class _App:
        state = _State()

    clock = {"now": datetime(probe_day.year, probe_day.month, probe_day.day, 14, 44,
                             tzinfo=BJ_TZ)}
    monkeypatch.setattr(evo, "beijing_now", lambda: clock["now"])

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(
            evo.evolution_scheduler(_App(), stop, run_hour=15, run_minute=45,
                                    check_interval_seconds=0.02),
        )
        try:
            await asyncio.sleep(0.1)
            clock["now"] = clock["now"].replace(hour=15, minute=46)  # 跨过窗口
            await asyncio.sleep(0.2)  # 给足若干 tick
            assert evo.get_agenda(probe_day.isoformat(), sf) is None, (
                "非交易日不得生成议程（否则会在节假日产出无依据的议程）"
            )
        finally:
            stop.set()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=2)

    asyncio.run(main())


# ---------------------------------------------------------------- 停机范围（R10，2026-09-14）


def _run_scheduler_ticks(monkeypatch, *, autonomy: bool, seconds: float = 0.25) -> dict:
    """驱动**真实** `evolution_scheduler` 若干 tick，返回两个自治写入口的调用计数。

    R10 的缺陷形态是「停机开关未覆盖调度器的自动转正/回滚」——所以这里不测单个函数，
    而是跑真调度循环、看它到底有没有去碰这两个写入口。

    窗口刻意错开议程：时钟固定 16:30，`run_hour=23:59` ⇒ 只打开
    「实验裁决（hour≥16）」与「影子评估（hour≥15）」两处，**不触发议程生成**
    （否则要连 LLM、交易日历一起桩掉，噪声压过被测点）。
    时钟与日期都自算、不读持久化日历 ⇒ 不与真实运行日耦合。
    """
    import contextlib

    import app.services.experiments as ex

    calls = {"conclude": 0, "shadow": 0}

    def _spy(name: str):
        def _fn(*_a, **_k):
            calls[name] += 1
            return []

        return _fn

    monkeypatch.setattr(ex, "conclude_due", _spy("conclude"))
    monkeypatch.setattr(ex, "evaluate_and_promote_shadow", _spy("shadow"))
    monkeypatch.setattr(evo, "autonomy_enabled", lambda: autonomy)
    # 模块级节流标志跨测试残留：不重置则前一个测试跑过就等于本次被跳过（假绿）
    monkeypatch.setattr(evo, "_LAST_CONCLUDE_DATE", "")
    monkeypatch.setattr(evo, "_LAST_SHADOW_DATE", "")
    monkeypatch.setattr(evo, "_MIN_TICK_INTERVAL_SEC", 0.01)

    class _Hub:
        provider = None

    class _State:
        hub = _Hub()

    class _App:
        state = _State()

    today = evo.beijing_now().date()  # ⚠️ 必须在替换 beijing_now **之前**取
    clock = {"now": datetime(today.year, today.month, today.day, 16, 30, tzinfo=BJ_TZ)}
    monkeypatch.setattr(evo, "beijing_now", lambda: clock["now"])

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(
            evo.evolution_scheduler(_App(), stop, run_hour=23, run_minute=59,
                                    check_interval_seconds=0.02),
        )
        try:
            await asyncio.sleep(seconds)
        finally:
            stop.set()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=2)

    asyncio.run(main())
    return calls


def test_scheduler_runs_autonomous_writers_when_enabled(monkeypatch):
    """**正向对照**（KB-ENG-65：单侧"零调用"断言可能只是窗口没走到）。

    自治开启时必须真的调到这两个写入口，否则下一条"零调用"测试毫无信息量。
    """
    calls = _run_scheduler_ticks(monkeypatch, autonomy=True)
    assert calls["conclude"] == 1, "自治开启时到期实验裁决应被调度（且每日仅一次）"
    assert calls["shadow"] == 1, "自治开启时影子评估应被调度（且每日仅一次）"


def test_scheduler_skips_autonomous_writers_when_stopped(monkeypatch):
    """停机开关必须覆盖自动转正/回滚：autonomy=0 时这两个写入口一次都不许进。

    此前它们位于 `autonomy_enabled()` 判据之外 ⇒「停机」只停住议程执行，
    调度仍在自动改参/回滚（审查 R10 静态确认）。
    """
    calls = _run_scheduler_ticks(monkeypatch, autonomy=False)
    assert calls == {"conclude": 0, "shadow": 0}, (
        "自治关闭时不得有任何自动状态迁移（含劣化自动回滚）"
    )


def test_b_class_never_touches_real_evolution_dir(sf, monkeypatch, tmp_path):
    """KB-ENG-19 回归：B 类执行只写隔离目录，绝不追加真实 docs/evolution/。

    2026-09-09 事故：_EVOLUTION_DIR 锚定真实仓库根而测试未隔离，每次 pytest
    都向真实 docs/evolution/当天.md 追加 LLM 夹具条目（单日累计 84 行垃圾）。
    """
    payload = json.dumps({"items": [
        {"class": "B", "finding": "隔离验证条目", "summary": "s", "priority": 1},
    ]})
    _fake_llm(monkeypatch, payload)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["items"][0]["status"] == "executed"
    # 写入发生在隔离目录（fixture 已把 _EVOLUTION_DIR 指到 tmp）
    assert (evo._EVOLUTION_DIR / f"{evo.beijing_now().date().isoformat()}.md").exists()
    assert "隔离验证条目" in (evo._EVOLUTION_DIR / f"{evo.beijing_now().date().isoformat()}.md").read_text()


def test_data_health_flags_stale_sentiment_metrics(sf, tmp_path, monkeypatch):
    """回归（2026-09-10 事故）：情绪指标库窗口陈旧必须报 NG。

    真实事故：回补调度把 provider 原始日历（字符串）喂给 backfill → TypeError 被
    `except Exception` 收成一条日志 ⇒ 库停在 6 个交易日之前，界面照旧写"按近 241
    个交易日分位校准"。哨兵不查这个文件就等于没告警——这条断言锁住"会报"。
    """
    from datetime import timedelta


    data_dir = tmp_path / "backend" / "data"
    data_dir.mkdir(parents=True)
    today = beijing_now().date()
    stale_tail = (today - timedelta(days=20)).isoformat()  # 肯定超 3 个交易日的线
    (data_dir / "sentiment_metrics.json").write_text(
        json.dumps({"days": {stale_tail: {"date": stale_tail}}}), encoding="utf-8"
    )
    (data_dir / "trade_calendar.json").write_text(
        json.dumps({"days": [(today - timedelta(days=i)).isoformat() for i in range(30)],
                    "source": "test"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(evo, "PROJECT_ROOT", tmp_path)

    out = evo._collect_data_health(sf)
    check = next(c for c in out["checks"] if c["name"] == "sentiment_metrics")
    assert check["ok"] is False
    assert "回补调度停跑" in check["detail"]
    assert any(i.startswith("sentiment_metrics：") for i in out["issues"])


# ---------------------------------------------------------------- P0-7：marketdb 陈旧闸门 + 确定性议程项


def test_data_health_marketdb_uses_content_date_not_mtime(sf, tmp_path, monkeypatch):
    """P0-7 收紧：marketdb 判据改为**内容日期 × 交易日滞后**，不是文件 mtime。

    旧判据 `mtime > 26h` 只证明「文件被写过」——一次失败的 `--full` 重跑会刷新
    mtime 而数据仍停在旧日期（假 OK）。本用例把 mtime 刷成"刚刚改过"、内容却停在
    10 天前，断言哨兵照样报 NG：判据必须落在数据上。
    """
    import duckdb


    mdb_dir = tmp_path / "backend" / "data" / "marketdb"
    mdb_dir.mkdir(parents=True)
    db = mdb_dir / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    tail = beijing_now().date() - timedelta(days=10)
    ms = int(datetime(tail.year, tail.month, tail.day,
                      tzinfo=BJ_TZ).timestamp() * 1000)
    con.execute("INSERT INTO daily_k_adj VALUES ('600519.SH', ?, 10.0)", [ms])
    con.close()
    db.touch()  # mtime = 现在：旧判据会被骗过

    monkeypatch.setattr(evo, "PROJECT_ROOT", tmp_path)
    out = evo._collect_data_health(sf)
    check = next(c for c in out["checks"] if c["name"] == "marketdb")
    assert check["ok"] is False
    assert "滞后" in check["detail"] and "个交易日" in check["detail"]
    assert "同步停跑" in check["detail"]
    assert any(i.startswith("marketdb：") for i in out["issues"])


def test_data_health_items_deterministic_and_b_class():
    """数据健康 NG → **确定性**议程条目（不依赖 LLM 是否注意到）。

    真实事故（2026-09-09/09-10）：marketdb 停跑两天都进了 inputs.data_health.issues，
    但 LLM 每天只产出 1 条别的条目 ⇒「检查到 ≠ 有人知道」。此用例锁住代码侧固化。
    """
    dh = {"available": True, "n_issues": 1,
          "issues": ["marketdb：367 MB，库内最新 2026-09-03，滞后 6 个交易日"],
          "checks": [{"name": "marketdb", "ok": False, "detail": "滞后 6 个交易日"},
                     {"name": "disk_free", "ok": True, "detail": "ok"}]}
    items = evo._data_health_items(dh)
    assert len(items) == 1
    it = items[0]
    assert it["class"] == "B" and it["origin"] == evo.DATA_HEALTH_ORIGIN
    assert it["status"] == "pending" and it["priority"] == 1
    assert "marketdb" in it["finding"]
    # evidence 只带未通过项（通过项不噪声化证据）
    assert [c["name"] for c in it["evidence"]["failed_checks"]] == ["marketdb"]
    # 无异常 / 检查不可用 → 不产出（宁缺毋滥）
    assert evo._data_health_items({"available": True, "n_issues": 0, "issues": []}) == []
    assert evo._data_health_items({"available": False, "issues": ["x"]}) == []


def test_data_health_item_bypasses_task_budget(sf, monkeypatch):
    """确定性议程项不得被自治任务预算挤掉——否则异常就又变回"没人知道"。

    预算按 `executed` 计数；LLM 用满后普通条目会 deferred，但数据健康项必须落账。
    """
    monkeypatch.setattr(evo.settings, "agent_daily_task_budget", 0)
    agenda = {
        "date": evo.beijing_now().date().isoformat(),
        "items": [
            {"class": "D", "finding": "占位", "summary": ""},  # 预算为 0 时必 deferred
            {"class": "B", "origin": evo.DATA_HEALTH_ORIGIN, "finding": "数据健康哨兵报警",
             "summary": "marketdb 停跑", "evidence": {}, "status": "pending", "result": ""},
        ],
    }
    out = evo.execute_agenda(agenda, sf)
    by_finding = {i.get("finding"): i for i in out["items"]}
    assert by_finding["数据健康哨兵报警"]["status"] == "executed"
    assert by_finding["占位"]["status"] == "deferred"


# ---------------------------------------------------------------- S1-2：持仓监护读取降级进哨兵


def test_data_health_surfaces_position_monitor_read_failure(sf):
    """S1-2 回归：`exit_engine` 两路持仓读取失败必须被数据健康哨兵看见。

    旧行为：读失败 `return {}` / `positions = []`，与「确实无持仓」返回值相同，
    哨兵与界面都显示正常 ⇒ 自动离场/硬止损/真实持仓提醒整轮静默跳过。
    """
    from app.picks import exit_engine as ee

    ee._PAPER_READ.update(state="failed", reason="OperationalError: database is locked", failures=2)
    ee._REAL_READ.update(state="failed", reason="OperationalError: database is locked", failures=1)
    try:
        out = evo._collect_data_health(sf)
        check = next(c for c in out["checks"] if c["name"] == "position_monitor_read")
        assert check["ok"] is False
        assert "监护降级" in check["detail"]
        assert any(i.startswith("position_monitor_read：") for i in out["issues"])
        # 文案只用异常类名（不含会变化的完整消息/计数），保证哨兵去重稳定
        assert "database is locked" not in check["detail"]

        # 恢复正常 → 不再报问题（避免"每天都红的门等于没有门"）
        ee._PAPER_READ.update(state="empty", reason=None, failures=0)
        ee._REAL_READ.update(state="ok", reason=None, failures=0)
        out2 = evo._collect_data_health(sf)
        assert next(c for c in out2["checks"] if c["name"] == "position_monitor_read")["ok"] is True
    finally:
        ee._PAPER_READ.update(state="unknown", reason=None, failures=0)
        ee._REAL_READ.update(state="unknown", reason=None, failures=0)


@pytest.mark.parametrize("status", ["proposed", "executed"])
def test_imp046_code_proposals_never_apply_review_items(sf, monkeypatch, status):
    import app.review.storage as storage
    calls = []
    monkeypatch.setattr(storage, "update_action_item_status", lambda *a, **kw: calls.append((a, kw)))
    meta = {"id": "r1", "title": "fixture", "category": "strategy"}
    agenda = {"inputs": {"review": {"available": True, "trade_date": "20260918", "action_items": [meta]}}}
    items = [{"class": "C", "status": status, "review_item": meta, "result": "proposal"},
             {"class": "B", "status": "executed", "review_item": meta, "result": "document"}]
    evo._sync_review_items(agenda, items, sf)
    assert len(calls) == 1
    assert calls[0][0][2] == "applied" and "document" in calls[0][1]["note"]


def test_imp046_summary_counts_proposals_separately(monkeypatch):
    import app.services.agent_tasks as at
    calls = []
    monkeypatch.setattr(at, "record_audit", lambda **kw: calls.append(kw))
    evo.record_summary_audit("2026-09-18", [
        {"class": "C", "status": "proposed"}, {"class": "C", "status": "executed"},
        {"class": "B", "status": "executed"}, {"class": "A", "status": "deferred"},
    ])
    assert len(calls) == 1
    assert calls[0]["after"] == {"date": "2026-09-18", "executed": 1, "total": 4, "proposed": 1}


def test_imp046_parser_does_not_accept_model_supplied_execution_evidence():
    items = evo._parse_items(json.dumps({"items": [{"class": "C", "finding": "fixture",
        "files": ["backend/app/demo.py"], "status": "executed", "result": "already applied",
        "merged": True, "review_required": False, "commit": "forged"}]}))
    assert len(items) == 1 and items[0]["status"] == "pending" and items[0]["result"] == ""
    assert not {"merged", "review_required", "commit"}.intersection(items[0])
