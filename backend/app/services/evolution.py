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
from app.models.agent import AgentAgenda, AgentAudit, AgentParamChange, AgentTask
from app.core.bjtime import beijing_now, beijing_now_naive  # S2-8 时区收敛

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


def _bj_cutoff_today() -> datetime:
    """北京今日 0 点（naive，agent 域 at/created_at 存北京 naive——2026-09-12 方案 A 起）。

    ⚠️ 不要用 `>= f"{today}T00:00:00"` 字符串：SQLite 把 datetime 存成
    "YYYY-MM-DD HH:MM:SS"（空格分隔），' ' < 'T' 使比较恒 False——
    今日过滤会静默失效（C 类执行器测试抓出的真 bug，防线形同虚设）。

    原名 `_utc_cutoff_today`（曾返回「北京 0 点 − 8h」对齐 UTC 存储），存储口径
    迁移北京后同步改名——函数名与口径不符的帮手是下一次事故的种子。
    """
    bj = beijing_now()
    return datetime(bj.year, bj.month, bj.day)


def _budget_status(session_factory) -> dict:
    """今日预算占用：LLM 调用数（审计计）与自动执行任务数。"""
    cutoff = _bj_cutoff_today()
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
                "n": len(items),
                "repeat_pending": _repeat_pending_items(session_factory)}
    except Exception as exc:  # noqa: BLE001  证据收集失败不阻断议程
        return {"available": False, "note": f"读取失败：{exc}"}


def _repeat_pending_items(session_factory, days: int = 7) -> list[dict]:
    """P2-2（2026-09-09）：近 N 日重复出现仍未处置（pending）的改进项。

    同一改进连续多日 pending = 「下轮强制改进」未落实的机器可读信号 → 议程据此
    判断应升级处置（连续 2 日同项 → C 类候选；06-review-framework §闭环规则）。
    按标题归一（去日期/序号差异）聚合，命中 ≥2 日的才列出。
    """
    from datetime import timedelta as _td

    try:
        from sqlalchemy import select as _sel

        from app.review.models import ReviewActionItemRow

        with session_factory() as db:
            cutoff = beijing_now().date() - _td(days=days)
            rows = db.execute(
                _sel(ReviewActionItemRow).where(
                    ReviewActionItemRow.trade_date >= cutoff.strftime("%Y%m%d"),
                    ReviewActionItemRow.status == "pending",
                )
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001  重复检测失败不阻断
        return [{"error": f"查询失败: {exc}"}]
    seen: dict[str, dict] = {}
    for r in rows:
        key = (r.category or "", r.title[:60])
        e = seen.setdefault(key, {"category": key[0], "title": key[1],
                                  "days": set(), "latest": ""})
        e["days"].add(r.trade_date)
        e["latest"] = max(e["latest"], r.trade_date)
    out = [
        {"category": e["category"], "title": e["title"],
         "pending_days": len(e["days"]), "latest": e["latest"]}
        for e in seen.values() if len(e["days"]) >= 2
    ]
    out.sort(key=lambda x: -x["pending_days"])
    return out[:5]


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


def _collect_factor_ic() -> dict:
    """因子 IC 证据（S2-11）：读 `factors/report.py` 对评估产物的只读访问层。

    与其它 `_collect_*` 同形：`{"available": ..., ...}`。
    **不吞异常的业务含义**——读失败就如实 `available: False`，绝不返回空列表
    让议程误以为"没有有效因子"（三态纪律：判不出 ≠ 没有）。
    """
    try:
        from app.factors.report import ic_evidence

        return ic_evidence()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}


def _collect_strategy_verification() -> dict:
    """战法核验结论（S2-11）：登记册里每个「已否决 / 观察」的状态是否真有证据。

    与 `factor_ic` 同形同因：核验器是离线重计算，结论此前只流向脚本 stdout，
    登记册状态靠人工誊写 ⇒ 议程一路看不到、也无从追问"凭什么否决"。
    这里只做**只读汇总**，不触发任何重算（重算走 `scripts/verify_*.py`）。

    `entries` 每条是 `verification_of` 的三态载荷；`without_evidence` 列出
    **标了 verify_key 却拿不到产物**的键——那才是真正该被追问的缺口
    （状态有、证据无）。
    """
    try:
        from app.picks.strategy_registry import (
            STATUS_ACTIVE,
            STATUS_OBSERVING,
            STATUS_REJECTED,
            SPECS,
        )
        from app.research.verify_registry import verification_of
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}

    #: 登记册状态 ↔ 核验结论的**预期**对应。不符即为「状态与证据打架」，
    #: 需要人工裁定（可能是重跑后数据变了，也可能是当初拍脑袋定的状态）。
    expected = {
        STATUS_ACTIVE: "pass",
        STATUS_OBSERVING: "observe",
        STATUS_REJECTED: "reject",
    }

    entries, missing, conflicts = [], [], []
    for spec in SPECS:
        if not spec.verify_key:
            continue
        try:
            v = verification_of(spec.verify_key)
        except Exception as exc:  # noqa: BLE001
            v = {"available": False, "verdict": None, "headline": None,
                 "reason": f"读取失败：{exc}"}
        v = {**v, "key": spec.verify_key, "name": spec.name, "status": spec.status}
        entries.append(v)
        if not v.get("available"):
            missing.append(spec.verify_key)
            continue
        if v.get("verdict") and expected.get(spec.status) != v["verdict"]:
            conflicts.append({
                "key": spec.verify_key,
                "status": spec.status,
                "verdict": v["verdict"],
                "headline": v.get("headline"),
            })

    return {
        "available": bool(entries),
        "total": len(entries),
        "with_evidence": len(entries) - len(missing),
        "without_evidence": missing,
        "conflicts": conflicts,
        "entries": entries,
        "note": None if entries else "登记册中无走核验的策略键",
        "caveat": "核验为离线重算（duckdb 全历史），结论随重跑更新；"
                  "超期产物只标注不删除，判读时须看 recorded_at；"
                  "`conflicts` 是状态与实测结论打架者，须人工裁定",
    }


