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
        out.append({
            "param": param,
            "file": file_name,
            "current": cur_runtime,
            "current_in_text": cur_text,
            "suggested": sug,
            "resolved": cur_runtime is not None,
            # 漂移提示：文本里的当前值与运行时实际值不符 → 改动前必须核对
            "stale": cur_runtime is not None and abs(cur_runtime - cur_text) > 1e-9,
        })
    return out
