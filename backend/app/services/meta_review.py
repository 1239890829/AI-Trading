"""元评估周报（docs/evolution-brain-plan.md P2-①）：AI 自审自己的改动。

进化闭环的"验证"环节：每日议程在改系统，但**谁来看 AI 改得好不好**？
元评估每周五盘后自动汇总本周全部自主改动（议程执行分布 / 实验结论 /
变更单回滚情况 / 审计统计），交给 LLM 做一次自我剖析：

- 本周自主改动的**错误模式**（哪类决策反复被门禁/实验拦截）
- **过度自信 / 过度保守**倾向（A 类提了不敢提？B 类灌水？）
- 下周聚焦建议（进下轮议程的证据输入——知识回流闭环）

产物：docs/evolution/meta-<ISO周>.md + 审计留痕。幂等：一周至多一份。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_session_factory
from app.models.agent import AgentAgenda, AgentAudit, AgentExperiment, AgentParamChange
from app.services.evolution import PROJECT_ROOT
from app.core.bjtime import beijing_now, BJ_OFFSET  # S2-8 时区收敛

log = logging.getLogger(__name__)

_EVOLUTION_DIR = PROJECT_ROOT / "docs" / "evolution"

_META_SYSTEM = (
    "你是交易系统进化大脑的**元评估器**：审视 AI 自己本周的自主改动，做一次诚实的自我剖析。\n"
    "给定本周数据（议程执行分布/实验结论/变更单回滚/审计统计），只输出 JSON：\n"
    "{\"patterns\": [\"错误模式（一句话，有数据支撑才写）\"], "
    "\"bias\": \"过度自信/过度保守/均衡（一句话论证）\", "
    "\"focus\": [\"下周聚焦建议（可执行，≤2 条）\"], "
    "\"score\": {\"decision_quality\": 1-5, \"safety_discipline\": 1-5}}\n"
    "纪律：证据不足的判断不要写；分要打得诚实——全绿不代表 5 分（可能是没干活）。"
)


def _week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _collect_week(sf) -> dict:
    """本周（周一 0 点起）自主改动全量数据。"""
    with sf() as db:
        agendas = db.execute(
            select(AgentAgenda).where(AgentAgenda.created_at >= _week_start_utc())
        ).scalars().all()
        experiments = db.execute(
            select(AgentExperiment).where(AgentExperiment.created_at >= _week_start_utc())
        ).scalars().all()
        changes = db.execute(
            select(AgentParamChange).where(AgentParamChange.created_at >= _week_start_utc())
        ).scalars().all()
        audits = db.execute(
            select(AgentAudit).where(AgentAudit.at >= _week_start_utc())
        ).scalars().all()

    item_stats: dict[str, int] = {}
    for a in agendas:
        for it in json.loads(a.items) if a.items else []:
            if isinstance(it, dict):
                key = f"{it.get('class')}:{it.get('status')}"
                item_stats[key] = item_stats.get(key, 0) + 1

    audit_stats: dict[str, int] = {}
    for r in audits:
        audit_stats[r.action] = audit_stats.get(r.action, 0) + 1

    return {
        "agenda_count": len(agendas),
        "agenda_item_stats": item_stats,
        "experiments": [
            {"id": e.id, "status": e.status, "key": e.param_key,
             "conclusion": _conclusion_of(e.result)}
            for e in experiments
        ],
        "param_changes": [
            {"id": c.id, "key": c.key, "status": c.status, "source": c.source_type}
            for c in changes
        ],
        "audit_stats": audit_stats,
        "n_audits": len(audits),
    }


def _conclusion_of(result_raw: str | None) -> str:
    """实验结论提取（result 是 JSON 字符串；解析失败不阻断汇总）。"""
    if not result_raw:
        return ""
    try:
        data = json.loads(result_raw)
        return str(data.get("conclusion", ""))[:80] if isinstance(data, dict) else ""
    except Exception:  # noqa: BLE001
        return ""


def _week_start_utc() -> datetime:
    """本周周一 0 点（北京）对应的 naive UTC。"""
    bj = beijing_now()
    monday = bj - timedelta(days=bj.weekday())
    return datetime(monday.year, monday.month, monday.day) - BJ_OFFSET


def meta_review_path(now: datetime | None = None) -> Path:
    return _EVOLUTION_DIR / f"meta-{_week_key(now or beijing_now())}.md"


def meta_review_done(now: datetime | None = None) -> bool:
    return meta_review_path(now).exists()


def generate_meta_review(session_factory=None) -> dict:
    """生成（或复用）本周元评估。幂等：meta-<week>.md 已存在则跳过。"""
    sf = session_factory or get_session_factory()
    now = beijing_now()
    if meta_review_done(now):
        return {"status": "skipped", "note": f"本周元评估已存在：{meta_review_path(now).name}"}

    data = _collect_week(sf)
    messages = [
        {"role": "system", "content": _META_SYSTEM},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)},
    ]
    try:
        from app.core.llm_client import chat_completion

        raw = chat_completion(
            base_url=settings.review_llm_base_url,
            api_key=settings.review_llm_api_key,
            model=settings.review_llm_model,
            messages=messages,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=120.0,
        )
    except Exception as exc:  # noqa: BLE001  LLM 不可用：只落数据不做 AI 判断（不伪装）
        return _write_fallback(data, str(exc)[:200])

    from app.core.llm_client import extract_json_object

    try:
        verdict = extract_json_object(raw) or {}
    except Exception:  # noqa: BLE001  非 JSON 输出 → 降级，不伪装
        verdict = {}
    if not isinstance(verdict.get("patterns"), list):
        return _write_fallback(data, "LLM 输出无法解析为元评估 JSON")

    out = _write_markdown(data, verdict, now)
    with sf() as db:
        db.add(AgentAudit(actor="ai", action="meta.review", target=meta_review_path(now).name))
        db.commit()
    return out


def _write_fallback(data: dict, reason: str) -> dict:
    """LLM 不可用：只落本周数据（无 AI 判断），显式标注降级。"""
    now = beijing_now()
    verdict = {"patterns": [], "bias": f"未评估（LLM 不可用：{reason}）",
               "focus": [], "score": {}}
    out = _write_markdown(data, verdict, now, degraded=True)
    return out


def _write_markdown(data: dict, verdict: dict, now: datetime, *, degraded: bool = False) -> dict:
    path = meta_review_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 元评估周报 · {_week_key(now)}（{now:%m-%d}）\n",
        f"> {'⚠️ 降级：LLM 不可用，本周仅存数据不做 AI 判断' if degraded else 'AI 自审：本周自主改动质量剖析'}\n",
        "## 本周数据\n",
        f"- 议程 {data['agenda_count']} 份；执行分布：{json.dumps(data['agenda_item_stats'], ensure_ascii=False)}\n",
        f"- 实验：{len(data['experiments'])} 个（{json.dumps(data['experiments'], ensure_ascii=False)}）\n",
        f"- 变更单：{len(data['param_changes'])} 张（{json.dumps(data['param_changes'], ensure_ascii=False)}）\n",
        f"- 审计：{data['n_audits']} 条（{json.dumps(data['audit_stats'], ensure_ascii=False)}）\n",
        "\n## AI 自审\n",
        f"- **错误模式**：{'；'.join(verdict.get('patterns') or []) or '（未识别到）'}\n",
        f"- **倾向判断**：{verdict.get('bias', '—')}\n",
        f"- **下周聚焦**：{'；'.join(verdict.get('focus') or []) or '（无）'}\n",
        f"- **自评分**：{json.dumps(verdict.get('score') or {}, ensure_ascii=False)}\n",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "executed", "path": str(path), "degraded": degraded, "verdict": verdict}
