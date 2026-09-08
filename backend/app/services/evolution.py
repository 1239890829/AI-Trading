"""AI 大脑：每日进化议程（docs/evolution-brain-plan.md v2）。

自主进化循环：**感知 → 诊断 → 议程 → 执行 → 验证 → 记忆**。

- 感知：五路证据（复盘改进项 / signal_health / 告警判读统计 / 计划对账；
  因子 IC 月度复核未到期时显式标注）。
- 诊断：LLM 汇总证据 → 严格 JSON 议程（A 参数 / B 文档 / C 代码），
  对复盘 action_items 逐条裁决（review_item 回执 → 执行后回写 applied）。
- 执行：A 类走参数变更单**自动生效**（白名单 + 红线 + 24h 频率闸 + 预算；
  30 日后置验证劣化自动回滚）；B 类写进化日报（docs/evolution/）；
  C 类走代码执行器（worktree 沙箱 → LLM patch → git apply --check →
  回归门禁 → commit → ff-only 合并；app/services/code_executor.py）。

安全模型（后置守护）：
- **红线清单**：风控/资金/推送/凭据/删除类——即使未来白名单扩张也碰不到。
- **停机开关**：`ASHARE_AGENT_AUTONOMY=0` → 只生成议程不执行（降级建议清单）；
  C 类另有独立开关 `ASHARE_AGENT_CODE_CHANGE`（最危险能力可单独关）。
- **预算**：每日 LLM 调用与自动任务数上限，超限议程照常生成但执行被拦。
- **频率闸**：同一参数 24h 内只允许一次自动变更（防抖动、防来回翻烧饼）。
- **C 类每日 ≤1**：audit 计数；文件白名单 + 禁改清单 + 干净工作区 + 回归门禁。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_session_factory
from app.market import trade_calendar as tc
from app.market.trading_status import beijing_now
from app.models.agent import AgentAgenda, AgentAudit, AgentParamChange, AgentTask

log = logging.getLogger(__name__)

#: 红线参数（不可被任何自动议程触碰；即使未来进入白名单也在此拦截）
REDLINE_KEYS = {
    "picks_gate_block_gap", "picks_gate_observe_gap", "picks_gate_anomaly_gap",
    "picks_gate_block_price", "risk_max_position_pct", "risk_max_total_exposure",
}

#: B 类文档白名单前缀（进化日报目录；其余路径一律拒绝）。
#: 锚定项目根（backend/app/services/ → parents[3]），不随进程 cwd 漂移。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EVOLUTION_DIR = PROJECT_ROOT / "docs" / "evolution"


# ---------------------------------------------------------------- 预算与开关


def autonomy_enabled() -> bool:
    return bool(settings.agent_autonomy_enabled)


def _utc_cutoff_today() -> datetime:
    """北京今日 0 点对应的 naive UTC 时刻（at/created_at 存 utcnow，naive）。

    ⚠️ 不要用 `>= f"{today}T00:00:00"` 字符串：SQLite 把 datetime 存成
    "YYYY-MM-DD HH:MM:SS"（空格分隔），' ' < 'T' 使比较恒 False——
    今日过滤会静默失效（C 类执行器测试抓出的真 bug，防线形同虚设）。
    """
    bj = beijing_now()
    return datetime(bj.year, bj.month, bj.day) - timedelta(hours=8)


def _budget_status(session_factory) -> dict:
    """今日预算占用：LLM 调用数（审计计）与自动执行任务数。"""
    cutoff = _utc_cutoff_today()
    with session_factory() as db:
        tasks = db.execute(
            select(AgentTask).where(AgentTask.created_at >= cutoff)
        ).scalars().all()
        audits = db.execute(
            select(AgentAudit).where(
                AgentAudit.action.in_(("agenda.generate", "triage.llm")),
                AgentAudit.at >= cutoff,
            )
        ).scalars().all()
    used_tasks = len([t for t in tasks if t.created_by == "ai"])
    used_llm = len(audits)
    return {
        "llm_used": used_llm, "llm_budget": settings.agent_daily_llm_budget,
        "tasks_used": used_tasks, "task_budget": settings.agent_daily_task_budget,
        "llm_exhausted": used_llm >= settings.agent_daily_llm_budget,
        "tasks_exhausted": used_tasks >= settings.agent_daily_task_budget,
    }


def _within_budget(budget: dict, *, need_llm: bool, need_task: bool) -> str | None:
    """返回拦截原因（None=放行）。议程生成需要 LLM 额度，执行需要任务额度。"""
    if need_llm and budget.get("llm_exhausted"):
        return f"今日 LLM 预算已用尽（{budget['llm_used']}/{budget['llm_budget']}）"
    if need_task and budget.get("tasks_exhausted"):
        return f"今日自动任务预算已用尽（{budget['tasks_used']}/{budget['task_budget']}）"
    return None


# ---------------------------------------------------------------- 证据收集


def _collect_review_improvements(session_factory) -> dict:
    """今日复盘报告的改进项（已有 15:30 复盘产出）。

    每条带齐**回写三元组**（id/title/category + 顶层 trade_date）——议程裁决
    为可自动化并执行成功后，用三元组把 action_items 状态回写为 applied
    （闭环第三段；update_action_item_status 的寻址守卫要求三元组吻合）。
    """
    try:
        from app.review.storage import get_report

        today = beijing_now().date().strftime("%Y%m%d")
        report = get_report(session_factory, today)
        if report is None:
            return {"available": False, "note": "今日复盘报告尚未生成"}
        items = [
            {"id": a.id, "title": a.title[:120], "category": a.category,
             "priority": a.priority, "target": a.target[:80],
             "expected_impact": a.expected_impact[:100]}
            for a in (report.action_items or [])[:10]
        ]
        return {"available": True, "trade_date": today, "action_items": items,
                "n": len(items)}
    except Exception as exc:  # noqa: BLE001  证据收集失败不阻断议程
        return {"available": False, "note": f"读取失败：{exc}"}


#: 方向校验（P1-④）：关键能力快照——与三份计划的阶段声明对账。
#: 每项 = (能力名, 检查方式)。缺 = 该计划项尚未落地（诚实标注，不冒充）。
_PLAN_CAPABILITY_CHECKS: list[tuple[str, Callable[[], bool]]] = [
    ("console.task_center", lambda: (Path(__file__).resolve().parents[1] / "services" / "agent_tasks.py").exists()),
    ("console.triage", lambda: (Path(__file__).resolve().parents[1] / "services" / "alert_triage.py").exists()),
    ("console.params", lambda: (Path(__file__).resolve().parents[1] / "services" / "agent_params.py").exists()),
    ("brain.agenda", lambda: True),  # 本模块自身
    ("brain.experiments", lambda: (Path(__file__).resolve().parents[1] / "services" / "experiments.py").exists()),
    ("signal_health", lambda: (Path(__file__).resolve().parents[1] / "picks" / "signal_health.py").exists()),
    ("chip_engine", lambda: (Path(__file__).resolve().parents[1] / "market" / "chip.py").exists()
     or (Path(__file__).resolve().parents[1] / "picks" / "chip_service.py").exists()),
    ("stock_flow", lambda: (Path(__file__).resolve().parents[1] / "market" / "stock_flow.py").exists()),
    ("hmm_regime", lambda: (Path(__file__).resolve().parents[1] / "picks" / "regime_hmm.py").exists()
     or (Path(__file__).resolve().parents[1] / "market" / "regime_hmm.py").exists()),
]


def _collect_plan_alignment() -> dict:
    """方向校验快照：关键能力存在性 → 差异表（对应三份计划的阶段声明）。"""
    landed, missing = [], []
    for name, check in _PLAN_CAPABILITY_CHECKS:
        try:
            (landed if check() else missing).append(name)
        except Exception:  # noqa: BLE001
            missing.append(name)
    return {
        "available": True, "landed": landed, "missing": missing,
        "note": "与 strategy-evolution/ai-brain/console-plan 的阶段声明对账；missing=计划声明但代码未落地",
    }


def _collect_signal_health(session_factory) -> dict:
    try:
        from app.picks.signal_health import collect_signal_health

        return {"available": True, "health": collect_signal_health(session_factory)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}


def _collect_triage_stats(session_factory) -> dict:
    """告警判读统计：哪些规则在产生噪音（ignore 占比）、哪些事件被升级。"""
    try:
        from app.models.agent import AgentTriage

        with session_factory() as db:
            rows = db.execute(select(AgentTriage)).scalars().all()
        by_verdict: dict[str, int] = {}
        for r in rows:
            by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
        return {"available": True, "total": len(rows), "by_verdict": by_verdict}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}


def collect_inputs(session_factory=None) -> dict:
    """六路证据汇总（factor_ic 月度复核到期时接入，缺席显式标注）。"""
    sf = session_factory or get_session_factory()
    return {
        "review": _collect_review_improvements(sf),
        "signal_health": _collect_signal_health(sf),
        "triage_stats": _collect_triage_stats(sf),
        "plan_alignment": _collect_plan_alignment(),
        "data_health": _collect_data_health(sf),
        "factor_ic": {"available": False, "note": "月度复核（factor_ic_review）到期接入"},
    }


# ---------------------------------------------------------------- 数据健康哨兵（P2-②，第六路证据）
#
# 规则层只做**机械合理性**检查（文件/计数/mtime）；「语义异常」的判断交给议程
# LLM（它有全盘视野）。曾在 2026-09-04 真实发生过：ths 官方端点失败 → 备源
# 30 天结果覆盖 243 天官方日历——这条哨兵就是那个事故的产物。


def _collect_data_health(session_factory) -> dict:
    checks: list[dict] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    # 1) 交易日历质量：覆盖天数 + 末日新鲜度（30 天覆盖事故的哨兵）
    try:
        raw = json.loads((PROJECT_ROOT / "backend" / "data" / "trade_calendar.json").read_text(encoding="utf-8"))
        days = raw.get("days") if isinstance(raw, dict) else raw
        tail = max(days) if days else ""
        stale = tail < (beijing_now().date() - timedelta(days=3)).isoformat()
        _add("trade_calendar", len(days) >= 200 and not stale,
             f"{len(days)} 天，末日 {tail}，source={raw.get('source') if isinstance(raw, dict) else '?'}"
             + ("（⚠️ 陈旧/覆盖不足——检查备源覆盖事故）" if (len(days) < 200 or stale) else ""))
    except Exception as exc:  # noqa: BLE001
        _add("trade_calendar", False, f"读取失败：{exc}")

    # 2) 快照落盘新鲜度（REPO_ROOT/data/parquet/snapshots；盘中 >30 分钟 = 停更）
    try:
        snaps = PROJECT_ROOT / "data" / "parquet" / "snapshots"
        today_dir = snaps / beijing_now().strftime("%Y%m%d")
        if not today_dir.exists():
            _add("snapshot_parquet", False, "今日快照目录不存在（快照管道可能停更）")
        else:
            newest = max(today_dir.glob("*.parquet"), key=lambda p: p.stat().st_mtime, default=None)
            age_min = (datetime.now().timestamp() - newest.stat().st_mtime) / 60 if newest else 1e9
            in_session = tc.in_trading_window(beijing_now())
            too_old = in_session and age_min > 30
            _add("snapshot_parquet", not too_old,
                 f"最新文件 {newest.name if newest else '—'}，{age_min:.0f} 分钟前"
                 + ("（⚠️ 盘中停更）" if too_old else ""))
    except Exception as exc:  # noqa: BLE001
        _add("snapshot_parquet", False, f"检查失败：{exc}")

    # 3) marketdb 日 K 仓同步（>26h 未更新 = 增量停跑）
    try:
        mdb = PROJECT_ROOT / "backend" / "data" / "marketdb" / "market.duckdb"
        if not mdb.exists():
            _add("marketdb", False, "market.duckdb 不存在")
        else:
            age_h = (datetime.now().timestamp() - mdb.stat().st_mtime) / 3600
            _add("marketdb", age_h <= 26,
                 f"{mdb.stat().st_size // (1024 * 1024)} MB，{age_h:.0f} 小时前更新"
                 + ("（⚠️ 同步停跑——scripts/sync_marketdb.py）" if age_h > 26 else ""))
    except Exception as exc:  # noqa: BLE001
        _add("marketdb", False, f"检查失败：{exc}")

    # 4) 告警与审计心跳（24h 全静默 = 管道可能挂了）
    try:
        from app.models.alert import AlertEvent

        cutoff = datetime.utcnow() - timedelta(hours=24)
        with session_factory() as db:
            n_events = len(db.execute(select(AlertEvent.id).where(AlertEvent.triggered_at >= cutoff)).scalars().all())
            n_audits = len(db.execute(select(AgentAudit.id).where(AgentAudit.at >= cutoff)).scalars().all())
        trading = tc.in_trading_window(beijing_now())
        quiet_suspect = trading and n_events == 0
        _add("alert_pipeline", not quiet_suspect,
             f"24h 告警 {n_events} 条 / 审计 {n_audits} 条"
             + ("（⚠️ 盘中零告警——watcher 管道疑似静默）" if quiet_suspect else ""))
    except Exception as exc:  # noqa: BLE001
        _add("alert_pipeline", False, f"检查失败：{exc}")

    # 5) 题材官方成分新鲜度（2026-09-08 用户指令：成分须与同花顺完全一致——
    #    TTL 内的滞后意味着成分调整期间归属错误。stale = synced_at 超 TTL）
    try:
        from app.models.theme_catalog import Theme

        ttl = settings.theme_members_ttl_hours
        stale_cutoff = datetime.utcnow() - timedelta(hours=ttl)
        with session_factory() as db:
            rows = db.execute(select(Theme.code, Theme.synced_at)).all()
        n_stale = len([c for c, ts in rows if ts is None or ts < stale_cutoff])
        _add("theme_members_fresh", n_stale == 0,
             f"官方成分超 {ttl}h 未同步的概念 {n_stale}/{len(rows)} 个"
             + ("（⚠️ 成分调整期归属会错——collect_news_events 每轮补 40 个）" if n_stale else ""))
    except Exception as exc:  # noqa: BLE001
        _add("theme_members_fresh", False, f"检查失败：{exc}")

    issues = [c for c in checks if not c["ok"]]
    return {"available": True, "checks": checks, "n_issues": len(issues),
            "issues": [f"{c['name']}：{c['detail']}" for c in issues]}


# ---------------------------------------------------------------- 议程生成


_SYSTEM_PROMPT = (
    "你是 A 股交易系统的自主进化大脑。给定今日系统证据（复盘改进项/信号健康/告警判读统计/计划对账），"
    "输出**今日进化议程**：对复盘 action_items 逐条裁决（可自动化的给出方案），并找出其他最值得立即改进的点。\n"
    "只输出 JSON：{\"items\": [{\"class\": \"A|B|C\", \"finding\": \"发现（一句话）\", "
    "\"evidence\": {…数据依据}, \"action\": \"…\", "
    "\"review_item\": {\"id\": \"…\", \"title\": \"…\", \"category\": \"…\"}（裁决某条复盘改进项时必带）, "
    "\"param\": {\"key\": \"picks_style_offsets_json\", \"after\": {…}}（仅 A 类必填）, "
    "\"summary\": \"…\"（仅 B 类）, "
    "\"files\": [\"backend/app/…\"]（仅 C 类：1-3 个要改的 .py，相对仓库根）, "
    "\"expected_effect\": \"预期效果\", \"verification\": \"如何验证\", \"priority\": 1}]}\n"
    "纪律：\n"
    "- 复盘的每条 action_items 都要裁决：可自动化的（改白名单参数→A 类并带 review_item 与 param；"
    "小规模 Python 代码修复→C 类并带 review_item 与 files；"
    "知识沉淀→B 类并带 review_item 与 summary）；不可自动化的**不要输出**（系统会自动标 deferred 并写明能力边界）\n"
    "- class=A 仅限 picks_style_offsets_json（after 是 {相位:{维度:delta}}，|delta|≤0.06）；"
    "没有充分数据依据就不要提 A 类\n"
    "- class=C 是**代码修改**：仅限 backend/app/、backend/tests/ 下的 .py；改动必须小而聚焦"
    "（修 bug、补校验、加守卫），禁止改架构、禁止碰 migrations/config/风控/资金/推送逻辑；"
    "执行器会用回归门禁（全量 pytest+pyflakes）验证，门禁不过会被丢弃\n"
    "- 不确定就不提；宁缺毋滥；最多 3 项；没有值得改的就输出空 items\n"
    "- 不提供买卖建议，不改风控/资金/推送相关任何东西"
)


def _parse_items(raw: str) -> list[dict]:
    """解析 LLM 议程输出：严格校验，非法项丢弃（丢弃项在返回值里注明原因）。"""
    from app.core.llm_client import extract_json_object

    data = extract_json_object(raw) or {}
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("议程输出缺少 items 数组")
    out: list[dict] = []
    for it in items[:3]:  # 上限 3 项（宁缺毋滥）
        if not isinstance(it, dict):
            continue
        cls = str(it.get("class") or "").strip().upper()
        if cls not in ("A", "B", "C"):
            continue
        item = {
            "class": cls,
            "finding": str(it.get("finding") or "")[:200],
            "evidence": it.get("evidence") if isinstance(it.get("evidence"), dict) else {},
            "action": str(it.get("action") or "")[:200],
            "expected_effect": str(it.get("expected_effect") or "")[:160],
            "verification": str(it.get("verification") or "")[:160],
            "priority": int(it.get("priority") or 9),
            "status": "pending",
            "result": "",
        }
        if cls == "A":
            param = it.get("param") or {}
            item["param"] = {
                "key": str(param.get("key") or ""),
                "after": param.get("after"),
            }
        if cls == "B":
            item["summary"] = str(it.get("summary") or "")[:500]
        if cls == "C":
            raw_files = it.get("files")
            item["files"] = [str(f) for f in raw_files][:3] if isinstance(raw_files, list) else []
        # 复盘改进项裁决回执（闭环回写用）
        ri = it.get("review_item")
        if isinstance(ri, dict) and ri.get("id"):
            item["review_item"] = {"id": str(ri["id"]), "title": str(ri.get("title") or "")[:120],
                                   "category": str(ri.get("category") or "")}
        out.append(item)
    return out


async def generate_agenda(session_factory=None) -> dict:
    """生成（或复用）今日议程：预算检查 → 收集证据 → LLM → 解析落库。

    ORM 纪律：实例不跨 session——每次更新都 `db.get` fresh load 后改属性，
    改完在**同一 session 内** dump（expire_on_commit 下 detached 访问会炸）。
    """
    sf = session_factory or get_session_factory()
    today = beijing_now().date().isoformat()
    with sf() as db:
        exist = db.execute(select(AgentAgenda).where(AgentAgenda.date == today)).scalars().first()
        if exist is not None and exist.status not in ("failed",):
            return _agenda_dump(exist)

    budget = _budget_status(sf)
    reason = _within_budget(budget, need_llm=True, need_task=False)

    with sf() as db:
        row = AgentAgenda(date=today, status="generating")
        db.add(row)
        db.commit()
        agenda_id = row.id

    if reason:
        with sf() as db:
            row = db.get(AgentAgenda, agenda_id)
            row.status = "skipped"
            row.error = json.dumps({"code": "Budget", "message": reason}, ensure_ascii=False)
            row.finished_at = datetime.utcnow()
            db.commit()
            return _agenda_dump(row)

    inputs = collect_inputs(sf)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, default=str)},
    ]
    try:
        from app.core.llm_client import chat_completion

        def _call() -> str:
            return chat_completion(
                base_url=settings.review_llm_base_url,
                api_key=settings.review_llm_api_key,
                model=settings.review_llm_model,
                messages=messages,
                provider=settings.llm_provider,
                cli_path=settings.llm_cli_path,
                timeout=120.0,
            )

        raw = await _llm_call(_call)
        items = _parse_items(raw)
        with sf() as db:
            row = db.get(AgentAgenda, agenda_id)
            row.inputs = json.dumps(inputs, ensure_ascii=False, default=str)
            row.items = json.dumps(items, ensure_ascii=False, default=str)
            row.status = "ready"
            db.commit()
            out = _agenda_dump(row)
    except Exception as exc:
        with sf() as db:
            row = db.get(AgentAgenda, agenda_id)
            row.status = "failed"
            row.error = json.dumps({"code": type(exc).__name__, "message": str(exc)[:300]},
                                   ensure_ascii=False)
            row.finished_at = datetime.utcnow()
            db.commit()
            out = _agenda_dump(row)
        log.warning("evolution agenda failed: %s", exc)
        return out

    with sf() as db:
        db.add(AgentAudit(actor="ai", action="agenda.generate", target="evolution"))
        db.commit()
    return out


async def _llm_call(fn) -> str:
    """LLM 同步调用包装（to_thread 不阻塞事件循环）。"""
    return await asyncio.wait_for(asyncio.to_thread(fn), timeout=150.0)


def _agenda_dump(row: AgentAgenda) -> dict:
    def _j(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return default

    return {
        "id": row.id, "date": row.date, "status": row.status,
        "inputs": _j(row.inputs, {}), "items": _j(row.items, []),
        "budget": _j(row.budget, {}),
        "error": _j(row.error, None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


# ---------------------------------------------------------------- 议程执行


def _param_change_in_24h(key: str, sf) -> bool:
    """频率闸：同参数 24h 内是否已有自动变更（applied 或 rolled_back）。"""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    with sf() as db:
        rows = db.execute(
            select(AgentParamChange).where(
                AgentParamChange.key == key,
                AgentParamChange.status.in_(("applied", "rolled_back")),
                AgentParamChange.created_at >= cutoff,
            )
        ).scalars().all()
        return bool(rows)


def _execute_a(item: dict, sf, agenda_date: str) -> dict:
    """A 类：参数变更单自动生效（白名单+红线+频率闸；证据随单落库）。

    生效成功即挂实验（后置验证：30 日后自动对比 signal_health，劣化自动回滚）。
    """
    param = item.get("param") or {}
    key = param.get("key") or ""
    if key in REDLINE_KEYS:
        return {**item, "status": "rejected", "result": f"参数 {key} 在红线清单，禁止自动修改"}
    try:
        from app.services import agent_params

        change = agent_params.propose(
            key, param.get("after"),
            source_type="ai_suggestion", source_id=f"agenda:{agenda_date}",
            evidence=item.get("evidence") or {}, session_factory=sf,
        )
        applied = agent_params.apply_change(change["id"], session_factory=sf)
        # 后置守护：自动挂实验（30 日窗口，劣化自动回滚——取代人工确认的机制）
        experiment = None
        with contextlib.suppress(Exception):
            from app.services.experiments import attach_experiment

            experiment = attach_experiment(
                applied["id"], key,
                hypothesis=item.get("expected_effect") or item.get("finding") or "",
                session_factory=sf,
            )
        result = f"变更单 #{applied['id']} 已自动生效"
        if experiment:
            result += f"（实验 #{experiment['id']} 已挂账，{VERIFY_NOTE}）"
        return {**item, "status": "executed", "result": result,
                "experiment_id": experiment["id"] if experiment else None}
    except ValueError as exc:
        return {**item, "status": "rejected", "result": f"校验拒绝：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {**item, "status": "failed", "result": f"{type(exc).__name__}: {exc}"}


VERIFY_NOTE = "30 日后置验证：劣化自动回滚"


def _execute_b(item: dict, agenda_date: str) -> dict:
    """B 类：进化日报（路径白名单 docs/evolution/，内容为 LLM 结论摘要）。"""
    try:
        day_dir = _EVOLUTION_DIR
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{agenda_date}.md"
        line = f"- **{item.get('finding', '')}**：{item.get('summary', '')}\n" \
               f"  依据：{json.dumps(item.get('evidence') or {}, ensure_ascii=False)}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return {**item, "status": "executed", "result": f"已写入 {path}"}
    except Exception as exc:  # noqa: BLE001
        return {**item, "status": "failed", "result": f"{type(exc).__name__}: {exc}"}


def execute_agenda(agenda: dict, session_factory=None) -> dict:
    """执行今日议程（autonomy 关闭时只记账不执行）。"""
    sf = session_factory or get_session_factory()
    if not autonomy_enabled():
        return {**agenda, "status": "skipped",
                "error": {"code": "AutonomyOff", "message": "自主执行已关闭（ASHARE_AGENT_AUTONOMY=0），议程仅作建议"}}
    budget = _budget_status(sf)
    items: list[dict] = []
    executed = 0
    for item in agenda.get("items") or []:
        cls = item.get("class")
        if cls == "C":
            from app.services import code_executor

            new = code_executor.execute_c_item(item, sf, agenda.get("date") or "")
            items.append(new)
            if new["status"] == "executed":
                executed += 1
            continue
        if executed >= settings.agent_daily_task_budget:
            items.append({**item, "status": "deferred",
                          "result": f"今日自动任务预算已用尽（{budget['task_budget']}）"})
            continue
        if cls == "A":
            if _param_change_in_24h(item.get("param", {}).get("key", ""), sf):
                items.append({**item, "status": "deferred",
                              "result": "同参数 24h 内已有自动变更（频率闸）"})
                continue
            new = _execute_a(item, sf, agenda.get("date") or "")
        elif cls == "B":
            new = _execute_b(item, agenda.get("date") or "")
        else:
            items.append({**item, "status": "rejected", "result": f"未知类别 {cls!r}"})
            continue
        items.append(new)
        if new["status"] == "executed":
            executed += 1

    with sf() as db:
        row = db.execute(select(AgentAgenda).where(AgentAgenda.date == agenda["date"])).scalars().first()
        if row is not None:
            row.items = json.dumps(items, ensure_ascii=False, default=str)
            row.budget = json.dumps(budget, ensure_ascii=False)
            row.status = "executed"
            row.finished_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
            out = _agenda_dump(row)
    record_summary_audit(agenda.get("date") or "", items)
    _sync_review_items(agenda, items, sf)
    return out


def _sync_review_items(agenda: dict, items: list[dict], sf) -> None:
    """闭环第三段：裁决为可自动化且**执行成功**的议程项 → 对应复盘改进项
    状态回写 applied（机器全权，无人工流转；review 的 status 枚举本就有 applied）。
    deferred/rejected 的改进项保持 pending——它们会出现在下次议程的输入里。
    """
    review = (agenda.get("inputs") or {}).get("review") or {}
    if not review.get("available"):
        return
    trade_date = review.get("trade_date")
    by_id = {str(i["id"]): i for i in review.get("action_items") or []}
    from app.review.storage import ActionItemStaleError, update_action_item_status

    for it in items:
        if it.get("status") != "executed":
            continue
        ri = it.get("review_item") or {}
        item_id = str(ri.get("id") or "")
        meta = by_id.get(item_id)
        if meta is None:
            continue
        try:
            update_action_item_status(
                sf, item_id, "applied",
                note=f"AI 大脑自动执行：{it.get('result', '')[:160]}",
                expect_trade_date=trade_date or "",
                expect_category=ri.get("category") or meta["category"],
                expect_title=ri.get("title") or meta["title"],
            )
        except (ValueError, LookupError, ActionItemStaleError) as exc:
            log.warning("review item %s sync failed: %s", item_id, exc)


def record_summary_audit(date: str, items: list[dict]) -> None:
    executed = [i for i in items if i.get("status") == "executed"]
    with contextlib.suppress(Exception):
        from app.services.agent_tasks import record_audit as _ra

        _ra(actor="ai", action="agenda.execute", target="evolution",
            after={"date": date, "executed": len(executed), "total": len(items)})


async def run_evolution_now(session_factory=None) -> dict:
    """手动/调度触发：生成今日议程 → autonomy 开启时立即执行。"""
    sf = session_factory or get_session_factory()
    agenda = await generate_agenda(sf)
    if agenda["status"] == "ready" and autonomy_enabled():
        agenda = execute_agenda(agenda, sf)
    return agenda


def get_agenda(date: str | None = None, session_factory=None) -> dict | None:
    sf = session_factory or get_session_factory()
    target = date or beijing_now().date().isoformat()
    with sf() as db:
        row = db.execute(select(AgentAgenda).where(AgentAgenda.date == target)).scalars().first()
        return _agenda_dump(row) if row else None


def list_agendas(limit: int = 14, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AgentAgenda).order_by(AgentAgenda.date.desc()).limit(limit)
        ).scalars().all()
        return [_agenda_dump(r) for r in rows]


# ---------------------------------------------------------------- 调度


async def evolution_scheduler(app, stop: asyncio.Event, *, run_hour: int, run_minute: int,
                              check_interval_seconds: float) -> None:
    """交易日 15:45 盘后议程（接在 15:30 复盘调度之后）——与 premarket_scheduler 同模式。

    顺带每日一次实验裁决（后置验证：到期实验对比 signal_health，劣化自动回滚）——
    conclude_due 幂等（只处理 running 且到期的），节流靠 _last_conclude_date。
    """
    global _LAST_CONCLUDE_DATE, _LAST_META_WEEK
    log.info("evolution scheduler started: daily at %02d:%02d", run_hour, run_minute)
    while not stop.is_set():
        try:
            now = beijing_now()
            today = now.date()
            # 每日一次：到期实验裁决（劣化自动回滚）
            if _LAST_CONCLUDE_DATE != today.isoformat() and now.hour >= 16:
                with contextlib.suppress(Exception):
                    from app.services.experiments import conclude_due

                    results = conclude_due()
                    _LAST_CONCLUDE_DATE = today.isoformat()
                    if results:
                        log.warning("[EVOLUTION] 实验裁决 %d 条：%s", len(results),
                                    json.dumps([{r["id"]: r["status"]} for r in results],
                                               ensure_ascii=False))
            if (now.hour, now.minute) >= (run_hour, run_minute):
                days = await tc.trading_days(app.state.hub.provider)
                if tc.last_trade_date(days, asof=today) == today:
                    existing = get_agenda(today.isoformat())
                    if existing is None or existing["status"] == "failed":
                        agenda = await run_evolution_now()
                        log.warning("[EVOLUTION] %s 议程完成：status=%s items=%d",
                                    today, agenda.get("status"), len(agenda.get("items") or []))
                    # 元评估周报（P2-①）：周五盘后 agenda 之后自动生成（幂等：一周一份）
                    if now.weekday() == 4 and _LAST_META_WEEK != today.isocalendar()[:2]:
                        with contextlib.suppress(Exception):
                            from app.services.meta_review import generate_meta_review

                            meta = await asyncio.to_thread(generate_meta_review)
                            _LAST_META_WEEK = today.isocalendar()[:2]
                            log.warning("[EVOLUTION] 元评估周报：%s", meta.get("status"))
        except Exception:
            log.exception("evolution scheduler tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=max(60.0, check_interval_seconds))


_LAST_CONCLUDE_DATE: str = ""
_LAST_META_WEEK: tuple = ()  # (ISO 年, 周)——元评估周报进程内幂等（文件存在性兜底）
