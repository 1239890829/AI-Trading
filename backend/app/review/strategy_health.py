"""复盘报告「策略健康」维度：信号健康度 + 相位对账（strategy-evolution-plan P1）。

数据源都是现成落库资产（daily_pick_review / sentiment_history），本模块只做
聚合与措辞，不新增采集。DimensionResult.status 语义：
- ok：正常出数；
- degraded：信号健康度采集失败（结论不可信，绝不冒充 ok）。
"""

from __future__ import annotations

import logging

from app.picks.signal_health import collect_signal_health
from app.review.schemas import ActionItem, DimensionResult
from app.sentiment.reconcile import load_prev_entry, reconcile

log = logging.getLogger(__name__)


def _fmt(v, suffix: str = "") -> str:
    """数值 → 展示串；None 显式「--」（缺失不冒充 0）。"""
    return "--" + suffix if v is None else f"{v}{suffix}"


def _collect_other_strategy_health(session_factory) -> dict:
    """跨策略键健康度摘要（**排除 daily_picks**，它已有组合级专门段落）。

    失败只记日志并返回空摘要——策略级是增量信息，不该让整个维度降级。
    """
    try:
        from app.picks.strategy_registry import collect_all_strategy_health

        out = collect_all_strategy_health(session_factory)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_registry: 复盘维度取数失败：%s", exc)
        return {"total": 0, "attention": [], "not_judgeable": 0, "error": str(exc)}

    items = [s for s in out["strategies"] if s.get("strategy_key") != "daily_picks"]
    attention = [s for s in items if s.get("status") in ("warning", "drift")]
    not_judgeable = sum(
        1 for s in items
        if s.get("status") in ("insufficient", "thin", "no_pipeline")
    )
    return {
        "total": len(items),
        "attention": attention,
        "not_judgeable": not_judgeable,
        "statuses": {s.get("strategy_key"): s.get("status") for s in items},
    }


def build_strategy_health_dimension(
    session_factory, sentiment: dict | None
) -> tuple[DimensionResult, dict]:
    """组装维度。返回 (DimensionResult, signal_health 原始 dict)——
    health 单独返回供 action_items 合成与告警接线复用，避免重复查库。"""
    sentiment = sentiment or {}
    findings: list[str] = []
    judgements: list[str] = []
    evidence: dict = {}
    status = "ok"

    # --- 信号健康度（滚动胜率 / 期望超额 / CUSUM 下漂）---
    health = collect_signal_health(session_factory)
    evidence["signal_health"] = {k: v for k, v in health.items() if k != "history"}
    h_status = health.get("status")
    if h_status == "error":
        status = "degraded"
        findings.append(f"信号健康度采集失败：{health.get('reason')}")
    elif h_status == "insufficient":
        w = health.get("window") or {}
        findings.append(
            f"信号健康度：样本不足（{health.get('counts', {}).get('groups', 0)} 组合日 < 10），"
            f"暂不判定——已有记录胜率 {_fmt(w.get('win_rate'))}、"
            f"日均超额 {_fmt(w.get('mean_excess'), '%')}，继续积累样本"
        )
    else:
        w = health.get("window") or {}
        findings.append(
            f"信号健康度[{h_status}]：近 {w.get('groups')} 组合日 {w.get('total_picks')} 只——"
            f"胜率 {_fmt(w.get('win_rate'))}、日均超额 {_fmt(w.get('mean_excess'), '%')}"
            f"（good {w.get('good')}/bad {w.get('bad')}/flat {w.get('flat')}）"
        )
        cusum = health.get("cusum") or {}
        if cusum:
            findings.append(
                f"CUSUM 下漂检测：s_max={cusum.get('s_max')}"
                f"（阈值 {cusum.get('threshold')}，基线 {_fmt(cusum.get('mu0'), '%')}）→ "
                f"{'触发漂移' if cusum.get('drift') else '未触发'}"
            )
        if h_status == "drift":
            judgements.append(
                "策略持续跑输历史基线（CUSUM 漂移）：优先复核近 20 组合日的入选逻辑与相位适配，"
                "评估降低出手档位/仓位上限——已生成改进项并推送告警"
            )
        elif h_status == "warning":
            judgements.append(
                "滚动胜率或超额越警戒线：关注下一批组合的确认/证伪记录，勿加仓执行"
            )

    # --- 跨策略键健康度（P1-37/P1-38）---
    # ⚠️ 去重纪律：`daily_picks` 已由上面的组合级段落覆盖，此处**排除**它，
    #    否则同一策略会被报两遍（口径重复计数）。
    strategy_health = _collect_other_strategy_health(session_factory)
    if strategy_health["total"] > 0:
        evidence["strategy_health"] = strategy_health
        attention = strategy_health["attention"]
        judged_na = strategy_health["not_judgeable"]
        if attention:
            for s in attention:
                basis_hint = (
                    "（口径=绝对收益，只反映该策略自身变化，非 alpha 衰减）"
                    if s.get("basis") == "absolute" else "（口径=市场中性超额）"
                )
                w = s.get("window") or {}
                findings.append(
                    f"策略[{s.get('name')}] 健康度[{s.get('status')}]："
                    f"{w.get('groups', 0)} 组日 {w.get('total_picks', 0)} 笔——"
                    f"胜率 {_fmt(w.get('win_rate'))}、"
                    f"日均{'收益' if s.get('basis') == 'absolute' else '超额'} "
                    f"{_fmt(w.get('mean_excess'), '%')}{basis_hint}"
                )
        if judged_na:
            findings.append(
                f"另有 {judged_na} 个未接入策略键无法判定（样本不足 / 触发次数不足 / 无逐日落库）"
                "——「判不出」不等于「失效」，勿据此停用"
            )
        if attention:
            judgements.append(
                "存在策略键健康度越线：核对是「策略失效」还是「相位/环境错配」"
                "（同一策略在不同相位表现可差一个档），确认失效则走处置台账（归档/改造）"
            )

    # --- 相位对账（昨日 switch_conditions vs 今日实际相位）---
    today_key = str(sentiment.get("trade_date") or "").replace("-", "") or None
    prev_entry = load_prev_entry(session_factory, today_key)
    rec = reconcile(prev_entry, sentiment)
    evidence["phase_reconcile"] = rec
    if rec["verdict"] == "unavailable":
        findings.append(f"相位对账：{rec['reason']}")
    else:
        sw = (rec.get("prev") or {}).get("switch_conditions")
        findings.append(
            f"相位对账[{rec['verdict_label']}]：{rec['reason']}"
            + (f"；昨日切换条件存档：「{sw}」" if sw else "")
        )
        if rec["verdict"] == "off_path":
            judgements.append(
                "相位实际落点偏离昨日提示路径：switch_conditions 文本阈值与 heat×earning "
                "矩阵实现存在偏差，或出现矩阵外极端行情——人工核对当日指标"
            )
    if sentiment.get("phase_unreliable"):
        findings.append("今日情绪相位标记为不可靠（数据缺口），对账结论置信度受限")

    return (
        DimensionResult(
            key="strategy_health",
            title="策略健康",
            status=status,
            findings=findings,
            judgements=judgements,
            evidence=evidence,
        ),
        health,
    )


