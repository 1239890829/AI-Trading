"""运行时参数覆盖层（进程内、免重启）。

## 为什么放在 core 而不是 services

读覆盖值的是**业务模块**（`picks/engine`、`picks/intraday_opportunity` …），
而覆盖值的来源是 services 层的 `agent_params`（有 DB 依赖）。依赖方向必须单向，
否则 picks 反向依赖 services 会引入循环。所以：

- **本体**（一个进程内 dict）放 core，无任何 app 内依赖；
- **写入方**是 `services/agent_params.refresh_runtime_overrides`（变更生效/回滚后调用）；
- **读取方**是各业务模块的默认值解析点。

## 纪律

1. **未设置的 key 一律回落调用方传入的默认值**（= 代码里的常量）⇒
   「没有覆盖层」与「覆盖层为空」行为完全一致，不会出现"参数没了值也没了"。
2. 只承载**标量**（int/float/bool/str）。结构化参数（如 style_offsets 的 JSON）
   仍走各自模块的 provider 通道（`style_router.set_override_provider`），不从这里过
   —— 在这里塞 dict 会让"类型契约"变成随便什么都行。
3. 覆盖层是**内存态**：进程重启后为空，由 `agent_params.refresh_runtime_overrides`
   在 lifespan 里按库中已生效值重新注入（同一个入口，不新增第二条回填路径）。
4. **不在这里做校验**：值域校验属于写入方（`agent_params._validate`，非法拒绝落库）。
   读取方只管"有没有、是不是数"，读到脏值时的兜底是回落默认值并记一条日志
   （宁可回落也不要让进程带着非法参数跑）。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

#: 覆盖层：key → 标量值。只有 agent_params 写入。
_overrides: dict[str, Any] = {}


def set_overrides(values: Mapping[str, Any]) -> None:
    """**全量替换**覆盖层（不是合并）。

    全量替换是有意的：合并会让"删掉一条覆盖"变成无法表达的操作
    （覆盖层与库中已生效集合是同一份真相，直接对齐即可）。
    """
    global _overrides
    _overrides = dict(values)


def clear() -> None:
    """清空覆盖层（测试/停机用）。"""
    global _overrides
    _overrides = {}


def get(key: str, default: T) -> T:
    """取覆盖值，未设置或类型不符 → 回落默认值。

    类型检查用 `isinstance(default, type(v))` 同族判定：默认值是 float 时
    接受 int（JSON 里 3 与 3.0 等价），但**不接受** str/bool 混入数值参数
    —— Python 里 `True` 是 `int` 的实例，故对 bool 特判排除。
    """
    if key not in _overrides:
        return default
    v = _overrides[key]
    if isinstance(default, bool) or isinstance(v, bool):
        if isinstance(v, bool) and isinstance(default, bool):
            return v  # type: ignore[return-value]
    elif isinstance(default, (int, float)) and isinstance(v, (int, float)):
        return v  # type: ignore[return-value]
    elif isinstance(default, str) and isinstance(v, str):
        return v  # type: ignore[return-value]
    log.warning("runtime_params: %s 覆盖值类型不符（%r），回落默认 %r", key, v, default)
    return default


def snapshot() -> dict[str, Any]:
    """当前覆盖层快照（诊断/接口展示用）。"""
    return dict(_overrides)
