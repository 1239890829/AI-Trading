"""助手「系统地图」单一真相源（2026-09-11 建立）。

## 为什么单独抽一个模块
助手的模块图此前是 `prompt.py` 里的一段**手写字符串**，没有任何东西保证它
跟着系统变——实测已经漂移：地图上还写着「每日精选 /picks」「研究 /research」，
而这两条路由在 2026-09-01 / 09-08 就已 302 下线（现役是 /hunting 与 /agent）。
模型拿着过期地图回答「这个功能在哪」，用户按图索骥必然找不到。

## 约束（由 tests/test_assistant.py 守卫，不靠人记）
- `MODULES` 的 path 集合必须与前端 `lib/nav-targets.ts::NAV_ALLOWED_PATHS` 一致
  —— 前端是导航的唯一实现方，后端只负责"说得出有哪些页"；
- 每个模块的 detail 里点名的功能，若要能跳转，必须用 `NAV_WORDS` 里的标准词；
- 新增/下线页面时**先改这里**（或改前端），守卫测试会立刻失败并指出差异。

单一真相源的意义：模块图只在这里写一遍，`prompt.py` 渲染它、测试校验它——
不再有"文档一份、提示词一份、前端一份"的三处漂移。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    path: str
    name: str
    detail: str


# 与前端 NAV_ALLOWED_PATHS 一一对应（顺序即导航顺序）。
MODULES: tuple[Module, ...] = (
    Module(
        "/workbench",
        "工作台",
        "自选股、实时行情、个股详情（分时/K线/资金/盘口/公告新闻/财务）、模拟交易",
    ),
    Module(
        "/tape",
        "盘面",
        "情绪周期、涨停池/跌停池/炸板池、题材梯队看板、题材人气榜、龙虎榜",
    ),
    Module(
        "/market",
        "市场",
        "市场概览、市场资金、板块排行、热力云图、事件面板",
    ),
    Module(
        "/hunting",
        "猎场",
        "每日精选（六维评分/梯队/阶段/空仓闸门/出场纪律）、盘中跟踪（盘前简报/盘中机会/盘中提醒）",
    ),
    Module(
        "/agent",
        "交易智能体",
        "任务中心、复盘报告、预警规则与告警、参数配置",
    ),
)

# 个股详情不是独立路由（工作台右面板展开），单列一行说明导航方式。
STOCK_DETAIL_NOTE = (
    "个股详情：任意页面点击标的 → 工作台右面板展开；指数需带市场前缀（如 sh000001）"
)


def render_module_map() -> str:
    """渲染进系统提示的「系统模块」段。"""
    lines = ["## 系统模块（回答\"项目怎么用/某功能在哪\"类问题时依据此图）"]
    for m in MODULES:
        lines.append(f"- {m.name} {m.path}：{m.detail}")
    lines.append(f"- {STOCK_DETAIL_NOTE}")
    return "\n".join(lines)


def module_paths() -> tuple[str, ...]:
    return tuple(m.path for m in MODULES)
