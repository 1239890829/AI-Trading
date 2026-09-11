"""applied 半自动写回提示（策略进化 P1 方向 5）。

## 方案边界（不做什么）

方案明确**不做全自动写回引擎**（§方向 5）——权重/阈值生效前必须过人工确认，
防过拟合 + 保审计。本模块做的是「半自动」的前半段：

1. 改进项被置为 `applied` 时，从其标题/处置说明里识别「参数 当前值→建议值」
   意图，匹配**参数注册表**（已知可调目标 → 文件 + 运行时当前值）；
2. 生成 diff 提示 `{param, file, current, suggested}` 挂到 PATCH 响应；
3. 运行时值读取失败/参数不认识 → 显式 `unresolved`，绝不臆造当前值。

「一键确认」即现有的 PATCH applied 动作本身（改进项处置闭环已留痕
resolved_at/resolution_note）；代码文件的实际修改仍由人执行——
这正是方案把 P2 walk-forward 门禁排在前面的原因。

匹配模式（title/note 文本）：`PARAM=旧值 → 新值`、`PARAM 旧值→新值`、
`PARAM 由旧值 调整为 新值`。箭头两侧数字（int/float）。

## S2-11：载荷化（原先"只产 diff 提示"的缺口）

原实现只给 `{param, file, current, suggested}` —— 人拿到后仍不知道
**改了会影响谁、怎么验证、怎么回滚、以及到底改没改**。于是 `applied` 沦为一个
没有内容的动作：点了采纳，响应里飘过一串数字，事后无从追问。

现在每条 diff 补齐决策所需的四件事（`risk` / `consumers` / `verify` / `rollback`），
并新增 `landed`（运行时值是否已等于建议值）——**落地与否可复核**，
"采纳率"这才从虚荣指标变成真实指标。

⚠️ 边界不变：本模块**绝不自动改代码**。它只负责把证据与后果摆到眼前，
实际修改仍由人执行（方案 §方向 5 明确排除全自动写回）。

⚠️ 载荷**刻意不落库**：`build_param_diff` 是纯函数（文本解析 + 运行时读值），
title/note 在表里，载荷随时可重算。为一个可重算物加列是给未来留迁移债。
"""
from __future__ import annotations

import importlib
import re

_PARAM_SUGGEST_RE = re.compile(
    r"\b(?P<param>[A-Z][A-Z0-9_]{2,})\s*(?:=|:)?\s*"
    r"(?P<cur>\d+(?:\.\d+)?)\s*(?:→|->|调[整至至为]|改为)\s*(?P<sug>\d+(?:\.\d+)?)"
)

#: 已知可参数化目标：参数名 → (模块路径, 文件展示名)。当前值运行时读取，
#: 模块读不到 → unresolved（不猜）。新增可调参数时在此登记。
PARAM_TARGETS: dict[str, tuple[str, str]] = {
    "RED_DEV_10D": ("app.picks.halt_risk", "app/picks/halt_risk.py"),
    "PENALTY_Y1": ("app.picks.halt_risk", "app/picks/halt_risk.py"),
    "PENALTY_Y3": ("app.picks.halt_risk", "app/picks/halt_risk.py"),
    "YELLOW_DEV_10D_LO": ("app.picks.halt_risk", "app/picks/halt_risk.py"),
    "FLOW_SURGE_YI": ("app.picks.watcher", "app/picks/watcher.py"),
    "CUSUM_THRESHOLD": ("app.picks.signal_health", "app/picks/signal_health.py"),
    "CUSUM_DELTA": ("app.picks.signal_health", "app/picks/signal_health.py"),
}