def _collect_applied_landed(session_factory) -> dict:
    """**采纳落地核对**（S2-11 的最后一米）。

    `audit_applied_landed` 在 S2-11 里随 applied 载荷一起写好，却**从头到尾没有
    生产调用方**——只有测试在调它。这正是本轮反复在治的「产出即死」：
    写了核对能力，却没人读，于是「采纳率」仍然只是一个状态位的计数。

    接进议程后，每日证据里会带出：标记了 `applied` 的改进项里，有多少**参数真的改了**
    （`landed`）、多少只是标了状态（`not_landed`）。
    """
    try:
        from sqlalchemy import select

        from app.review.models import ReviewActionItemRow
        from app.review.writeback import audit_applied_landed
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"模块导入失败：{exc}"}

    try:
        with session_factory() as db:
            rows = db.execute(
                select(ReviewActionItemRow).where(ReviewActionItemRow.status == "applied")
            ).scalars().all()
            items = [{"id": r.id, "title": r.title, "status": r.status,
                      "resolution_note": r.resolution_note or ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}

    out = audit_applied_landed(items)
    out["available"] = True
    return out


def _collect_decision_ledger(session_factory) -> dict:
    """决策台账（S2-11 第 5 步「决策级记忆」）：只读聚合视图，零迁移。

    把散在四处的决策痕迹（改进项处置 / 策略核验 / 参数变更 / 标 applied 却未落地）
    并陈到一个台账里，回答「当初为什么这么定、后来兑现了吗」。
    只读、不落库、不做因果判定——详细边界见 `app/services/decision_ledger.py`。
    """
    try:
        from app.services.decision_ledger import collect_decision_ledger

        return collect_decision_ledger(session_factory)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}


def _collect_cognition_gaps() -> dict:
    """助手认知缺口清单（P2-28③）：此前只 `log.warning`，**没有任何消费方**。

    KB-ENG-49 说"日志即自动产出的缺口清单"——但日志会滚动、没人聚合，
    那份"清单"实际上没人看。落台账（`data/cognition_gaps.jsonl`）后这里读出来，
    让"助手哪些地方其实有数据却说没有"成为每日可见的证据。
    """
    try:
        from app.assistant.cognition import recent_gaps

        gaps = recent_gaps()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}
    return {
        "available": bool(gaps),
        "n": len(gaps),
        "recent": gaps[-10:],
        "note": None if gaps else "暂无认知缺口记录",
        "caveat": "命中只是信号不是判据：可能是工具覆盖缺口，也可能是提示词没说清；需人工复核后再改",
    }


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
    """多路证据汇总（第八/九路 + 第十路 review + framework_backlog P2-2）。"""
    sf = session_factory or get_session_factory()
    return {
        "review": _collect_review_improvements(sf),
        "signal_health": _collect_signal_health(sf),
        "triage_stats": _collect_triage_stats(sf),
        "plan_alignment": _collect_plan_alignment(),
        "data_health": _collect_data_health(sf),
        # S2-11（2026-09-11）：原先这里是硬编码的「不可用 + 一句托辞」——评估跑完了
        # 却没人读，闭环断在最后一米。现改读 `factors/report.py`，真实三态见 payload。
        # 守卫见 tests/test_factor_report.py（源码里不许再出现那句托辞）。
        "factor_ic": _collect_factor_ic(),
        "strategy_verification": _collect_strategy_verification(),
        "applied_landed": _collect_applied_landed(sf),
        "decision_ledger": _collect_decision_ledger(sf),
        "cognition_gaps": _collect_cognition_gaps(),
        "prediction": _collect_recent_prediction(sf),
        "knowledge_base": _collect_knowledge_base(),
        # 第九路（2026-09-09 用户指令「跟踪本质是实时选股」）：台账复盘×进化依据
        "tracking": _collect_tracking_stats(sf),
        # P2-2（2026-09-09）：复盘框架自优化待办（06 §7 演化日志最近条目）
        "framework_backlog": _collect_framework_backlog(),
    }