def build_signal_health_action_item(health: dict) -> ActionItem | None:
    """warning/drift → 自动改进项（走既有指纹机制：category+title 继承处置状态）。

    ok/insufficient/error → None（insufficient 是样本不足不是缺陷）。
    """
    status = health.get("status")
    if status not in ("warning", "drift"):
        return None
    w = health.get("window") or {}
    cusum = health.get("cusum") or {}
    if status == "drift" and cusum:
        ev = (
            f"近 {w.get('groups')} 组合日 {w.get('total_picks')} 只：胜率 {_fmt(w.get('win_rate'))}、"
            f"日均超额 {_fmt(w.get('mean_excess'), '%')}；"
            f"CUSUM s_max={cusum.get('s_max')} > 阈值 {cusum.get('threshold')}"
            f"（基线 {_fmt(cusum.get('mu0'), '%')}）"
        )
    else:
        ev = (
            f"近 {w.get('groups')} 组合日 {w.get('total_picks')} 只：胜率 {_fmt(w.get('win_rate'))}、"
            f"日均超额 {_fmt(w.get('mean_excess'), '%')}——越过警戒线（胜率 <40% 或日均超额 <-1%）"
        )
    return ActionItem(
        title=f"策略信号健康度[{status}]：滚动胜率 {_fmt(w.get('win_rate'))}，需复核入选逻辑",
        category="strategy",
        priority="P0" if status == "drift" else "P1",
        expected_impact=(
            "策略失效期内按原档位继续执行会放大回撤；复核后要么修正逻辑要么降档，恢复信号可信度"
        ),
        evidence=ev,
        target="picks/signal_health + picks/engine",
        proposed_change=(
            "人工复核：近 20 组合日逐笔看归因（logic_failed 集中度）、相位适配与六维权重；"
            "确认失效则降低执行档位/仓位上限"
        ),
    )


def build_strategy_key_action_items(session_factory) -> list[ActionItem]:
    """跨策略键 warning/drift → 改进项（**排除 daily_picks**，它走上面那条）。

    `insufficient`/`thin`/`no_pipeline` → 不产改进项：
    那是「还没法判」，不是「策略有缺陷」——产了会变成噪音待办。
    """
    from app.picks.strategy_registry import collect_all_strategy_health

    try:
        out = collect_all_strategy_health(session_factory)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_registry: 改进项取数失败：%s", exc)
        return []

    items: list[ActionItem] = []
    for s in out["strategies"]:
        if s.get("strategy_key") == "daily_picks":
            continue
        status = s.get("status")
        if status not in ("warning", "drift"):
            continue
        w = s.get("window") or {}
        cusum = s.get("cusum") or {}
        basis_label = "绝对收益" if s.get("basis") == "absolute" else "市场中性超额"
        ev = (
            f"[{s.get('name')}] 近 {w.get('groups')} 组日 {w.get('total_picks')} 笔："
            f"胜率 {_fmt(w.get('win_rate'))}、日均{basis_label} {_fmt(w.get('mean_excess'), '%')}"
        )
        if status == "drift" and cusum:
            ev += f"；CUSUM s_max={cusum.get('s_max')} > 阈值 {cusum.get('threshold')}"
        else:
            ev += "——越过警戒线（胜率 <40% 或均值 <-1%）"
        if s.get("basis") == "absolute":
            ev += "。⚠️ 该口径为绝对收益，只说明「策略自身在变差」，不能读作 alpha 衰减"
        items.append(ActionItem(
            title=f"策略[{s.get('name')}] 健康度[{status}]：需复核是否失效",
            category="strategy",
            priority="P0" if status == "drift" else "P1",
            expected_impact=(
                "策略衰减期继续按原档位执行会放大回撤；确认失效应走处置台账（归档/改造），"
                "而非继续沿用"
            ),
            evidence=ev,
            target=f"picks/strategy_registry + {s.get('source')}",
            proposed_change=(
                "对照 docs/strategy/strategy-registry.md 该条目的「失效判据」人工复核；"
                "确认失效则在处置台账追加一行（含证据链与样本边界），并按三选一动作处置"
            ),
        ))
    return items