#: 模块级默认元信息：`risk` 风险等级 / `consumers` 下游消费者 / `verify` 验证方式。
#: 参数级差异用 `PARAM_META` 覆盖；两者都没有时退回 `MODULE_DEFAULT_FALLBACK`，
#: **不臆造**——没有就标 `unknown`，而不是编一个听起来合理的验证步骤。
MODULE_META: dict[str, dict] = {
    "app.picks.halt_risk": {
        "risk": "high",
        "consumers": ["每日精选六维评分", "风控闸门（halt_risk）", "出场纪律"],
        "verify": "改后重跑 pytest 全量 + 观察 10 个交易日的红黄牌命中率与误杀率",
    },
    "app.picks.signal_health": {
        "risk": "medium",
        "consumers": ["策略健康度监控", "CUSUM 下漂告警", "自动改进项生成"],
        "verify": "改后重跑 pytest 全量 + 回看近 20 组合日的告警是否仍合理（避免告警消失或刷屏）",
    },
    "app.picks.watcher": {
        "risk": "medium",
        "consumers": ["盘中跟踪台账", "首见登记与收盘清算"],
        "verify": "改后重跑 pytest 全量 + 观察 5 个交易日的盘中登记量与清算 verdict 分布",
    },
}

MODULE_DEFAULT_FALLBACK: dict = {
    "risk": "unknown",
    "consumers": [],
    "verify": "未登记验证方式——改动前请先确认该参数的消费方与验证口径",
}

#: 参数级覆盖（只写与模块默认不同的部分）
PARAM_META: dict[str, dict] = {
    # 监控类参数改错只会让告警变钝/变吵，不会直接影响下单与评分
    "CUSUM_THRESHOLD": {"risk": "low"},
    "CUSUM_DELTA": {"risk": "low"},
}


def param_meta(module_path: str, param: str) -> dict:
    """取参数元信息（模块默认 ← 参数覆盖），缺则退回显式 unknown。"""
    base = dict(MODULE_META.get(module_path, MODULE_DEFAULT_FALLBACK))
    base.update(PARAM_META.get(param, {}))
    return base


def _current_value(module_path: str, param: str) -> float | None:
    try:
        mod = importlib.import_module(module_path)
        val = getattr(mod, param)
        return float(val)
    except Exception:  # noqa: BLE001 —— 读不到就是读不到，不臆造
        return None