def _collect_framework_backlog() -> dict:
    """复盘框架演化日志（docs/kb/06-review-framework.md §7）最近待复查条目。

    06 框架闭环规则：自评低分维 → §7 登记改进 → 下一轮复盘先执行该改进再开始。
    此处把最近登记的自优化项作为议程输入之一，让每日议程能对照检查落实
    （「连续 2 轮同维低分 → 升级 C 类」由议程 LLM 依据本条+repeat_pending 裁决）。
    """
    path = PROJECT_ROOT / "docs" / "kb" / "06-review-framework.md"
    if not path.exists():
        return {"available": False, "note": "06-review-framework.md 不存在"}
    import re as _re
    try:
        text = path.read_text(encoding="utf-8")
        m = _re.search(r"##\s*7\.\s*框架演化日志(.*?)(?:\n## |\Z)", text, _re.S)
        section = m.group(1) if m else text[-800:]
        lines = [_l.strip() for _l in section.splitlines() if _l.strip().startswith("- ")
                 and _re.match(r"-\s*\d{4}-\d{2}-\d{2}", _l.strip())]
        return {"available": True, "n": len(lines), "recent": lines[-3:]}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"解析失败: {exc}"}


def _collect_tracking_stats(session_factory) -> dict:
    """第九路：盘中跟踪台账的复盘×进化依据（对照 signal_health 同看）。

    跟踪=实时选股：台账 5 日按来源层/准入门槛 × 判定聚合胜率与平均盈亏，
    供议程 LLM 发现「某来源层系统性误判」类改进项（如降某层准入权重）。
    """
    try:
        from app.picks.watch_ledger import tracking_review_stats

        return tracking_review_stats(5, session_factory)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"台账统计失败: {exc}"}


def _collect_knowledge_base() -> dict:
    """第八路（2026-09-09 知识库机制）：docs/kb/ 落地跟踪进议程。

    读 00-INDEX.md 索引表格（| ID | 一句话 | 状态 | 来源日 |），统计各状态条数：
    ⏳ 待落地条目可成为议程 C 类改进项候选；❌ 被取代条目防止回退；
    ⏳ 超 14 天未动的列入 stale_pending 复查。知识库缺失/解析失败显式 unavailable（三态）。
    """
    import re as _re

    kb_index = PROJECT_ROOT / "docs" / "kb" / "00-INDEX.md"
    if not kb_index.exists():
        return {"available": False, "note": "docs/kb/00-INDEX.md 不存在（知识库未初始化）"}
    try:
        pattern = _re.compile(
            r"^\| (KB-(?:STOCK|TRADE|ENG|DEC)-\d+) \| (.+?) \| ([✅🔶⏳❌]) \| (\d{4}-\d{2}-\d{2}) \|"
        )
        entries: list[dict] = []
        for line in kb_index.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line.strip())
            if m:
                entries.append(
                    {"id": m.group(1), "title": m.group(2), "status": m.group(3), "since": m.group(4)}
                )
        if not entries:
            return {"available": False, "note": "00-INDEX.md 无可解析条目（表格格式漂移？）"}
        by_status: dict[str, int] = {}
        for e in entries:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
        pending = [e for e in entries if e["status"] == "⏳"]
        stale_cutoff = (beijing_now().date() - timedelta(days=14)).isoformat()
        stale_pending = [e["id"] for e in pending if e["since"] < stale_cutoff]
        return {
            "available": True,
            "total": len(entries),
            "by_status": by_status,
            "pending": [{"id": e["id"], "title": e["title"][:50]} for e in pending[:8]],
            "stale_pending_14d": stale_pending,
            "note": "⏳ 待落地条目可成为 C 类改进项候选；❌ 条目防止回退（知识库永不删条目）",
        }
    except Exception as exc:  # noqa: BLE001  证据缺席不阻塞议程
        return {"available": False, "note": f"知识库解析失败: {exc}"}


def _collect_recent_prediction(session_factory) -> dict:
    """第七路（P1-4 消费回路）：最近一份新题材预判进议程——预判产出后不再孤立。

    只带方向名/评分/四问结论摘要（≤3 条），供议程 LLM 评估「是否值得提前埋伏」
    类改进项。无预判/读取失败显式 unavailable。
    """
    sf = session_factory or get_session_factory()
    try:
        from sqlalchemy import select as _sel

        from app.predict.models import PredictionReportRow

        with sf() as db:
            row = db.execute(
                _sel(PredictionReportRow).order_by(PredictionReportRow.created_at.desc()).limit(1)
            ).scalars().first()
        if row is None:
            return {"available": False, "note": "尚无新题材预判产出"}
        try:
            payload = json.loads(row.payload) if row.payload else {}
        except Exception:  # noqa: BLE001
            payload = {}
        themes = []
        for t in (payload.get("themes") or [])[:3]:
            themes.append({
                "theme": t.get("theme") or t.get("name"),
                "score": t.get("score"),
                "basis": (t.get("basis") or t.get("logic") or "")[:120],
            })
        return {
            "available": bool(themes),
            "target_date": row.target_date,
            "verdict_summary": row.verdict_summary,
            "themes": themes,
            "note": "预判数据供参考——采纳前须核对当日盘面是否已启动（防追高）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "note": f"读取失败：{exc}"}


# ---------------------------------------------------------------- 数据健康哨兵（P2-②，第六路证据）
#
# 规则层只做**机械合理性**检查（文件/计数/mtime）；「语义异常」的判断交给议程
# LLM（它有全盘视野）。曾在 2026-09-04 真实发生过：ths 官方端点失败 → 备源
# 30 天结果覆盖 243 天官方日历——这条哨兵就是那个事故的产物。


#: 情绪指标库允许的滞后交易日数。库尾是"上一个交易日"属正常（`backfill` 刻意
#: 不回补今天），所以 1 是稳态；给到 3 是给周末/长假与调度周期留余量。
_METRIC_HISTORY_MAX_LAG = 3


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

    # 3) marketdb 日 K 仓新鲜度（P0-7 收紧：**内容日期**口径，不是文件 mtime）
    #    原判据是 `mtime > 26h`——只证明「文件被写过」，不证明「数据到了新交易日」：
    #    一次失败的 `--full` 重跑会刷新 mtime 而数据仍停在旧日期（假 OK）。
    #    现改为库内 MAX(date_ms) × **交易日**滞后（口径与下游 rps/chip 闸门同源，
    #    避免"哨兵说没问题、下游却在用陈旧截面"的口径分裂）。
    try:
        mdb = PROJECT_ROOT / "backend" / "data" / "marketdb" / "market.duckdb"
        if not mdb.exists():
            _add("marketdb", False, "market.duckdb 不存在")
        else:
            from app.market.marketdb_freshness import freshness as _mdb_freshness

            fr = _mdb_freshness(mdb)
            age_h = (datetime.now().timestamp() - mdb.stat().st_mtime) / 3600
            ok = fr["available"] and not fr["stale"]
            detail = (f"{mdb.stat().st_size // (1024 * 1024)} MB，{age_h:.0f} 小时前更新；"
                      f"库内最新 {fr['latest'] or '—'}，滞后 {fr['lag']} 个交易日"
                      f"（阈值 {fr['threshold']}）")
            if not ok:
                detail += "（⚠️ 同步停跑——scripts/sync_marketdb.py；下游 RPS/筹码已降级）"
            _add("marketdb", ok, detail)
    except Exception as exc:  # noqa: BLE001
        _add("marketdb", False, f"检查失败：{exc}")

    # 4) 告警与审计心跳（24h 全静默 = 管道可能挂了）
    try:
        from app.models.alert import AlertEvent

        cutoff = beijing_now_naive() - timedelta(hours=24)
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
        stale_cutoff = beijing_now_naive() - timedelta(hours=ttl)
        with session_factory() as db:
            rows = db.execute(select(Theme.code, Theme.synced_at)).all()
        n_stale = len([c for c, ts in rows if ts is None or ts < stale_cutoff])
        _add("theme_members_fresh", n_stale == 0,
             f"官方成分超 {ttl}h 未同步的概念 {n_stale}/{len(rows)} 个"
             + ("（⚠️ 成分调整期归属会错——collect_news_events 每轮补 40 个）" if n_stale else ""))
    except Exception as exc:  # noqa: BLE001
        _add("theme_members_fresh", False, f"检查失败：{exc}")

    # 6) 磁盘可用空间（<10G 会导致 parquet 写失败/DB 损坏风险）
    try:
        import shutil

        usage = shutil.disk_usage(PROJECT_ROOT)
        free_g = usage.free / (1024 ** 3)
        _add("disk_free", free_g >= 10,
             f"工作卷可用 {free_g:.1f} GB"
             + ("（⚠️ <10G——parquet/duckdb 写入有损坏风险）" if free_g < 10 else ""))
    except Exception as exc:  # noqa: BLE001
        _add("disk_free", False, f"检查失败：{exc}")

    # 7) 快照目录堆积（>120 个 = parquet 保留窗口未执行）
    try:
        snaps = PROJECT_ROOT / "data" / "parquet" / "snapshots"
        n_dirs = len([p for p in snaps.iterdir() if p.is_dir()]) if snaps.exists() else 0
        _add("snapshot_dirs", n_dirs <= 120,
             f"{n_dirs} 个交易日目录（≈{n_dirs * 30} MB，保留窗口 90 天→120 个）"
             + ("（⚠️ 运行 prune_old_snapshots 归档）" if n_dirs > 120 else ""))
    except Exception as exc:  # noqa: BLE001
        _add("snapshot_dirs", False, f"检查失败：{exc}")

    # 8) 情绪指标库窗口新鲜度（2026-09-10 实测事故纳入哨兵）：
    #    回补调度把 provider 原始日历（字符串）喂给 `backfill` → TypeError 被
    #    `except Exception` 收成一条日志 ⇒ **库停在 2026-09-01、连续 6 个交易日
    #    没更新**，而界面照旧写着「按近 241 个交易日的历史分位校准」——窗口漂移
    #    了 6 天却没有任何告警。这正是"陈旧比缺失更危险"那一类（静默、看着正常）。
    #    判据用**交易日滞后数**而非自然日差：周末/长假用自然日会虚报。
    try:
        raw = json.loads(
            (PROJECT_ROOT / "backend" / "data" / "sentiment_metrics.json").read_text(encoding="utf-8")
        )
        keys = sorted((raw.get("days") or {}).keys())
        tail = keys[-1] if keys else ""
        cal_raw = json.loads(
            (PROJECT_ROOT / "backend" / "data" / "trade_calendar.json").read_text(encoding="utf-8")
        )
        cal_days = cal_raw.get("days") if isinstance(cal_raw, dict) else cal_raw
        today_iso = beijing_now().date().isoformat()
        # 上限截到"今天"：官方日历**含未来日期**，不截会把未来交易日也算成滞后
        lag = sum(1 for d in (cal_days or []) if tail < d <= today_iso) if tail else None
        ok = bool(tail) and lag is not None and lag <= _METRIC_HISTORY_MAX_LAG
        _add("sentiment_metrics", ok,
             f"窗口尾 {tail or '—'}，滞后 {lag if lag is not None else '?'} 个交易日，共 {len(keys)} 天"
             + ("（⚠️ 回补调度停跑——分位校准窗口在漂移）" if not ok else ""))
    except Exception as exc:  # noqa: BLE001
        _add("sentiment_metrics", False, f"检查失败：{exc}")

    # 9) 持仓监护读取状态（S1-2，2026-09-11）：`exit_engine` 的两路持仓读取
    #    过去都是 `except: return {}` / `positions = []`——失败与「确实无持仓」返回值
    #    完全相同、连日志都没有 ⇒ 一次 DB 抖动就让**自动离场/硬止损/真实持仓提醒整轮跳过**
    #    而在任何界面上都表现为「今天没有信号」。此处把该状态接进哨兵：
    #    state=failed 即报问题（issue 文本**不含计数器**，保证 15 分钟一轮的去重稳定，
    #    不会因为失败次数变化而反复推送）。
    try:
        from app.picks.exit_engine import position_monitor_state

        pm = position_monitor_state()
        bad = [k for k, v in pm.items() if v.get("state") == "failed"]
        # 只用**异常类名**（如 OperationalError）拼文案：类名对同一故障模式稳定，
        # 而完整错误消息可能每次都不同（含行号/耗时），会把哨兵的字符串去重打穿、
        # 变成每 15 分钟推一次。完整错误已在 exit_engine 的日志里。
        detail = "、".join(
            f"{'模拟' if k == 'paper' else '真实'}持仓（{(pm[k].get('reason') or '未知').split(':')[0]}）"
            for k in bad
        )
        _add("position_monitor_read", not bad,
             "两路持仓读取正常" if not bad
             else f"读取失败：{detail}——本轮自动离场/硬止损/真实持仓提醒已跳过"
             + ("（⚠️ 监护降级，期间持仓不受止损保护）"))
    except Exception as exc:  # noqa: BLE001
        _add("position_monitor_read", False, f"检查失败：{exc}")

    issues = [c for c in checks if not c["ok"]]
    return {"available": True, "checks": checks, "n_issues": len(issues),
            "issues": [f"{c['name']}：{c['detail']}" for c in issues]}


#: 数据健康确定性议程项的来源标记（execute_agenda 据此免占自治任务预算）。
DATA_HEALTH_ORIGIN = "data_health"


def _data_health_items(data_health: dict) -> list[dict]:
    """数据健康未通过项 → **确定性**议程条目（B 类：只写进化日报，不改代码/参数）。

    为什么必须由代码固化（2026-09-09/09-10 实测）：marketdb 停跑每天都进了
    `inputs.data_health.issues`，但两天 LLM 各只产出 1 条**别的**条目、都没提它
    ⇒「检查到 ≠ 有人知道」。故此处不依赖 LLM 是否注意到。

    为什么是 B 类而非 C 类：处置动作是「跑同步脚本 / 查上游连通性」，不是改代码；
    让自治执行器改代码去修数据管道既无效又危险（红线：不碰风控/资金/推送/凭据）。
    B 类只往 docs/evolution/ 追加一行，零副作用。
    """
    if not data_health.get("available") or not data_health.get("n_issues"):
        return []
    issues = list(data_health.get("issues") or [])
    failed = [c for c in (data_health.get("checks") or []) if not c["ok"]]
    return [{
        "class": "B",
        "origin": DATA_HEALTH_ORIGIN,
        "finding": f"数据健康哨兵报警：{len(issues)} 项未通过（{', '.join(c['name'] for c in failed)}）",
        "evidence": {"issues": issues, "failed_checks": failed},
        "action": "核对上游数据管道，按 check 名对应脚本补跑"
                  "（marketdb → scripts/sync_marketdb.py）",
        "expected_effect": "数据管道恢复新鲜；下游 RPS / 筹码 / 分位不再用陈旧截面算分",
        "verification": "下一轮数据健康哨兵同项转 ok（盘中每 15 分钟一轮）",
        "priority": 1,
        "summary": "；".join(issues),
        "status": "pending",
        "result": "",
    }]


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
    "- 不提供买卖建议，不改风控/资金/推送相关任何东西\n"
    "- 裁决复盘改进项时遵循 docs/kb/06-review-framework.md（复盘执行框架 v1.0）："
    "结论须挂数据、规律须可证伪（量化触发条件+适用边界+验证状态）、"
    "与既有 KB 条目冲突须显式标注而非静默覆盖；证据不足宁可 deferred 也不编造"
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
            row.finished_at = beijing_now_naive()
            db.commit()
            return _agenda_dump(row)

    # ⚠️ 必须 to_thread：collect_inputs 是同步函数，内部九路证据全是**阻塞 IO**——
    # 其中 _collect_data_health 会 `duckdb.connect(market.duckdb)` 跑 `MAX(date_ms)`
    # （1027 万行库），另有多次 SQLite 全表读 + 文件读 + shutil.disk_usage。
    # 本函数是 async（被 run_evolution_now / API 手动触发），直接调用会阻塞事件循环。
    inputs = await asyncio.to_thread(collect_inputs, sf)
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
        # 确定性追加：数据健康 NG 不依赖 LLM 是否注意到（见 _data_health_items）。
        # 放在 _parse_items 之后 ⇒ 不占 LLM 的 3 项上限；至多 1 条。
        items = _parse_items(raw) + _data_health_items(inputs.get("data_health") or {})
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
            row.finished_at = beijing_now_naive()
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
    """频率闸：同参数 24h 内是否已有自动变更（applied / rolled_back / shadow）。

    shadow 必须计入——影子队列在议程前评估转正，若不计入，同参数会每天
    入队一条影子（堆积且评估重复）。
    """
    cutoff = beijing_now_naive() - timedelta(hours=24)
    with sf() as db:
        rows = db.execute(
            select(AgentParamChange).where(
                AgentParamChange.key == key,
                AgentParamChange.status.in_(("applied", "rolled_back", "shadow")),
                AgentParamChange.created_at >= cutoff,
            )
        ).scalars().all()
        return bool(rows)


def _execute_a(item: dict, sf, agenda_date: str) -> dict:
    """A 类：参数变更**进影子队列**（2026-09-08 用户指令：新策略需经数据验证
    有效后方可启用——不再直接生效）。

    影子流：propose → shadow（不写运行时覆盖层）→ 议程调度器每日评估
    （experiments.evaluate_and_promote_shadow：权重剧变检测）→ 达标转正
    （真正生效 + 挂 30 日胜率劣化回滚实验）→ 剧变/灭声拒绝归档。
    转正后仍受 experiments 30 日劣化自动回滚守护（后置安全网不变）。
    """
    param = item.get("param") or {}
    key = param.get("key") or ""
    if key in REDLINE_KEYS:
        return {**item, "status": "rejected", "result": f"参数 {key} 在红线清单，禁止自动修改"}
    mutation_id = None
    try:
        from app.services import agent_params
        from app.services.agent_tasks import record_mutation, update_mutation_result

        mutation_id = record_mutation(
            source="agenda", kind="param_shadow",
            summary=f"A类参数进影子队列 {key}：{json.dumps(param.get('after'), ensure_ascii=False)}",
            detail={"agenda_date": agenda_date, "evidence": item.get("evidence") or {}},
        )

        change = agent_params.propose(
            key, param.get("after"),
            source_type="ai_suggestion", source_id=f"agenda:{agenda_date}",
            evidence=item.get("evidence") or {}, session_factory=sf,
        )
        shadowed = agent_params.shadow_change(change["id"], sf)
        result = f"变更单 #{shadowed['id']} 已入影子队列（待数据评估，达标自动转正）"
        update_mutation_result(mutation_id, "succeeded", f"{result}；变更单 #{shadowed['id']}")
        return {**item, "status": "executed", "result": result,
                "mutation_task_id": mutation_id, "shadow_change_id": shadowed["id"]}
    except ValueError as exc:
        if mutation_id:
            from app.services.agent_tasks import update_mutation_result
            update_mutation_result(mutation_id, "failed", f"校验拒绝：{exc}")
        return {**item, "status": "rejected", "result": f"校验拒绝：{exc}"}
    except Exception as exc:  # noqa: BLE001
        if mutation_id:
            from app.services.agent_tasks import update_mutation_result
            update_mutation_result(mutation_id, "failed", f"{type(exc).__name__}: {exc}")
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
        if item.get("origin") == DATA_HEALTH_ORIGIN:
            # 系统异常留痕**不占自治任务预算**：它是"记账"不是"自治行动"。
            # 若被预算挤掉（LLM 条目用满 3 项时必然发生），异常就又变回"没人知道"
            # ——正是本项存在的理由。执行体复用 B 类（docs/evolution/ 白名单，零副作用）。
            items.append(_execute_b(item, agenda.get("date") or ""))
            continue
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

    # 兜底：无对应议程行（或库不可用）时返回**本次执行结果**本身，
    # 否则 `row is None` 会让 out 未绑定（UnboundLocalError）而丢掉执行回执。
    out: dict = {**agenda, "items": items}
    with sf() as db:
        row = db.execute(select(AgentAgenda).where(AgentAgenda.date == agenda["date"])).scalars().first()
        if row is not None:
            row.items = json.dumps(items, ensure_ascii=False, default=str)
            row.budget = json.dumps(budget, ensure_ascii=False)
            row.status = "executed"
            row.finished_at = beijing_now_naive()
            db.commit()
            db.refresh(row)
            out = _agenda_dump(row)
    record_summary_audit(agenda.get("date") or "", items)
    _sync_review_items(agenda, items, sf)
    # 议程摘要不再由后端推飞书——2026-09-08/09 用户指令「每天仅一次进化总结报告」，
    # 由 15:45 automation 统一承担（what/why/how 三段式含台账统计）。
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
    global _LAST_CONCLUDE_DATE, _LAST_SHADOW_DATE, _LAST_META_WEEK
    # WARNING 而非 INFO：app logger 级别为 WARNING，INFO 不落盘（KB-ENG-18）——
    # 调度器是否存活只能靠这条日志 + scheduler_status() liveness 面取证
    log.warning("evolution scheduler started: daily at %02d:%02d", run_hour, run_minute)
    while not stop.is_set():
        try:
            now = beijing_now()
            today = now.date()
            _SCHED_LAST_TICK["at"] = now.isoformat(timespec="seconds")
            _SCHED_LAST_TICK["date"] = today.isoformat()
            # 每日一次：到期实验裁决（劣化自动回滚）
            if _LAST_CONCLUDE_DATE != today.isoformat() and now.hour >= 16:
                with contextlib.suppress(Exception):
                    from app.services.experiments import conclude_due

                    # ⚠️ 同样必须 to_thread：同步函数，内部是 SQLite **全表读**
                    # （DailyPickReview/DailyPickSet 全量 + 每条到期实验各读一遍）。
                    # 无 DuckDB，严重度低于下面的影子评估，但：① 该读随逐日累积增长；
                    # ② 紧跟 15:30 复盘写窗口，SQLite 写锁未释放时会等锁 ⇒ 阻塞事件循环。
                    # 与影子评估同属「同步函数被 async 调度器直接调用」一类（2026-09-11）。
                    results = await asyncio.to_thread(conclude_due)
                    _LAST_CONCLUDE_DATE = today.isoformat()
                    if results:
                        log.warning("[EVOLUTION] 实验裁决 %d 条：%s", len(results),
                                    json.dumps([{r["id"]: r["status"]} for r in results],
                                               ensure_ascii=False))
            # 每日一次：影子队列评估（P1-4：剧变检测 → 达标转正 / 剧变拒绝）——
            # 在议程生成前跑，转正结果进当日议程证据（独立节流标志，不与实验裁决互斥）
            if _LAST_SHADOW_DATE != today.isoformat() and now.hour >= 15:
                with contextlib.suppress(Exception):
                    from app.services.experiments import evaluate_and_promote_shadow

                    # ⚠️ 必须 to_thread：该函数是同步的，且内部会走 **DuckDB 全表查询**
                    # （shadow_eval 的落选者补验，1027 万行 + LEAD 窗口）。
                    # 直接在 async 调度器里调用会**阻塞事件循环**——影响 QuoteHub 的 1s
                    # 行情节奏。2026-09-11 接上补验后才发现（P2-1~15「DuckDB 同步调用
                    # 未 to_thread」的另一处）。
                    shadow_results = await asyncio.to_thread(evaluate_and_promote_shadow)
                    _LAST_SHADOW_DATE = today.isoformat()
                    if shadow_results:
                        log.warning("[EVOLUTION] 影子队列评估 %d 条：%s", len(shadow_results),
                                    json.dumps(shadow_results, ensure_ascii=False)[:400])
            if (now.hour, now.minute) >= (run_hour, run_minute):
                days = await tc.trading_days(app.state.hub.provider)
                if tc.last_trade_date(days, asof=today) == today:
                    existing = get_agenda(today.isoformat())
                    if existing is None or existing["status"] == "failed":
                        log.warning("[EVOLUTION] %s 15:45 窗口触发：开始生成议程", today)
                        agenda = await run_evolution_now()
                        log.warning("[EVOLUTION] %s 议程完成：status=%s items=%d",
                                    today, agenda.get("status"), len(agenda.get("items") or []))
                    else:
                        _log_skip_once(today.isoformat(), "agenda-exists",
                                       f"今日议程已存在（status={existing['status']}）")
                else:
                    _log_skip_once(today.isoformat(), "not-trade-day",
                                   f"非交易日（last_trade_date={tc.last_trade_date(days, asof=today)}）")
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
            await asyncio.wait_for(stop.wait(),
                                   timeout=max(_MIN_TICK_INTERVAL_SEC, check_interval_seconds))


_LAST_CONCLUDE_DATE: str = ""
_LAST_SHADOW_DATE: str = ""  # 2026-09-09 P0：被 global 声明/读写却从未定义 → 每 tick NameError，
# 议程生成代码（在它之后）永远走不到 → 15:45 议程静默不触发的根因
_LAST_META_WEEK: tuple = ()  # (ISO 年, 周)——元评估周报进程内幂等（文件存在性兜底）

# 调度器 liveness 面（进程内）：每个 tick 刷新，经 scheduler_status() 暴露给
# /api/agent/agenda meta——"调度停摆比缺日志更危险"（同指标库停更哨兵哲学）。
_SCHED_LAST_TICK: dict = {"at": "", "date": ""}
# 跳过原因按 (日期, 桶) 去重：60s 一 tick，不节流会每分钟刷屏
_SKIP_LOGGED: set = set()


def _log_skip_once(day: str, bucket: str, detail: str) -> None:
    key = f"{day}:{bucket}"
    if key in _SKIP_LOGGED:
        return
    _SKIP_LOGGED.add(key)
    log.warning("[EVOLUTION] %s 议程跳过：%s", day, detail)


def scheduler_status() -> dict:
    """调度器存活状态（供 /api/agent/agenda meta 与哨兵消费）。

    last_tick_age_sec 持续 > 2×tick 周期（120s）即视为调度停摆。
    """
    now = beijing_now()
    last = _SCHED_LAST_TICK.get("at") or ""
    age: int | None = None
    if last:
        try:
            age = round((now - datetime.fromisoformat(last)).total_seconds())
        except ValueError:  # pragma: no cover - 格式异常按未知处理
            age = None
    return {"last_tick_at": last, "last_tick_age_sec": age, "expected_interval_sec": 60}


#: tick 最小等待（秒）。生产恒为 60s；测试注入小值以驱动时钟跨越窗口。
_MIN_TICK_INTERVAL_SEC = 60.0


def prune_old_snapshots(days: int = 90, *, dry_run: bool = True) -> dict:
    """parquet 快照保留窗口（2026-09-09 系统审查 #9）：删除 >N 天的快照目录。

    **默认 dry_run**（只列出待删，不真删）——删除动作必须显式 dry_run=False 触发
    （automation 或手动）。目录名格式 YYYYMMDD，按日期字符串比较，删前二次确认
    目标都晚于 20200101（防格式异常整目录误删）。
    """
    import shutil as _shutil

    snaps = PROJECT_ROOT / "data" / "parquet" / "snapshots"
    if not snaps.exists():
        return {"removed": [], "kept": 0, "dry_run": dry_run}
    cutoff = (beijing_now().date() - timedelta(days=days)).strftime("%Y%m%d")
    victims: list[Path] = []
    for p in sorted(snaps.iterdir()):
        if not p.is_dir() or not p.name.isdigit() or len(p.name) != 8:
            continue
        if p.name < cutoff and p.name >= "20200101":
            victims.append(p)
    if not dry_run:
        for p in victims:
            _shutil.rmtree(p, ignore_errors=True)
    return {
        "removed": [p.name for p in victims] if not dry_run else [],
        "would_remove": [p.name for p in victims] if dry_run else [],
        "kept": len([p for p in snaps.iterdir() if p.is_dir()]) - (len(victims) if not dry_run else 0),
        "cutoff": cutoff, "dry_run": dry_run,
    }