def build_param_diff(text: str) -> list[dict]:
    """从改进项文本提取参数调整意图 → diff 提示列表（无匹配返回空）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _PARAM_SUGGEST_RE.finditer(text or ""):
        param = m.group("param")
        if param in seen or param not in PARAM_TARGETS:
            continue
        seen.add(param)
        module_path, file_name = PARAM_TARGETS[param]
        cur_runtime = _current_value(module_path, param)
        cur_text = float(m.group("cur"))
        sug = float(m.group("sug"))
        meta = param_meta(module_path, param)
        out.append({
            "param": param,
            "file": file_name,
            "current": cur_runtime,
            "current_in_text": cur_text,
            "suggested": sug,
            "resolved": cur_runtime is not None,
            # 漂移提示：文本里的当前值与运行时实际值不符 → 改动前必须核对
            "stale": cur_runtime is not None and abs(cur_runtime - cur_text) > 1e-9,
            # 落地核对：运行时值是否已等于建议值。刚标记 applied 时为 False 是正常的
            # （人还没改）；**持续**为 False 才是「标了采纳却没落地」——这才是要抓的。
            "landed": cur_runtime is not None and abs(cur_runtime - sug) < 1e-9,
            "risk": meta["risk"],
            "consumers": list(meta["consumers"]),
            "verify": meta["verify"],
            "rollback": (f"改回 {param}={cur_runtime} 并重启服务（8000）"
                         if cur_runtime is not None else
                         f"改回 {param} 原值并重启服务（当前值读取失败，须人工确认原值）"),
        })
    return out


def build_applied_payload(text: str) -> dict:
    """`applied` 处置的完整载荷（S2-11）。

    与 `build_param_diff` 的区别：后者给"改哪些参数"，本函数给"**这次采纳意味着什么**"
    —— 风险、未识别项、落地状态、以及一条明确的边界声明（系统不会自动改代码）。

    返回 `{"available", "items", "counts", "unresolved", "summary", "caveat"}`；
    `unresolved` 是**文本里提到了但不在参数注册表**的疑似参数（人看得见，系统改不了），
    显式列出而不是静默丢弃——否则用户会以为系统已经处理了。
    """
    items = build_param_diff(text)
    mentioned = {m.group("param") for m in _PARAM_SUGGEST_RE.finditer(text or "")}
    unresolved = sorted(mentioned - {i["param"] for i in items})
    counts = {"total": len(items), "high": 0, "medium": 0, "low": 0, "unknown": 0,
              "landed": 0, "stale": 0}
    for i in items:
        counts[i["risk"]] = counts.get(i["risk"], 0) + 1
        counts["landed"] += int(bool(i["landed"]))
        counts["stale"] += int(bool(i["stale"]))

    if not items:
        summary = ("未识别到可写回的参数调整（可能本项不是参数类改进，"
                   "或参数未在注册表中登记）")
    else:
        risky = [i["param"] for i in items if i["risk"] == "high"]
        tail = f"；其中高风险 {len(risky)} 项（{', '.join(risky)}）须先过人工复核" if risky else ""
        summary = (f"{len(items)} 项参数待落地（高风险 {counts['high']} / "
                   f"中 {counts['medium']} / 低 {counts['low']}）{tail}")

    return {
        "available": bool(items),
        "items": items,
        "counts": counts,
        "unresolved": unresolved,
        "summary": summary,
        "caveat": "系统不会自动修改代码——以上为**待人工执行**的变更清单；"
                  "`landed` 由运行时值判定，改完后重取本项即可看到它翻转",
    }


def audit_applied_landed(rows: list[dict]) -> dict:
    """**落地核对**（S2-11）：被标记 `applied` 的改进项，参数真的改了吗？

    这是"采纳率"从虚荣指标变真实指标的关键一环。此前 `applied` 只是一个状态位——
    点了就算采纳，至于代码改没改、参数生效没生效，无人知晓也无从追问，
    于是「采纳率 <20% ⇒ 该维度疑似产出噪音」这条演进建议建立在**未验证的动作**上。

    :param rows: 改进项行（至少含 `status` / `title` / `resolution_note`）
    :returns: `{"total_applied", "landed", "not_landed", "no_param_intent", "items", "note"}`
        —— `no_param_intent` 是**本来就不涉及参数**的采纳项（如流程/数据类改进），
        它们既不算落地也不算未落地，**单列**以免稀释分母。
    """
    landed, not_landed, no_intent = [], [], []
    for r in rows or []:
        if (r.get("status") or "") != "applied":
            continue
        text = f"{r.get('title') or ''} {r.get('resolution_note') or ''}"
        items = build_param_diff(text)
        if not items:
            no_intent.append({
                "id": r.get("id"), "title": r.get("title"),
                "reason": "未识别到参数调整意图（流程/数据/策略类改进，不适用参数落地核对）",
            })
            continue
        done = [i for i in items if i["landed"]]
        entry = {
            "id": r.get("id"), "title": r.get("title"),
            "params": [i["param"] for i in items],
            "pending": [i["param"] for i in items if not i["landed"]],
            "resolved": all(i["resolved"] for i in items),
        }
        (landed if len(done) == len(items) else not_landed).append(entry)

    total = len(landed) + len(not_landed)
    return {
        "total_applied": len(landed) + len(not_landed) + len(no_intent),
        "with_param_intent": total,
        "landed": len(landed),
        "not_landed": len(not_landed),
        "no_param_intent": len(no_intent),
        "items": {"landed": landed, "not_landed": not_landed, "no_param_intent": no_intent},
        # ⚠️ 判空必须用 **total_applied**（含无参数意图者），不能用 `total`
        # （只含有参数意图者）：否则 3 条流程类 applied 改进项会被报成
        # "尚无 applied 改进项"——明明有采纳、却说没有，比不报更糟（2026-09-11 实测）。
        "note": None if (len(landed) + len(not_landed) + len(no_intent)) else "尚无 applied 改进项",
        "caveat": "`not_landed` = 标记已采纳但运行时值仍不等于建议值；"
                  "参数类改进须以运行时值为准，不以状态位为准",
    }
